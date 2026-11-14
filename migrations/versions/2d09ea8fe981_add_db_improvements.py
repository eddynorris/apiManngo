"""add_db_improvements

Revision ID: 2d09ea8fe981
Revises: d312602d0682
Create Date: 2026-07-21 17:44:06.508456

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '2d09ea8fe981'
down_revision = 'd312602d0682'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Clientes: Añadir clave foránea de almacen_preferido_id (si no existe ya en la BD)
    try:
        op.create_foreign_key('fk_clientes_almacen_preferido', 'clientes', 'almacenes', ['almacen_preferido_id'], ['id'], ondelete='SET NULL')
    except Exception:
        pass

    # 2. ComandosVozLogs: Convertir JSONB a JSON
    with op.batch_alter_table('comandos_voz_logs', schema=None) as batch_op:
        batch_op.alter_column('interpretacion',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               type_=sa.JSON(),
               existing_nullable=True)
        # Ajustamos los índices de created_at para no usar expresiones desc en Alembic si no es necesario
        batch_op.drop_index('idx_comandos_voz_created')
        batch_op.create_index('idx_comandos_voz_created', ['created_at'], unique=False)
        batch_op.drop_index('idx_comandos_voz_exito')
        batch_op.create_index('idx_comandos_voz_exito', ['exito', 'created_at'], unique=False)

    # 3. Gastos: Corregir clave foránea
    try:
        op.drop_constraint('gastos_lote_id_fkey', 'gastos', type_='foreignkey')
    except Exception:
        pass
    op.create_foreign_key('gastos_lote_id_fkey', 'gastos', 'lotes', ['lote_id'], ['id'])

    # 4. Inventario: Añadir clave única compuesta y el índice parcial uq_inventario_sin_lote
    with op.batch_alter_table('inventario', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_inventario_compuesto', ['presentacion_id', 'almacen_id', 'lote_id'])

    op.create_index('uq_inventario_sin_lote', 'inventario', ['presentacion_id', 'almacen_id'], unique=True, postgresql_where=sa.text('lote_id IS NULL'))

    # 5. Lotes: Hacer producto_id y peso_humedo_kg nullables en el modelo
    with op.batch_alter_table('lotes', schema=None) as batch_op:
        batch_op.alter_column('producto_id',
               existing_type=sa.INTEGER(),
               nullable=True)
        batch_op.alter_column('peso_humedo_kg',
               existing_type=sa.NUMERIC(precision=10, scale=2),
               nullable=True)

    # 6. Movimientos: Añadir columna venta_id y su FK
    with op.batch_alter_table('movimientos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('venta_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_movimientos_venta_id'), ['venta_id'], unique=False)
        batch_op.create_foreign_key('fk_movimientos_venta_id', 'ventas', ['venta_id'], ['id'], ondelete='SET NULL')

    # Backfill de venta_id desde motivo (p. ej. "Venta ID: 12")
    op.execute("""
        UPDATE movimientos m
        SET venta_id = sub.vid
        FROM (
            SELECT id, (regexp_match(motivo, '^Venta ID: (\\d+)'))[1]::int AS vid
            FROM movimientos
            WHERE motivo ~ '^Venta ID: \\d+'
        ) sub
        WHERE m.id = sub.id
          AND EXISTS (SELECT 1 FROM ventas v WHERE v.id = sub.vid)
    """)

    # 7. Pagos: Ajustar tipos y nombres de índices
    with op.batch_alter_table('pagos', schema=None) as batch_op:
        batch_op.alter_column('depositado',
               existing_type=sa.BOOLEAN(),
               nullable=False)

    # Recrear índices de pagos para usar nombres normalizados de SQLAlchemy
    try:
        op.drop_index('idx_pagos_depositado', 'pagos')
    except Exception:
        pass
    try:
        op.drop_index('idx_pagos_fecha_deposito', 'pagos')
    except Exception:
        pass
    try:
        op.drop_index('idx_pagos_monto_depositado', 'pagos')
    except Exception:
        pass
    try:
        op.drop_index('idx_pagos_venta', 'pagos')
    except Exception:
        pass

    op.create_index('idx_pago_depositado_fecha', 'pagos', ['depositado', 'fecha_deposito'], unique=False)
    op.create_index('idx_pago_fecha_deposito', 'pagos', ['fecha_deposito'], unique=False)

    # 8. PresentacionesProducto: Corregir unique constraint
    try:
        op.drop_constraint('presentaciones_producto_producto_id_nombre_key', 'presentaciones_producto', type_='unique')
    except Exception:
        pass
    try:
        op.create_unique_constraint('uq_producto_nombre_presentacion', 'presentaciones_producto', ['producto_id', 'nombre'])
    except Exception:
        pass

    # 9. Recetas: Modificar created_at y updated_at a nullables
    with op.batch_alter_table('recetas', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True)
        batch_op.alter_column('updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True)

    # 10. Users: Modificar JSONB a JSON
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('telegram_context',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               type_=sa.JSON(),
               existing_nullable=True)
        batch_op.alter_column('telegram_history',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               type_=sa.JSON(),
               existing_nullable=True)

    # 11. VentaDetalles: Cambiar cantidad a NUMERIC
    with op.batch_alter_table('venta_detalles', schema=None) as batch_op:
        batch_op.alter_column('cantidad',
               existing_type=sa.INTEGER(),
               type_=sa.Numeric(precision=12, scale=4),
               existing_nullable=False)

    # 12. Agregar restricción CHECK faltante en lotes (BD-07)
    op.create_check_constraint('ck_lotes_kg_no_negativo', 'lotes', 'cantidad_disponible_kg >= 0')

    # 13. Agregar Índices Faltantes de Rendimiento (BD-04)
    op.create_index('idx_ventas_almacen_fecha', 'ventas', ['almacen_id', 'fecha'], unique=False)
    op.create_index('idx_ventas_vendedor', 'ventas', ['vendedor_id'], unique=False)
    op.create_index('idx_ventas_estado_pago_parcial', 'ventas', ['estado_pago'], unique=False, postgresql_where=sa.text("estado_pago <> 'pagado'"))
    op.create_index('idx_venta_detalles_venta', 'venta_detalles', ['venta_id'], unique=False)
    op.create_index('idx_venta_detalles_presentacion', 'venta_detalles', ['presentacion_id'], unique=False)
    op.create_index('idx_movimientos_lote', 'movimientos', ['lote_id'], unique=False)
    op.create_index('idx_movimientos_tipo_operacion_fecha', 'movimientos', ['tipo_operacion', 'fecha'], unique=False)
    op.create_index('idx_pagos_fecha', 'pagos', ['fecha'], unique=False)
    op.create_index('idx_pagos_usuario', 'pagos', ['usuario_id'], unique=False)
    op.create_index('idx_gastos_fecha', 'gastos', ['fecha'], unique=False)
    op.create_index('idx_gastos_almacen', 'gastos', ['almacen_id'], unique=False)
    op.create_index('idx_inventario_lote', 'inventario', ['lote_id'], unique=False)


def downgrade():
    # 1. Eliminar índices adicionales de rendimiento
    op.drop_index('idx_ventas_almacen_fecha', table_name='ventas')
    op.drop_index('idx_ventas_vendedor', table_name='ventas')
    op.drop_index('idx_ventas_estado_pago_parcial', table_name='ventas')
    op.drop_index('idx_venta_detalles_venta', table_name='venta_detalles')
    op.drop_index('idx_venta_detalles_presentacion', table_name='venta_detalles')
    op.drop_index('idx_movimientos_lote', table_name='movimientos')
    op.drop_index('idx_movimientos_tipo_operacion_fecha', table_name='movimientos')
    op.drop_index('idx_pagos_fecha', table_name='pagos')
    op.drop_index('idx_pagos_usuario', table_name='pagos')
    op.drop_index('idx_gastos_fecha', table_name='gastos')
    op.drop_index('idx_gastos_almacen', table_name='gastos')
    op.drop_index('idx_inventario_lote', table_name='inventario')

    # 2. Eliminar restricción CHECK
    op.drop_constraint('ck_lotes_kg_no_negativo', 'lotes', type_='check')

    # 3. VentaDetalles: Cambiar cantidad a INTEGER
    with op.batch_alter_table('venta_detalles', schema=None) as batch_op:
        batch_op.alter_column('cantidad',
               existing_type=sa.Numeric(precision=12, scale=4),
               type_=sa.INTEGER(),
               existing_nullable=False)

    # 4. Users: Revertir JSON a JSONB
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('telegram_history',
               existing_type=sa.JSON(),
               type_=postgresql.JSONB(astext_type=sa.Text()),
               existing_nullable=True)
        batch_op.alter_column('telegram_context',
               existing_type=sa.JSON(),
               type_=postgresql.JSONB(astext_type=sa.Text()),
               existing_nullable=True)

    # 5. Recetas: Revertir nullables
    with op.batch_alter_table('recetas', schema=None) as batch_op:
        batch_op.alter_column('updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False)
        batch_op.alter_column('created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False)

    # 6. PresentacionesProducto: Revertir unique constraint
    try:
        op.drop_constraint('uq_producto_nombre_presentacion', 'presentaciones_producto', type_='unique')
    except Exception:
        pass
    try:
        op.create_unique_constraint('presentaciones_producto_producto_id_nombre_key', 'presentaciones_producto', ['producto_id', 'nombre'])
    except Exception:
        pass

    # 7. Pagos: Revertir tipos
    op.drop_index('idx_pago_fecha_deposito', table_name='pagos')
    op.drop_index('idx_pago_depositado_fecha', table_name='pagos')

    with op.batch_alter_table('pagos', schema=None) as batch_op:
        batch_op.alter_column('depositado',
               existing_type=sa.BOOLEAN(),
               nullable=True)

    # 8. Movimientos: Revertir venta_id
    with op.batch_alter_table('movimientos', schema=None) as batch_op:
        batch_op.drop_constraint('fk_movimientos_venta_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_movimientos_venta_id'))
        batch_op.drop_column('venta_id')

    # 9. Lotes: Revertir nullables
    with op.batch_alter_table('lotes', schema=None) as batch_op:
        batch_op.alter_column('peso_humedo_kg',
               existing_type=sa.NUMERIC(precision=10, scale=2),
               nullable=False)
        batch_op.alter_column('producto_id',
               existing_type=sa.INTEGER(),
               nullable=False)

    # 10. Inventario: Revertir constraints
    op.drop_index('uq_inventario_sin_lote', table_name='inventario')
    with op.batch_alter_table('inventario', schema=None) as batch_op:
        batch_op.drop_constraint('uq_inventario_compuesto', type_='unique')

    # 11. Gastos: Revertir FK
    try:
        op.drop_constraint('gastos_lote_id_fkey', 'gastos', type_='foreignkey')
    except Exception:
        pass
    op.create_foreign_key('gastos_lote_id_fkey', 'gastos', 'lotes', ['lote_id'], ['id'], ondelete='SET NULL')

    # 12. ComandosVozLogs: Revertir JSON a JSONB
    with op.batch_alter_table('comandos_voz_logs', schema=None) as batch_op:
        batch_op.drop_index('idx_comandos_voz_exito')
        batch_op.create_index('idx_comandos_voz_exito', ['exito', sa.literal_column('created_at DESC')], unique=False)
        batch_op.drop_index('idx_comandos_voz_created')
        batch_op.create_index('idx_comandos_voz_created', [sa.literal_column('created_at DESC')], unique=False)
        batch_op.alter_column('interpretacion',
               existing_type=sa.JSON(),
               type_=postgresql.JSONB(astext_type=sa.Text()),
               existing_nullable=True)

    # 13. Clientes: Revertir FK
    try:
        op.drop_constraint('fk_clientes_almacen_preferido', 'clientes', type_='foreignkey')
    except Exception:
        pass
