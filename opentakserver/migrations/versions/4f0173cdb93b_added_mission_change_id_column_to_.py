"""Added mission_change_id column to mission_uids table

Revision ID: 4f0173cdb93b
Revises: 00546817d518
Create Date: 2024-10-11 20:52:40.425560

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4f0173cdb93b'
down_revision = '00546817d518'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('mission_uids', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mission_change_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key("mission_change", 'mission_changes', ['mission_change_id'], ['id'])

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('mission_uids', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_column('mission_change_id')

    # ### end Alembic commands ###