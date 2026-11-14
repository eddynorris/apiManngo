"""add_movimiento_scopes

Revision ID: c4d5e6f7a8b9
Revises: b7c1d2e3f4a5
Create Date: 2026-10-24 00:00:00.000000

Procedencia exacta de movimientos manuales nuevos. No se rellena para
movimientos históricos: esos movimientos quedan sin alcance verificable y no
pueden revertirse borrando una sola pierna.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4d5e6f7a8b9'
down_revision = 'b7c1d2e3f4a5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'movimiento_scopes',
        sa.Column('movimiento_id', sa.Integer(), nullable=False),
        sa.Column('inventario_id', sa.Integer(), nullable=False),
        sa.Column('almacen_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['movimiento_id'], ['movimientos.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['inventario_id'], ['inventario.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['almacen_id'], ['almacenes.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('movimiento_id'),
    )
    op.create_index(
        'idx_movimiento_scopes_inventario', 'movimiento_scopes', ['inventario_id']
    )
    op.create_index(
        'idx_movimiento_scopes_almacen', 'movimiento_scopes', ['almacen_id']
    )


def downgrade():
    op.drop_index('idx_movimiento_scopes_almacen', table_name='movimiento_scopes')
    op.drop_index('idx_movimiento_scopes_inventario', table_name='movimiento_scopes')
    op.drop_table('movimiento_scopes')
