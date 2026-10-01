import re
import os

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

from fastapi.testclient import TestClient

from crm.app import create_app
from crm.config import Settings
from crm.db import Base
from crm.models import Admin, Bot, BotScenario, BotScenarioGraph
from crm.security import hash_password


def test_canvas_is_bot_scoped_versioned_and_csrf_protected(tmp_path):
    app = create_app(Settings(database_url=f'sqlite:///{tmp_path / "studio.db"}',
                              session_secret='s' * 40, cookie_secure=False))
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(Admin(email='admin@example.com', password_hash=hash_password('strong-password-123')))
        db.add_all([Bot(key='first', name='First'), Bot(key='second', name='Second')])
        db.flush()
        db.add(BotScenario(bot_id=1, key='welcome', name='Welcome', trigger_value='/start'))
        db.commit()
    with TestClient(app) as client:
        path = '/api/bots/1/scenarios/1/canvas'
        assert client.get(path).status_code == 401
        login = client.get('/login')
        token = re.search(r'name="token" value="([^"]+)"', login.text).group(1)
        assert client.post('/login', data={'token': token, 'email': 'admin@example.com',
                                           'password': 'strong-password-123'},
                           follow_redirects=False).status_code == 303
        page = client.get('/bots/1/scenarios/1/canvas')
        assert page.status_code == 200 and 'Конструктор диалога' in page.text
        csrf = re.search(r'data-csrf="([^"]+)"', page.text).group(1)
        assert client.get('/api/bots/2/scenarios/1/canvas').status_code == 404
        graph = client.get(path).json()['graph']
        assert client.post(path + '/check', json=graph).json()['valid']
        assert client.post(path + '/preview', json={'graph': graph}).json()['finished']
        assert client.put(path, json={'version': 0, 'graph': graph}).status_code == 403
        saved = client.put(path, json={'version': 0, 'graph': graph},
                           headers={'X-CSRF-Token': csrf})
        assert saved.status_code == 200 and saved.json()['version'] == 1
        assert client.put(path, json={'version': 0, 'graph': graph},
                          headers={'X-CSRF-Token': csrf}).status_code == 409
        manifest = client.get(path + '/export').json()
        assert manifest['bot_key'] == 'first' and manifest['scenario_key'] == 'welcome'
        assert manifest['graph'] == graph
        graph['edges'][0]['target'] = 'absent'
        assert client.put(path, json={'version': 1, 'graph': graph},
                          headers={'X-CSRF-Token': csrf}).status_code == 422
    with app.state.session_factory() as db:
        assert db.query(BotScenarioGraph).count() == 1
        assert db.get(BotScenario, 1).status == 'draft'
    app.state.engine.dispose()
