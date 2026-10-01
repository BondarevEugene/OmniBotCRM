import json
import os
import re
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
from crm.models import Admin, Bot, BotPaymentProvider, BotSetting, Customer, Order, Payment
from crm.payment_providers import VerificationError, verify_payment
from crm.security import hash_password


class FakeResponse:
    def raise_for_status(self): pass
    def json(self):
        return [{'shopOrderNumber':'order-1','payee_id':'SHOP-2','status':'PAYED',
                 'chargeback':'N','shopBillId':'bill-1','billAmount':'120.00'}]


class FakeTransport:
    def __init__(self): self.payload = None
    def post(self, url, **kwargs):
        self.payload = kwargs.get('json')
        return FakeResponse()


def test_bot_specific_acquirer_and_arbitrary_ordered_settings(tmp_path):
    config = Settings(database_url='sqlite:///' + str(tmp_path/'bot-config.db'), session_secret='s'*40,
        cookie_secure=False, encryption_key=Fernet.generate_key().decode(),
        portmone_login='legacy', portmone_password='legacy-secret', portmone_payee_id='SHOP-1')
    app=create_app(config); Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='owner@example.org',password_hash=hash_password('unique-password-123')))
        db.add_all([Bot(key='food_porn',name='Food Porn'),Bot(key='new_bot',name='New Bot')])
        db.commit()
    with TestClient(app) as client:
        token=re.search(r'name="token" value="([^"]+)',client.get('/login').text).group(1)
        client.post('/login',data={'token':token,'email':'owner@example.org','password':'unique-password-123'})
        html=client.get('/bots/2/configure')
        assert html.status_code==200 and 'Portmone' in html.text and 'Произвольные параметры' in html.text
        token=re.search(r'name="token" value="([^"]+)',html.text).group(1)
        for key,kind,value in [('custom_note','multiline','many\nlines'),('enabled_feature','boolean','true'),
                               ('rollout','list','[1,2,3]'),('private_api_key','secret','very-private-value')]:
            result=client.post('/bots/2/settings',data={'token':token,'key':key,'kind':kind,'value':value,
                'label':key,'group_name':'experimental'},follow_redirects=False)
            assert result.status_code==303
        assert client.post('/bots/2/settings',data={'token':token,'key':'private_token',
            'kind':'text','value':'unsafe','label':'Unsafe'}).status_code==422
        with app.state.session_factory() as db:
            values=db.scalars(select(BotSetting).where(BotSetting.bot_id==2).order_by(BotSetting.display_order)).all()
            assert [v.key for v in values]==['custom_note','enabled_feature','rollout','private_api_key']
            assert 'very-private-value' not in values[-1].value
        assert 'very-private-value' not in client.get('/bots/2/configure').text
        assert client.post('/bots/2/settings/4/move',data={'token':token,'direction':'up'},follow_redirects=False).status_code==303
        with app.state.session_factory() as db:
            values=db.scalars(select(BotSetting).where(BotSetting.bot_id==2).order_by(BotSetting.display_order)).all()
            assert [v.key for v in values]==['custom_note','enabled_feature','private_api_key','rollout']
        assert client.post('/bots/2/acquirers',data={'token':token,'provider':'portmone','login':'merchant2',
            'password':'secret2','payee_id':'SHOP-2','enabled':'true'},follow_redirects=False).status_code==303
        with app.state.session_factory() as db:
            row=db.scalar(select(BotPaymentProvider))
            assert row.bot_id==2 and row.enabled and 'secret2' not in row.encrypted_credentials
            customer=Customer(name='Buyer');db.add(customer);db.flush()
            order=Order(bot_id=2,customer_id=customer.id,external_id='order-1',amount=Decimal('120'),
                        currency='UAH',title='Product')
            db.add(order);db.flush()
            payment=Payment(bot_id=2,customer_id=customer.id,order_id=order.id,
                            external_id='payment-1',amount=Decimal('120'),currency='UAH',provider='portmone')
            db.add(payment);db.commit()
            transport=FakeTransport()
            assert verify_payment(db,payment,config,transport)=='bill-1'
            assert transport.payload['params']['data']['payeeId']=='SHOP-2'
            assert transport.payload['params']['data']['password']=='secret2'
            db.rollback()
        assert client.post('/bots/2/acquirers',data={'token':token,'provider':'portmone',
            'enabled':'false'},follow_redirects=False).status_code==303
        with app.state.session_factory() as db:
            payment=db.scalar(select(Payment))
            with pytest.raises(VerificationError, match='выключен'):
                verify_payment(db,payment,config,FakeTransport())
    app.state.engine.dispose()
