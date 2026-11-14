"""guia_remision_electronica

Revision ID: a7b8c9d0e1f2
Revises: e8f9a0b1c2d3
Create Date: 2026-11-14 00:00:00.000000

GRE remitente (SUNAT): datos maestros (ubigeos, tipo de documento, vehículos,
conductores, descripción de guía), correlativo por serie y tabla de guías.
Solo añade columnas nullable y tablas nuevas; no modifica datos existentes.
"""
import sqlalchemy as sa
from alembic import op

revision = 'a7b8c9d0e1f2'
down_revision = 'e8f9a0b1c2d3'
branch_labels = None
depends_on = None


def upgrade():
    # Datos maestros en tablas existentes (todas nullable, sin backfill).
    with op.batch_alter_table('clientes') as batch:
        batch.add_column(sa.Column('tipo_documento', sa.String(length=1), nullable=True))
        batch.add_column(sa.Column('ubigeo', sa.String(length=6), nullable=True))

    with op.batch_alter_table('almacenes') as batch:
        batch.add_column(sa.Column('ubigeo', sa.String(length=6), nullable=True))
        batch.add_column(sa.Column('codigo_establecimiento_sunat', sa.String(length=4), nullable=True))
        batch.add_column(sa.Column('cliente_guia_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(
            'fk_almacenes_cliente_guia', 'clientes', ['cliente_guia_id'], ['id'], ondelete='SET NULL'
        )

    with op.batch_alter_table('presentaciones_producto') as batch:
        batch.add_column(sa.Column('descripcion_guia', sa.String(length=150), nullable=True))

    op.create_table(
        'vehiculos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('placa', sa.String(length=8), nullable=False),
        sa.Column('descripcion', sa.String(length=100), nullable=True),
        sa.Column('predeterminado', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('placa', name='uq_vehiculos_placa'),
    )

    op.create_table(
        'conductores',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tipo_documento', sa.String(length=1), nullable=False, server_default='1'),
        sa.Column('numero_documento', sa.String(length=15), nullable=False),
        sa.Column('nombres', sa.String(length=100), nullable=True),
        sa.Column('apellidos', sa.String(length=100), nullable=True),
        sa.Column('licencia', sa.String(length=20), nullable=True),
        sa.Column('predeterminado', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('numero_documento', name='uq_conductores_numero_documento'),
    )

    op.create_table(
        'series_gre',
        sa.Column('serie', sa.String(length=4), primary_key=True),
        sa.Column('ultimo_numero', sa.Integer(), nullable=False, server_default='0'),
        sa.CheckConstraint('ultimo_numero >= 0 AND ultimo_numero <= 99999999', name='ck_series_gre_rango'),
    )

    op.create_table(
        'guias_remision',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('serie', sa.String(length=4), nullable=False),
        sa.Column('numero', sa.Integer(), nullable=True),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('motivo', sa.String(length=2), nullable=False),
        sa.Column('origen_tipo', sa.String(length=10), nullable=False),
        sa.Column('venta_id', sa.Integer(), sa.ForeignKey('ventas.id', ondelete='SET NULL'), nullable=True),
        sa.Column('almacen_origen_id', sa.Integer(), sa.ForeignKey('almacenes.id', ondelete='RESTRICT'),
                  nullable=False),
        sa.Column('almacen_destino_id', sa.Integer(), sa.ForeignKey('almacenes.id', ondelete='RESTRICT'),
                  nullable=True),
        sa.Column('destinatario_cliente_id', sa.Integer(), sa.ForeignKey('clientes.id', ondelete='RESTRICT'),
                  nullable=True),
        sa.Column('vehiculo_id', sa.Integer(), sa.ForeignKey('vehiculos.id', ondelete='RESTRICT'),
                  nullable=False),
        sa.Column('conductor_id', sa.Integer(), sa.ForeignKey('conductores.id', ondelete='RESTRICT'),
                  nullable=False),
        sa.Column('fecha_inicio_traslado', sa.Date(), nullable=False),
        sa.Column('datos', sa.JSON(), nullable=False),
        sa.Column('xml_firmado', sa.Text(), nullable=True),
        sa.Column('hash_zip', sa.String(length=64), nullable=True),
        sa.Column('ticket', sa.String(length=64), nullable=True),
        sa.Column('cdr_codigo', sa.String(length=10), nullable=True),
        sa.Column('cdr_mensaje', sa.Text(), nullable=True),
        sa.Column('cdr_observaciones', sa.JSON(), nullable=True),
        sa.Column('cdr_xml', sa.Text(), nullable=True),
        sa.Column('qr_url', sa.Text(), nullable=True),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('creado_por_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('chat_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('serie', 'numero', name='uq_guias_remision_serie_numero'),
        sa.UniqueConstraint('idempotency_key', name='uq_guias_remision_idempotency_key'),
        sa.CheckConstraint("motivo IN ('01', '04')", name='ck_guias_remision_motivo'),
        sa.CheckConstraint("origen_tipo IN ('venta', 'traslado')", name='ck_guias_remision_origen'),
        sa.CheckConstraint(
            "estado IN ('enviada', 'en_proceso', 'aceptada', 'rechazada', 'incierta', 'error')",
            name='ck_guias_remision_estado',
        ),
    )
    op.create_index('idx_guias_remision_estado', 'guias_remision', ['estado'])
    op.create_index('ix_guias_remision_venta_id', 'guias_remision', ['venta_id'])

    with op.batch_alter_table('movimientos') as batch:
        batch.add_column(sa.Column('guia_remision_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(
            'fk_movimientos_guia_remision', 'guias_remision', ['guia_remision_id'], ['id'], ondelete='SET NULL'
        )
    op.create_index('ix_movimientos_guia_remision_id', 'movimientos', ['guia_remision_id'])


def downgrade():
    op.drop_index('ix_movimientos_guia_remision_id', table_name='movimientos')
    with op.batch_alter_table('movimientos') as batch:
        batch.drop_constraint('fk_movimientos_guia_remision', type_='foreignkey')
        batch.drop_column('guia_remision_id')

    op.drop_index('ix_guias_remision_venta_id', table_name='guias_remision')
    op.drop_index('idx_guias_remision_estado', table_name='guias_remision')
    op.drop_table('guias_remision')
    op.drop_table('series_gre')
    op.drop_table('conductores')
    op.drop_table('vehiculos')

    with op.batch_alter_table('presentaciones_producto') as batch:
        batch.drop_column('descripcion_guia')

    with op.batch_alter_table('almacenes') as batch:
        batch.drop_constraint('fk_almacenes_cliente_guia', type_='foreignkey')
        batch.drop_column('cliente_guia_id')
        batch.drop_column('codigo_establecimiento_sunat')
        batch.drop_column('ubigeo')

    with op.batch_alter_table('clientes') as batch:
        batch.drop_column('ubigeo')
        batch.drop_column('tipo_documento')
