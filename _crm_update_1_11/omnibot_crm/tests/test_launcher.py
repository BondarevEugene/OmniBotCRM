"""Operational gates must be safe before a database or proxy is configured."""
import json
from types import SimpleNamespace

import launcher
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from crm.db import Base
from crm.models import Bot, BotSetting


def test_missing_sqlite_diagnostics_does_not_create_database(tmp_path):
    path = tmp_path / 'does-not-exist.db'
    config = SimpleNamespace(database_url=f'sqlite:///{path}')
    checks = launcher.inspect_database(config, '0008_ads_workspace', False, 2)
    assert checks[0].status == launcher.Status.FAIL
    assert not path.exists()


def test_socks_dependency_gate_and_sanitized_report(tmp_path, monkeypatch):
    monkeypatch.setenv('ALL_PROXY', 'socks5h://user:private-password@localhost:9050')
    assert launcher.has_socks_proxy()
    original = launcher.importlib.util.find_spec
    monkeypatch.setattr(launcher.importlib.util, 'find_spec',
                        lambda module: None if module == 'socksio' else original(module))
    assert 'SOCKS proxy support' in launcher.check_environment()[3].detail
    monkeypatch.setattr(launcher, 'REPORT', tmp_path / 'report.json')
    checks = [launcher.Check('SYSTEM', 'Proxy', launcher.Status.FAIL,
                             'SOCKS proxy support missing', critical=True)]
    launcher.save_report(checks, 0)
    report = json.loads(launcher.REPORT.read_text(encoding='utf-8'))
    assert report['health_score'] == 0
    assert 'private-password' not in launcher.REPORT.read_text(encoding='utf-8')


def test_bot_readiness_lists_own_settings_without_secret_values(tmp_path):
    url = f'sqlite:///{tmp_path / "bots.db"}'
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
        conn.execute(text("INSERT INTO alembic_version VALUES ('0008_ads_workspace')"))
    with Session(engine) as db:
        bot = Bot(key='my_bot', name='My Bot')
        db.add(bot)
        db.flush()
        db.add(BotSetting(bot_id=bot.id, key='ingest_secret', label='Signing secret',
                          kind='secret', value='encrypted-hidden-value'))
        db.commit()
    engine.dispose()
    config = SimpleNamespace(database_url=url, ingest_secrets={}, bot_tokens={})
    checks = launcher.inspect_database(config, '0008_ads_workspace', False, 2)
    assert any(c.name == 'Бот my_bot' and 'подпись=да' in c.detail for c in checks)
    assert 'encrypted-hidden-value' not in str(checks)
