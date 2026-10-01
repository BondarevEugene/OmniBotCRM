import hashlib
import os
os.environ.setdefault("SESSION_SECRET", "test-secret-with-at-least-32-characters")
os.environ.setdefault("COOKIE_SECURE", "false")
import hmac
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from crm.app import create_app
from crm.config import Settings
from crm.db import Base
from crm.finance_hub import cashbook
from crm.models import (Admin, Bot, Customer, ExchangeRate, Expense, Order, Payment,
                        PaymentVerification, Product)
from crm.payment_providers import merchant_for_payment
from crm.security import hash_password


def setup(tmp_path):
    config = Settings(database_url=f'sqlite:///{tmp_path / "finance.db"}',
        session_secret='s' * 40, cookie_secure=False, encryption_key=Fernet.generate_key().decode(),
        ingest_secrets={'food_porn': 'a-bot-secret-more-than-24-characters'})
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='owner@example.com', password_hash=hash_password('long-unique-password')))
        db.add_all([Bot(key='food_porn', name='Food Porn'), Bot(key='another', name='Another')])
        db.commit()
    client = TestClient(app)
    token = re.search(r'name="token" value="([^"]+)', client.get('/login').text).group(1)
    assert client.post('/login', data={'token': token, 'email': 'owner@example.com',
        'password': 'long-unique-password'}, follow_redirects=False).status_code == 303
    token = re.search(r'name="token" value="([^"]+)', client.get('/finance').text).group(1)
    return app, client, config, token


def test_cashbook_only_verified_with_dated_rates_and_bot_scope(tmp_path):
    app, client, _, token = setup(tmp_path)
    with app.state.session_factory() as db:
        customer = Customer(name='Test')
        db.add(customer); db.flush()
        order = Order(bot_id=1, customer_id=customer.id, external_id='ORDER-1', title='Digital product',
            amount=Decimal('15.00'), currency='USD')
        db.add(order); db.flush()
        paid = Payment(bot_id=1, customer_id=customer.id, order_id=order.id, external_id='PAY-1',
            amount=Decimal('15.00'), currency='USD', provider='portmone', status='paid')
        untrusted = Payment(bot_id=1, customer_id=customer.id, external_id='PAY-2',
            amount=Decimal('100.00'), currency='USD', provider='portmone', status='reported_paid')
        db.add_all([paid, untrusted]); db.flush()
        db.add(PaymentVerification(payment_id=paid.id, provider='portmone', provider_reference='PROOF-1',
            amount=Decimal('15.00'), currency='USD', status='verified'))
        db.add(Expense(bot_id=1, category='Advertising', vendor='Ads', amount=Decimal('5.00'), currency='EUR'))
        db.commit()
        today = date.today()
        summary = cashbook(db, today - timedelta(days=2), today, 1)
        assert summary['received'] == {'USD': Decimal('15.00')}
        assert summary['missing'] == 2
        assert len(summary['rows']) == 2
    assert client.post('/finance/rates', data={'token': token, 'rate_date': date.today().isoformat(),
        'currency': 'USD', 'uah_per_unit': '41.5', 'source': 'manual'}, follow_redirects=False).status_code == 303
    assert client.post('/finance/rates', data={'token': token, 'rate_date': date.today().isoformat(),
        'currency': 'EUR', 'uah_per_unit': '45.123456'}, follow_redirects=False).status_code == 303
    with app.state.session_factory() as db:
        summary = cashbook(db, today - timedelta(days=2), today, 1)
        assert summary['uah_received'] == Decimal('622.50')
        assert summary['uah_spent'] == Decimal('225.62')
        assert summary['missing'] == 0
        assert cashbook(db, today - timedelta(days=2), today, 2)['rows'] == []
    response = client.get('/finance/export.csv?bot_id=1')
    assert response.status_code == 200 and 'PROOF-1' in response.text
    assert 'PAY-2' not in response.text
    assert client.get('/finance?bot_id=1').status_code == 200
    app.state.engine.dispose()


