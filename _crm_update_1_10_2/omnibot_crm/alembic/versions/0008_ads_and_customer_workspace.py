"""Ads reporting accounts, remote campaign IDs and conversion metrics.

Revision ID: 0008_ads_workspace
Revises: 0007_bot_modules
"""
from alembic import op
import sqlalchemy as sa
from crm.db import Base
from crm import models  # noqa: F401

revision = '0008_ads_workspace'
down_revision = '0007_bot_modules'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table, column in (
        ('crm_marketing_campaigns', sa.Column('external_id', sa.String(100))),
        ('crm_marketing_metrics', sa.Column('conversions', sa.Numeric(14, 2), nullable=False, server_default='0')),
        ('crm_marketing_metrics', sa.Column('origin', sa.String(20), nullable=False, server_default='manual')),
        ('crm_ingest_events', sa.Column('scenario_key', sa.String(80))),
    ):
        if column.name not in {c['name'] for c in sa.inspect(bind).get_columns(table)}:
            op.add_column(table, column)
    # Existing manually created campaigns have NULL external_id and remain untouched.
    table = 'crm_marketing_campaigns'
    known = {tuple(c['column_names']) for c in sa.inspect(bind).get_unique_constraints(table)}
    known.update(tuple(c['column_names']) for c in sa.inspect(bind).get_indexes(table) if c['unique'])
    if ('channel_id', 'external_id') not in known:
        op.create_index('uq_marketing_channel_external', table, ['channel_id', 'external_id'], unique=True)
    if 'ix_crm_ingest_events_scenario_key' not in {c['name'] for c in sa.inspect(bind).get_indexes('crm_ingest_events')}:
        op.create_index('ix_crm_ingest_events_scenario_key', 'crm_ingest_events', ['scenario_key'])
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables['crm_marketing_ad_accounts']], checkfirst=True)
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables['crm_bot_scenarios'],
                                               Base.metadata.tables['crm_bot_scenario_steps']], checkfirst=True)


def downgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=[Base.metadata.tables['crm_marketing_ad_accounts']], checkfirst=True)
    Base.metadata.drop_all(bind=bind, tables=[Base.metadata.tables['crm_bot_scenario_steps'],
                                             Base.metadata.tables['crm_bot_scenarios']], checkfirst=True)
    if 'uq_marketing_channel_external' in {c['name'] for c in sa.inspect(bind).get_indexes('crm_marketing_campaigns')}:
        op.drop_index('uq_marketing_channel_external', table_name='crm_marketing_campaigns')
    op.drop_column('crm_marketing_metrics', 'origin')
    op.drop_column('crm_marketing_metrics', 'conversions')
    op.drop_column('crm_marketing_campaigns', 'external_id')
    if 'ix_crm_ingest_events_scenario_key' in {c['name'] for c in sa.inspect(bind).get_indexes('crm_ingest_events')}:
        op.drop_index('ix_crm_ingest_events_scenario_key', table_name='crm_ingest_events')
    op.drop_column('crm_ingest_events', 'scenario_key')
