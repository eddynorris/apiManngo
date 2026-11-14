from datetime import UTC, datetime, time, timedelta

import pytz
from sqlalchemy import Date, and_, func

# Zona horaria de Perú
PERU_TZ = pytz.timezone('America/Lima')

def get_peru_now():
    """Retorna la fecha y hora actual en la zona horaria de Perú."""
    return datetime.now(PERU_TZ)

def to_peru_time(dt):
    """
    Convierte un objeto datetime a la zona horaria de Perú.
    Si el datetime no tiene timezone, asume UTC.
    """
    if dt is None:
        return None

    if dt.tzinfo is None:
        # Asumir UTC si no tiene timezone
        dt = dt.replace(tzinfo=UTC)

    return dt.astimezone(PERU_TZ)

def rango_utc_de_dias_peru(fecha_inicio, fecha_fin):
    """
    Convierte días de negocio de Perú [fecha_inicio, fecha_fin] (ambos inclusive)
    en un rango UTC semiabierto [inicio, fin) para filtrar columnas DateTime en UTC.
    """
    inicio = PERU_TZ.localize(datetime.combine(fecha_inicio, time.min)).astimezone(UTC)
    fin = PERU_TZ.localize(datetime.combine(fecha_fin + timedelta(days=1), time.min)).astimezone(UTC)
    return inicio, fin

def filtro_dias_peru(columna, fecha_inicio, fecha_fin):
    """Condición SQL: `columna` (DateTime en UTC) cae en los días de Perú indicados."""
    inicio, fin = rango_utc_de_dias_peru(fecha_inicio, fecha_fin)
    return and_(columna >= inicio, columna < fin)

def dia_peru_sql(columna, dialecto):
    """Expresión SQL con el día de Perú de una columna DateTime en UTC (para agrupar)."""
    if dialecto == 'postgresql':
        return func.date(func.timezone(PERU_TZ.zone, columna), type_=Date)
    # SQLite (tests): Lima es UTC-5 fijo, sin horario de verano
    return func.date(columna, '-5 hours', type_=Date)

def format_peru_date(dt, format_str='%Y-%m-%d %H:%M:%S'):
    """Formatea una fecha en hora Perú."""
    peru_dt = to_peru_time(dt)
    if peru_dt:
        return peru_dt.strftime(format_str)
    return None
