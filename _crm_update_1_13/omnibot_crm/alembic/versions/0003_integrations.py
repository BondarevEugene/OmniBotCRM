"""Track source imports and verified payment evidence.

Revision ID: 0003_integrations
Revises: 0002_web_projects
"""
from alembic import op
from crm.db import Base
from crm import models

revision = '0003_integrations'
down_revision = '0002_web_projects'
branch_labels = None
depends_on = None
NAMES = ('crm_products', 'crm_order_items', 'crm_sync_runs', 'crm_bot_incidents', 'crm_payment_verifications')


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables[n] for n in NAMES], checkfirst=True)


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind(), tables=[Base.metadata.tables[n] for n in NAMES], checkfirst=True)
