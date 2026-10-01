"""Release isolation and a signed, deduplicated Meta lead-to-revenue flow."""
import hashlib
import hmac
import json
import os
import re
import time

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from crm.app import create_app
from crm.bot_settings import encrypt
from crm.config import Settings
from crm.db import Base
from crm.models import (Admin, Bot, BotConfigRelease, Customer, Deal, MarketingAdAccount,
                        MarketingCampaign, MarketingChannel, MarketingLead, MarketingTouch,
                        Payment, PaymentVerification, Pipeline, Stage)
from crm.security import hash_password


def owner(client):
    page = client.get('/login')
    token = re.search(r'name="token" value="([^"]+)', page.text).group(1)
    response = client.post('/login', data={'token': token, 'email': 'owner@example.org',
        'password': 'unique-password-123'}, follow_redirects=False)
    assert response.status_code == 303
    return re.search(r'name="token" value="([^"]+)', client.get('/').text).group(1)


def setup(tmp_path):
    config = Settings(database_url=f'sqlite:///{tmp_path / "crm.db"}', session_secret='s'*40,
                      cookie_secure=False, encryption_key=Fernet.generate_key().decode())
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='owner@example.org', password_hash=hash_password('unique-password-123')))
        db.commit()
    return app, config


def test_conditional_field_release_and_rollback(tmp_path):
    app, config = setup(tmp_path)
    with app.state.session_factory() as db:
        db.add(Bot(key='store', name='Store')); db.commit()
    secret = 's' * 32
    with TestClient(app) as client:
        token = owner(client)
        for key, kind, value, choices, visible_key, visible_value in [
            ('ingest_secret', 'secret', secret, '', '', ''),
            ('provider', 'choice', 'paypal', 'paypal\nliqpay', '', ''),
            ('paypal_title', 'text', 'PayPal enabled', '', 'provider', 'paypal')]:
            response = client.post('/bots/1/settings', data={'token': token, 'key': key, 'label': key,
                'kind': kind, 'value': value, 'rules_enabled': '1', 'choices': choices,
                'visible_if_key': visible_key, 'visible_if_value': visible_value,
                'required': 'true'}, follow_redirects=False)
            assert response.status_code == 303, response.text
        editor = client.get('/bots/1/configure')
        assert editor.status_code == 200 and 'data-preview-key="paypal_title"' in editor.text
        stamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), (stamp+'.GET./api/v1/bot-config').encode(), hashlib.sha256).hexdigest()
        headers = {'X-Bot-Key': 'store', 'X-Timestamp': stamp, 'X-Signature': signature}
        assert client.post('/bots/1/releases', data={'token': token}, follow_redirects=False).status_code == 303
        first = client.get('/api/v1/bot-config', headers=headers).json()
        assert first['release_version'] == 1 and first['settings']['paypal_title'] == 'PayPal enabled'
        assert client.post('/bots/1/settings', data={'token': token, 'key': 'provider',
            'label': 'provider', 'kind': 'choice', 'value': 'liqpay', 'rules_enabled': '1',
            'choices': 'paypal\nliqpay', 'required': 'true'}, follow_redirects=False).status_code == 303
        assert client.get('/api/v1/bot-config', headers=headers).json()['settings']['provider'] == 'paypal'
        assert client.post('/bots/1/releases', data={'token': token}, follow_redirects=False).status_code == 303
        second = client.get('/api/v1/bot-config', headers=headers).json()
        assert second['release_version'] == 2 and second['settings'] == {'provider': 'liqpay'}
        assert client.post('/bots/1/releases/1/rollback', data={'token': token},
            follow_redirects=False).status_code == 303
        third = client.get('/api/v1/bot-config', headers=headers).json()
        assert third['release_version'] == 3 and third['settings']['paypal_title'] == 'PayPal enabled'
        assert client.get('/api/v1/bot-config', headers={**headers,
            'If-None-Match': '"'+third['revision']+'"'}).status_code == 304
        with app.state.session_factory() as db:
            assert db.scalar(select(BotConfigRelease).where(BotConfigRelease.version == 3)).action == 'rollback'
    app.state.engine.dispose()


