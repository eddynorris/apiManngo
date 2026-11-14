"""add_storage_cleanup_tasks

Revision ID: a1b2c3d4e5f6
Revises: 6efd542ce540
Create Date: 2026-10-22 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = '6efd542ce540'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'storage_cleanup_tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('storage_key', sa.String(length=512), nullable=False),
        sa.Column('reason', sa.String(length=50), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.String(length=255), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('storage_key', name='uq_storage_cleanup_storage_key'),
    )
    op.create_index(
        'idx_storage_cleanup_next_attempt',
        'storage_cleanup_tasks',
        ['next_attempt_at'],
        unique=False,
    )


def downgrade():
    op.drop_index('idx_storage_cleanup_next_attempt', table_name='storage_cleanup_tasks')
    op.drop_table('storage_cleanup_tasks')
