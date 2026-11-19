"""Fecha estimada de la próxima compra de un cliente, en día de negocio de Lima.

Aplica las mismas reglas que la vista ``vista_clientes_proyeccion`` (``database/db.sql``),
pero calcula el día en America/Lima y no en la zona de la sesión de PostgreSQL (UTC).
"""
from datetime import date, timedelta

from utils.date_utils import to_peru_time


def fecha_estimada(cliente) -> date | None:
    """``proxima_compra_manual`` si existe; si no, día de Lima de la última compra + frecuencia."""
    manual = getattr(cliente, 'proxima_compra_manual', None)
    if manual is not None:
        return manual

    frecuencia = getattr(cliente, 'frecuencia_compra_dias', None)
    ultima = getattr(cliente, 'ultima_fecha_compra', None)
    if not frecuencia or frecuencia <= 0 or ultima is None:
        return None
    try:
        return to_peru_time(ultima).date() + timedelta(days=int(frecuencia))
    except OverflowError:
        return None  # frecuencia absurda (p. ej. 2147483647): sin proyección, no tumba la lista
