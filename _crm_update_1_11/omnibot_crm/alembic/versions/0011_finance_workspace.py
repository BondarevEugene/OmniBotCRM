"""Shared payment defaults and dated manual exchange rates.

Revision ID: 0011_finance_workspace
Revises: 0010_bot_canvas
"""
from alembic import op
import sqlalchemy as sa

revision = '0011_finance_workspace'
down_revision = '0010_bot_canvas'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('crm_shared_payment_providers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provider', sa.String(80), nullable=False),
        sa.Column('encrypted_credentials', sa.Text(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('crm_admins.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('provider'))
    op.create_index('ix_crm_shared_payment_providers_provider',
        'crm_shared_payment_providers', ['provider'], unique=True)
    op.create_table('crm_exchange_rates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('rate_date', sa.Date(), nullable=False),
        sa.Column('uah_per_unit', sa.Numeric(16, 6), nullable=False),
        sa.Column('source', sa.String(120), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('crm_admins.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('currency', 'rate_date'))
    op.create_index('ix_crm_exchange_rates_currency', 'crm_exchange_rates', ['currency'])
    op.create_index('ix_crm_exchange_rates_rate_date', 'crm_exchange_rates', ['rate_date'])
    op.create_table('crm_bot_accounting_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bot_id', sa.Integer(), sa.ForeignKey('crm_bots.id'), nullable=False),
        sa.Column('owner_name', sa.String(200), nullable=False),
        sa.Column('tax_id', sa.String(30), nullable=False),
        sa.Column('tax_group', sa.String(30), nullable=False),
        sa.Column('iban', sa.String(50), nullable=False),
        sa.Column('vat_status', sa.String(60), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('crm_admins.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('bot_id'))
    op.create_index('ix_crm_bot_accounting_profiles_bot_id',
        'crm_bot_accounting_profiles', ['bot_id'], unique=True)


def downgrade():
    op.drop_index('ix_crm_bot_accounting_profiles_bot_id', table_name='crm_bot_accounting_profiles')
    op.drop_table('crm_bot_accounting_profiles')
    op.drop_index('ix_crm_exchange_rates_rate_date', table_name='crm_exchange_rates')
    op.drop_index('ix_crm_exchange_rates_currency', table_name='crm_exchange_rates')
    op.drop_table('crm_exchange_rates')
    op.drop_index('ix_crm_shared_payment_providers_provider', table_name='crm_shared_payment_providers')
    op.drop_table('crm_shared_payment_providers')
