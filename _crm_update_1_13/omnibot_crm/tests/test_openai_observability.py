import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from crm.app import create_app
from crm.bot_settings import decrypt, encrypt
from crm.config import Settings
from crm.db import Base
from crm.models import (Admin, Bot, OpenAIBotBinding, OpenAICost, OpenAIOrganization,
                        OpenAIResource, OpenAIUsage)
from crm.openai_admin import report
from crm.openai_sync import OpenAISyncError, _pages, sync_organization
from crm.security import hash_password


class Response:
    status_code = 200
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
    def json(self): return self.body
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError('Provider error')


class FakeProvider:
    def __init__(self, price='1.25', deny_family=''):
        self.price, self.deny_family = price, deny_family
        self.calls = []
    def get(self, url, *, headers, params=None, **kwargs):
        assert headers['Authorization'] == 'Bearer sk-admin-test-secret'
        assert headers['OpenAI-Organization'] == 'org_company'
        path = urlsplit(url).path
        assert path.startswith('/v1/organization/')
        self.calls.append((path, params))
        end = int(datetime(2026, 9, 30, tzinfo=timezone.utc).timestamp())
        begin = end - 86400
        if path == '/v1/organization/projects':
            return Response({'data': [{'id': 'proj_food', 'name': 'Food Porn',
                'status': 'active', 'secret': 'never-store-this'}], 'has_more': False})
        if path == '/v1/organization/admin_api_keys':
            return Response({'data': [{'id': 'admin_key_id', 'name': 'CRM observer',
                'created_at': 1700000000, 'secret': 'never-store-this'}],
                'has_more': False})
        if path.endswith('/api_keys'):
            return Response({'data': [{'id': 'key_food', 'name': 'Food image key',
                'last_used_at': 1750000000, 'owner': {'type': 'service_account',
                'service_account': {'id': 'svc_food'}}, 'secret': 'never-store-this'}],
                'has_more': False})
        if path.endswith('/rate_limits') or path.endswith('/spend_alerts'):
            return Response({'data': [], 'has_more': False})
        if path.endswith('/spend_limit'):
            return Response({'threshold_amount': 25000, 'currency': 'USD',
                             'interval': 'month', 'enforcement': {'status': 'enforcing'}})
        if path.endswith('/costs'):
            row = {'object': 'organization.costs.result',
                'project_id': 'proj_food', 'api_key_id': 'key_food',
                'line_item': 'image_output', 'amount': {'value': self.price, 'currency': 'usd'},
                'quantity': 5, 'quantity_unit': 'images'}
            return Response({'data': [{'start_time': begin, 'end_time': end,
                'results': [row]}], 'has_more': False})
        if '/usage/' in path:
            if path.endswith(self.deny_family) and self.deny_family:
                return Response({}, 403)
            family = path.rsplit('/', 1)[-1]
            rows = [{'object': 'organization.usage.completions.result',
                'project_id': 'proj_food', 'api_key_id': 'key_food',
                'model': 'gpt-test', 'input_tokens': 120, 'output_tokens': 30,
                'num_model_requests': 2}] if family == 'completions' else []
            return Response({'data': [{'start_time': begin, 'end_time': end,
                'results': rows}], 'has_more': False})
        raise AssertionError(path)


def setup(tmp_path):
    config = Settings(database_url='sqlite:///' + str(tmp_path/'openai.db'),
        session_secret='s'*40, cookie_secure=False,
        encryption_key=Fernet.generate_key().decode())
    app = create_app(config)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='owner@example.com', password_hash=hash_password('password-for-testing')))
        db.add_all([Bot(key='food_porn', name='Food Porn'), Bot(key='another', name='Other')])
        db.commit()
    client = TestClient(app)
    token = re.search(r'name="token" value="([^"]+)', client.get('/login').text).group(1)
    client.post('/login', data={'token': token, 'email': 'owner@example.com',
                                'password': 'password-for-testing'})
    token = re.search(r'name="token" value="([^"]+)', client.get('/openai').text).group(1)
    return app, client, config, token