def test_signed_meta_lead_creates_customer_deal_and_attribution(tmp_path, monkeypatch):
    app, config = setup(tmp_path)
    with app.state.session_factory() as db:
        pipeline = Pipeline(name='Incoming'); db.add(pipeline); db.flush()
        db.add(Stage(pipeline_id=pipeline.id, name='New', position=1))
        channel = MarketingChannel(platform='facebook', account='Meta'); db.add(channel); db.flush()
        db.add(MarketingAdAccount(channel_id=channel.id, external_id='12345', currency='USD',
            api_version='v25.0', enabled=True,
            encrypted_credentials=encrypt(json.dumps({'access_token': 'ads-access'}), config.encryption_key)))
        db.commit()
    def graph(request):
        assert request.url.host == 'graph.facebook.com'
        assert request.headers['authorization'] == 'Bearer page-access'
        return httpx.Response(200, json={'id': '314159', 'campaign_id': '271828', 'form_id': '42',
            'ad_id': '35', 'field_data': [
                {'name': 'full_name', 'values': ['Alice']},
                {'name': 'email', 'values': ['alice@example.org']} ]})
    real_client = httpx.Client
    monkeypatch.setattr('crm.meta_leads.httpx.Client', lambda **kwargs:
        real_client(transport=httpx.MockTransport(graph), trust_env=False))
    body = json.dumps({'object': 'page', 'entry': [{'id': '987654', 'changes': [
        {'field': 'leadgen', 'value': {'leadgen_id': '314159', 'page_id': '987654', 'form_id': '42'}}]}]}).encode()
    signature = 'sha256=' + hmac.new(b'long-app-secret-for-testing', body, hashlib.sha256).hexdigest()
    with TestClient(app) as client:
        token = owner(client)
        assert client.post('/marketing/channels/1/meta-leads', data={'token': token,
            'page_id': '987654', 'pipeline_id': '1', 'page_access_token': 'page-access',
            'app_secret': 'long-app-secret-for-testing',
            'verify_token': 'a-unique-verify-token-at-least-24'},
            follow_redirects=False).status_code == 303
        assert client.post('/marketing/channels/1/ads-account', data={'token': token,
            'external_id': '12345', 'currency': 'USD', 'api_version': 'v25.0',
            'enabled': 'true'}, follow_redirects=False).status_code == 303
        assert client.get('/api/v1/meta/lead-ads/1', params={'hub.mode': 'subscribe',
            'hub.verify_token': 'a-unique-verify-token-at-least-24', 'hub.challenge': 'challenge'}).text == 'challenge'
        assert client.post('/api/v1/meta/lead-ads/1', content=body,
            headers={'X-Hub-Signature-256': 'sha256=bad'}).status_code == 401
        assert client.post('/api/v1/meta/lead-ads/1', content=body,
            headers={'X-Hub-Signature-256': signature}).json()['created'] == 1
        assert client.post('/api/v1/meta/lead-ads/1', content=body,
            headers={'X-Hub-Signature-256': signature}).json()['created'] == 0
        with app.state.session_factory() as db:
            lead = db.scalar(select(MarketingLead))
            customer = db.get(Customer, lead.customer_id)
            assert customer.email == 'alice@example.org' and not customer.marketing_consent
            assert db.scalar(select(Deal)).customer_id == customer.id
            campaign = db.scalar(select(MarketingCampaign))
            assert campaign.external_id == '271828'
            assert db.scalar(select(MarketingTouch)).campaign_id == campaign.id
            bot = Bot(key='shop', name='Shop'); db.add(bot); db.flush()
            pay = Payment(bot_id=bot.id, customer_id=customer.id, external_id='pay-42',
                provider='paypal', status='paid', amount=42, currency='USD')
            db.add(pay); db.flush()
            db.add(PaymentVerification(payment_id=pay.id, provider='paypal',
                provider_reference='capture-42', amount=42, currency='USD', status='verified'))
            db.commit()
        result = client.get('/marketing/attribution')
        assert result.status_code == 200 and '42.00 USD' in result.text
        assert '271828' in result.text
        assert client.get('/marketing/attribution.xlsx').status_code == 200
        assert client.get('/marketing').status_code == 200
    app.state.engine.dispose()
