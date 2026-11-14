# services/authorization.py
"""Política central de autorización por almacén y propietario.

Regla conservadora de este lote: solo el rol exacto ``admin`` puede cruzar
almacenes o propietarios. El resto de usuarios debe tener un almacén asignado
coincidente y, cuando el recurso tenga dueño, ser su propietario.

La política siempre resuelve el actor contra la base de datos vigente; no
confía únicamente en los claims del JWT ni en un ``almacen_id`` enviado por
el cliente.
"""
from collections.abc import Iterable
from typing import Any

from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from extensions import db
from models import Users


def _convertir_id(valor: Any, campo: str = "ID") -> int:
    """Convierte solo IDs enteros positivos auténticos.

    Se aceptan enteros positivos y strings compuestos únicamente por dígitos
    ASCII que representen un entero positivo. Se rechazan bool, float, Decimal,
    cero, negativos, whitespace, signos, puntos y cualquier formato no entero.
    No se trunca ni se repara el valor recibido.
    """
    if isinstance(valor, bool):
        raise BadRequest(f"{campo} inválido")
    if isinstance(valor, int):
        if valor > 0:
            return valor
        raise BadRequest(f"{campo} inválido")
    if isinstance(valor, str):
        if valor.isascii() and valor.isdigit():
            numero = int(valor)
            if numero > 0:
                return numero
        raise BadRequest(f"{campo} inválido")
    raise BadRequest(f"{campo} inválido")


def obtener_actor(actor_id: Any) -> Users | None:
    """Carga el usuario vigente en BD a partir del ID del actor."""
    if actor_id is None:
        return None
    usuario = db.session.get(Users, _convertir_id(actor_id, "usuario_id"))
    if usuario is None:
        raise Forbidden("Usuario no autorizado")
    return usuario


def es_admin(usuario: Users | None) -> bool:
    return usuario is not None and usuario.rol == "admin"


def autorizar_almacen(actor_id: Any, almacen_id: Any) -> Users:
    """Autoriza al actor para operar sobre un almacén concreto."""
    usuario = obtener_actor(actor_id)
    if usuario is None:
        raise Forbidden("Usuario no autorizado")

    # Validar el destino antes de cualquier atajo por rol: ni un admin puede
    # operar con un ID de almacén de formato inválido.
    destino = _convertir_id(almacen_id, "almacen_id")
    if es_admin(usuario):
        return usuario

    if usuario.almacen_id is None or _convertir_id(usuario.almacen_id, "almacen_id") != destino:
        raise Forbidden("No tiene permiso para operar en este almacén")
    return usuario


def autorizar_venta(actor_id: Any, venta) -> Users:
    """Autoriza al actor sobre una venta real ya cargada."""
    usuario = autorizar_almacen(actor_id, venta.almacen_id)
    if es_admin(usuario):
        return usuario
    if venta.vendedor_id is None or _convertir_id(venta.vendedor_id, "vendedor_id") != usuario.id:
        raise Forbidden("No tiene permiso para operar sobre esta venta")
    return usuario


def autorizar_venta_para_pago(actor_id: Any, venta) -> Users:
    """Autoriza crear o cobrar contra una venta real.

    La creación de pagos y los lotes solo exigen que un usuario no admin
    pertenezca al almacén de la venta. No se usa el vendedor ni ningún
    ``almacen_id`` enviado por el cliente.
    """
    return autorizar_almacen(actor_id, venta.almacen_id)


def autorizar_pago(actor_id: Any, pago) -> Users:
    """Autoriza leer, mutar o borrar un pago existente.

    Además del almacén de la venta, un usuario no admin solo puede operar
    sobre pagos registrados por él mismo. El actor se resuelve siempre contra
    la base de datos vigente.
    """
    usuario = autorizar_venta_para_pago(actor_id, pago.venta)
    if es_admin(usuario):
        return usuario
    if pago.usuario_id is None:
        raise Forbidden("No tiene permiso para operar sobre este pago")
    if _convertir_id(pago.usuario_id, "usuario_id") != usuario.id:
        raise Forbidden("No tiene permiso para operar sobre este pago")
    return usuario


def autorizar_pedido(actor_id: Any, pedido) -> Users:
    """Autoriza al actor sobre un pedido real ya cargado."""
    usuario = autorizar_almacen(actor_id, pedido.almacen_id)
    if es_admin(usuario):
        return usuario
    if pedido.vendedor_id is None or _convertir_id(pedido.vendedor_id, "vendedor_id") != usuario.id:
        raise Forbidden("No tiene permiso para operar sobre este pedido")
    return usuario


def normalizar_ids(data: Any) -> Any:
    """Normaliza IDs numéricos que pueden llegar como string desde JSON/multipart."""
    if isinstance(data, dict):
        resultado = dict(data)
        for campo in (
            "almacen_id", "cliente_id", "vendedor_id", "venta_id", "pedido_id",
            "presentacion_id", "lote_id", "usuario_id", "pago_id", "detalle_id",
        ):
            if campo in resultado and resultado[campo] not in (None, ""):
                resultado[campo] = _convertir_id(resultado[campo], campo)
        for campo_anidado in ("detalles", "items", "pagos", "ids"):
            valor = resultado.get(campo_anidado)
            if isinstance(valor, list):
                resultado[campo_anidado] = [
                    normalizar_ids(item) if isinstance(item, dict) else item
                    for item in valor
                ]
        return resultado
    if isinstance(data, list):
        return [normalizar_ids(item) for item in data]
    return data


def normalizar_lista_ids(ids: Iterable[Any]) -> list[int]:
    """Convierte una lista heterogénea de IDs (string/int) en enteros únicos."""
    if isinstance(ids, (str, bytes)) or not isinstance(ids, list):
        raise BadRequest("Los IDs deben ser una lista")
    normalizados: list[int] = []
    for valor in ids:
        identificador = _convertir_id(valor, "id")
        if identificador not in normalizados:
            normalizados.append(identificador)
    if not normalizados:
        raise BadRequest("Debe proporcionar al menos un ID")
    return normalizados


def requerir_encontrados(solicitados: list[int], encontrados: list[int]) -> None:
    """Exige que todos los IDs solicitados existan antes de mutar el lote."""
    if set(solicitados) != set(encontrados):
        raise NotFound("Uno o más recursos no existen")