def test_shared_defaults_require_bot_account_and_publish_snapshot(tmp_path):
    app, client, config, token = setup(tmp_path)
    assert client.post('/finance/providers', data={'token': token, 'provider': 'portmone',
        'login': 'shared-login', 'password': 'shared-password'}, follow_redirects=False).status_code == 303
    with app.state.session_factory() as db:
        customer = Customer(); db.add(customer); db.flush()
        payment = Payment(bot_id=2, customer_id=customer.id, external_id='P', provider='portmone',
            amount=Decimal('1'), currency='UAH'); db.add(payment); db.commit()
        from crm.payment_providers import VerificationError
        import pytest
        with pytest.raises(VerificationError):
            merchant_for_payment(db, payment, config)
    response = client.post('/bots/1/acquirers', data={'token': token, 'provider': 'portmone',
        'payee_id': 'specific-merchant-1', 'enabled': 'true'}, follow_redirects=False)
    assert response.status_code == 303, response.text
    with app.state.session_factory() as db:
        payment.bot_id = 1
        account = merchant_for_payment(db, payment, config)
        assert account.portmone_login == 'shared-login'
        assert account.portmone_payee_id == 'specific-merchant-1'
    stamp = str(int(time.time()))
    signature = hmac.new(config.ingest_secrets['food_porn'].encode(),
        (stamp + '.GET./api/v1/bot-config').encode(), hashlib.sha256).hexdigest()
    headers = {'X-Bot-Key': 'food_porn', 'X-Timestamp': stamp, 'X-Signature': signature}
    response = client.get('/api/v1/bot-config', headers=headers)
    assert response.status_code == 200
    assert response.json()['payment_accounts']['portmone']['payee_id'] == 'specific-merchant-1'
    assert client.post('/finance/rates', data={'token': token, 'rate_date': date.today().isoformat(),
        'currency': 'USD', 'uah_per_unit': '41'}, follow_redirects=False).status_code == 303
    assert client.get('/api/v1/bot-config', headers=headers).json()['exchange_rates']['USD']['uah_per_unit'] == '41.000000'
    assert client.post('/bots/1/releases', data={'token': token}, follow_redirects=False).status_code == 303
    assert client.post('/finance/rates', data={'token': token, 'rate_date': date.today().isoformat(),
        'currency': 'USD', 'uah_per_unit': '42'}, follow_redirects=False).status_code == 303
    assert client.get('/api/v1/bot-config', headers=headers).json()['exchange_rates']['USD']['uah_per_unit'] == '41.000000'
    assert client.get('/bots/1?view=payments').status_code == 200
    assert client.get('/bots/1?view=services').status_code == 200
    assert client.get('/bots/2?view=payments').status_code == 200
    assert client.get('/bots/1?view=unknown').status_code == 404
    app.state.engine.dispose()


def test_owner_can_edit_expenses_catalog_and_accounting_profile(tmp_path):
    app, client, _, token = setup(tmp_path)
    with app.state.session_factory() as db:
        product = Product(bot_id=1, sku='menu', title='Menu',
            price=Decimal('15'), currency='USD')
        expense = Expense(bot_id=1, category='Ads', amount=Decimal('10'), currency='UAH')
        db.add_all([product, expense]); db.commit()
        product_id, expense_id = product.id, expense.id
    response = client.post(f'/bots/1/products/{product_id}', data={'token': token,
        'title': 'Menu Premium', 'price': '25', 'currency': 'EUR', 'active': 'true'},
        follow_redirects=False)
    assert response.status_code == 303
    assert client.post(f'/bots/2/products/{product_id}', data={'token': token,
        'title': 'Bad scope', 'price': '1', 'currency': 'EUR'}, follow_redirects=False).status_code == 404
    assert client.post(f'/expenses/{expense_id}', data={'token': token, 'category': 'Ads',
        'amount': '12.50', 'currency': 'USD', 'bot_id': '1', 'spent_on': '2026-09-29'},
        follow_redirects=False).status_code == 303
    assert client.post('/bots/1/accounting', data={'token': token,
        'owner_name': 'ФОП Тестовый', 'tax_group': '3', 'tax_id': '1234567890'},
        follow_redirects=False).status_code == 303
    with app.state.session_factory() as db:
        assert db.get(Product, product_id).price == Decimal('25')
        assert db.get(Expense, expense_id).spent_at.date() == date(2026, 9, 29)
    assert 'ФОП Тестовый' in client.get('/finance?bot_id=1').text
    assert 'ФОП Тестовый' in client.get('/finance/export.csv?bot_id=1').text
    app.state.engine.dispose()
