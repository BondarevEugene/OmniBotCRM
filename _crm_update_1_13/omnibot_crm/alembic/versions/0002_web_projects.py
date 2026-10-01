"""Companies, pipelines, deals, project delivery and support.

Revision ID: 0002_web_projects
Revises: 0001_initial
"""
from alembic import op
from crm.db import Base
from crm import models

revision='0002_web_projects'
down_revision='0001_initial'
branch_labels=None
depends_on=None

NAMES=('crm_companies','crm_company_contacts','crm_pipelines','crm_stages',
       'crm_deals','crm_projects','crm_milestones','crm_tickets')


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(),tables=[Base.metadata.tables[n] for n in NAMES],checkfirst=True)


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind(),tables=[Base.metadata.tables[n] for n in NAMES],checkfirst=True)
