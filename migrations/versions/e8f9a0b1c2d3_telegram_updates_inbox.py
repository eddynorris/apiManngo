"""telegram_updates_inbox

Revision ID: e8f9a0b1c2d3
Revises: f1a2b3c4d5e6
Create Date: 2026-11-13 00:00:00.000000

Convierte ``telegram_updates`` (antes solo deduplicación) en una bandeja de
entrada duradera para el webhook (A08): estado, chat, payload e intentos.

Las filas existentes provienen de la versión anterior, que ya procesó esos
updates: quedan como ``procesado`` para no reprocesarlas.
"""
import sqlalchemy as sa
from alembic import op

revision = 'e8f9a0b1c2d3'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('telegram_updates') as batch:
        batch.add_column(sa.Column('estado', sa.String(length=20), nullable=False, server_default='procesado'))
        batch.add_column(sa.Column('chat_id', sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column('payload', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('intentos', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('ultimo_error', sa.String(length=300), nullable=True))
        batch.add_column(sa.Column('actualizado_en', sa.DateTime(timezone=True), nullable=True))
    op.create_index('idx_telegram_updates_chat_estado', 'telegram_updates', ['chat_id', 'estado'])
    op.create_index('idx_telegram_updates_estado', 'telegram_updates', ['estado'])


def downgrade():
    op.drop_index('idx_telegram_updates_estado', table_name='telegram_updates')
    op.drop_index('idx_telegram_updates_chat_estado', table_name='telegram_updates')
    with op.batch_alter_table('telegram_updates') as batch:
        batch.drop_column('actualizado_en')
        batch.drop_column('ultimo_error')
        batch.drop_column('intentos')
        batch.drop_column('payload')
        batch.drop_column('chat_id')
        batch.drop_column('estado')
