"""
Utilidades centralizadas para parseo de fechas.
Elimina duplicación de código en handlers de Telegram.
"""
from datetime import datetime, timezone


def parse_telegram_date(fecha_str: str) -> datetime:
    """
    Parsea una fecha en formato YYYY-MM-DD y la combina con la hora actual.
    
    Args:
        fecha_str: Fecha en formato YYYY-MM-DD
        
    Returns:
        datetime: Fecha parseada con hora actual, o datetime.now() si hay error
    """
    if not fecha_str:
        return datetime.now()
    
    try:
        fecha_parsed = datetime.strptime(fecha_str, "%Y-%m-%d")
        ahora = datetime.now()
        return datetime(
            fecha_parsed.year, fecha_parsed.month, fecha_parsed.day,
            ahora.hour, ahora.minute, ahora.second, ahora.microsecond, ahora.tzinfo
        )
    except (ValueError, TypeError):
        return datetime.now()


def parse_telegram_date_only(fecha_str: str):
    """
    Parsea una fecha en formato YYYY-MM-DD y retorna solo la fecha (sin hora).
    
    Args:
        fecha_str: Fecha en formato YYYY-MM-DD
        
    Returns:
        date: Fecha parseada, o date.today() si hay error
    """
    if not fecha_str:
        return datetime.now().date()
    
    try:
        return datetime.strptime(fecha_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return datetime.now().date()
