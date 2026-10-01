import os
import re

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

from crm import bot_databases  # noqa: E402
from crm.app import create_app  # noqa: E402
from crm.config import Settings  # noqa: E402
from crm.db import Base  # noqa: E402
from crm.models import Admin, Bot, BotDataMapping, BotDatabaseConnection, MarketingChannel  # noqa: E402
from crm.bot_sources import food_porn_source  # noqa: E402
from crm.security import hash_password  # noqa: E402


def setup(tmp_path):
    config = Settings(database_url=f'sqlite:///{tmp_path / "sources.db"}',
                      session_secret='s' * 40, cookie_secure=False,
                      encryption_key=Fernet.generate_key().decode())
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='owner@example.com', password_hash=hash_password('long-unique-password')))
        db.add_all([Bot(key='food_porn', name='Food Porn'), Bot(key='another', name='Other')])
        db.commit()
    client = TestClient(app)
    login_csrf = re.search(r'name="token" value="([^"]+)', client.get('/login').text).group(1)
    assert client.post('/login', data={'token': login_csrf, 'email': 'owner@example.com',
        'password': 'long-unique-password'}, follow_redirects=False).status_code == 303
    csrf = re.search(r'name="token" value="([^"]+)', client.get('/bots/1/database').text).group(1)
    return app, client, csrf


def test_source_secret_is_scoped_and_mapping_uses_reflected_columns(tmp_path, monkeypatch):
    app, client, csrf = setup(tmp_path)
    try:
        secret = 'postgresql://reader:very-private-password@db.example/food'
        response = client.post('/bots/1/database', data={'token': csrf, 'label': 'Production',
            'dialect': 'neon', 'url': secret, 'schema_name': 'public'}, follow_redirects=False)
        assert response.status_code == 303
        assert 'Production' in client.get('/bots/1/database').text
        assert secret not in client.get('/bots/1/database').text
        assert 'Production' not in client.get('/bots/2/database').text
        with app.state.session_factory() as db:
            source = db.scalar(select(BotDatabaseConnection))
            assert secret not in source.encrypted_url
            source_id = source.id
            assert not food_porn_source(db, app.state.settings)
        assert client.post(f'/bots/2/database/{source_id}/inspect',
            data={'token': csrf}, follow_redirects=False).status_code == 404
        monkeypatch.setattr(bot_databases, 'inspect_source', lambda url, dialect, schema: {
            'tables': [{'name': 'customers', 'columns': [
                {'name': 'id', 'type': 'INTEGER', 'nullable': False},
                {'name': 'telegram_user_id', 'type': 'BIGINT', 'nullable': False}],
                'primary_key': ['id'], 'foreign_keys': [], 'indexes': []}],
            'latency_ms': 18, 'schema': schema, 'database': 'food', 'dialect': dialect})
        assert client.post(f'/bots/1/database/{source_id}/inspect',
            data={'token': csrf}, follow_redirects=False).status_code == 303
        html = client.get('/bots/1/database').text
        assert 'customers' in html and '18' in html and secret not in html
        assert client.post(f'/bots/1/database/{source_id}/mapping', data={
            'token': csrf, 'entity': 'customer', 'table_name': 'customers', 'source_id': 'id',
            'email': 'missing_column'}, follow_redirects=False).status_code == 422
        assert client.post(f'/bots/1/database/{source_id}/mapping', data={
            'token': csrf, 'entity': 'customer', 'table_name': 'customers', 'source_id': 'id',
            'telegram_user_id': 'telegram_user_id'}, follow_redirects=False).status_code == 303
        with app.state.session_factory() as db:
            assert db.scalar(select(BotDataMapping)).table_name == 'customers'
            assert food_porn_source(db, app.state.settings).startswith('postgresql+psycopg://reader:')
        monkeypatch.setattr(bot_databases, 'probe_source', lambda url, dialect: None)
        bot_databases.monitor_connections(app.state.session_factory, app.state.settings)
        with app.state.session_factory() as db:
            assert db.get(BotDatabaseConnection, source_id).last_status == 'available'
        def unavailable(url, dialect):
            raise RuntimeError(f'private={secret}')
        monkeypatch.setattr(bot_databases, 'probe_source', unavailable)
        bot_databases.monitor_connections(app.state.session_factory, app.state.settings)
        assert secret not in client.get('/bots/1/database').text
        with app.state.session_factory() as db:
            assert db.get(BotDatabaseConnection, source_id).last_status == 'failed'
            assert secret not in db.get(BotDatabaseConnection, source_id).last_error
    finally:
        app.state.engine.dispose()


def test_url_validation_and_ad_channel_scope(tmp_path):
    app, client, csrf = setup(tmp_path)
    try:
        assert client.post('/bots/1/database', data={'token': csrf, 'label': 'Wrong',
            'dialect': 'postgresql', 'url': 'sqlite:///tmp/other.db'},
            follow_redirects=False).status_code == 422
        assert client.post('/bots/1/database', data={'token': csrf, 'label': 'Wrong',
            'dialect': 'mysql', 'url': 'mysql+pymysql://u:p@host/db'},
            follow_redirects=False).status_code == 422
        assert client.post('/marketing/channels', data={'token': csrf, 'platform': 'google_ads',
            'account': 'Test', 'bot_id': '1'}, follow_redirects=False).status_code == 303
        with app.state.session_factory() as db:
            channel = db.scalar(select(MarketingChannel))
            assert channel.bot_id == 1
            channel_id = channel.id
        assert client.post(f'/marketing/channels/{channel_id}/bot', data={
            'token': csrf, 'bot_id': '2'}, follow_redirects=False).status_code == 303
        assert client.get('/bots/1?view=marketing').status_code == 200
        assert client.get('/bots/2?view=marketing').status_code == 200
        with app.state.session_factory() as db:
            assert db.get(MarketingChannel, channel_id).bot_id == 2
    finally:
        app.state.engine.dispose()


def test_migrations_to_new_head_on_sqlite(tmp_path, monkeypatch):
    path = tmp_path / 'migrated.sqlite3'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{path}')
    monkeypatch.setenv('SESSION_SECRET', 's' * 40)
    monkeypatch.setenv('CRM_ENCRYPTION_KEY', Fernet.generate_key().decode())
    config = Config('alembic.ini')
    command.upgrade(config, 'head')
    from sqlalchemy import create_engine, inspect
    engine = create_engine(f'sqlite:///{path}')
    inspector = inspect(engine)
    assert 'crm_bot_database_connections' in inspector.get_table_names()
    assert 'bot_id' in {column['name'] for column in inspector.get_columns('crm_marketing_channels')}
    engine.dispose()


def test_upgrade_older_marketing_table_without_bot_column(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    path = tmp_path / 'legacy.sqlite3'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{path}')
    monkeypatch.setenv('SESSION_SECRET', 's' * 40)
    monkeypatch.setenv('CRM_ENCRYPTION_KEY', Fernet.generate_key().decode())
    config = Config('alembic.ini')
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine, tables=[Base.metadata.tables['crm_admins'],
                                           Base.metadata.tables['crm_bots']])
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE crm_marketing_channels (id INTEGER PRIMARY KEY, '
                                'platform VARCHAR(80), account VARCHAR(150))'))
    engine.dispose()
    command.stamp(config, '0011_finance_workspace')
    command.upgrade(config, 'head')
    engine = create_engine(f'sqlite:///{path}')
    assert 'bot_id' in {c['name'] for c in inspect(engine).get_columns('crm_marketing_channels')}
    engine.dispose()
