import hashlib
import hmac
import json
import os
import re
import time
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

from crm.app import create_app
from crm.bot_graph import template
from crm.config import Settings
from crm.config_releases import save_release
from crm.db import Base
from crm.models import (Admin, Bot, BotDialogueEvent, BotDialogueSession, BotGraphRelease,
                        BotScenario, Customer, Identity, Order, Payment, PaymentVerification, Ticket)
from crm.payment_providers import VerificationError, verify_payment
from crm.security import hash_password


def signed(client, url, payload, key='food_porn', secret='a'*32):
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
    return client.post(url, content=raw, headers={'X-Bot-Key': key, 'X-Timestamp': stamp,
        'X-Signature': signature, 'Content-Type': 'application/json'})


@pytest.fixture
def crm(tmp_path):
    config = Settings(database_url='sqlite:///' + str(tmp_path/'operations.db'),
        session_secret='s'*40, cookie_secure=False, encryption_key=Fernet.generate_key().decode(),
        ingest_secrets={'food_porn': 'a'*32, 'other': 'b'*32}, bot_tokens={'food_porn': '123:TEST'})
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='owner@example.com', password_hash=hash_password('long-test-password')))
        db.add_all([Bot(key='food_porn', name='Food Porn'), Bot(key='other', name='Other')])
        db.flush()
        db.add(BotScenario(bot_id=1, key='consult', name='Консультант'))
        db.commit()
    with TestClient(app) as client:
        token = re.search(r'name="token" value="([^"]+)', client.get('/login').text).group(1)
        client.post('/login', data={'token': token, 'email': 'owner@example.com',
            'password': 'long-test-password'})
        yield app, client, config
    app.state.engine.dispose()


def test_published_dialogue_pins_version_creates_customer_and_handoff(crm):
    app, client, _ = crm
    path = '/api/bots/1/scenarios/1/canvas'
    csrf = re.search(r'data-csrf="([^"]+)', client.get('/bots/1/scenarios/1/canvas').text).group(1)
    graph = template()
    graph['nodes'] = [
        dict(id='launch', type='start'),
        dict(id='hello', type='message', text='Здравствуйте!'),
        dict(id='ask', type='question', text='Ваше имя?', variable='name'),
        dict(id='choice', type='buttons', label='Что дальше, {{name}}?', buttons=['Оператор', 'Готово']),
        dict(id='handoff', type='handoff', text='Передаю, {{name}}', queue='sales'),
        dict(id='finish', type='end')]
    graph['edges'] = [dict(id=f'e{i}', source=s, target=t, handle=h) for i, (s, t, h) in enumerate([
        ('launch', 'hello', 'next'), ('hello', 'ask', 'next'), ('ask', 'choice', 'next'),
        ('choice', 'handoff', 'button_0'), ('choice', 'finish', 'button_1')])]
    assert client.put(path, json={'version': 0, 'graph': graph},
                      headers={'X-CSRF-Token': csrf}).status_code == 200
    assert client.post(path + '/publish', headers={'X-CSRF-Token': csrf}).json()['version'] == 1
    url = '/api/v1/bot-dialogue'
    def send(eid, text, user='42'):
        return signed(client, url, dict(event_id=eid, scenario_key='consult',
                                        external_user_id=user, text=text))
    assert send('1', '/start').json()['actions'] == [
        {'type': 'text', 'text': 'Здравствуйте!'}, {'type': 'text', 'text': 'Ваше имя?'}]
    assert send('1', '/start').json()['actions'][-1]['text'] == 'Ваше имя?'
    assert send('2', 'Евгений').json()['actions'][0]['buttons'] == ['Оператор', 'Готово']
    with app.state.session_factory() as db:
        # A newly published version must not change a dialogue already in progress.
        db.add(BotGraphRelease(bot_id=1, scenario_id=1, version=2,
            graph_json=json.dumps(template()), created_by=1))
        db.commit()
    result = send('3', 'Оператор').json()
    assert result['status'] == 'handoff' and result['release_version'] == 1
    assert result['actions'][0]['text'] == 'Передаю, Евгений'
    assert send('4', 'Привет').json()['restart_command'] == '/start'
    assert send('5', '/start').json()['release_version'] == 2
    assert signed(client, url, dict(event_id='6', scenario_key='consult',
        external_user_id='42', text='/start'), key='other', secret='b'*32).status_code == 404
    with app.state.session_factory() as db:
        assert db.scalar(select(Customer).where(Customer.name == 'Евгений')) is not None
        assert db.query(Identity).count() == 1
        assert db.query(Ticket).count() == 1
        assert db.query(BotDialogueEvent).count() == 5


