"""Bot Swarm registry and per-bot configuration contract.

Revision ID: 0011_bot_swarm
Revises: 0010_bot_canvas
"""
from alembic import op
import sqlalchemy as sa

revision = '0011_bot_swarm'
down_revision = '0010_bot_canvas'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'crm_bot_swarms',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(80), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('crm_bot_swarms.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_crm_bot_swarms_key', 'crm_bot_swarms', ['key'], unique=True)
    op.create_index('ix_crm_bot_swarms_parent_id', 'crm_bot_swarms', ['parent_id'])
    op.create_table(
        'crm_bot_swarm_members',
        sa.Column('bot_id', sa.Integer(), sa.ForeignKey('crm_bots.id'), primary_key=True),
        sa.Column('swarm_id', sa.Integer(), sa.ForeignKey('crm_bot_swarms.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_crm_bot_swarm_members_swarm_id', 'crm_bot_swarm_members', ['swarm_id'])
    op.create_table(
        'crm_bot_fleet_instances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bot_id', sa.Integer(), sa.ForeignKey('crm_bots.id'), nullable=False, unique=True),
        sa.Column('telegram_bot_id', sa.BigInteger(), unique=True),
        sa.Column('deployment_provider', sa.String(32), nullable=False),
        sa.Column('deployment_ref', sa.String(120), nullable=False),
        sa.Column('runtime_version', sa.String(80), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('last_heartbeat_at', sa.DateTime(timezone=True)),
        sa.Column('manifest_json', sa.Text(), nullable=False),
        sa.Column('manifest_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table('crm_bot_fleet_instances')
    op.drop_index('ix_crm_bot_swarm_members_swarm_id', table_name='crm_bot_swarm_members')
    op.drop_table('crm_bot_swarm_members')
    op.drop_index('ix_crm_bot_swarms_parent_id', table_name='crm_bot_swarms')
    op.drop_index('ix_crm_bot_swarms_key', table_name='crm_bot_swarms')
    op.drop_table('crm_bot_swarms')
