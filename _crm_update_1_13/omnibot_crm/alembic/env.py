import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from crm.db import Base
from crm import models
from crm.config import settings

config=context.config
target_metadata=Base.metadata
config.set_main_option('sqlalchemy.url', settings().database_url.replace('%','%%'))


def offline():
    context.configure(url=config.get_main_option('sqlalchemy.url'),target_metadata=target_metadata,literal_binds=True)
    with context.begin_transaction(): context.run_migrations()


def online():
    engine=engine_from_config(config.get_section(config.config_ini_section),prefix='sqlalchemy.',poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction(): context.run_migrations()
    engine.dispose()


if context.is_offline_mode(): offline()
else: online()
