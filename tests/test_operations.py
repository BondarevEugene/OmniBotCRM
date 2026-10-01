import hashlib
import hmac
import json
import os
import re
import time
from decimal import Decimal

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from crm.app import create_app
from crm.config import Settings
from crm.db import Base
from crm.metrics import summary
from crm.models import Admin, Bot, BotIncident, Customer, Identity, Order, OrderItem, Payment, PaymentVerification, Product
from crm.payment_providers import VerificationError, verify_payment
from crm.security import hash_password


@pytest.fixture
def env(tmp_path):
    secret = 'ingest-secret-with-at-least-24-characters'
    config = Settings(database_url='sqlite:///' + str(tmp_path / 'crm.db'), session_secret='a' * 40,
        cookie_secure=False, ingest_secrets={'food_porn': secret}, bot_tokens={},
        portmone_login='login', portmone_password='password', portmone_payee_id='1185',
        wayforpay_merchant_account='merchant', wayforpay_secret_key='secret')
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add_all([Bot(key='food_porn', name='Food Porn'),
            Admin(email='test@example.com', password_hash=hash_password('long-unique-password'))])
        db.commit()
    client = TestClient(app)
    yield app, client, config
    client.close()
    app.state.engine.dispose()


def signed_event(client, config, event_id, kind, data):
    body = json.dumps({'event_id': event_id, 'type': kind, 'data': data}).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(config.ingest_secrets['food_porn'].encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    return client.post('/api/v1/events', content=body,
        headers={'X-Bot-Key': 'food_porn', 'X-Timestamp': stamp, 'X-Signature': signature})


def login(client):
    token = re.search(r'name="token" value="([^"]+)', client.get('/login').text).group(1)
    assert client.post('/login', data={'email': 'test@example.com', 'password': 'long-unique-password', 'token': token}).status_code == 200
    return re.search(r'name="token" value="([^"]+)', client.get('/integrations').text).group(1)


def test_bot_telemetry_reports_and_untrusted_payment(env):
    app, client, config = env
    assert signed_event(client, config, 'h-1', 'bot.heartbeat', {}).status_code == 200
    assert signed_event(client, config, 'e-1', 'bot.error', {'component': 'payment', 'severity': 'error', 'message': 'Timeout'}).status_code == 200
    assert signed_event(client, config, 'c-1', 'customer.upsert', {'external_user_id': '42', 'name': 'Клиент'}).status_code == 200
    assert signed_event(client, config, 'o-1', 'order.upsert', {'external_user_id': '42', 'external_id': 'order-42', 'title': 'Услуга', 'amount': '100', 'status': 'paid'}).status_code == 200
    assert signed_event(client, config, 'p-1', 'payment.upsert', {'external_user_id': '42', 'external_id': 'txn-42', 'order_external_id': 'order-42', 'provider': 'portmone', 'amount': '100', 'status': 'paid'}).status_code == 200
    with app.state.session_factory() as db:
        assert db.scalar(select(Order)).status == 'reported_paid'
        assert db.scalar(select(Payment)).status == 'reported_paid'
        assert summary(db)['finance'] == []
        assert db.scalar(select(BotIncident)).message == 'Timeout'
    assert client.get('/reports').status_code == 401
    token = login(client)
    for route in ['/integrations', '/commerce', '/reports', '/bots/1', '/reports/export.csv']:
        assert client.get(route).status_code == 200, route
    assert 'Не сверено' in client.get('/reports').text or 'НЕ СВЕРЕНО' in client.get('/reports').text
    assert 'НЕ СВЕРЕНО' in client.get('/reports').text
    assert client.post('/bots/1/incidents/1/resolve', data={'token': token}, follow_redirects=False).status_code == 303


class FakeResponse:
    def __init__(self, result): self.result = result
    def raise_for_status(self): pass
    def json(self): return self.result


class FakeTransport:
    def __init__(self, result): self.result = result
    def post(self, url, **kwargs): return FakeResponse(self.result)


def test_portmone_requires_merchant_reference_amount_and_no_chargeback(env):
    app, client, config = env
    with app.state.session_factory() as db:
        bot = db.scalar(select(Bot)); customer = Customer(source='food_porn'); db.add(customer); db.flush()
        order = Order(bot_id=bot.id, customer_id=customer.id, external_id='A-10', title='Product', amount=Decimal('100.00'), currency='UAH')
        db.add(order); db.flush()
        payment = Payment(bot_id=bot.id, customer_id=customer.id, order_id=order.id, external_id='T-10', provider='portmone', amount=Decimal('100.00'), currency='UAH', status='reported_paid')
        db.add(payment); db.commit()
        valid = {'payee_id': '1185', 'shopOrderNumber': 'A-10', 'status': 'PAYED', 'billAmount': '100.00', 'shopBillId': 'B-1', 'chargeback': 'N'}
        for change in ({'payee_id': 'other'}, {'billAmount': '1'}, {'chargeback': 'Y'}, {'shopOrderNumber': 'other'}):
            with pytest.raises(VerificationError):
                verify_payment(db, payment, config, FakeTransport([{**valid, **change}]))
            assert db.scalar(select(PaymentVerification)) is None
        assert verify_payment(db, payment, config, FakeTransport([valid])) == 'B-1'
        db.commit()
        assert summary(db)['finance'][0]['revenue'] == Decimal('100.00')


def test_wayforpay_rejects_bad_signature(env):
    app, client, config = env
    with app.state.session_factory() as db:
        bot = db.scalar(select(Bot)); customer = Customer(); db.add(customer); db.flush()
        order = Order(bot_id=bot.id, customer_id=customer.id, external_id='W-1', title='Product', amount=Decimal('25'), currency='UAH')
        db.add(order); db.flush()
        payment = Payment(bot_id=bot.id, customer_id=customer.id, order_id=order.id, external_id='W-1', provider='wayforpay', amount=Decimal('25'), currency='UAH')
        db.add(payment); db.commit()
        row = {'merchantAccount': 'merchant', 'orderReference': 'W-1', 'amount': '25', 'currency': 'UAH',
               'authCode': 'ABC', 'cardPan': '44****', 'transactionStatus': 'Approved', 'reasonCode': '1100', 'merchantSignature': 'invalid'}
        with pytest.raises(VerificationError, match='подпись'):
            verify_payment(db, payment, config, FakeTransport(row))
        signed = ';'.join(str(row[key]) for key in ('merchantAccount', 'orderReference', 'amount', 'currency', 'authCode', 'cardPan', 'transactionStatus', 'reasonCode'))
        row['merchantSignature'] = hmac.new(b'secret', signed.encode(), hashlib.md5).hexdigest()
        assert verify_payment(db, payment, config, FakeTransport(row)) == 'ABC'


def test_catalog_order_items_and_mobile_view(env):
    app, client, config = env
    assert signed_event(client, config, 'catalog-1', 'product.upsert',
                        {'sku': 'S-1', 'title': 'Подписка', 'price': '50.00', 'currency': 'UAH'}).status_code == 200
    assert signed_event(client, config, 'customer-1', 'customer.upsert', {'external_user_id': '25'}).status_code == 200
    order = {'external_user_id': '25', 'external_id': 'order-25', 'title': 'Подписка',
             'amount': '100', 'currency': 'UAH', 'items': [{'sku': 'S-1', 'quantity': 2, 'unit_price': '50'}]}
    assert signed_event(client, config, 'order-bad', 'order.upsert', {**order, 'amount': '90'}).status_code == 422
    assert signed_event(client, config, 'order-good', 'order.upsert', order).status_code == 200
    with app.state.session_factory() as db:
        assert db.scalar(select(Product)).sku == 'S-1'
        assert db.scalar(select(OrderItem)).quantity == 2
        assert db.scalar(select(Order)).amount == Decimal('100')
    login(client)
    assert 'Подписка' in client.get('/commerce').text
    assert 'Подписка' in client.get('/commerce/orders/1').text
    assert client.get('/m/t/products').status_code == 200
    assert client.get('/m/t/order_items').status_code == 200
