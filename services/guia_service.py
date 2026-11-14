"""Orquestación de la Guía de Remisión Electrónica (GRE remitente, SUNAT).

Flujo: borrador (sin escritura) -> ``faltantes`` -> ``emitir`` (reserva número,
firma, persiste y envía) -> ``consultar`` / ``procesar_pendientes`` (ticket y CDR).

Reglas (docs/SUNAT_FUENTES_VERIFICADAS.md):
- Solo datos guardados en la BD o en la configuración; nunca valores inventados.
- El número se reserva al emitir (no al preparar), con la fila de ``series_gre``
  bloqueada, y la guía se confirma en la BD ANTES de enviarla a SUNAT.
- Un envío incierto no se reintenta con otro número.
- Solo un CDR válido con ResponseCode 0 marca la guía como aceptada.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
from dataclasses import asdict
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

import click
from flask.cli import with_appcontext

from extensions import db
from models import (
    Almacen,
    Cliente,
    Conductor,
    GuiaRemision,
    Movimiento,
    PresentacionProducto,
    SerieGre,
    Vehiculo,
    Venta,
    VentaDetalle,
)
from services.sunat_service import (
    SunatAuthError,
    SunatError,
    SunatTransmissionUncertain,
    _parse_env_flag,
    sunat_service,
)
from services.sunat_xml import (
    CDR_PENDIENTE,
    GRE_REMITENTE_TIPO,
    GRE_TRANSPORTE_PRIVADO_MODALIDAD,
    BienGre,
    DestinatarioGre,
    EmisorGre,
    GreRemitenteData,
    SunatXmlError,
    TransportePrivadoGre,
    extract_qr_url,
    load_pkcs12,
    prepare_gre,
)
from services.telegram_service import telegram_service
from utils.date_utils import get_peru_now

logger = logging.getLogger(__name__)

ESTADO_ENVIADA = "enviada"
ESTADO_EN_PROCESO = "en_proceso"
ESTADO_ACEPTADA = "aceptada"
ESTADO_RECHAZADA = "rechazada"
ESTADO_INCIERTA = "incierta"
ESTADO_ERROR = "error"
ESTADOS_PENDIENTES = (ESTADO_ENVIADA, ESTADO_EN_PROCESO)

MOTIVO_VENTA = "01"
MOTIVO_TRASLADO_PROPIO = "04"
TIPO_DOC_RUC = "6"
UNIDAD_KILOGRAMO = "KGM"

_SERIE_RE = re.compile(r"T[A-Z0-9]{3}")
_UBIGEO_RE = re.compile(r"\d{6}")
_TIPOS_DOCUMENTO = {"1", "4", "6", "7", "0", "A", "B"}


class GuiaError(Exception):
    """Error de la capa de guías (mensaje apto para el usuario)."""


class GuiaDatosFaltantes(GuiaError):
    def __init__(self, faltantes: list[str]):
        self.faltantes = list(faltantes)
        super().__init__("Faltan datos para la guía: " + "; ".join(self.faltantes))


class GuiaEnvioDeshabilitado(GuiaError):
    pass


class GuiaConflicto(GuiaError):
    pass


# ---------------------------------------------------------------------------
# Configuración del emisor
# ---------------------------------------------------------------------------

def _env(nombre: str) -> str:
    return (os.environ.get(nombre) or "").strip()


def _config_emisor() -> dict:
    return {
        "ruc": _env("SUNAT_RUC"),
        "razon_social": _env("SUNAT_RAZON_SOCIAL"),
        "direccion": _env("SUNAT_EMISOR_DIRECCION"),
        "ubigeo": _env("SUNAT_EMISOR_UBIGEO"),
        "serie": _env("SUNAT_SERIE_GRE").upper(),
    }


def envio_habilitado() -> bool:
    return _parse_env_flag(os.environ.get("SUNAT_ENVIO_HABILITADO"))


def _material_firma():
    contenido = _env("SUNAT_CERT_P12_B64")
    if not contenido:
        raise GuiaError("Falta el certificado digital (SUNAT_CERT_P12_B64).")
    try:
        p12 = base64.b64decode(contenido, validate=True)
    except (binascii.Error, ValueError):
        raise GuiaError("SUNAT_CERT_P12_B64 no es base64 válido.") from None
    try:
        return load_pkcs12(p12, os.environ.get("SUNAT_CERT_PASSWORD"))
    except SunatXmlError:
        raise GuiaError("No se pudo abrir el certificado digital (¿clave incorrecta?).") from None


# ---------------------------------------------------------------------------
# Borradores (sin escritura en BD)
# ---------------------------------------------------------------------------

def _decimal(valor) -> Decimal:
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        raise GuiaError(f"Cantidad inválida: {valor!r}") from None
    if not numero.is_finite() or numero <= 0:
        raise GuiaError(f"La cantidad debe ser mayor que 0: {valor!r}")
    return numero


def _texto_cantidad(cantidad: Decimal) -> str:
    if cantidad == cantidad.to_integral_value():
        return str(int(cantidad))
    return format(cantidad.normalize(), "f")


def _numero_json(valor: Decimal):
    return int(valor) if valor == valor.to_integral_value() else float(valor)


def _bienes(items) -> list[dict]:
    bienes = []
    for item in items or []:
        presentacion_id = int(item["presentacion_id"])
        cantidad = _decimal(item["cantidad"])
        presentacion = db.session.get(PresentacionProducto, presentacion_id)
        if presentacion is None:
            raise GuiaError(f"No existe la presentación {presentacion_id}.")
        texto = (presentacion.descripcion_guia or presentacion.nombre or "").strip().upper()
        capacidad = Decimal(str(presentacion.capacidad_kg)) if presentacion.capacidad_kg is not None else None
        peso = capacidad * cantidad if capacidad is not None and capacidad > 0 else None
        bienes.append({
            "presentacion_id": presentacion_id,
            "cantidad": _numero_json(cantidad),
            "descripcion": f"{_texto_cantidad(cantidad)} {texto}",
            "unidad": UNIDAD_KILOGRAMO,
            "peso_kg": _numero_json(peso) if peso is not None else None,
        })
    return bienes


def _peso_total(bienes) -> float | int:
    total = sum((Decimal(str(b["peso_kg"])) for b in bienes if b.get("peso_kg")), Decimal("0"))
    return _numero_json(total) if total > 0 else 0


def _predeterminado(modelo, indicado):
    if indicado is not None:
        return int(indicado)
    fila = modelo.query.filter_by(predeterminado=True, activo=True).order_by(modelo.id.asc()).first()
    return fila.id if fila else None


def _fecha_inicio(valor) -> str:
    if valor is None:
        return get_peru_now().date().isoformat()
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    try:
        return date.fromisoformat(str(valor)).isoformat()
    except ValueError:
        raise GuiaError(f"Fecha de inicio de traslado inválida: {valor!r}") from None


def borrador_traslado(actor, almacen_origen_id, almacen_destino_id, items, *, fecha_inicio=None,
                      vehiculo_id=None, conductor_id=None, destinatario_cliente_id=None) -> dict:
    """Borrador de guía para un traslado entre almacenes propios.

    Motivo 04 si ambos almacenes tienen código de establecimiento SUNAT; si no,
    motivo 01 (venta) al ``destinatario_cliente_id`` o al cliente de guía del
    almacén destino (decisión de Eddy del 14/11/2026).
    """
    origen = db.session.get(Almacen, int(almacen_origen_id))
    destino = db.session.get(Almacen, int(almacen_destino_id))
    if origen is None or destino is None:
        raise GuiaError("Almacén de origen o destino inexistente.")
    if origen.id == destino.id:
        raise GuiaError("El almacén de origen y el de destino no pueden ser el mismo.")

    ambos_con_codigo = bool(origen.codigo_establecimiento_sunat and destino.codigo_establecimiento_sunat)
    if ambos_con_codigo:
        motivo, destinatario = MOTIVO_TRASLADO_PROPIO, None
    else:
        motivo = MOTIVO_VENTA
        destinatario = (int(destinatario_cliente_id) if destinatario_cliente_id is not None
                        else destino.cliente_guia_id)

    bienes = _bienes(items)
    return {
        "motivo": motivo,
        "origen_tipo": "traslado",
        "venta_id": None,
        "almacen_origen_id": origen.id,
        "almacen_destino_id": destino.id,
        "destinatario_cliente_id": destinatario,
        "vehiculo_id": _predeterminado(Vehiculo, vehiculo_id),
        "conductor_id": _predeterminado(Conductor, conductor_id),
        "fecha_inicio_traslado": _fecha_inicio(fecha_inicio),
        "bienes": bienes,
        "peso_total_kg": _peso_total(bienes),
    }


def borrador_venta(actor, venta_id, *, fecha_inicio=None, vehiculo_id=None, conductor_id=None) -> dict:
    """Borrador de guía (motivo 01) para una venta registrada."""
    venta = db.session.get(Venta, int(venta_id))
    if venta is None:
        raise GuiaError(f"No existe la venta {venta_id}.")
    detalles = VentaDetalle.query.filter_by(venta_id=venta.id).order_by(VentaDetalle.id.asc()).all()
    bienes = _bienes([{"presentacion_id": d.presentacion_id, "cantidad": d.cantidad} for d in detalles])
    return {
        "motivo": MOTIVO_VENTA,
        "origen_tipo": "venta",
        "venta_id": venta.id,
        "almacen_origen_id": venta.almacen_id,
        "almacen_destino_id": None,
        "destinatario_cliente_id": venta.cliente_id,
        "vehiculo_id": _predeterminado(Vehiculo, vehiculo_id),
        "conductor_id": _predeterminado(Conductor, conductor_id),
        "fecha_inicio_traslado": _fecha_inicio(fecha_inicio),
        "bienes": bienes,
        "peso_total_kg": _peso_total(bienes),
    }


# ---------------------------------------------------------------------------
# Validación de datos faltantes
# ---------------------------------------------------------------------------

def _llegada(borrador):
    """(ubigeo, dirección, etiqueta, código de establecimiento) del punto de llegada."""
    if borrador.get("origen_tipo") == "traslado":
        destino = db.session.get(Almacen, borrador.get("almacen_destino_id") or 0)
        if destino is None:
            return None, None, "almacén de destino", None
        return destino.ubigeo, destino.direccion, f"almacén {destino.nombre}", destino.codigo_establecimiento_sunat
    cliente = db.session.get(Cliente, borrador.get("destinatario_cliente_id") or 0)
    if cliente is None:
        return None, None, "cliente", None
    return cliente.ubigeo, cliente.direccion, f"cliente {cliente.nombre}", None


def faltantes(borrador) -> list[str]:
    """Lista legible de datos que faltan para poder emitir. Vacía si está todo."""
    falta: list[str] = []
    conf = _config_emisor()
    for clave, variable in (("ruc", "SUNAT_RUC"), ("razon_social", "SUNAT_RAZON_SOCIAL"),
                            ("direccion", "SUNAT_EMISOR_DIRECCION"), ("ubigeo", "SUNAT_EMISOR_UBIGEO"),
                            ("serie", "SUNAT_SERIE_GRE")):
        if not conf[clave]:
            falta.append(f"Configura {variable} (datos del emisor).")
    if conf["serie"] and not _SERIE_RE.fullmatch(conf["serie"]):
        falta.append("SUNAT_SERIE_GRE debe ser T más 3 caracteres (p. ej. T001).")
    if conf["ubigeo"] and not _UBIGEO_RE.fullmatch(conf["ubigeo"]):
        falta.append("SUNAT_EMISOR_UBIGEO debe tener 6 dígitos.")

    origen = db.session.get(Almacen, borrador.get("almacen_origen_id") or 0)
    if origen is None:
        falta.append("Falta el almacén de origen.")
    else:
        if not origen.ubigeo or not _UBIGEO_RE.fullmatch(origen.ubigeo):
            falta.append(f"Falta el ubigeo (6 dígitos) del almacén {origen.nombre}.")
        if not (origen.direccion or "").strip():
            falta.append(f"Falta la dirección del almacén {origen.nombre}.")

    ubigeo, direccion, etiqueta, _codigo = _llegada(borrador)
    if not ubigeo or not _UBIGEO_RE.fullmatch(str(ubigeo)):
        falta.append(f"Falta el ubigeo (6 dígitos) de llegada del {etiqueta}.")
    if not (direccion or "").strip():
        falta.append(f"Falta la dirección de llegada del {etiqueta}.")

    if borrador.get("motivo") == MOTIVO_VENTA:
        cliente = db.session.get(Cliente, borrador.get("destinatario_cliente_id") or 0)
        if cliente is None:
            falta.append("Falta el destinatario de la guía (cliente de guía del almacén destino).")
        else:
            if cliente.tipo_documento not in _TIPOS_DOCUMENTO:
                falta.append(f"Falta el tipo de documento del destinatario {cliente.nombre} (DNI o RUC).")
            if not (cliente.ruc or "").strip():
                falta.append(f"Falta el número de documento del destinatario {cliente.nombre}.")

    vehiculo = db.session.get(Vehiculo, borrador.get("vehiculo_id") or 0)
    if vehiculo is None:
        falta.append("Falta el vehículo (vehiculo_id): registra uno o marca uno como predeterminado.")
    conductor = db.session.get(Conductor, borrador.get("conductor_id") or 0)
    if conductor is None:
        falta.append("Falta el conductor (conductor_id): registra uno o marca uno como predeterminado.")
    else:
        for campo, nombre in (("licencia", "licencia"), ("nombres", "nombres"), ("apellidos", "apellidos")):
            if not (getattr(conductor, campo) or "").strip():
                falta.append(f"Falta {nombre} del conductor {conductor.numero_documento}.")

    bienes = borrador.get("bienes") or []
    if not bienes:
        falta.append("La guía no tiene bienes.")
    for bien in bienes:
        if not bien.get("peso_kg") or Decimal(str(bien["peso_kg"])) <= 0:
            presentacion = db.session.get(PresentacionProducto, bien.get("presentacion_id") or 0)
            nombre = presentacion.nombre if presentacion else bien.get("presentacion_id")
            falta.append(f"La presentación {nombre} no tiene peso (capacidad_kg).")

    try:
        inicio = date.fromisoformat(str(borrador.get("fecha_inicio_traslado")))
        if inicio < get_peru_now().date():
            falta.append("La fecha de inicio del traslado no puede ser anterior a hoy.")
    except ValueError:
        falta.append("Falta una fecha de inicio de traslado válida.")
    return falta


# ---------------------------------------------------------------------------
# Construcción de los datos firmados (y su serialización para consultar)
# ---------------------------------------------------------------------------

def _gre_data(borrador, serie: str, numero: int, ahora: datetime) -> GreRemitenteData:
    conf = _config_emisor()
    origen = db.session.get(Almacen, borrador["almacen_origen_id"])
    ubigeo_llegada, direccion_llegada, _etiqueta, codigo_llegada = _llegada(borrador)
    vehiculo = db.session.get(Vehiculo, borrador["vehiculo_id"])
    conductor = db.session.get(Conductor, borrador["conductor_id"])

    if borrador["motivo"] == MOTIVO_TRASLADO_PROPIO:
        destinatario = DestinatarioGre(TIPO_DOC_RUC, conf["ruc"], conf["razon_social"],
                                       ubigeo_llegada, direccion_llegada)
        codigo_partida = origen.codigo_establecimiento_sunat
    else:
        cliente = db.session.get(Cliente, borrador["destinatario_cliente_id"])
        destinatario = DestinatarioGre(cliente.tipo_documento, cliente.ruc.strip(), cliente.nombre,
                                       ubigeo_llegada, direccion_llegada)
        codigo_partida = codigo_llegada = None

    bienes = tuple(
        BienGre(codigo=f"P{b['presentacion_id']}", descripcion=b["descripcion"], unidad=b["unidad"],
                cantidad=Decimal(str(b["peso_kg"])), peso_kg=Decimal(str(b["peso_kg"])))
        for b in borrador["bienes"]
    )
    return GreRemitenteData(
        serie=serie,
        numero=numero,
        fecha_emision=ahora.date(),
        hora_emision=ahora.time().replace(microsecond=0, tzinfo=None),
        fecha_inicio_traslado=date.fromisoformat(borrador["fecha_inicio_traslado"]),
        motivo_traslado=borrador["motivo"],
        tipo_documento=GRE_REMITENTE_TIPO,
        modalidad_traslado=GRE_TRANSPORTE_PRIVADO_MODALIDAD,
        emisor=EmisorGre(conf["ruc"], conf["razon_social"], conf["ubigeo"], conf["direccion"]),
        destinatario=destinatario,
        partida_ubigeo=origen.ubigeo,
        partida_direccion=origen.direccion.strip(),
        llegada_ubigeo=ubigeo_llegada,
        llegada_direccion=direccion_llegada.strip(),
        transporte=TransportePrivadoGre(
            placa=vehiculo.placa,
            conductor_tipo_documento=conductor.tipo_documento,
            conductor_numero_documento=conductor.numero_documento,
            conductor_nombre=conductor.nombres.strip(),
            conductor_licencia=conductor.licencia.strip(),
            conductor_apellidos=conductor.apellidos.strip(),
        ),
        bienes=bienes,
        partida_codigo_establecimiento=codigo_partida,
        llegada_codigo_establecimiento=codigo_llegada,
    )


def _gre_a_dict(data: GreRemitenteData) -> dict:
    return {
        "serie": data.serie,
        "numero": data.numero,
        "fecha_emision": data.fecha_emision.isoformat(),
        "hora_emision": data.hora_emision.isoformat(),
        "fecha_inicio_traslado": data.fecha_inicio_traslado.isoformat(),
        "motivo_traslado": data.motivo_traslado,
        "emisor": asdict(data.emisor),
        "destinatario": asdict(data.destinatario),
        "partida_ubigeo": data.partida_ubigeo,
        "partida_direccion": data.partida_direccion,
        "llegada_ubigeo": data.llegada_ubigeo,
        "llegada_direccion": data.llegada_direccion,
        "transporte": asdict(data.transporte),
        "bienes": [
            {"codigo": b.codigo, "descripcion": b.descripcion, "unidad": b.unidad,
             "cantidad": str(b.cantidad), "peso_kg": str(b.peso_kg)}
            for b in data.bienes
        ],
        "partida_codigo_establecimiento": data.partida_codigo_establecimiento,
        "llegada_codigo_establecimiento": data.llegada_codigo_establecimiento,
    }


def gre_desde_dict(d: dict) -> GreRemitenteData:
    return GreRemitenteData(
        serie=d["serie"],
        numero=int(d["numero"]),
        fecha_emision=date.fromisoformat(d["fecha_emision"]),
        hora_emision=time.fromisoformat(d["hora_emision"]),
        fecha_inicio_traslado=date.fromisoformat(d["fecha_inicio_traslado"]),
        motivo_traslado=d["motivo_traslado"],
        tipo_documento=GRE_REMITENTE_TIPO,
        modalidad_traslado=GRE_TRANSPORTE_PRIVADO_MODALIDAD,
        emisor=EmisorGre(**d["emisor"]),
        destinatario=DestinatarioGre(**d["destinatario"]),
        partida_ubigeo=d["partida_ubigeo"],
        partida_direccion=d["partida_direccion"],
        llegada_ubigeo=d["llegada_ubigeo"],
        llegada_direccion=d["llegada_direccion"],
        transporte=TransportePrivadoGre(**d["transporte"]),
        bienes=tuple(
            BienGre(codigo=b["codigo"], descripcion=b["descripcion"], unidad=b["unidad"],
                    cantidad=Decimal(b["cantidad"]), peso_kg=Decimal(b["peso_kg"]))
            for b in d["bienes"]
        ),
        partida_codigo_establecimiento=d.get("partida_codigo_establecimiento"),
        llegada_codigo_establecimiento=d.get("llegada_codigo_establecimiento"),
    )


def _huella(borrador) -> str:
    canonico = json.dumps(borrador, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _reservar_numero(serie: str) -> int:
    fila = db.session.query(SerieGre).filter_by(serie=serie).with_for_update().first()
    if fila is None:
        db.session.add(SerieGre(serie=serie, ultimo_numero=0))
        db.session.flush()
        fila = db.session.query(SerieGre).filter_by(serie=serie).with_for_update().one()
    if fila.ultimo_numero >= 99999999:
        raise GuiaError(f"La serie {serie} llegó a su número máximo.")
    fila.ultimo_numero += 1
    db.session.flush()
    return fila.ultimo_numero


def _mensaje_seguro(exc: Exception) -> str:
    """Mensaje fijo por tipo de error: nunca se copia el texto de la excepción (puede
    contener respuestas de SUNAT o datos sensibles)."""
    if isinstance(exc, SunatTransmissionUncertain):
        return ("Envío incierto: no se sabe si SUNAT recibió la guía. Verifícala en SOL "
                "antes de emitir otra; no se reintenta con otro número.")
    if isinstance(exc, SunatAuthError):
        return "SUNAT rechazó la autenticación: revisa las credenciales API y el usuario SOL."
    return f"No se pudo enviar la guía a SUNAT ({type(exc).__name__})."


# ---------------------------------------------------------------------------
# Emisión y consulta
# ---------------------------------------------------------------------------

def emitir(borrador, actor, idempotency_key, *, chat_id=None, movimiento_ids=None) -> GuiaRemision:
    """Reserva número, firma, persiste (commit) y envía la guía a SUNAT."""
    key = str(idempotency_key or "").strip()
    if not key or len(key) > 64:
        raise GuiaError("idempotency_key inválida.")

    falta = faltantes(borrador)
    if falta:
        raise GuiaDatosFaltantes(falta)
    if not envio_habilitado():
        raise GuiaEnvioDeshabilitado(
            "El envío a SUNAT está deshabilitado (SUNAT_ENVIO_HABILITADO). No se emitió la guía."
        )

    huella = _huella(borrador)
    existente = GuiaRemision.query.filter_by(idempotency_key=key).first()
    if existente is not None:
        if (existente.datos or {}).get("huella") == huella:
            return existente
        raise GuiaConflicto("Ya existe una guía con esa clave y datos distintos.")

    material = _material_firma()  # antes de reservar: un certificado roto no consume número
    serie = _config_emisor()["serie"]
    try:
        numero = _reservar_numero(serie)
        ahora = get_peru_now()
        data = _gre_data(borrador, serie, numero, ahora)
        prepared = prepare_gre(data, material.private_key_pem, material.certificate_pem)
        guia = GuiaRemision(
            serie=serie,
            numero=numero,
            estado=ESTADO_INCIERTA,  # hasta conocer el resultado del envío
            motivo=borrador["motivo"],
            origen_tipo=borrador["origen_tipo"],
            venta_id=borrador.get("venta_id"),
            almacen_origen_id=borrador["almacen_origen_id"],
            almacen_destino_id=borrador.get("almacen_destino_id"),
            destinatario_cliente_id=borrador.get("destinatario_cliente_id"),
            vehiculo_id=borrador["vehiculo_id"],
            conductor_id=borrador["conductor_id"],
            fecha_inicio_traslado=date.fromisoformat(borrador["fecha_inicio_traslado"]),
            datos={"huella": huella, "borrador": borrador, "gre": _gre_a_dict(data)},
            xml_firmado=prepared.signed_xml_bytes.decode("utf-8"),
            hash_zip=prepared.package.sha256_hex,
            idempotency_key=key,
            creado_por_id=getattr(actor, "id", None),
            chat_id=chat_id,
        )
        db.session.add(guia)
        db.session.flush()
        if movimiento_ids:
            Movimiento.query.filter(Movimiento.id.in_([int(m) for m in movimiento_ids])).update(
                {Movimiento.guia_remision_id: guia.id}, synchronize_session=False
            )
        db.session.commit()  # número y guía confirmados ANTES de tocar la red
    except Exception:
        db.session.rollback()
        raise

    try:
        resultado = sunat_service.emitir_guia_remision(prepared)
    except SunatTransmissionUncertain as exc:
        guia.estado = ESTADO_INCIERTA
        guia.cdr_mensaje = _mensaje_seguro(exc)
    except SunatError as exc:
        guia.estado = ESTADO_ERROR
        guia.cdr_mensaje = _mensaje_seguro(exc)
    else:
        guia.estado = ESTADO_ENVIADA
        guia.ticket = resultado.ticket
    db.session.commit()
    return guia


def consultar(guia: GuiaRemision) -> GuiaRemision:
    """Consulta el ticket y actualiza el estado solo con evidencia del CDR."""
    if guia.estado not in ESTADOS_PENDIENTES or not guia.ticket:
        return guia
    esperado = gre_desde_dict(guia.datos["gre"])
    resultado = sunat_service.consultar_estado_ticket(guia.ticket, esperado)

    cdr = resultado.cdr
    if resultado.accepted and cdr is not None:
        guia.estado = ESTADO_ACEPTADA
        guia.cdr_codigo = cdr.response_code
        guia.cdr_xml = cdr.cdr_xml.decode("utf-8") if isinstance(cdr.cdr_xml, bytes) else cdr.cdr_xml
        guia.cdr_observaciones = list(cdr.observations)
        guia.cdr_mensaje = resultado.message
        guia.qr_url = extract_qr_url(cdr.cdr_xml)
    elif resultado.estado == CDR_PENDIENTE:
        guia.estado = ESTADO_EN_PROCESO
    elif cdr is not None or resultado.ticket_code == "99":
        guia.estado = ESTADO_RECHAZADA
        guia.cdr_codigo = cdr.response_code if cdr is not None else resultado.ticket_code
        detalle = "; ".join(cdr.observations) if cdr is not None and cdr.observations else ""
        guia.cdr_mensaje = (resultado.message + (f": {detalle}" if detalle else ""))[:2000]
        if cdr is not None:
            guia.cdr_observaciones = list(cdr.observations)
    db.session.commit()
    return guia


def numero_completo(guia: GuiaRemision) -> str:
    return f"{guia.serie}-{guia.numero:08d}" if guia.numero is not None else guia.serie


def _avisar(guia: GuiaRemision) -> None:
    if not guia.chat_id:
        return
    if guia.estado == ESTADO_ACEPTADA:
        from services.guia_pdf import generar_pdf
        telegram_service.send_message(
            guia.chat_id,
            f"✅ <b>Guía {numero_completo(guia)} Aceptada por SUNAT.</b>\n"
            "El conductor puede mostrar el QR del PDF adjunto.",
        )
        telegram_service.send_document(
            guia.chat_id, f"{numero_completo(guia)}.pdf", generar_pdf(guia),
            caption=f"Guía de remisión {numero_completo(guia)}",
        )
    elif guia.estado == ESTADO_RECHAZADA:
        telegram_service.send_message(
            guia.chat_id,
            f"❌ <b>Guía {numero_completo(guia)} Rechazada por SUNAT.</b>\n"
            f"Código {guia.cdr_codigo}: {guia.cdr_mensaje or 'sin detalle'}",
        )


def procesar_pendientes(ahora=None) -> dict:
    """Consulta las guías enviadas/en proceso y avisa por Telegram al terminar."""
    resumen = {"consultadas": 0, "aceptadas": 0, "rechazadas": 0, "errores": 0}
    ids = [g.id for g in GuiaRemision.query.filter(GuiaRemision.estado.in_(ESTADOS_PENDIENTES))
           .order_by(GuiaRemision.id.asc()).all()]
    for guia_id in ids:
        try:
            guia = db.session.get(GuiaRemision, guia_id)
            antes = guia.estado
            resumen["consultadas"] += 1
            consultar(guia)
            if guia.estado != antes and guia.estado in (ESTADO_ACEPTADA, ESTADO_RECHAZADA):
                resumen["aceptadas" if guia.estado == ESTADO_ACEPTADA else "rechazadas"] += 1
                _avisar(guia)
        except Exception as exc:  # una guía con problemas no detiene las demás
            db.session.rollback()
            resumen["errores"] += 1
            logger.error("Error consultando la guía %s: %s", guia_id, type(exc).__name__)
    return resumen


@click.command("guias-pendientes")
@with_appcontext
def guias_pendientes_command():
    """Consulta en SUNAT los tickets de las guías enviadas (cron)."""
    r = procesar_pendientes()
    click.echo(
        f"guias-pendientes: consultadas={r['consultadas']} aceptadas={r['aceptadas']} "
        f"rechazadas={r['rechazadas']} errores={r['errores']}"
    )
