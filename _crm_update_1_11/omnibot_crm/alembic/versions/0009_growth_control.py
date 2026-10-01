"""Lead Ads ingestion and versioned bot configuration.

Revision ID: 0009_growth_control
Revises: 0008_ads_workspace
"""
from alembic import op
import sqlalchemy as sa
from crm.db import Base
from crm import models  # noqa: F401

revision = '0009_growth_control'
down_revision = '0008_ads_workspace'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table, column in (
        ('crm_bot_settings', sa.Column('schema_json', sa.Text(), nullable=False, server_default='{}')),
        ('crm_marketing_ad_accounts', sa.Column('meta_page_id', sa.String(40), nullable=False, server_default='')),
    ):
        if column.name not in {c['name'] for c in sa.inspect(bind).get_columns(table)}:
            op.add_column(table, column)
    if 'meta_pipeline_id' not in {c['name'] for c in sa.inspect(bind).get_columns('crm_marketing_ad_accounts')}:
        with op.batch_alter_table('crm_marketing_ad_accounts') as batch:
            batch.add_column(sa.Column('meta_pipeline_id', sa.Integer(),
                                       sa.ForeignKey('crm_pipelines.id', name='fk_ad_accounts_meta_pipeline')))
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables['crm_bot_config_releases'],
                                               Base.metadata.tables['crm_marketing_leads']], checkfirst=True)


def downgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=[Base.metadata.tables['crm_marketing_leads'],
                                             Base.metadata.tables['crm_bot_config_releases']], checkfirst=True)
    with op.batch_alter_table('crm_marketing_ad_accounts') as batch:
        batch.drop_column('meta_pipeline_id')
    op.drop_column('crm_marketing_ad_accounts', 'meta_page_id')
    op.drop_column('crm_bot_settings', 'schema_json')
