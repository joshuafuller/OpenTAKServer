"""Added eud_stats table

Revision ID: 1041fae84708
Revises: 21fb5a21f356
Create Date: 2024-12-17 04:37:54.374451

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1041fae84708'
down_revision = '21fb5a21f356'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('eud_stats',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=True),
    sa.Column('eud_uid', sa.String(length=255), nullable=False),
    sa.Column('heap_free_size', sa.Integer(), nullable=True),
    sa.Column('app_framerate', sa.Integer(), nullable=True),
    sa.Column('storage_total', sa.Integer(), nullable=True),
    sa.Column('heap_current_size', sa.Integer(), nullable=True),
    sa.Column('battery', sa.Integer(), nullable=True),
    sa.Column('battery_temp', sa.Integer(), nullable=True),
    sa.Column('deviceDataRx', sa.Integer(), nullable=True),
    sa.Column('heap_max_size', sa.Integer(), nullable=True),
    sa.Column('storage_available', sa.Integer(), nullable=True),
    sa.Column('deviceDataTx', sa.Integer(), nullable=True),
    sa.Column('ip_address', sa.String(length=255), nullable=True),
    sa.Column('battery_status', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['eud_uid'], ['euds.uid'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )

def downgrade():
    op.drop_table('eud_stats')
    # ### end Alembic commands ###