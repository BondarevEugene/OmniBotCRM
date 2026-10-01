"""CRM dedicated schema, no changes to bot-owned tables.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
from crm.db import Base
from crm import models

revision='0001_initial'
down_revision=None
branch_labels=None
depends_on=None


def upgrade():
    # Keep revision 0001 scoped even when later models are added.
    names = ('crm_admins','crm_bots','crm_customers','crm_identities','crm_activities',
             'crm_orders','crm_payments','crm_expenses','crm_tasks','crm_campaigns',
             'crm_deliveries','crm_ingest_events','crm_audit')
    Base.metadata.create_all(bind=op.get_bind(),tables=[Base.metadata.tables[n] for n in names],checkfirst=True)


def downgrade():
    names = ('crm_admins','crm_bots','crm_customers','crm_identities','crm_activities',
             'crm_orders','crm_payments','crm_expenses','crm_tasks','crm_campaigns',
             'crm_deliveries','crm_ingest_events','crm_audit')
    Base.metadata.drop_all(bind=op.get_bind(),tables=[Base.metadata.tables[n] for n in names],checkfirst=True)
