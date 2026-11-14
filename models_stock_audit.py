# models_stock_audit.py
"""Modelos auxiliares de auditoría de stock.

Este módulo se mantiene separado de ``models.py`` para que el lote L3 no
modifique el archivo propiedad de L4. El recurso de movimientos lo importa
antes de ``db.create_all`` para registrar su metadata.

``MovimientoScope`` conserva la procedencia exacta de los movimientos
manuales nuevos. Los movimientos históricos o derivados no tienen fila aquí y,
por tanto, no son reversibles mediante ``DELETE /movimientos/<id>``.
"""
from sqlalchemy import Index

from extensions import db


class MovimientoScope(db.Model):
    __tablename__ = 'movimiento_scopes'

    movimiento_id = db.Column(
        db.Integer,
        db.ForeignKey('movimientos.id', ondelete='CASCADE'),
        primary_key=True,
    )
    inventario_id = db.Column(
        db.Integer,
        db.ForeignKey('inventario.id', ondelete='RESTRICT'),
        nullable=False,
    )
    almacen_id = db.Column(
        db.Integer,
        db.ForeignKey('almacenes.id', ondelete='RESTRICT'),
        nullable=False,
    )
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=db.func.now()
    )

    __table_args__ = (
        Index('idx_movimiento_scopes_inventario', 'inventario_id'),
        Index('idx_movimiento_scopes_almacen', 'almacen_id'),
    )
