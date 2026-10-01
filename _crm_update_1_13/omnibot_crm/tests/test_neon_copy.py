from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text

from crm.db import Base
from crm.models import Admin, Bot, BotSetting
from migrate_crm_to_neon import copy_database, snapshot_sqlite


def test_snapshot_and_copy_refuses_nonempty_destination(tmp_path):
    original = tmp_path/'original.db'
    source_engine = create_engine('sqlite:///' + str(original))
    target_engine = create_engine('sqlite:///' + str(tmp_path/'target.db'))
    for engine in (source_engine, target_engine):
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32))'))
            conn.execute(text("INSERT INTO alembic_version VALUES ('0012_bot_data_connections')"))
    with source_engine.begin() as conn:
        conn.execute(Admin.__table__.insert(), {'id': 3, 'email': 'test@example.com',
            'password_hash': 'scrypt$fake$fake', 'role': 'owner', 'active': True})
        conn.execute(Bot.__table__.insert(), {'id': 7, 'key': 'food_porn', 'name': 'Food Porn'})
        conn.execute(BotSetting.__table__.insert(), {'id': 11, 'bot_id': 7,
            'key': 'private_token', 'kind': 'secret', 'label': 'Token', 'value': 'encrypted-secret'})
    snap = snapshot_sqlite(original)
    try:
        with source_engine.begin() as conn:
            conn.execute(Bot.__table__.insert(), {'id': 9, 'key': 'new_bot', 'name': 'Later'})
        snapshot_engine = create_engine('sqlite:///' + str(snap))
        try:
            counts = copy_database(snapshot_engine, target_engine, '0012_bot_data_connections')
            assert counts['crm_bots'] == 1
            assert copy_database(snapshot_engine, target_engine, '0012_bot_data_connections', apply=True) == counts
            with target_engine.connect() as conn:
                assert conn.scalar(select(Bot.name).where(Bot.id == 7)) == 'Food Porn'
                assert conn.scalar(select(BotSetting.value).where(BotSetting.id == 11)) == 'encrypted-secret'
            with pytest.raises(ValueError, match='contains CRM records'):
                copy_database(snapshot_engine, target_engine, '0012_bot_data_connections', apply=True)
        finally:
            snapshot_engine.dispose()
    finally:
        source_engine.dispose(); target_engine.dispose(); Path(snap).unlink(missing_ok=True)
