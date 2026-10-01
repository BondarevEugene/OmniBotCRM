import os
import re
import json
import hashlib
import hmac
import time

os.environ.setdefault('SESSION_SECRET','test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE','false')

from fastapi.testclient import TestClient
from sqlalchemy import select
from crm.app import create_app
from crm.config import Settings
from crm.db import Base
from crm.mobile import TABLES
from crm.models import Admin, Bot, Deal, Project, Ticket
from crm.security import hash_password


def token(html):
    return re.search(r'name="token" value="([^"]+)',html).group(1)


def test_it_pipeline_project_support_and_mobile_read_only(tmp_path):
    config=Settings(database_url='sqlite:///'+str(tmp_path/'crm.db'),session_secret='s'*40,cookie_secure=False,
        ingest_secrets={'it_bot':'a-very-long-secret-for-the-bot-123'})
    app=create_app(config); Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add_all([Admin(email='owner@example.com',password_hash=hash_password('a-very-strong-password'),role='owner'),
                    Bot(key='it_bot',name='IT Bot')])
        db.commit()
    client=TestClient(app)
    assert client.get('/m').status_code==401
    login=client.get('/login')
    assert client.post('/login',data={'email':'owner@example.com','password':'a-very-strong-password','token':token(login.text)},follow_redirects=False).status_code==303
    csrf=token(client.get('/companies').text)
    assert client.post('/customers/new',data={'token':csrf,'name':'Тестовый клиент'},follow_redirects=False).status_code==303
    assert client.post('/companies',data={'token':csrf,'name':'Веб-заказчик'},follow_redirects=False).status_code==303
    assert client.post('/companies/1/contacts',data={'token':csrf,'customer_id':'1','role':'директор'},follow_redirects=False).status_code==303
    assert client.post('/pipeline',data={'token':csrf,'name':'Разработка веб-проектов'},follow_redirects=False).status_code==303
    def ingest(event_id,kind,data):
        raw=json.dumps({'event_id':event_id,'type':kind,'data':{'external_user_id':'abc',**data}}).encode()
        stamp=str(int(time.time()))
        signature=hmac.new(config.ingest_secrets['it_bot'].encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        return client.post('/api/v1/events',content=raw,headers={'X-Bot-Key':'it_bot','X-Timestamp':stamp,'X-Signature':signature})
    assert ingest('lead-1','lead.upsert',{'external_id':'req-1','title':'Сайт из бота','service':'website','budget':'2000'}).status_code==200
    assert ingest('ticket-1','ticket.upsert',{'external_id':'support-1','title':'Вопрос из бота','description':'Звонок'}).status_code==200
    assert client.post('/deals/new',data={'token':csrf,'title':'Корпоративный портал','pipeline_id':'1','stage_id':'1','customer_id':'1','company_id':'1','service':'portal','budget':'40000.00','currency':'UAH'},follow_redirects=False).status_code==303
    assert 'Корпоративный портал' in client.get('/pipeline').text
    # Default sixth stage is the won stage; only then can the deal become a project.
    assert client.post('/deals/2/project',data={'token':csrf}).status_code==409
    assert client.post('/deals/2/stage',data={'token':csrf,'stage_id':'6'},follow_redirects=False).status_code==303
    assert client.post('/deals/2/project',data={'token':csrf},follow_redirects=False).status_code==303
    assert client.post('/projects/1/milestones',data={'token':csrf,'title':'Прототип','due_at':'2026-12-01'},follow_redirects=False).status_code==303
    assert client.post('/tickets',data={'token':csrf,'title':'Доработка формы','project_id':'1','customer_id':'1'},follow_redirects=False).status_code==303
    assert 'Прототип' in client.get('/projects/1').text
    assert client.get('/tickets/2').status_code==200
    mobile=client.get('/m')
    assert mobile.status_code==200 and 'OMNIFACTORY' in mobile.text and '/bots' in mobile.text
    assert 'Веб-проекты' in mobile.text and '/m/t/projects' in mobile.text
    for key in TABLES:
        assert client.get('/m/t/'+key).status_code==200, key
    detail=client.get('/m/t/deals/2')
    assert detail.status_code==200 and '40000.00' in detail.text
    assert 'type="hidden" name="token"' not in detail.text
    assert client.post('/m/t/deals/2').status_code==405
    assert client.get('/m/export/customers.xlsx').status_code==200
    with app.state.session_factory() as db:
        assert db.scalar(select(Deal).where(Deal.title=='Корпоративный портал')).budget==40000
        assert db.scalar(select(Deal).where(Deal.title=='Сайт из бота')).source=='it_bot'
        assert db.scalar(select(Project)).service=='portal'
        assert len(db.scalars(select(Ticket)).all())==2
    app.state.engine.dispose()
