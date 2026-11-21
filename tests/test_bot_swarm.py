"""End-to-end Bot Swarm registry and dynamic Mini CRM manifest."""
import os
import re

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

from fastapi.testclient import TestClient

from crm.app import create_app
from crm.config import Settings
from crm.db import Base
from crm.models import Admin, Bot, BotModule, BotSetting
from crm.security import hash_password


def test_swarm_registry_identity_and_manifest(tmp_path):
    app = create_app(Settings(database_url=f'sqlite:///{tmp_path / "swarm.db"}',
                              session_secret='s' * 40, cookie_secure=False))
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='owner@example.com', role='owner',
                     password_hash=hash_password('strong-password-123')))
        db.add_all([Bot(key='food_porn', name='Food Porn'),
                    Bot(key='client_bot', name='Client bot')])
        db.flush()
        db.add(BotSetting(bot_id=1, key='menu_price_uah', label='Menu price',
                          group_name='prices', kind='number', value='200'))
        db.add(BotModule(bot_id=1, key='little_legend', name='Little Legend'))
        db.commit()
    with TestClient(app) as client:
        assert client.get('/api/fleet/overview').status_code == 401
        login = client.get('/login')
        token = re.search(r'name="token" value="([^"]+)"', login.text).group(1)
        result = client.post('/login', data={
            'token': token, 'email': 'owner@example.com',
            'password': 'strong-password-123',
        }, follow_redirects=False)
        assert result.status_code == 303, result.text
        page = client.get('/bots/1/configure')
        assert page.status_code == 200
        token_value = re.search(
            r'name="token" value="([^"]+)"', page.text
        ).group(1)
        headers = {'X-Omni-CSRF': token_value}
        swarm = client.post('/api/fleet/swarms', headers=headers, json={
            'key': 'food_group', 'name': 'Food Group',
        })
        assert swarm.status_code == 201, swarm.text
        swarm_id = swarm.json()['id']
        member = client.post(f'/api/fleet/swarms/{swarm_id}/members',
                             headers=headers, json={'bot_id': 1})
        assert member.status_code == 201, member.text
        instance = client.put('/api/fleet/bots/1/instance', headers=headers,
                              json={'telegram_bot_id': 123456789,
                                    'deployment_provider': 'railway'})
        assert instance.status_code == 200, instance.text
        duplicate = client.put('/api/fleet/bots/2/instance', headers=headers,
                               json={'telegram_bot_id': 123456789})
        assert duplicate.status_code == 409
        overview = client.get('/api/fleet/overview').json()
        assert overview['swarms'][0]['bot_ids'] == [1]
        assert overview['instances'][0]['status'] == 'unconnected'
        assert overview['live_control_enabled'] is False
        manifest = client.get('/api/fleet/bots/1/mini-crm-manifest')
        assert manifest.status_code == 200, manifest.text
        assert manifest.json()['settings'][0]['key'] == 'menu_price_uah'
        assert manifest.json()['modules'] == ['little_legend']
        assert 'value' not in manifest.json()['settings'][0]
