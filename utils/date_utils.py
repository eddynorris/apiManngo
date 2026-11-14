from datetime import UTC, datetime, time, timedelta

import pytz

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

def format_peru_date(dt, format_str='%Y-%m-%d %H:%M:%S'):
    """Formatea una fecha en hora Perú."""
    peru_dt = to_peru_time(dt)
    if peru_dt:
        return peru_dt.strftime(format_str)
    return None
