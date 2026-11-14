"""add_pagos_venta_index

Revision ID: f1a2b3c4d5e6
Revises: c4d5e6f7a8b9
Create Date: 2026-10-24 00:00:00.000000

Restaura un índice útil para ``pagos(venta_id)`` sin duplicar un índice
equivalente preexistente. En PostgreSQL el índice creado por esta migración se
marca con un comentario para que el downgrade solo elimine el índice propio.

Limitación documentada del downgrade: si el índice ya existía antes de esta
migración, o si el motor no soporta el marcador (por ejemplo SQLite), no es
posible demostrar quién creó el índice. En ese caso el downgrade no lo elimina
para no borrar un índice preexistente.
"""
from alembic import op
import sqlalchemy as sa


revision = 'f1a2b3c4d5e6'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None

NOMBRE_INDICE = 'idx_pagos_venta'
TABLA = 'pagos'
COLUMNA = 'venta_id'
MARCADOR = 'manngo:created_by_migration:f1a2b3c4d5e6'


def _indices_del_pago(bind):
    return {
        indice.get('name'): indice
        for indice in sa.inspect(bind).get_indexes(TABLA)
        if indice.get('name')
    }


def _indice_equivalente(indices):
    """Devuelve un índice cuya primera columna sea venta_id, si existe."""
    for nombre, indice in indices.items():
        columnas = indice.get('column_names') or []
        if columnas and columnas[0] == COLUMNA:
            return nombre
    return None


def _comentario_indice(bind, nombre):
    if bind.dialect.name != 'postgresql':
        return None
    consulta = sa.text(
        """
        SELECT d.description
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_description d
          ON d.objoid = c.oid
         AND d.classoid = 'pg_class'::regclass
        WHERE c.relname = :nombre
          AND n.nspname = current_schema()
        """
    )
    return bind.execute(consulta, {'nombre': nombre}).scalar()


def upgrade():
    bind = op.get_bind()
    indices = _indices_del_pago(bind)

    if _indice_equivalente(indices) is not None:
        return
    if NOMBRE_INDICE in indices:
        # Existe el nombre, pero no como índice útil por venta_id. No se
        # duplica ni se reemplaza un objeto preexistente desde esta migración.
        return

    op.create_index(NOMBRE_INDICE, TABLA, [COLUMNA], unique=False)
    if bind.dialect.name == 'postgresql':
        op.execute(f"COMMENT ON INDEX {NOMBRE_INDICE} IS '{MARCADOR}'")


def downgrade():
    bind = op.get_bind()
    indices = _indices_del_pago(bind)
    indice = indices.get(NOMBRE_INDICE)
    if indice is None:
        return

    columnas = indice.get('column_names') or []
    if columnas[:1] != [COLUMNA]:
        return

    if bind.dialect.name != 'postgresql':
        # Sin marcador no se puede demostrar la propiedad del índice.
        return
    if _comentario_indice(bind, NOMBRE_INDICE) != MARCADOR:
        # Índice preexistente o creado por otro mecanismo: no se elimina.
        return

    op.drop_index(NOMBRE_INDICE, table_name=TABLA)
