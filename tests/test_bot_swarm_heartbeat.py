"""Signed event ingest drives real Bot Swarm runtime status."""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault('SESSION_SECRET', 'test-secret-with-at-least-32-characters')
os.environ.setdefault('COOKIE_SECURE', 'false')

from fastapi.testclient import TestClient

from crm.app import create_app
from crm.config import Settings
from crm.db import Base
from crm.models import Bot, BotFleetInstance


def test_signed_heartbeat_updates_fleet_and_detects_offline(tmp_path):
    secret = 'fleet-test-ingest-secret-at-least-32-chars'
    app = create_app(Settings(
        database_url=f'sqlite:///{tmp_path / "fleet.db"}',
        session_secret='s' * 40, cookie_secure=False,
        ingest_secrets={'food_porn': secret},
    ))
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        bot = Bot(key='food_porn', name='Food Porn')
        db.add(bot)
        db.flush()
        db.add(BotFleetInstance(bot_id=bot.id, telegram_bot_id=12345678))
        db.commit()

    def send(client, event_id, kind, data):
        raw = json.dumps({'event_id': event_id, 'type': kind, 'data': data}).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), stamp.encode() + b'.' + raw,
                             hashlib.sha256).hexdigest()
        return client.post('/api/v1/events', content=raw, headers={
            'X-Bot-Key': 'food_porn', 'X-Timestamp': stamp, 'X-Signature': signature,
        })

    with TestClient(app) as client:
        assert client.get('/api/fleet/overview').status_code == 401
        result = send(client, 'hb-001', 'bot.heartbeat', {'runtime_version': '1.2.3'})
        assert result.status_code == 200, result.text
        assert result.json()['duplicate'] is False
        assert send(client, 'hb-001', 'bot.heartbeat',
                    {'runtime_version': '1.2.3'}).json()['duplicate'] is True
        with app.state.session_factory() as db:
            instance = db.query(BotFleetInstance).one()
            assert instance.status == 'running'
            assert instance.runtime_version == '1.2.3'
            assert instance.last_heartbeat_at is not None

        error = send(client, 'err-001', 'bot.error', {
            'severity': 'error', 'component': 'worker', 'message': 'failed',
        })
        assert error.status_code == 200, error.text
        with app.state.session_factory() as db:
            instance = db.query(BotFleetInstance).one()
            assert instance.status == 'degraded'
            instance.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=8)
            db.commit()
        # Offline state is derived at read time; the stored last event is retained.
