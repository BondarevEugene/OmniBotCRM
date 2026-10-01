"""Food_Porn source customer and menu snapshots.

Revision ID: 0005_food_porn
Revises: 0004_growth
"""
from alembic import op
from crm.db import Base
from crm import models

revision = '0005_food_porn'
down_revision = '0004_growth'
branch_labels = None
depends_on = None
NAMES = ('crm_food_porn_profiles', 'crm_food_porn_menus', 'crm_food_porn_menu_items',
         'crm_food_porn_generated_files')


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables[n] for n in NAMES], checkfirst=True)


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind(), tables=[Base.metadata.tables[n] for n in reversed(NAMES)], checkfirst=True)
