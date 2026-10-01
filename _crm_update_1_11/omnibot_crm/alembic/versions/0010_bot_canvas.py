"""Visual Telegram scenario graphs.

Revision ID: 0010_bot_canvas
Revises: 0009_growth_control
"""
from alembic import op
import sqlalchemy as sa

revision = '0010_bot_canvas'
down_revision = '0009_growth_control'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'crm_bot_scenario_graphs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('scenario_id', sa.Integer(), sa.ForeignKey('crm_bot_scenarios.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('graph_json', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('crm_admins.id')),
    )
    op.create_index('ix_crm_bot_scenario_graphs_scenario_id', 'crm_bot_scenario_graphs', ['scenario_id'], unique=True)


def downgrade():
    op.drop_index('ix_crm_bot_scenario_graphs_scenario_id', table_name='crm_bot_scenario_graphs')
    op.drop_table('crm_bot_scenario_graphs')
