import re

from sqlalchemy import create_engine, select, text

from crm import neon_inventory
from crm.models import Customer, Order, Payment, SourceRecordLink
from tests.test_bot_data_connections import setup


def test_local_sqlite_preview_and_repeatable_import(tmp_path):
    app, client, csrf = setup(tmp_path)
    source_file = tmp_path / 'offline.db'
    engine = create_engine(f'sqlite:///{source_file}')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE clients (id INTEGER PRIMARY KEY, tg INTEGER, name TEXT, password TEXT)'))
        conn.execute(text("INSERT INTO clients VALUES (10, 4321, 'Anna', 'hidden-value')"))
        conn.execute(text('CREATE TABLE purchases (id INTEGER PRIMARY KEY, client_id INTEGER, total NUMERIC, currency TEXT)'))
        conn.execute(text("INSERT INTO purchases VALUES (30, 10, 25.50, 'USD')"))
        conn.execute(text('CREATE TABLE receipts (id INTEGER PRIMARY KEY, client_id INTEGER, amount NUMERIC, state TEXT)'))
        conn.execute(text("INSERT INTO receipts VALUES (40, 10, 25.50, 'paid')"))
    engine.dispose()
    try:
        saved = client.post('/bots/1/database', data={'token': csrf, 'label': 'Offline',
            'dialect': 'sqlite', 'url': f'sqlite:///{source_file}', 'schema_name': ''}, follow_redirects=False)
        assert saved.status_code == 303
        assert client.post('/bots/1/database/1/inspect', data={'token': csrf}, follow_redirects=False).status_code == 303
        preview = client.get('/bots/1/database/1/preview/clients')
        assert preview.status_code == 200 and 'Anna' in preview.text and 'hidden-value' not in preview.text
        assert client.get('/bots/2/database/1/preview/clients').status_code == 404
        def mapping(entity, table, **fields):
            result = client.post('/bots/1/database/1/mapping', data={
                'token': csrf, 'entity': entity, 'table_name': table, 'source_id': 'id', **fields},
                follow_redirects=False)
            assert result.status_code == 303
        mapping('customer', 'clients', telegram_user_id='tg', name='name')
        mapping('order', 'purchases', customer_id='client_id', amount='total', currency='currency')
        mapping('payment', 'receipts', customer_id='client_id', amount='amount', status='state')
        for entity in ('customer', 'order', 'payment', 'customer', 'order', 'payment'):
            result = client.post(f'/bots/1/database/1/sync/{entity}', data={'token': csrf, 'offset': 0},
                                 follow_redirects=False)
            assert result.status_code == 303
        with app.state.session_factory() as db:
            assert db.query(Customer).count() == 1
            assert db.query(Order).count() == 1
            payment = db.scalar(select(Payment))
            assert payment.status == 'pending' and payment.currency == 'UAH'
            assert db.query(SourceRecordLink).count() == 3
            assert db.scalar(select(Order)).currency == 'USD'
    finally:
        app.state.engine.dispose()


def test_neon_catalog_lists_projects_without_exposing_key(tmp_path, monkeypatch):
    app, client, _ = setup(tmp_path)
    object.__setattr__(app.state.settings, 'neon_api_key', 'top-secret-api-key')
    monkeypatch.setattr(neon_inventory, 'neon_get', lambda key, path, params=None: {
        '/projects': {'projects': [{'id': 'test-project', 'name': 'Food Porn'}]},
        '/projects/test-project/branches': {'branches': [{'id': 'br-main', 'name': 'main'}]},
        '/projects/test-project/branches/br-main/databases': {'databases': [{'name': 'neondb'}]},
    }[path])
    try:
        response = client.get('/neon?project_id=test-project')
        assert response.status_code == 200
        assert 'Food Porn' in response.text and 'neondb' in response.text
        assert 'top-secret-api-key' not in response.text
        assert client.get('/neon?project_id=other-project').status_code == 404
    finally:
        app.state.engine.dispose()
