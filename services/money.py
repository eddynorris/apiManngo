# services/money.py
"""Validación estricta de cantidades monetarias.

Las cantidades de dinero se manejan con ``Decimal``, nunca con ``float`` para
conciliación. Una cantidad válida debe ser finita y estrictamente positiva.
Se rechazan negativos, cero, NaN, Infinity, texto no numérico y valores fuera
del rango soportado por las columnas ``Numeric(12, 2)``.
"""
from decimal import Decimal, InvalidOperation

MAX_MONTO = Decimal("9999999999.99")


def monto_decimal_positivo(valor, campo="monto") -> Decimal:
    """Convierte y valida una cantidad monetaria estrictamente positiva.

    Lanza ``ValueError`` con un mensaje apto para el usuario. No repara,
    trunca ni redondea el valor recibido.
    """
    if valor is None or isinstance(valor, bool):
        raise ValueError(f"Monto inválido para {campo}")

    if isinstance(valor, Decimal):
        numero = valor
    elif isinstance(valor, int):
        numero = Decimal(valor)
    elif isinstance(valor, float):
        if valor != valor or valor in (float("inf"), float("-inf")):
            raise ValueError(f"Monto inválido para {campo}")
        numero = Decimal(str(valor))
    elif isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            raise ValueError(f"Monto inválido para {campo}")
        try:
            numero = Decimal(texto)
        except InvalidOperation:
            raise ValueError(f"Monto inválido para {campo}: {valor!r}") from None
    else:
        raise ValueError(f"Monto inválido para {campo}")

    if not numero.is_finite():
        raise ValueError(f"Monto inválido para {campo}: no se aceptan NaN ni Infinity")
    if numero <= 0:
        raise ValueError(f"El monto para {campo} debe ser positivo: {numero}")
    if numero > MAX_MONTO:
        raise ValueError(f"Monto inválido para {campo}: excede el máximo permitido")
    return numero
