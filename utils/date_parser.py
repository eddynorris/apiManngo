"""
Utilidades centralizadas para parseo de fechas.

Convención de entrada:
- Una fecha ISO con zona horaria se respeta como instante.
- Una fecha ISO sin zona y una hora de operación ingenua se interpretan en
  ``America/Lima`` y se convierten a UTC para almacenar.
- Una fecha-día ``YYYY-MM-DD`` representa ese día calendario en Perú y se
  interpreta como medianoche de Perú (05:00 UTC) cuando se necesita un
  ``datetime``.
- Un campo ausente puede usar el valor actual; un campo presente pero nulo,
  vacío o no parseable se rechaza con ``ValueError``.
"""
from datetime import UTC, date, datetime, time

from utils.date_utils import PERU_TZ, get_peru_now


def _texto_requerido(value, field_name: str) -> str:
    if value is None:
        raise ValueError(f"El campo '{field_name}' no puede ser nulo o vacío")
    if not isinstance(value, str):
        raise ValueError(f"El campo '{field_name}' debe ser una fecha en texto")
    text = value.strip()
    if not text:
        raise ValueError(f"El campo '{field_name}' no puede estar vacío")
    return text


def _parse_dia_estricto(value, field_name: str = "fecha") -> date:
    """Parsea exclusivamente ``YYYY-MM-DD`` como día calendario peruano."""
    text = _texto_requerido(value, field_name)
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(
            f"Fecha inválida para '{field_name}': use el formato YYYY-MM-DD"
        ) from None


def parse_fecha_operacion(value, field_name: str = "fecha") -> datetime:
    """Parsea una fecha de operación explícita y la devuelve en UTC.

    Acepta ``YYYY-MM-DD`` o ISO 8601. Una fecha con offset/Z conserva el
    instante; una fecha u hora sin zona se interpreta como hora de Perú.
    Rechaza valores nulos, vacíos o no parseables.
    """
    text = _texto_requerido(value, field_name)

    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            dia = datetime.strptime(text, "%Y-%m-%d").date()
            local = PERU_TZ.localize(datetime.combine(dia, time.min))
        else:
            normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
            local = datetime.fromisoformat(normalized)
            if local.tzinfo is None:
                local = PERU_TZ.localize(local)
    except (ValueError, TypeError):
        raise ValueError(
            f"Fecha inválida para '{field_name}': use YYYY-MM-DD o ISO 8601"
        ) from None

    return local.astimezone(UTC)


def parse_telegram_date(fecha_str) -> datetime:
    """Parsea ``YYYY-MM-DD`` de Telegram y conserva la hora actual de Perú.

    Un valor ausente (``None`` o cadena vacía) es válido y usa el instante
    actual. Un valor presente no parseable lanza ``ValueError`` en vez de
    sustituirse silenciosamente por hoy. El resultado queda con zona Perú;
    al almacenarse en una columna con zona, la base lo convierte a UTC.
    """
    if fecha_str is None or (isinstance(fecha_str, str) and not fecha_str.strip()):
        return get_peru_now()

    dia = _parse_dia_estricto(fecha_str)
    ahora = get_peru_now()
    return PERU_TZ.localize(datetime(
        dia.year, dia.month, dia.day,
        ahora.hour, ahora.minute, ahora.second, ahora.microsecond,
    ))


def parse_telegram_date_only(fecha_str):
    """Parsea ``YYYY-MM-DD`` y retorna el día calendario de Perú.

    Un valor ausente usa el día actual en Perú. Un valor presente no
    parseable lanza ``ValueError`` en vez de sustituirse por hoy.
    """
    if fecha_str is None or (isinstance(fecha_str, str) and not fecha_str.strip()):
        return get_peru_now().date()

    return _parse_dia_estricto(fecha_str)
