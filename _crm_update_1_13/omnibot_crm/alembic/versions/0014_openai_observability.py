"""OpenAI organization inventory, provider costs, usage and bot attribution.

Revision ID: 0014_openai_observability
Revises: 0013_bot_operations
"""
from alembic import op
import sqlalchemy as sa

revision = '0014_openai_observability'
down_revision = '0013_bot_operations'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('crm_openai_organizations',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.String(120), nullable=False, unique=True),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('encrypted_admin_key', sa.Text, nullable=False),
        sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('last_error', sa.String(200), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_crm_openai_organizations_organization_id', 'crm_openai_organizations', ['organization_id'], unique=True)
    op.create_table('crm_openai_resources',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('crm_openai_organizations.id'), nullable=False),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('external_id', sa.String(180), nullable=False),
        sa.Column('payload_json', sa.Text, nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('organization_id', 'kind', 'external_id'))
    op.create_index('ix_crm_openai_resources_organization_id', 'crm_openai_resources', ['organization_id'])
    op.create_table('crm_openai_costs',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('crm_openai_organizations.id'), nullable=False),
        sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('bucket_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('project_id', sa.String(120), nullable=False),
        sa.Column('api_key_id', sa.String(120), nullable=False),
        sa.Column('line_item', sa.String(200), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('amount', sa.Numeric(20, 8), nullable=False),
        sa.Column('quantity', sa.Numeric(20, 8)),
        sa.Column('quantity_unit', sa.String(60), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('organization_id', 'bucket_start', 'fingerprint'))
    op.create_index('ix_crm_openai_costs_organization_id', 'crm_openai_costs', ['organization_id'])
    op.create_index('ix_crm_openai_costs_bucket_start', 'crm_openai_costs', ['bucket_start'])
    op.create_table('crm_openai_usage',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('crm_openai_organizations.id'), nullable=False),
        sa.Column('family', sa.String(40), nullable=False),
        sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('bucket_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('dimensions_json', sa.Text, nullable=False),
        sa.Column('metrics_json', sa.Text, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('organization_id', 'family', 'bucket_start', 'fingerprint'))
    op.create_index('ix_crm_openai_usage_organization_id', 'crm_openai_usage', ['organization_id'])
    op.create_index('ix_crm_openai_usage_bucket_start', 'crm_openai_usage', ['bucket_start'])
    op.create_table('crm_openai_bot_bindings',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('crm_openai_organizations.id'), nullable=False),
        sa.Column('bot_id', sa.Integer, sa.ForeignKey('crm_bots.id'), nullable=False),
        sa.Column('label', sa.String(120), nullable=False),
        sa.Column('project_id', sa.String(120), nullable=False),
        sa.Column('api_key_id', sa.String(120), nullable=False),
        sa.Column('notes', sa.String(300), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('bot_id', 'label'))
    op.create_index('ix_crm_openai_bot_bindings_organization_id', 'crm_openai_bot_bindings', ['organization_id'])
    op.create_index('ix_crm_openai_bot_bindings_bot_id', 'crm_openai_bot_bindings', ['bot_id'])


def downgrade():
    op.drop_table('crm_openai_bot_bindings')
    op.drop_table('crm_openai_usage')
    op.drop_table('crm_openai_costs')
    op.drop_table('crm_openai_resources')
    op.drop_table('crm_openai_organizations')
