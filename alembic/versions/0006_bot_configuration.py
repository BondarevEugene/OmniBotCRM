"""Per-bot acquirers and explicit ordering of arbitrary settings.

Revision ID: 0006_bot_configuration
Revises: 0005_food_porn
"""
from alembic import op
import sqlalchemy as sa
from crm.db import Base
from crm import models

revision = '0006_bot_configuration'
down_revision = '0005_food_porn'
branch_labels = None
depends_on = None


def upgrade():
    # Older migrations create new tables from current SQLAlchemy metadata; a
    # fresh install may already have this field when this revision is reached.
    if 'display_order' not in {col['name'] for col in sa.inspect(op.get_bind()).get_columns('crm_bot_settings')}:
        op.add_column('crm_bot_settings', sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'))
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables['crm_bot_payment_providers']], checkfirst=True)


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind(), tables=[Base.metadata.tables['crm_bot_payment_providers']], checkfirst=True)
    if 'display_order' in {col['name'] for col in sa.inspect(op.get_bind()).get_columns('crm_bot_settings')}:
        op.drop_column('crm_bot_settings', 'display_order')
