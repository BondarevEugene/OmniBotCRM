"""Add module registry and safely unconfigured parameter declarations.

Revision ID: 0007_bot_modules
Revises: 0006_bot_configuration
"""
from alembic import op
import sqlalchemy as sa
from crm.db import Base
from crm import models

revision = '0007_bot_modules'
down_revision = '0006_bot_configuration'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if 'is_configured' not in {col['name'] for col in sa.inspect(bind).get_columns('crm_bot_settings')}:
        op.add_column('crm_bot_settings', sa.Column('is_configured', sa.Boolean(),
                      nullable=False, server_default=sa.true()))
    for table in ('crm_ingest_events', 'crm_orders', 'crm_payments', 'crm_products'):
        if 'module_key' not in {col['name'] for col in sa.inspect(bind).get_columns(table)}:
            op.add_column(table, sa.Column('module_key', sa.String(80), nullable=True))
        name = f'ix_{table}_module_key'
        if name not in {index['name'] for index in sa.inspect(bind).get_indexes(table)}:
            op.create_index(name, table, ['module_key'])
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables['crm_bot_modules']], checkfirst=True)


def downgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=[Base.metadata.tables['crm_bot_modules']], checkfirst=True)
    for table in ('crm_ingest_events', 'crm_orders', 'crm_payments', 'crm_products'):
        name = f'ix_{table}_module_key'
        if name in {index['name'] for index in sa.inspect(bind).get_indexes(table)}:
            op.drop_index(name, table_name=table)
        if 'module_key' in {col['name'] for col in sa.inspect(bind).get_columns(table)}:
            op.drop_column(table, 'module_key')
    if 'is_configured' in {col['name'] for col in sa.inspect(bind).get_columns('crm_bot_settings')}:
        op.drop_column('crm_bot_settings', 'is_configured')
