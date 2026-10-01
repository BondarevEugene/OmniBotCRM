"""Per-bot source database topology and advertising ownership.

Revision ID: 0012_bot_data_connections
Revises: 0011_finance_workspace
"""
from alembic import op
import sqlalchemy as sa

revision = '0012_bot_data_connections'
down_revision = '0011_finance_workspace'
branch_labels = None
depends_on = None


def upgrade():
    # An older migration creates this table from live ORM metadata on fresh installs.
    # Existing installations created before this release still need the new column.
    inspector = sa.inspect(op.get_bind())
    if 'bot_id' not in {c['name'] for c in inspector.get_columns('crm_marketing_channels')}:
        with op.batch_alter_table('crm_marketing_channels') as batch:
            batch.add_column(sa.Column('bot_id', sa.Integer(), nullable=True))
            batch.create_foreign_key('fk_crm_marketing_channels_bot_id', 'crm_bots', ['bot_id'], ['id'])
    if 'ix_crm_marketing_channels_bot_id' not in {i['name'] for i in inspector.get_indexes('crm_marketing_channels')}:
        op.create_index('ix_crm_marketing_channels_bot_id', 'crm_marketing_channels', ['bot_id'])
    op.create_table('crm_bot_database_connections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bot_id', sa.Integer(), sa.ForeignKey('crm_bots.id'), nullable=False),
        sa.Column('label', sa.String(120), nullable=False),
        sa.Column('dialect', sa.String(20), nullable=False),
        sa.Column('encrypted_url', sa.Text(), nullable=False),
        sa.Column('schema_name', sa.String(120), nullable=False),
        sa.Column('last_status', sa.String(20), nullable=False),
        sa.Column('last_error', sa.String(300), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(timezone=True)),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('crm_admins.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('bot_id', 'label'))
    op.create_index('ix_crm_bot_database_connections_bot_id', 'crm_bot_database_connections', ['bot_id'])
    op.create_table('crm_bot_database_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('crm_bot_database_connections.id'), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('table_count', sa.Integer(), nullable=False),
        sa.Column('schema_json', sa.Text(), nullable=False),
        sa.Column('schema_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_crm_bot_database_snapshots_connection_id', 'crm_bot_database_snapshots', ['connection_id'])
    op.create_index('ix_crm_bot_database_snapshots_schema_hash', 'crm_bot_database_snapshots', ['schema_hash'])
    op.create_table('crm_bot_data_mappings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('crm_bot_database_connections.id'), nullable=False),
        sa.Column('entity', sa.String(30), nullable=False),
        sa.Column('table_name', sa.String(120), nullable=False),
        sa.Column('columns_json', sa.Text(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('crm_admins.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('connection_id', 'entity'))
    op.create_index('ix_crm_bot_data_mappings_connection_id', 'crm_bot_data_mappings', ['connection_id'])


def downgrade():
    op.drop_index('ix_crm_bot_data_mappings_connection_id', table_name='crm_bot_data_mappings')
    op.drop_table('crm_bot_data_mappings')
    op.drop_index('ix_crm_bot_database_snapshots_schema_hash', table_name='crm_bot_database_snapshots')
    op.drop_index('ix_crm_bot_database_snapshots_connection_id', table_name='crm_bot_database_snapshots')
    op.drop_table('crm_bot_database_snapshots')
    op.drop_index('ix_crm_bot_database_connections_bot_id', table_name='crm_bot_database_connections')
    op.drop_table('crm_bot_database_connections')
    with op.batch_alter_table('crm_marketing_channels') as batch:
        batch.drop_index('ix_crm_marketing_channels_bot_id')
        batch.drop_column('bot_id')
