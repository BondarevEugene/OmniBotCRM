import os
import re

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text

from crm.app import create_app
from crm.config import Settings
from crm.db import Base
from crm.models import (Admin, BotSetting, FoodPornCustomerProfile, FoodPornGeneratedFile,
                        FoodPornMenuItem, FoodPornMenuSnapshot, Order, Payment, Product)
from crm.security import hash_password
from integrations.food_porn_import import import_data, source_inventory


def test_food_porn_setup_import_and_no_fake_payment(tmp_path):
    source_url = 'sqlite:///' + str(tmp_path / 'source.db')
    target_url = 'sqlite:///' + str(tmp_path / 'crm.db')
    source = create_engine(source_url)
    with source.begin() as connection:
        connection.execute(text('CREATE TABLE customers (id INTEGER PRIMARY KEY, telegram_user_id INTEGER, '
            'name TEXT, phone TEXT, language TEXT, last_seen_at TEXT, country TEXT, city TEXT)'))
        connection.execute(text('CREATE TABLE menus (id INTEGER PRIMARY KEY, customer_id INTEGER, status TEXT, '
            'is_paid BOOLEAN, cover_photo_path TEXT, spread_photo_path TEXT, error_message TEXT, payment_system TEXT)'))
        connection.execute(text('CREATE TABLE menu_items (id INTEGER PRIMARY KEY, menu_id INTEGER, category TEXT, '
            'position INTEGER, title TEXT, generation_status TEXT)'))
        connection.execute(text('CREATE TABLE generated_files (id INTEGER PRIMARY KEY, menu_id INTEGER, kind TEXT, path TEXT)'))
        connection.execute(text("INSERT INTO customers VALUES (1,123,'Тест','+3800000','UK',NULL,'UA','Київ')"))
        connection.execute(text("INSERT INTO menus VALUES (9,1,'PAID',1,'cover.jpg',NULL,NULL,'portmone')"))
        connection.execute(text("INSERT INTO menu_items VALUES (6,9,'MAIN',1,'Борщ','DONE')"))
        connection.execute(text("INSERT INTO generated_files VALUES (3,9,'PRINT_PDF','private/order9.pdf')"))
    source.dispose()

    config = Settings(database_url=target_url, session_secret='s'*40, cookie_secure=False,
                      food_porn_source_database_url=source_url)
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='owner@example.org', password_hash=hash_password('safe-password-123')))
        db.commit()
    with TestClient(app) as client:
        token = re.search(r'name="token" value="([^"]+)', client.get('/login').text).group(1)
        client.post('/login', data={'email':'owner@example.org','password':'safe-password-123','token':token})
        home = client.get('/food-porn')
        assert home.status_code == 200 and 'Зарегистрировать Food Porn' in home.text
        token = re.search(r'name="token" value="([^"]+)', home.text).group(1)
        assert client.post('/food-porn/setup',data={'token':token},follow_redirects=False).status_code == 303
        assert client.post('/food-porn/setup',data={'token':token},follow_redirects=False).status_code == 303
        with app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(BotSetting)) == 0
        assert client.post('/food-porn/catalog',data={'token':token,'sku':'menu_digital',
            'price':'500','currency':'UAH','active':'true'}, follow_redirects=False).status_code == 303
        with app.state.session_factory() as db:
            assert db.scalar(select(Product)).price == 500
        assert source_inventory(source_url)['menu_items'] == 1
        assert import_data(source_url, target_url, dry_run=True) == (1,1)
        with app.state.session_factory() as db:
            assert db.scalar(select(Order)) is None
        assert import_data(source_url, target_url) == (1,1)
        assert import_data(source_url, target_url) == (1,1)
        with app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(Order)) == 1
            assert db.scalar(select(Order)).status == 'reported_paid'
            assert db.scalar(select(Order)).amount == 0
            assert db.scalar(select(Payment)) is None
            assert db.scalar(select(FoodPornCustomerProfile)).city == 'Київ'
            assert db.scalar(select(FoodPornMenuSnapshot)).cover_uploaded
            assert db.scalar(select(FoodPornMenuItem)).title == 'Борщ'
            assert db.scalar(select(FoodPornGeneratedFile)).kind == 'print_pdf'
        assert 'Борщ' in client.get('/food-porn/menus/1').text
        assert '1 записей' in client.get('/food-porn').text
        assert client.get('/m/t/food_porn_menu_items').status_code == 200
    app.state.engine.dispose()
