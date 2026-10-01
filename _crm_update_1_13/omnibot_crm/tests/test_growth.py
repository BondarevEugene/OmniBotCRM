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
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from crm.app import create_app
from crm.config import Settings
from crm.db import Base
from crm.models import Admin, Bot, BotSetting, Customer, Invoice, MarketingMetric, MarketingTouch, Payment, PaymentVerification, Project, ProjectExpense, SeoKeyword, SeoRankSnapshot
from crm.security import hash_password


@pytest.fixture
def setup(tmp_path):
    key = Fernet.generate_key().decode()
    config = Settings(database_url='sqlite:///' + str(tmp_path/'growth.db'), session_secret='s'*40,
                      encryption_key=key, cookie_secure=False)
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add_all([Admin(email='owner@example.com', password_hash=hash_password('unique-password-123')),
                    Bot(key='food_porn', name='Food Porn')])
        db.commit()
    client = TestClient(app)
    login_page = client.get('/login')
    token = re.search(r'name="token" value="([^"]+)', login_page.text).group(1)
    assert client.post('/login', data={'email':'owner@example.com','password':'unique-password-123',
                                      'token':token}).status_code == 200
    token = re.search(r'name="token" value="([^"]+)', client.get('/').text).group(1)
    yield app, client, config, token
    client.close()
    app.state.engine.dispose()


def bot_event(client, secret, event_id, kind, data):
    raw = json.dumps({'event_id':event_id,'type':kind,'data':data}).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode()+b'.'+raw, hashlib.sha256).hexdigest()
    return client.post('/api/v1/events', content=raw, headers={'X-Bot-Key':'food_porn',
        'X-Timestamp':stamp,'X-Signature':signature})


def test_bot_secret_settings_and_language(setup):
    app, client, config, token = setup
    secret = 'a-random-bot-secret-over-24-characters'
    response = client.post('/bots/1/settings', data={'token':token,'key':'ingest_secret',
        'label':'Event key','group_name':'security','kind':'secret','value':secret},follow_redirects=False)
    assert response.status_code == 303
    response = client.post('/bots/1/settings', data={'token':token,'key':'render_quality',
        'label':'Image quality','group_name':'generation','kind':'number','value':'0.75'},follow_redirects=False)
    assert response.status_code == 303
    with app.state.session_factory() as db:
        rows = db.scalars(select(BotSetting)).all()
        assert len(rows) == 2
        assert next(row for row in rows if row.key=='ingest_secret').value != secret
    page = client.get('/bots/1').text
    assert 'render_quality' in page and '0.75' in page
    assert secret not in page
    assert bot_event(client, secret, 'new-1', 'bot.heartbeat', {}).status_code == 200
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), (stamp+'.GET./api/v1/bot-config').encode(), hashlib.sha256).hexdigest()
    response = client.get('/api/v1/bot-config',headers={'X-Bot-Key':'food_porn','X-Timestamp':stamp,'X-Signature':signature})
    assert response.json()['settings'] == {'render_quality':0.75}
    assert response.headers['cache-control'] == 'no-store'
    client.post('/preferences',data={'token':token,'theme':'dark','locale':'en'},follow_redirects=False)
    page = client.get('/').text
    assert 'data-theme="dark"' in page and '<html lang="en">' in page and 'Dashboard' in page


def test_invoice_pdf_and_verified_settlement(setup):
    app, client, config, token = setup
    with app.state.session_factory() as db:
        db.add(Customer(name='Покупатель')); db.commit()
    response = client.post('/invoices',data={'token':token,'seller_name':'ФОП Тест','bill_to':'Покупатель',
        'currency':'EUR','seller_details':'IBAN: UA123','customer_id':'1'},follow_redirects=False)
    assert response.status_code == 303
    assert client.post('/invoices/1/lines',data={'token':token,'title':'Разработка сайта','quantity':'2',
        'unit_price':'150.00'},follow_redirects=False).status_code == 303
    with app.state.session_factory() as db:
        assert db.get(Invoice,1).total == Decimal('300')
    assert client.post('/invoices/1/issue',data={'token':token},follow_redirects=False).status_code == 303
    pdf = client.get('/invoices/1/download.pdf')
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF') and len(pdf.content) > 3000
    assert client.post('/invoices/1/settle',data={'token':token,'payment_id':'99'}).status_code == 409
    with app.state.session_factory() as db:
        db.add(Payment(bot_id=1,customer_id=1,external_id='paid-eur',provider='wayforpay',
                       amount=Decimal('300'),currency='EUR',status='paid'))
        db.flush()
        db.add(PaymentVerification(payment_id=1,provider='wayforpay',provider_reference='X-1',
                                   amount=Decimal('300'),currency='EUR',status='verified'))
        db.commit()
    assert client.post('/invoices/1/settle',data={'token':token,'payment_id':'1'},follow_redirects=False).status_code == 303
    with app.state.session_factory() as db:
        assert db.get(Invoice,1).status == 'paid'
    assert 'Разработка сайта' in client.get('/invoices/1').text


