import hashlib
import hmac
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

from fastapi.testclient import TestClient
from sqlalchemy import select

from crm.app import create_app
from crm.config import Settings
from crm.db import Base
from crm.models import (Admin, Bot, BotSetting, Customer, Identity, IngestEvent,
                        Order, Payment, PaymentVerification)
from crm.security import hash_password


def sign(path, key='food_porn', secret='a'*32):
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), (stamp + '.GET.' + path).encode(), hashlib.sha256).hexdigest()
    return {'X-Bot-Key': key, 'X-Timestamp': stamp, 'X-Signature': signature}


def test_read_api_is_scoped_and_never_exposes_secrets(tmp_path):
    config = Settings(database_url='sqlite:///' + str(tmp_path/'api.db'), session_secret='s'*40,
                      cookie_secure=False, ingest_secrets={'food_porn': 'a'*32, 'other': 'b'*32})
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='owner@example.com', password_hash=hash_password('password-for-testing')))
        bot = Bot(key='food_porn', name='Food Porn'); other = Bot(key='other', name='Other')
        db.add_all([bot, other]); db.flush()
        customer = Customer(name='Sensitive customer'); db.add(customer); db.flush()
        db.add(Identity(bot_id=bot.id, customer_id=customer.id, external_user_id='123'))
        db.add_all([BotSetting(bot_id=bot.id, key='openai_api_key', kind='secret',
            value='encrypted-sensitive-blob', label='API key', group_name='api'),
            BotSetting(bot_id=bot.id, key='welcome_message', kind='text',
            value='Hello', label='Greeting', group_name='dialogue')])
        db.add(IngestEvent(bot_id=bot.id, event_id='heartbeat-1', event_type='bot.heartbeat'))
        db.add(Order(bot_id=bot.id, customer_id=customer.id, external_id='order-1',
                     amount=Decimal('10.00'), currency='USD', title='Menu'))
        payment = Payment(bot_id=bot.id, customer_id=customer.id, external_id='payment-1',
                          amount=Decimal('10.00'), currency='USD', provider='portmone')
        db.add(payment); db.flush()
        db.add(PaymentVerification(payment_id=payment.id, provider='portmone',
            provider_reference='reference-1', amount=Decimal('10.00'), currency='USD',
            status='verified', created_at=datetime.now(timezone.utc)))
        db.commit()
    with TestClient(app) as client:
        path = '/api/v1/bots/food_porn/settings'
        assert client.get(path).status_code == 401
        response = client.get(path, headers=sign(path))
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert 'encrypted-sensitive-blob' not in response.text
        assert 'Sensitive customer' not in response.text
        assert next(r for r in response.json()['settings'] if r['key']=='openai_api_key')['value'] is None
        assert next(r for r in response.json()['settings'] if r['key']=='welcome_message')['value'] == 'Hello'
        assert client.get('/api/v1/bots/other/settings', headers=sign('/api/v1/bots/other/settings')).status_code == 404
        report = '/api/v1/bots/food_porn/overview?days=7'
        data = client.get(report, headers=sign(report)).json()
        assert data['customers'] == 1 and data['orders'] == 1
        assert data['verified_revenue'] == {'USD': '10.00'}
        assert len(data['event_series']) == 7 and data['events_by_type']['bot.heartbeat'] == 1
        assert 'Sensitive customer' not in str(data)
        assert client.get(report.replace('7','30'), headers=sign(report)).status_code == 401
        assert client.get('/bots/1?view=analytics').status_code == 401
        login = client.get('/login')
        token = re.search(r'name="token" value="([^"]+)', login.text).group(1)
        client.post('/login', data={'token': token, 'email': 'owner@example.com',
                                     'password': 'password-for-testing'})
        assert 'Динамика бота' in client.get('/bots/1?view=analytics').text
        assert 'Карта параметров' in client.get('/bots/1/configure').text
    app.state.engine.dispose()
