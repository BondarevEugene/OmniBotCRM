import hashlib
import hmac
import json
import os
import time
from sqlalchemy import select
from sqlalchemy.orm import Session

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

from fastapi.testclient import TestClient
from crm.app import create_app
from crm.config import Settings
from crm.db import Base
from crm.models import Admin, Bot, Campaign, Customer, Delivery, Identity, Order, Payment
from crm.security import hash_password
from crm.worker import close_finished, process_one


def test_customer_order_payment_and_broadcast(tmp_path):
    config = Settings(database_url='sqlite:///'+str(tmp_path/'crm.db'),session_secret='a'*40,cookie_secure=False,
        ingest_secrets={'food_porn':'very-long-unique-bot-secret-123456'}, bot_tokens={'food_porn':'token-for-tests'})
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add_all([Bot(key='food_porn',name='Food Porn'),Admin(email='a@example.com',password_hash=hash_password('very-strong-password'))])
        db.commit()
    client=TestClient(app)

    def event(eid,kind,data):
        raw=json.dumps({'event_id':eid,'type':kind,'data':{'external_user_id':'123',**data}}).encode()
        stamp=str(int(time.time()))
        sig=hmac.new(config.ingest_secrets['food_porn'].encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        return client.post('/api/v1/events',content=raw,headers={'X-Bot-Key':'food_porn','X-Timestamp':stamp,'X-Signature':sig})

    assert client.get('/').status_code==401
    assert client.get('/', headers={'Accept': 'text/html'}, follow_redirects=False).headers['location']=='/login'
    assert client.post('/api/v1/events',content=b'{}').status_code==401
    assert event('user-1','customer.upsert',{'name':'Евгений','chat_id':'123'}).json()['ok']
    assert event('consent-1','customer.consent',{'consent':True}).status_code==200
    assert event('order-1','order.upsert',{'external_id':'42','title':'Меню','amount':'750.00','status':'paid'}).status_code==200
    assert event('payment-1','payment.upsert',{'external_id':'transaction-42','order_external_id':'42','amount':'750.00','status':'paid'}).status_code==200
    assert event('payment-1','payment.upsert',{'external_id':'transaction-42','amount':'750.00','status':'paid'}).json()['duplicate']
    assert event('bad-amount','payment.upsert',{'external_id':'bad','amount':'NaN'}).status_code==422
    with app.state.session_factory() as db:
        assert len(db.scalars(select(Customer)).all())==1
        assert len(db.scalars(select(Payment)).all())==1
        assert db.scalar(select(Order)).amount==750
    login=client.get('/login')
    assert login.status_code==200
    csrf=client.cookies.get('session')
    # Get signed CSRF value by reading form markup (browser sends same token).
    import re
    token=re.search(r'name="token" value="([^"]+)',login.text).group(1)
    assert client.post('/login',data={'email':'a@example.com','password':'very-strong-password','token':token},follow_redirects=False).status_code==303
    assert 'Евгений' in client.get('/customers').text
    for path in ['/', '/analytics', '/bots', '/orders', '/payments', '/expenses', '/tasks', '/audit', '/customers/1', '/customers/new']:
        assert client.get(path).status_code==200, path
    token=re.search(r'name="token" value="([^"]+)',client.get('/customers/new').text).group(1)
    assert client.post('/customers/new',data={'token':token,'name':'Новый клиент','email':'new@example.com'},follow_redirects=False).status_code==303
    assert client.post('/customers/1/activity',data={'token':token,'summary':'Перезвонить завтра'},follow_redirects=False).status_code==303
    assert 'Перезвонить завтра' in client.get('/customers/1').text
    token=re.search(r'name="token" value="([^"]+)',client.get('/campaigns').text).group(1)
    assert client.post('/campaigns',data={'token':token,'bot_id':1,'title':'Акция','message':'Привет!'},follow_redirects=False).status_code==303
    assert '1' in client.get('/campaigns/1').text
    assert client.post('/campaigns/1/launch',data={'token':token},follow_redirects=False).status_code==303

    class FakeResponse:
        status_code=200
        def json(self): return {'ok':True,'result':{'message_id':77}}
    class FakeClient:
        def post(self,url,**kwargs):
            assert kwargs['json']['chat_id']=='123'
            return FakeResponse()
    assert process_one(app.state.session_factory,config.bot_tokens,FakeClient())
    close_finished(app.state.session_factory)
    with app.state.session_factory() as db:
        assert db.scalar(select(Delivery)).status=='sent'
        assert db.scalar(select(Campaign)).status=='completed'
    app.state.engine.dispose()


def test_import_food_porn_without_invented_payments(tmp_path):
    from sqlalchemy import create_engine, text
    from integrations.food_porn_import import import_data
    source_url='sqlite:///'+str(tmp_path/'legacy.db')
    target_url='sqlite:///'+str(tmp_path/'crm.db')
    source=create_engine(source_url)
    with source.begin() as con:
        con.execute(text('CREATE TABLE customers (id INTEGER PRIMARY KEY, telegram_user_id BIGINT, name TEXT, phone TEXT, language TEXT, last_seen_at DATETIME)'))
        con.execute(text('CREATE TABLE menus (id INTEGER PRIMARY KEY, customer_id INTEGER, status TEXT, is_paid BOOLEAN)'))
        con.execute(text("INSERT INTO customers VALUES (1,123,'Евгений','+380123','UK',NULL)"))
        con.execute(text("INSERT INTO menus VALUES (9,1,'PAID',1)"))
    engine=create_engine(target_url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Bot(key='food_porn',name='Food Porn')); db.commit()
    assert import_data(source_url,target_url,dry_run=True)==(1,1)
    assert import_data(source_url,target_url)==(1,1)
    assert import_data(source_url,target_url)==(1,1)
    with Session(engine) as db:
        assert len(db.scalars(select(Customer)).all())==1
        assert len(db.scalars(select(Order)).all())==1
        assert len(db.scalars(select(Payment)).all())==0
        assert db.scalar(select(Order)).amount==0
    source.dispose(); engine.dispose()


def test_revoked_consent_skips_delivery(tmp_path):
    config=Settings(database_url='sqlite:///'+str(tmp_path/'test.db'),session_secret='a'*40,cookie_secure=False)
    app=create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        bot=Bot(key='other',name='Other'); admin=Admin(email='b@example.com',password_hash=hash_password('very-strong-password'))
        db.add_all([bot,admin]); db.flush()
        customer=Customer(name='B',marketing_consent=False); db.add(customer); db.flush()
        identity=Identity(bot_id=bot.id,customer_id=customer.id,external_user_id='4',chat_id='4'); db.add(identity); db.flush()
        campaign=Campaign(bot_id=bot.id,title='T',message='Hello',status='queued',created_by=admin.id); db.add(campaign); db.flush()
        db.add(Delivery(campaign_id=campaign.id,identity_id=identity.id)); db.commit()
    class Never:
        def post(self,*args,**kwargs): raise AssertionError('Should not send')
    assert process_one(app.state.session_factory,{'other':'token'},Never())
    with app.state.session_factory() as db:
        assert db.scalar(select(Delivery)).status=='skipped'
    app.state.engine.dispose()
