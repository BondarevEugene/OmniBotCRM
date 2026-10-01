"""Neon project binding and repeatable source imports.

Revision ID: 0015_neon_source_workspace
Revises: 0014_openai_observability
"""
from alembic import op
import sqlalchemy as sa

revision = '0015_neon_source_workspace'
down_revision = '0014_openai_observability'
branch_labels = None
depends_on = None


def upgrade():
    table = 'crm_bot_database_connections'
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}
    with op.batch_alter_table(table) as batch:
        if 'project_id' not in columns:
            batch.add_column(sa.Column('project_id', sa.Integer()))
            batch.create_foreign_key('fk_crm_bot_database_connections_project_id',
                                     'crm_projects', ['project_id'], ['id'])
        for name, length in [('neon_project_id', 80), ('neon_branch_id', 80), ('neon_database_name', 120)]:
            if name not in columns:
                batch.add_column(sa.Column(name, sa.String(length), nullable=False, server_default=''))
    if 'ix_crm_bot_database_connections_project_id' not in {i['name'] for i in sa.inspect(op.get_bind()).get_indexes(table)}:
        op.create_index('ix_crm_bot_database_connections_project_id', table, ['project_id'])
    if 'crm_source_record_links' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table('crm_source_record_links',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('connection_id', sa.Integer(), sa.ForeignKey(f'{table}.id'), nullable=False),
            sa.Column('entity', sa.String(30), nullable=False),
            sa.Column('source_id', sa.String(120), nullable=False),
            sa.Column('crm_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint('connection_id', 'entity', 'source_id'))
        op.create_index('ix_crm_source_record_links_connection_id', 'crm_source_record_links', ['connection_id'])


def downgrade():
    op.drop_table('crm_source_record_links')
    with op.batch_alter_table('crm_bot_database_connections') as batch:
        batch.drop_index('ix_crm_bot_database_connections_project_id')
        for name in ('neon_project_id', 'neon_branch_id', 'neon_database_name', 'project_id'):
            batch.drop_column(name)