def test_marketing_signed_metrics_and_bot_attribution(setup):
    app, client, config, token = setup
    channel = client.post('/marketing/channels',data={'token':token,'platform':'instagram','account':'brand'},follow_redirects=False)
    assert channel.status_code == 303
    campaign = client.post('/marketing/campaigns',data={'token':token,'channel_id':'1',
        'title':'Launch','utm_campaign':'launch_26','budget':'100','currency':'USD'},follow_redirects=False)
    assert campaign.status_code == 303
    secret = 'signed-marketing-key-over-24-characters'
    assert client.post('/marketing/channels/1/secret',data={'token':token,'secret':secret},follow_redirects=False).status_code == 303
    body = json.dumps({'utm_campaign':'launch_26','day':'2026-09-28','impressions':1000,'clicks':80,
                       'leads':9,'spend':'31.50'}).encode()
    stamp = str(int(time.time()))
    sig = hmac.new(secret.encode(),stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    headers={'X-Channel-ID':'1','X-Timestamp':stamp,'X-Signature':sig}
    assert client.post('/api/v1/marketing/metrics',content=body,headers=headers).status_code == 200
    assert client.post('/api/v1/marketing/metrics',content=body,headers={**headers,'X-Signature':'bad'}).status_code == 401
    with app.state.session_factory() as db:
        assert db.scalar(select(MarketingMetric)).leads == 9
    # A bot reports the UTM independently of aggregate platform metrics.
    bot_secret = 'bot-key-with-more-than-24-characters'
    client.post('/bots/1/settings',data={'token':token,'key':'ingest_secret','label':'Event key',
                'kind':'secret','value':bot_secret},follow_redirects=False)
    assert bot_event(client,bot_secret,'touch-1','marketing.touch',{'external_user_id':'101',
                     'utm_campaign':'launch_26','medium':'instagram'}).status_code == 200
    with app.state.session_factory() as db:
        assert db.scalar(select(MarketingTouch)).medium == 'instagram'
    assert 'Launch' in client.get('/marketing').text


def test_project_expense_allocation_and_read_only_mobile(setup):
    app, client, config, token = setup
    with app.state.session_factory() as db:
        db.add(Project(title='Client portal', currency='EUR', budget=Decimal('3000'), status='development'))
        db.commit()
    result = client.post('/expenses', data={'token':token, 'category':'Hosting', 'vendor':'Provider',
        'amount':'90.00', 'currency':'EUR', 'project_id':'1'}, follow_redirects=False)
    assert result.status_code == 303
    with app.state.session_factory() as db:
        assert db.scalar(select(ProjectExpense)).project_id == 1
    assert '90.00' in client.get('/projects/1').text
    assert client.get('/m/t/project_expenses').status_code == 200
    assert client.get('/m/t/bot_settings').status_code == 200


def test_seo_keyword_rank_history(setup):
    app, client, config, token = setup
    with app.state.session_factory() as db:
        db.add(Project(title='Search site', currency='USD', budget=Decimal('500'), status='development'))
        db.commit()
    created = client.post('/marketing/seo/keywords', data={'token': token, 'project_id': '1',
        'phrase': 'crm for bots', 'country': 'US', 'target_url':'https://example.org/crm'}, follow_redirects=False)
    assert created.status_code == 303
    assert client.post('/marketing/seo/keywords', data={'token': token, 'project_id': '1',
        'phrase': 'crm for bots', 'country': 'US'}).status_code == 409
    for day, position in [('2026-09-25', '11'), ('2026-09-27', '7'), ('2026-09-27', '5')]:
        response = client.post('/marketing/seo/keywords/1/rank', data={'token':token,'day':day,
            'position':position}, follow_redirects=False)
        assert response.status_code == 303
    with app.state.session_factory() as db:
        assert db.scalar(select(SeoKeyword)).phrase == 'crm for bots'
        assert [r.position for r in db.scalars(select(SeoRankSnapshot).order_by(SeoRankSnapshot.day)).all()] == [11,5]
    assert 'crm for bots' in client.get('/marketing').text
    assert client.get('/m/t/seo_rank_snapshots').status_code == 200
