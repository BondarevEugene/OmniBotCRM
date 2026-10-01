"""Bot runtime settings, invoices and marketing reporting.

Revision ID: 0004_growth
Revises: 0003_integrations
"""
from alembic import op
from crm.db import Base
from crm import models

revision = '0004_growth'
down_revision = '0003_integrations'
branch_labels = None
depends_on = None
NAMES = ('crm_bot_settings', 'crm_project_expenses', 'crm_invoices', 'crm_invoice_lines', 'crm_marketing_channels',
         'crm_marketing_campaigns', 'crm_marketing_metrics', 'crm_marketing_touches',
         'crm_seo_keywords', 'crm_seo_rank_snapshots')


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables[n] for n in NAMES], checkfirst=True)


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind(), tables=[Base.metadata.tables[n] for n in reversed(NAMES)], checkfirst=True)