def test_ack_and_per_bot_action_center(crm):
    app, client, config = crm
    with app.state.session_factory() as db:
        release = save_release(db, 1, {'schema': 1, 'settings': {}, 'modules': [],
            'scenarios': []}, 1, config.encryption_key)
        db.flush(); revision = release.revision
        customer = Customer(name='Bill'); db.add(customer); db.flush()
        db.add(Identity(bot_id=1, customer_id=customer.id, external_user_id='bill'))
        order = Order(bot_id=1, customer_id=customer.id, external_id='order',
            title='Service', amount=Decimal('5'), currency='USD')
        db.add(order); db.flush()
        db.add(Payment(bot_id=1, customer_id=customer.id, order_id=order.id,
            external_id='pending', amount=Decimal('5'), currency='USD', provider='paypal',
            status='reported_paid'))
        db.commit()
    html = client.get('/control-center?bot_id=1').text
    assert 'Не применена' in html and 'оплат ожидают проверки' in html
    response = signed(client, '/api/v1/config-applied', {'revision': revision, 'status': 'applied'})
    assert response.status_code == 200 and response.json()['status'] == 'applied'
    assert 'Актуальна' in client.get('/control-center?bot_id=1').text
    assert 'Bill' not in client.get('/control-center?bot_id=2').text
    assert signed(client, '/api/v1/config-applied', {'revision': 'f'*64, 'status': 'applied'}).json()['status'] == 'stale'


class FakeStarResponse:
    def __init__(self, rows): self.rows = rows
    def raise_for_status(self): pass
    def json(self): return {'ok': True, 'result': {'transactions': self.rows}}


class FakeStarTransport:
    def __init__(self, rows): self.rows = rows
    def get(self, url, **kwargs):
        assert 'getStarTransactions' in url
        return FakeStarResponse(self.rows)


def test_stars_proof_requires_amount_and_no_refund(crm):
    app, client, config = crm
    with app.state.session_factory() as db:
        customer = Customer(name='Star'); db.add(customer); db.flush()
        db.add(Identity(bot_id=1, customer_id=customer.id, external_user_id='42'))
        order = Order(bot_id=1, customer_id=customer.id, external_id='order-star',
            title='Digital', amount=Decimal('25'), currency='XTR')
        db.add(order); db.flush()
        payment = Payment(bot_id=1, customer_id=customer.id, order_id=order.id,
            external_id='charge_1', provider='telegram_stars', amount=Decimal('25'),
            currency='XTR', status='reported_paid')
        db.add(payment); db.flush()
        source = {'type': 'user', 'transaction_type': 'invoice_payment',
            'invoice_payload': 'order-star', 'user': {'id': 42}}
        with pytest.raises(VerificationError):
            verify_payment(db, payment, config, FakeStarTransport([{'id': 'charge_1',
                'amount': 25, 'source': source}, {'id': 'charge_1', 'amount': -25,
                'receiver': {'type': 'user'}}]))
        with pytest.raises(VerificationError):
            verify_payment(db, payment, config, FakeStarTransport([{'id': 'charge_1',
                'amount': 24, 'source': source}]))
        with pytest.raises(VerificationError):
            verify_payment(db, payment, config, FakeStarTransport([{'id': 'charge_1',
                'amount': 25, 'source': {**source, 'user': {'id': 55}}}]))
        assert verify_payment(db, payment, config, FakeStarTransport([{'id': 'charge_1',
            'amount': 25, 'source': source}])) == 'charge_1'
        db.commit()
        assert db.scalar(select(PaymentVerification)).amount == Decimal('25')
        assert db.get(Order, order.id).status == 'paid'


def test_upgrade_from_0012_preserves_existing_bot(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    configuration = Config('alembic.ini')
    path = tmp_path / 'upgrade.db'
    configuration.set_main_option('sqlalchemy.url', 'sqlite:///' + str(path))
    # env.py loads DATABASE_URL from Settings; use this database for both revisions.
    old_url = os.environ.get('DATABASE_URL')
    os.environ['DATABASE_URL'] = 'sqlite:///' + str(path)
    try:
        command.upgrade(configuration, '0012_bot_data_connections')
        engine = create_engine('sqlite:///' + str(path))
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO crm_bots (id, key, name, description, active, created_at) "
                "VALUES (97, 'old-bot', 'Existing', '', 1, CURRENT_TIMESTAMP)"))
        command.upgrade(configuration, 'head')
        with engine.connect() as connection:
            assert connection.execute(text('SELECT name FROM crm_bots WHERE id=97')).scalar_one() == 'Existing'
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0014_openai_observability'
        engine.dispose()
    finally:
        if old_url is None: os.environ.pop('DATABASE_URL', None)
        else: os.environ['DATABASE_URL'] = old_url