def test_openai_credentials_sync_attribution_and_idempotency(tmp_path):
    app, client, config, token = setup(tmp_path)
    assert client.post('/openai/organizations', data={'token': token,
        'organization_id': 'org_company', 'name': 'Company',
        'admin_key': 'sk-admin-test-secret'}, follow_redirects=False).status_code == 303
    with app.state.session_factory() as db:
        org = db.scalar(select(OpenAIOrganization))
        assert 'sk-admin-test-secret' not in org.encrypted_admin_key
        assert decrypt(org.encrypted_admin_key, config.encryption_key) == 'sk-admin-test-secret'
        provider = FakeProvider()
        now = datetime(2026, 9, 30, 18, tzinfo=timezone.utc)
        assert sync_organization(db, org, config.encryption_key, provider, now)['cost_rows'] == 1
        assert db.query(OpenAICost).count() == 1
        assert db.query(OpenAIUsage).count() == 1
        assert all('never-store-this' not in row.payload_json for row in db.scalars(select(OpenAIResource)))
        assert report(db, org)['totals'] == {'USD': Decimal('1.25')}
        assert report(db, org)['unassigned'] == {'USD': Decimal('1.25')}
        provider.price = '2.50'
        sync_organization(db, org, config.encryption_key, provider, now)
        assert db.query(OpenAICost).count() == 1
        assert report(db, org)['totals'] == {'USD': Decimal('2.50')}
        provider.deny_family = 'completions'
        sync_organization(db, org, config.encryption_key, provider, now)
        assert org.status == 'partial'
        assert db.query(OpenAIUsage).count() == 1
    dashboard = client.get('/openai').text
    assert 'sk-admin-test-secret' not in dashboard
    assert '2.50 USD' in dashboard and 'key_food' in dashboard
    assert client.post('/openai/bindings', data={'token': token, 'organization_id': 1,
        'bot_id': 1, 'label': 'Images', 'project_id': 'proj_food',
        'api_key_id': 'key_food'}, follow_redirects=False).status_code == 303
    assert client.post('/openai/bindings', data={'token': token, 'organization_id': 1,
        'bot_id': 2, 'label': 'Conflict', 'project_id': 'proj_food',
        'api_key_id': 'key_food'}).status_code == 409
    assert '2.50 USD' in client.get('/openai?organization_id=1&bot_id=1').text
    assert 'Нет данных' in client.get('/openai?organization_id=1&bot_id=2').text
    csv_data = client.get('/openai/costs.csv?organization_id=1&bot_id=1').text
    assert 'image_output' in csv_data and '2.50000000' in csv_data
    assert 'image_output' not in client.get('/openai/costs.csv?organization_id=1&bot_id=2').text
    app.state.engine.dispose()


def test_openai_auth_csrf_and_cost_failure_preserves_previous_snapshot(tmp_path):
    app, client, config, token = setup(tmp_path)
    assert client.post('/openai/organizations', data={'token': 'bad',
        'organization_id': 'org_company', 'name': 'Company',
        'admin_key': 'sk-admin-test-secret'}).status_code == 403
    with app.state.session_factory() as db:
        org = OpenAIOrganization(organization_id='org_company', name='Company',
            encrypted_admin_key=encrypt('sk-admin-test-secret', config.encryption_key))
        db.add(org); db.commit()
        sync_organization(db, org, config.encryption_key, FakeProvider(),
            datetime(2026, 9, 30, 18, tzinfo=timezone.utc))
        class Failing(FakeProvider):
            def get(self, url, **kwargs):
                if url.endswith('/organization/costs'): return Response({}, 403)
                return super().get(url, **kwargs)
        try:
            sync_organization(db, org, config.encryption_key, Failing(),
                datetime(2026, 9, 30, 19, tzinfo=timezone.utc))
            assert False, 'Costs must fail the sync'
        except OpenAISyncError as exc:
            assert exc.code == 'admin_permission_denied'
            db.rollback()
        assert db.query(OpenAICost).count() == 1
    with TestClient(app) as unauthenticated:
        assert unauthenticated.get('/openai').status_code == 401
        assert unauthenticated.get('/openai/costs.csv?organization_id=1').status_code == 401
    app.state.engine.dispose()


def test_provider_cursor_pagination_reads_each_page_once():
    class Pages:
        def __init__(self): self.cursors = []
        def get(self, url, *, headers, params, **kwargs):
            self.cursors.append(params.get('page'))
            if len(self.cursors) == 1:
                return Response({'data': [{'id': 'first'}], 'has_more': True,
                                 'next_page': 'cursor-2'})
            return Response({'data': [{'id': 'second'}], 'has_more': False})
    provider = Pages()
    rows = [row for page in _pages(provider, '/organization/costs', {},
                                    {'start_time': 1}) for row in page]
    assert [r['id'] for r in rows] == ['first', 'second']
    assert provider.cursors == [None, 'cursor-2']
