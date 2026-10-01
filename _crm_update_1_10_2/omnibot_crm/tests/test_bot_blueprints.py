"""A source profile must never invent prices, credentials or a second bot."""
import hashlib
import hmac
import json
import os
import re
import time

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from crm.app import create_app
from crm.bot_blueprints import BLUEPRINTS
from crm.bot_sources import food_porn_source
from crm.bot_settings import runtime_settings
from crm.config import Settings
from crm.db import Base
from crm.models import Admin, Bot, BotModule, BotPaymentProvider, BotSetting, IngestEvent, Order, Payment
from crm.security import hash_password


def test_profiles_are_idempotent_drafts_and_module_events_are_scoped(tmp_path):
    signing = 'a-unique-telegram-signature-for-this-bot'
    config = Settings(database_url=f'sqlite:///{tmp_path / "crm.db"}', session_secret='s'*40,
                      cookie_secure=False, encryption_key=Fernet.generate_key().decode(),
                      ingest_secrets={'food_porn': signing},
                      food_porn_source_database_url='sqlite:///legacy-source.db')
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='owner@example.com', password_hash=hash_password('strong-password-123')))
        db.add_all([Bot(key='food_porn', name='Food Porn'), Bot(key='other_bot', name='Other')])
        db.commit()

    with TestClient(app) as client:
        token = re.search(r'name="token" value="([^"]+)', client.get('/login').text).group(1)
        assert client.post('/login', data={'token': token, 'email': 'owner@example.com',
                                          'password': 'strong-password-123'}, follow_redirects=False).status_code == 303
        page = client.get('/bots/1/configure')
        assert page.status_code == 200 and 'Обои-манифестации' in page.text
        token = re.search(r'name="token" value="([^"]+)', page.text).group(1)
        for profile in BLUEPRINTS:
            for _ in range(2):
                assert client.post(f'/bots/1/blueprints/{profile}',
                    data={'token': token}, follow_redirects=False).status_code == 303
        with app.state.session_factory() as db:
            rows = db.scalars(select(BotSetting).where(BotSetting.bot_id == 1)).all()
            assert len(rows) == sum(len(p.parameters) for p in BLUEPRINTS.values())
            assert all(not r.is_configured and r.value == '' for r in rows)
            assert runtime_settings(db, 1, config) == {}
            assert db.scalars(select(BotSetting).where(BotSetting.bot_id == 2)).all() == []
            assert [m.key for m in db.scalars(select(BotModule).where(BotModule.bot_id == 1)
                                            .order_by(BotModule.id))] == ['menu', 'wallpaper']
        assert 'нужен ввод' in client.get('/bots/1/configure').text
        assert client.post('/bots/1/settings', data={'token': token, 'key': 'menu_price',
            'label': 'Цена меню', 'kind': 'number', 'group_name': 'menu', 'value': '700.00'},
            follow_redirects=False).status_code == 303
        assert client.post('/bots/1/settings', data={'token': token, 'key': 'openai_api_key',
            'label': 'OpenAI', 'kind': 'secret', 'group_name': 'telegram_core', 'value': 'private-openai-value'},
            follow_redirects=False).status_code == 303
        assert client.post('/bots/1/settings', data={'token': token, 'key': 'source_database_url',
            'label': 'Адрес исходной базы', 'kind': 'secret', 'group_name': 'telegram_core',
            'value': 'sqlite:///new-source.db'}, follow_redirects=False).status_code == 303
        with app.state.session_factory() as db:
            assert runtime_settings(db, 1, config) == {'menu_price': 700.0,
                                                        'openai_api_key': 'private-openai-value'}
            assert food_porn_source(db, config) == 'sqlite:///new-source.db'
            assert 'new-source.db' not in db.scalar(select(BotSetting).where(
                BotSetting.bot_id == 1, BotSetting.key == 'source_database_url')).value
        assert 'private-openai-value' not in client.get('/bots/1/configure').text
        assert client.post('/bots/1/acquirers', data={'token': token, 'provider': 'redsys',
            'merchant_code': 'shop', 'terminal': '001', 'secret_key': 'private-redsys',
            'currency': '978', 'enabled': 'true'}, follow_redirects=False).status_code == 303
        with app.state.session_factory() as db:
            provider = db.scalar(select(BotPaymentProvider))
            assert provider.provider == 'redsys' and 'private-redsys' not in provider.encrypted_credentials

        def event(event_id, kind, data):
            raw = json.dumps({'event_id': event_id, 'type': kind,
                              'data': {'external_user_id': 'customer-1', **data}}).encode()
            stamp = str(int(time.time()))
            sig = hmac.new(signing.encode(), stamp.encode() + b'.' + raw,
                           hashlib.sha256).hexdigest()
            return client.post('/api/v1/events', content=raw, headers={
                'X-Bot-Key': 'food_porn', 'X-Timestamp': stamp, 'X-Signature': sig})

        assert event('bad-1', 'customer.upsert', {'module_key': 'unknown'}).status_code == 422
        assert event('user-1', 'customer.upsert', {'name': 'Alice', 'module_key': 'menu'}).status_code == 200
        assert event('order-1', 'order.upsert', {'module_key': 'menu', 'external_id': 'o-1',
            'title': 'Food', 'amount': '700.00', 'currency': 'UAH'}).status_code == 200
        assert event('payment-bad', 'payment.upsert', {'module_key': 'wallpaper', 'external_id': 'p-1',
            'order_external_id': 'o-1', 'amount': '700.00', 'currency': 'UAH'}).status_code == 409
        assert event('payment-1', 'payment.upsert', {'module_key': 'menu', 'external_id': 'p-1',
            'order_external_id': 'o-1', 'provider': 'redsys', 'amount': '700.00', 'currency': 'UAH',
            'status': 'paid'}).status_code == 200
        with app.state.session_factory() as db:
            assert db.scalar(select(Order)).module_key == 'menu'
            payment = db.scalar(select(Payment))
            assert payment.module_key == 'menu' and payment.status == 'reported_paid'
            assert db.scalar(select(IngestEvent).where(IngestEvent.event_id == 'payment-1')).module_key == 'menu'
        assert '2 событий за 7 дней' in client.get('/bots/1').text or '3 событий за 7 дней' in client.get('/bots/1').text
    app.state.engine.dispose()
