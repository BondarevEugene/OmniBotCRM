"""Applied bot configuration and executable dialogue sessions.

Revision ID: 0013_bot_operations
Revises: 0012_bot_data_connections
"""
from alembic import op
import sqlalchemy as sa

revision = '0013_bot_operations'
down_revision = '0012_bot_data_connections'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('crm_bot_config_applied',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('bot_id', sa.Integer, sa.ForeignKey('crm_bots.id'), nullable=False, unique=True),
        sa.Column('revision', sa.String(64), nullable=False),
        sa.Column('release_version', sa.Integer),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('error', sa.String(200), nullable=False),
        sa.Column('reported_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_crm_bot_config_applied_bot_id', 'crm_bot_config_applied', ['bot_id'], unique=True)
    op.create_table('crm_bot_graph_releases',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('bot_id', sa.Integer, sa.ForeignKey('crm_bots.id'), nullable=False),
        sa.Column('scenario_id', sa.Integer, sa.ForeignKey('crm_bot_scenarios.id'), nullable=False),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('graph_json', sa.Text, nullable=False),
        sa.Column('created_by', sa.Integer, sa.ForeignKey('crm_admins.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('scenario_id', 'version'))
    op.create_index('ix_crm_bot_graph_releases_bot_id', 'crm_bot_graph_releases', ['bot_id'])
    op.create_index('ix_crm_bot_graph_releases_scenario_id', 'crm_bot_graph_releases', ['scenario_id'])
    op.create_table('crm_bot_dialogue_sessions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('bot_id', sa.Integer, sa.ForeignKey('crm_bots.id'), nullable=False),
        sa.Column('scenario_id', sa.Integer, sa.ForeignKey('crm_bot_scenarios.id'), nullable=False),
        sa.Column('release_id', sa.Integer, sa.ForeignKey('crm_bot_graph_releases.id'), nullable=False),
        sa.Column('external_user_id', sa.String(100), nullable=False),
        sa.Column('current_node_id', sa.String(50), nullable=False),
        sa.Column('values_json', sa.Text, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('scenario_id', 'external_user_id'))
    op.create_index('ix_crm_bot_dialogue_sessions_bot_id', 'crm_bot_dialogue_sessions', ['bot_id'])
    op.create_index('ix_crm_bot_dialogue_sessions_scenario_id', 'crm_bot_dialogue_sessions', ['scenario_id'])
    op.create_table('crm_bot_dialogue_events',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('bot_id', sa.Integer, sa.ForeignKey('crm_bots.id'), nullable=False),
        sa.Column('session_id', sa.Integer, sa.ForeignKey('crm_bot_dialogue_sessions.id'), nullable=False),
        sa.Column('event_id', sa.String(120), nullable=False),
        sa.Column('result_json', sa.Text, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('bot_id', 'event_id'))
    op.create_index('ix_crm_bot_dialogue_events_bot_id', 'crm_bot_dialogue_events', ['bot_id'])
    op.create_index('ix_crm_bot_dialogue_events_session_id', 'crm_bot_dialogue_events', ['session_id'])


def downgrade():
    op.drop_table('crm_bot_dialogue_events')
    op.drop_table('crm_bot_dialogue_sessions')
    op.drop_table('crm_bot_graph_releases')
    op.drop_table('crm_bot_config_applied')
