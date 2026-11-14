"""Utilidades locales y puras para preparar GRE remitente (UBL 2.1).

Este módulo NO envía documentos a SUNAT, no usa Flask/SQLAlchemy/dotenv y no
realiza peticiones HTTP. Solo valida datos explícitos, construye/firma/empaqueta
el XML localmente y analiza CDR con límites de seguridad.

La firma local con un certificado sintético no es evidencia de aceptación
fiscal ni de habilitación del emisor.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from lxml import etree


UBL_NS = "urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2"
CAC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
EXT_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"

GRE_REMITENTE_TIPO = "09"
GRE_TRANSPORTE_PRIVADO_MODALIDAD = "02"
SIGNATURE_ID = "SignatureSP"

MOTIVOS_TRASLADO = frozenset({"01", "02", "04", "08", "09", "13", "14", "18", "19"})

CDR_ACEPTADO = "aceptado"
CDR_RECHAZADO = "rechazado"
CDR_PENDIENTE = "pendiente"
CDR_ERROR = "error"

MAX_CDR_ZIP_BYTES = 2 * 1024 * 1024
MAX_CDR_ENTRIES = 16
MAX_CDR_XML_BYTES = 4 * 1024 * 1024
MAX_CDR_TOTAL_UNCOMPRESSED = 8 * 1024 * 1024
MAX_CDR_COMPRESSION_RATIO = 100

_LEGACY_NAMES = frozenset({"cliente externo", "sin nombre", "cliente generico", "cliente genérico"})
_PLACEHOLDER_ADDRESSES = frozenset({"sin direccion", "sin dirección", "n/a", "na", "-"})
_PLACEHOLDER_LICENSES = frozenset({"q+dni", "q dni", "dni", "sin licencia"})
_PLACEHOLDER_PLATES = frozenset({"sin placa", "000000"})


class SunatXmlError(ValueError):
    """Error local de preparación/lectura SUNAT."""


class GreValidationError(SunatXmlError):
    """Los datos explícitos de la GRE no son válidos."""


class GreUnsupportedError(GreValidationError):
    """Tipo o modalidad no implementados en este alcance."""


class GreSignatureError(SunatXmlError):
    """Firma o digest XML inválidos."""


class XsdValidationError(SunatXmlError):
    """El XML no cumple el XSD estructural suministrado."""


class CdrError(SunatXmlError):
    """CDR ausente, corrupto, ajeno o inseguro."""


# ---------------------------------------------------------------------------
# Datos explícitos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmisorGre:
    ruc: str
    razon_social: str
    ubigeo: str
    direccion: str


@dataclass(frozen=True)
class DestinatarioGre:
    tipo_documento: str
    numero_documento: str
    razon_social: str
    ubigeo: str
    direccion: str


@dataclass(frozen=True)
class BienGre:
    codigo: str
    descripcion: str
    unidad: str
    cantidad: object
    peso_kg: object


@dataclass(frozen=True)
class TransportePrivadoGre:
    placa: str
    conductor_tipo_documento: str
    conductor_numero_documento: str
    conductor_nombre: str
    conductor_licencia: str
    conductor_apellidos: str | None = None


@dataclass(frozen=True)
class GreRemitenteData:
    serie: str
    numero: int
    fecha_emision: date
    hora_emision: time
    fecha_inicio_traslado: date
    motivo_traslado: str
    tipo_documento: str
    modalidad_traslado: str
    emisor: EmisorGre
    destinatario: DestinatarioGre
    partida_ubigeo: str
    partida_direccion: str
    llegada_ubigeo: str
    llegada_direccion: str
    transporte: TransportePrivadoGre
    bienes: tuple[BienGre, ...]


@dataclass(frozen=True)
class SigningMaterial:
    private_key_pem: bytes
    certificate_pem: bytes


@dataclass(frozen=True)
class SignatureInfo:
    reference_uri: str
    digest_value: str
    signature_algorithm: str
    digest_algorithm: str


@dataclass(frozen=True)
class GrePackage:
    filename: str
    xml_filename: str
    zip_bytes: bytes
    base64_content: str
    sha256_hex: str


@dataclass(frozen=True)
class PreparedGre:
    data: GreRemitenteData
    xml_bytes: bytes
    signed_xml_bytes: bytes
    package: GrePackage


@dataclass(frozen=True)
class CdrResult:
    estado: str
    accepted: bool
    response_code: str
    document_id: str
    observations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    cdr_xml: bytes = b""


@dataclass(frozen=True)
class TicketResult:
    estado: str
    accepted: bool
    ticket_code: str
    cdr: CdrResult | None = None
    message: str = ""


# ---------------------------------------------------------------------------
# Validación de datos explícitos
# ---------------------------------------------------------------------------


def _text(value, field_name: str, *, max_len: int = 500) -> str:
    if not isinstance(value, str):
        raise GreValidationError(f"{field_name} es obligatorio y debe ser texto")
    text = value.strip()
    if not text:
        raise GreValidationError(f"{field_name} es obligatorio")
    if len(text) > max_len:
        raise GreValidationError(f"{field_name} excede {max_len} caracteres")
    for char in text:
        code = ord(char)
        if code < 0x20 and char not in "\t\n\r":
            raise GreValidationError(f"{field_name} contiene caracteres de control no permitidos")
    return text


def _business_text(value, field_name: str, forbidden: frozenset[str], *, max_len: int = 500) -> str:
    text = _text(value, field_name, max_len=max_len)
    if text.casefold() in forbidden:
        raise GreValidationError(f"{field_name} contiene un valor de relleno no permitido")
    return text


def _ruc(value, field_name: str = "RUC") -> str:
    text = _text(value, field_name, max_len=11)
    if not re.fullmatch(r"\d{11}", text) or text == "0" * 11:
        raise GreValidationError(f"{field_name} debe tener 11 dígitos")
    return text


def _documento(value, tipo: str, field_name: str) -> str:
    text = _text(value, field_name, max_len=15)
    if tipo == "6":
        if not re.fullmatch(r"\d{11}", text) or text == "0" * 11:
            raise GreValidationError(f"{field_name} debe tener 11 dígitos para RUC")
    elif tipo == "1":
        if not re.fullmatch(r"\d{8}", text) or text == "0" * 8:
            raise GreValidationError(f"{field_name} debe tener 8 dígitos para DNI")
    else:
        raise GreValidationError(f"{field_name}: tipo de documento no soportado (use 1 o 6)")
    return text


def _ubigeo(value, field_name: str) -> str:
    text = _text(value, field_name, max_len=6)
    if not re.fullmatch(r"\d{6}", text):
        raise GreValidationError(f"{field_name} debe tener 6 dígitos")
    return text


def _serie(value) -> str:
    text = _text(value, "serie", max_len=4)
    if not re.fullmatch(r"T[A-Z0-9]{3}", text):
        raise GreValidationError("La serie GRE remitente debe iniciar con T y tener 4 caracteres alfanuméricos en mayúscula")
    return text


def _numero(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GreValidationError("El número de GRE debe ser un entero explícito")
    if value < 1 or value > 99999999:
        raise GreValidationError("El número de GRE debe estar entre 1 y 99999999")
    return value


def _fecha(value, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise GreValidationError(f"{field_name} debe ser una fecha explícita")
    return value


def _hora(value, field_name: str) -> time:
    if not isinstance(value, time):
        raise GreValidationError(f"{field_name} debe ser una hora explícita")
    if value.tzinfo is not None:
        raise GreValidationError(f"{field_name} debe ser hora local sin zona horaria")
    return value.replace(microsecond=0)


def _decimal_positivo(value, field_name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise GreValidationError(f"{field_name} es obligatorio y debe ser numérico")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise GreValidationError(f"{field_name} debe ser numérico") from None
    if not number.is_finite() or number <= 0:
        raise GreValidationError(f"{field_name} debe ser finito y mayor que cero")
    return number


def _decimal_texto(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _codigo(value, field_name: str, *, max_len: int = 20) -> str:
    text = _text(value, field_name, max_len=max_len)
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", text):
        raise GreValidationError(f"{field_name} contiene caracteres no permitidos")
    return text


def _document_id(serie: str, numero: int) -> str:
    return f"{serie}-{numero:08d}"


def _validar_transporte(transporte: TransportePrivadoGre) -> None:
    if not isinstance(transporte, TransportePrivadoGre):
        raise GreValidationError("transporte es obligatorio")
    _business_text(transporte.placa, "placa", _PLACEHOLDER_PLATES, max_len=20)
    tipo = _text(transporte.conductor_tipo_documento, "conductor_tipo_documento", max_len=1)
    _documento(transporte.conductor_numero_documento, tipo, "conductor_numero_documento")
    _business_text(transporte.conductor_nombre, "conductor_nombre", _LEGACY_NAMES, max_len=200)
    if transporte.conductor_apellidos is not None:
        _business_text(transporte.conductor_apellidos, "conductor_apellidos", _LEGACY_NAMES, max_len=200)
    _business_text(transporte.conductor_licencia, "conductor_licencia", _PLACEHOLDER_LICENSES, max_len=30)


def validate_gre_data(data: GreRemitenteData) -> None:
    """Valida datos explícitos de GRE remitente 09 / transporte privado 02.

    No completa ni reemplaza campos: cualquier faltante o valor inválido lanza
    ``GreValidationError``. Los tipos/modalidades no implementados lanzan
    ``GreUnsupportedError``.
    """

    if not isinstance(data, GreRemitenteData):
        raise GreValidationError("Se requieren datos GreRemitenteData")
    if data.tipo_documento != GRE_REMITENTE_TIPO:
        raise GreUnsupportedError("Solo se soporta GRE remitente tipo 09")
    if data.modalidad_traslado != GRE_TRANSPORTE_PRIVADO_MODALIDAD:
        raise GreUnsupportedError("Solo se soporta transporte privado (modalidad 02)")
    _serie(data.serie)
    _numero(data.numero)
    _fecha(data.fecha_emision, "fecha_emision")
    _hora(data.hora_emision, "hora_emision")
    _fecha(data.fecha_inicio_traslado, "fecha_inicio_traslado")
    motivo = _text(data.motivo_traslado, "motivo_traslado", max_len=2)
    if motivo not in MOTIVOS_TRASLADO:
        raise GreValidationError("motivo_traslado no pertenece al catálogo soportado")
    _ubigeo(data.partida_ubigeo, "partida_ubigeo")
    _business_text(data.partida_direccion, "partida_direccion", _PLACEHOLDER_ADDRESSES)
    _ubigeo(data.llegada_ubigeo, "llegada_ubigeo")
    _business_text(data.llegada_direccion, "llegada_direccion", _PLACEHOLDER_ADDRESSES)

    emisor = data.emisor
    if not isinstance(emisor, EmisorGre):
        raise GreValidationError("emisor es obligatorio")
    _ruc(emisor.ruc, "emisor.ruc")
    _business_text(emisor.razon_social, "emisor.razon_social", _LEGACY_NAMES)
    _ubigeo(emisor.ubigeo, "emisor.ubigeo")
    _business_text(emisor.direccion, "emisor.direccion", _PLACEHOLDER_ADDRESSES)

    destinatario = data.destinatario
    if not isinstance(destinatario, DestinatarioGre):
        raise GreValidationError("destinatario es obligatorio")
    tipo = _text(destinatario.tipo_documento, "destinatario.tipo_documento", max_len=1)
    _documento(destinatario.numero_documento, tipo, "destinatario.numero_documento")
    _business_text(destinatario.razon_social, "destinatario.razon_social", _LEGACY_NAMES)
    _ubigeo(destinatario.ubigeo, "destinatario.ubigeo")
    _business_text(destinatario.direccion, "destinatario.direccion", _PLACEHOLDER_ADDRESSES)

    _validar_transporte(data.transporte)

    if not isinstance(data.bienes, (tuple, list)) or not data.bienes:
        raise GreValidationError("Debe existir al menos un bien explícito")
    for index, bien in enumerate(data.bienes, start=1):
        if not isinstance(bien, BienGre):
            raise GreValidationError(f"bien {index} es obligatorio")
        _codigo(bien.codigo, f"bien {index}.codigo")
        _text(bien.descripcion, f"bien {index}.descripcion")
        unidad = _text(bien.unidad, f"bien {index}.unidad", max_len=10)
        if not re.fullmatch(r"[A-Za-z0-9]+", unidad):
            raise GreValidationError(f"bien {index}.unidad debe ser un código alfanumérico")
        _decimal_positivo(bien.cantidad, f"bien {index}.cantidad")
        _decimal_positivo(bien.peso_kg, f"bien {index}.peso_kg")


# ---------------------------------------------------------------------------
# Construcción del XML UBL
# ---------------------------------------------------------------------------


def _qn(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _sub(parent, namespace: str, name: str, text: str | None = None, **attrs):
    element = etree.SubElement(parent, _qn(namespace, name))
    if text is not None:
        element.text = text
    for key, value in attrs.items():
        if value is not None:
            element.set(key, str(value))
    return element


def _party(parent, etiqueta: str, tipo_documento: str, numero_documento: str, razon_social: str,
           ubigeo: str, direccion: str):
    party_wrapper = _sub(parent, CAC_NS, etiqueta)
    party = _sub(party_wrapper, CAC_NS, "Party")
    identificacion = _sub(party, CAC_NS, "PartyIdentification")
    _sub(identificacion, CBC_NS, "ID", numero_documento, schemeID=tipo_documento)
    nombre = _sub(party, CAC_NS, "PartyName")
    _sub(nombre, CBC_NS, "Name", razon_social)
    direccion_el = _sub(party, CAC_NS, "PostalAddress")
    _sub(direccion_el, CBC_NS, "ID", ubigeo)
    _sub(direccion_el, CBC_NS, "StreetName", direccion)
    legal = _sub(party, CAC_NS, "PartyLegalEntity")
    _sub(legal, CBC_NS, "RegistrationName", razon_social)
    return party_wrapper


def build_despatch_xml(data: GreRemitenteData) -> bytes:
    """Construye un ``DespatchAdvice`` UBL 2.1 (remitente 09, privado 02).

    El texto se escribe mediante lxml, que escapa ``&``, ``<``, etc. No se
    insertan valores por defecto: los datos deben ser explícitos y válidos.
    """

    validate_gre_data(data)

    serie = _serie(data.serie)
    numero = _numero(data.numero)
    motivo = _text(data.motivo_traslado, "motivo_traslado", max_len=2)
    hora = _hora(data.hora_emision, "hora_emision")

    nsmap = {
        None: UBL_NS,
        "cac": CAC_NS,
        "cbc": CBC_NS,
        "ext": EXT_NS,
        "ds": DS_NS,
    }
    root = etree.Element(_qn(UBL_NS, "DespatchAdvice"), nsmap=nsmap)

    _sub(root, CBC_NS, "UBLVersionID", "2.1")
    _sub(root, CBC_NS, "CustomizationID", "2.0")
    _sub(root, CBC_NS, "ID", _document_id(serie, numero))
    _sub(root, CBC_NS, "IssueDate", data.fecha_emision.isoformat())
    _sub(root, CBC_NS, "IssueTime", hora.isoformat(timespec="seconds"))
    _sub(root, CBC_NS, "DespatchAdviceTypeCode", GRE_REMITENTE_TIPO)

    firma_ubl = _sub(root, CAC_NS, "Signature")
    _sub(firma_ubl, CBC_NS, "ID", SIGNATURE_ID)
    adjunto = _sub(firma_ubl, CAC_NS, "DigitalSignatureAttachment")
    referencia_externa = _sub(adjunto, CAC_NS, "ExternalReference")
    _sub(referencia_externa, CBC_NS, "URI", f"#{SIGNATURE_ID}")

    emisor = data.emisor
    _party(
        root,
        "DespatchSupplierParty",
        "6",
        _ruc(emisor.ruc, "emisor.ruc"),
        _business_text(emisor.razon_social, "emisor.razon_social", _LEGACY_NAMES),
        _ubigeo(emisor.ubigeo, "emisor.ubigeo"),
        _business_text(emisor.direccion, "emisor.direccion", _PLACEHOLDER_ADDRESSES),
    )

    destinatario = data.destinatario
    tipo_destino = _text(destinatario.tipo_documento, "destinatario.tipo_documento", max_len=1)
    _party(
        root,
        "DeliveryCustomerParty",
        tipo_destino,
        _documento(destinatario.numero_documento, tipo_destino, "destinatario.numero_documento"),
        _business_text(destinatario.razon_social, "destinatario.razon_social", _LEGACY_NAMES),
        _ubigeo(destinatario.ubigeo, "destinatario.ubigeo"),
        _business_text(destinatario.direccion, "destinatario.direccion", _PLACEHOLDER_ADDRESSES),
    )

    envio = _sub(root, CAC_NS, "Shipment")
    _sub(envio, CBC_NS, "ID", _document_id(serie, numero))
    _sub(envio, CBC_NS, "HandlingCode", motivo)
    peso_total = sum((_decimal_positivo(b.peso_kg, "peso_kg") for b in data.bienes), Decimal("0"))
    _sub(envio, CBC_NS, "GrossWeightMeasure", _decimal_texto(peso_total), unitCode="KGM")

    etapa = _sub(envio, CAC_NS, "ShipmentStage")
    _sub(etapa, CBC_NS, "TransportModeCode", GRE_TRANSPORTE_PRIVADO_MODALIDAD)
    periodo = _sub(etapa, CAC_NS, "TransitPeriod")
    _sub(periodo, CBC_NS, "StartDate", _fecha(data.fecha_inicio_traslado, "fecha_inicio_traslado").isoformat())
    medios = _sub(etapa, CAC_NS, "TransportMeans")
    carretera = _sub(medios, CAC_NS, "RoadTransport")
    _sub(carretera, CBC_NS, "LicensePlateID", _business_text(
        data.transporte.placa, "placa", _PLACEHOLDER_PLATES, max_len=20
    ))

    conductor = _sub(etapa, CAC_NS, "DriverPerson")
    tipo_conductor = _text(data.transporte.conductor_tipo_documento, "conductor_tipo_documento", max_len=1)
    _sub(conductor, CBC_NS, "ID", _documento(
        data.transporte.conductor_numero_documento, tipo_conductor, "conductor_numero_documento"
    ), schemeID=tipo_conductor)
    _sub(conductor, CBC_NS, "FirstName", _business_text(
        data.transporte.conductor_nombre, "conductor_nombre", _LEGACY_NAMES, max_len=200
    ))
    if data.transporte.conductor_apellidos is not None:
        _sub(conductor, CBC_NS, "FamilyName", _business_text(
            data.transporte.conductor_apellidos, "conductor_apellidos", _LEGACY_NAMES, max_len=200
        ))
    licencia = _sub(conductor, CAC_NS, "IdentityDocumentReference")
    _sub(licencia, CBC_NS, "ID", _business_text(
        data.transporte.conductor_licencia, "conductor_licencia", _PLACEHOLDER_LICENSES, max_len=30
    ))

    entrega = _sub(envio, CAC_NS, "Delivery")
    direccion_entrega = _sub(entrega, CAC_NS, "DeliveryAddress")
    _sub(direccion_entrega, CBC_NS, "ID", _ubigeo(data.llegada_ubigeo, "llegada_ubigeo"))
    _sub(direccion_entrega, CBC_NS, "StreetName", _business_text(
        data.llegada_direccion, "llegada_direccion", _PLACEHOLDER_ADDRESSES
    ))

    origen = _sub(envio, CAC_NS, "OriginAddress")
    _sub(origen, CBC_NS, "ID", _ubigeo(data.partida_ubigeo, "partida_ubigeo"))
    _sub(origen, CBC_NS, "StreetName", _business_text(
        data.partida_direccion, "partida_direccion", _PLACEHOLDER_ADDRESSES
    ))

    for indice, bien in enumerate(data.bienes, start=1):
        linea = _sub(root, CAC_NS, "DespatchLine")
        _sub(linea, CBC_NS, "ID", str(indice))
        cantidad = _decimal_positivo(bien.cantidad, f"bien {indice}.cantidad")
        unidad = _text(bien.unidad, f"bien {indice}.unidad", max_len=10)
        _sub(linea, CBC_NS, "DeliveredQuantity", _decimal_texto(cantidad), unitCode=unidad)
        referencia = _sub(linea, CAC_NS, "OrderLineReference")
        _sub(referencia, CBC_NS, "LineID", str(indice))
        item = _sub(linea, CAC_NS, "Item")
        _sub(item, CBC_NS, "Description", _text(bien.descripcion, f"bien {indice}.descripcion"))
        identificacion = _sub(item, CAC_NS, "SellersItemIdentification")
        _sub(identificacion, CBC_NS, "ID", _codigo(bien.codigo, f"bien {indice}.codigo"))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


# ---------------------------------------------------------------------------
# Firma XMLDSig local
# ---------------------------------------------------------------------------


def _pem_text(value, field_name: str) -> str:
    if value is None:
        raise GreSignatureError(f"{field_name} es obligatorio")
    if isinstance(value, bytes):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError:
            raise GreSignatureError(f"{field_name} no es un PEM válido") from None
    elif isinstance(value, str):
        text = value
    else:
        raise GreSignatureError(f"{field_name} debe ser PEM en texto o bytes")
    if "-----BEGIN" not in text:
        raise GreSignatureError(f"{field_name} no es un PEM válido")
    return text


def load_pkcs12(pfx_bytes: bytes, password: bytes | str | None = None) -> SigningMaterial:
    """Carga un PFX/PKCS#12 local y devuelve clave/certificado en PEM.

    No escribe ni registra la contraseña. El certificado debe ser aportado
    explícitamente por activación humana; este módulo no trae ninguno.
    """

    if not isinstance(pfx_bytes, (bytes, bytearray)) or not pfx_bytes:
        raise GreSignatureError("PFX vacío o inválido")
    if isinstance(password, str):
        password_bytes = password.encode("utf-8")
    elif isinstance(password, (bytes, bytearray)) or password is None:
        password_bytes = bytes(password) if password is not None else None
    else:
        raise GreSignatureError("Contraseña de PFX inválida")

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12
    except ImportError:  # pragma: no cover - dependencia declarada
        raise GreSignatureError("cryptography no está disponible para leer el PFX") from None

    try:
        bundle = pkcs12.load_key_and_certificates(bytes(pfx_bytes), password_bytes)
    except Exception:
        raise GreSignatureError("No se pudo abrir el PFX (contraseña o formato inválido)") from None
    key, cert, _chain = bundle
    if key is None or cert is None:
        raise GreSignatureError("El PFX no contiene clave privada y certificado")
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return SigningMaterial(private_key_pem=key_pem, certificate_pem=cert_pem)


def _load_xml_for_signing(xml_bytes: bytes) -> etree._Element:
    if not isinstance(xml_bytes, (bytes, bytearray)):
        raise GreSignatureError("El XML debe ser bytes")
    if bytes(xml_bytes).startswith(b"\xef\xbb\xbf"):
        raise GreSignatureError("El XML no debe incluir BOM UTF-8")
    try:
        root = _parse_secure_xml(bytes(xml_bytes))
    except SunatXmlError as exc:
        raise GreSignatureError(str(exc)) from None
    if _local_name(root) != "DespatchAdvice":
        raise GreSignatureError("El XML no es un DespatchAdvice")
    return root


def _find_signature(root: etree._Element):
    signatures = root.findall(f".//{_qn(DS_NS, 'Signature')}")
    return signatures


def _ensure_signature_reference(root: etree._Element) -> None:
    firmas_ubl = [child for child in root if child.tag == _qn(CAC_NS, "Signature")]
    if len(firmas_ubl) != 1:
        raise GreSignatureError("El XML debe contener exactamente un cac:Signature")
    firma_ubl = firmas_ubl[0]
    id_el = firma_ubl.find(_qn(CBC_NS, "ID"))
    if id_el is None or _element_text(id_el) != SIGNATURE_ID:
        raise GreSignatureError("cac:Signature/cbc:ID no coincide con la firma digital")
    adjunto = firma_ubl.find(_qn(CAC_NS, "DigitalSignatureAttachment"))
    if adjunto is None:
        raise GreSignatureError("cac:Signature no contiene DigitalSignatureAttachment")
    referencia = adjunto.find(_qn(CAC_NS, "ExternalReference"))
    if referencia is None:
        raise GreSignatureError("DigitalSignatureAttachment no contiene ExternalReference")
    uri_el = referencia.find(_qn(CBC_NS, "URI"))
    if uri_el is None or _element_text(uri_el) != f"#{SIGNATURE_ID}":
        raise GreSignatureError("cac:Signature URI no apunta al Id real de la firma")


def _ensure_extension_content(root: etree._Element):
    if _find_signature(root):
        raise GreSignatureError("El XML ya contiene una firma digital")
    extensiones = [child for child in root if child.tag == _qn(EXT_NS, "UBLExtensions")]
    if extensiones:
        if len(extensiones) != 1:
            raise GreSignatureError("El XML contiene más de un UBLExtensions")
        ubl_extensions = extensiones[0]
        if root[0] is not ubl_extensions:
            root.remove(ubl_extensions)
            root.insert(0, ubl_extensions)
        contenidos = [el for el in ubl_extensions.iter() if el.tag == _qn(EXT_NS, "ExtensionContent")]
        if len(contenidos) != 1:
            raise GreSignatureError("UBLExtensions debe contener exactamente un ExtensionContent")
        contenido = contenidos[0]
        if len(contenido):
            raise GreSignatureError("ExtensionContent ya contiene contenido; no se sobrescribe")
        return contenido
    ubl_extensions = etree.Element(_qn(EXT_NS, "UBLExtensions"))
    root.insert(0, ubl_extensions)
    ubl_extension = etree.SubElement(ubl_extensions, _qn(EXT_NS, "UBLExtension"))
    return etree.SubElement(ubl_extension, _qn(EXT_NS, "ExtensionContent"))


def _annotate_signature(*args, **kwargs) -> None:
    for value in (*args, *kwargs.values()):
        if isinstance(value, etree._Element) and value.tag == _qn(DS_NS, "Signature"):
            value.set("Id", SIGNATURE_ID)
            return


def sign_despatch_xml(xml_bytes: bytes, private_key_pem, certificate_pem) -> bytes:
    """Firma XMLDSig enveloped RSA/SHA-256 dentro de ExtensionContent.

    Usa el placeholder oficial de signxml (``ds:Signature Id="placeholder"``)
    antes de firmar. El URI de la referencia es el documento completo (""),
    generado sin pasar ``reference_uri``. La firma no se mueve después de ser
    creada: se verifica sobre los mismos bytes devueltos.
    """

    root = _load_xml_for_signing(xml_bytes)
    _ensure_signature_reference(root)
    contenido = _ensure_extension_content(root)
    placeholder = etree.SubElement(contenido, _qn(DS_NS, "Signature"))
    placeholder.set("Id", "placeholder")

    key_text = _pem_text(private_key_pem, "clave privada")
    cert_text = _pem_text(certificate_pem, "certificado")

    try:
        from signxml import XMLSigner, methods
    except ImportError:  # pragma: no cover - dependencia declarada
        raise GreSignatureError("signxml no está disponible para firmar") from None

    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    )
    signer.signature_annotators.append(_annotate_signature)
    try:
        signed_root = signer.sign(root, key=key_text, cert=cert_text)
    except Exception:
        raise GreSignatureError("No se pudo firmar el XML localmente") from None
    if isinstance(signed_root, (bytes, bytearray)):
        signed_root = _parse_secure_xml(bytes(signed_root))
    elif not isinstance(signed_root, etree._Element):
        signed_root = signed_root.getroot()

    firmas = _find_signature(signed_root)
    if len(firmas) != 1:
        raise GreSignatureError("La firma generada no es única")
    firma = firmas[0]
    padre = firma.getparent()
    if padre is None or padre.tag != _qn(EXT_NS, "ExtensionContent"):
        raise GreSignatureError("La firma no quedó dentro de ExtensionContent")
    if firma.get("Id") != SIGNATURE_ID:
        raise GreSignatureError("La firma no conserva el Id real")
    if signed_root.find(_qn(DS_NS, "Signature")) is not None:
        raise GreSignatureError("La firma no debe quedar como hijo directo del documento")
    _ensure_signature_reference(signed_root)

    signed_bytes = etree.tostring(signed_root, xml_declaration=True, encoding="UTF-8")
    verify_despatch_signature(signed_bytes, cert_text)
    return signed_bytes


def verify_despatch_signature(signed_xml: bytes, certificate_pem=None,
                              expected_reference_uri: str | None = "") -> SignatureInfo:
    """Verifica firma y digest con signxml; no modifica el XML."""

    if isinstance(signed_xml, str):
        signed_xml = signed_xml.encode("utf-8")
    try:
        root = _parse_secure_xml(signed_xml)
    except SunatXmlError as exc:
        raise GreSignatureError(str(exc)) from None
    firmas = _find_signature(root)
    if len(firmas) != 1:
        raise GreSignatureError("Se esperaba exactamente una firma XMLDSig")
    firma = firmas[0]
    if firma.get("Id") != SIGNATURE_ID:
        raise GreSignatureError("La firma no usa el Id real esperado")
    _ensure_signature_reference(root)
    referencias = firma.findall(f".//{_qn(DS_NS, 'Reference')}")
    if len(referencias) != 1:
        raise GreSignatureError("Se esperaba exactamente una referencia de firma")
    referencia = referencias[0]
    uri = referencia.get("URI") or ""
    if expected_reference_uri is not None and uri != expected_reference_uri:
        raise GreSignatureError("La referencia de firma no es la preestablecida")

    digest_el = referencia.find(f".//{_qn(DS_NS, 'DigestValue')}")
    metodo_digest = referencia.find(f".//{_qn(DS_NS, 'DigestMethod')}")
    metodo_firma = firma.find(f".//{_qn(DS_NS, 'SignatureMethod')}")
    if digest_el is None or metodo_digest is None or metodo_firma is None:
        raise GreSignatureError("La firma XML carece de digest o algoritmos")

    cert_text = _pem_text(certificate_pem, "certificado") if certificate_pem is not None else None
    try:
        from signxml import XMLVerifier
    except ImportError:  # pragma: no cover - dependencia declarada
        raise GreSignatureError("signxml no está disponible para verificar") from None
    try:
        XMLVerifier().verify(root, x509_cert=cert_text)
    except Exception:
        raise GreSignatureError("La firma XML o el digest no son válidos") from None

    return SignatureInfo(
        reference_uri=uri,
        digest_value=(digest_el.text or "").strip(),
        signature_algorithm=metodo_firma.get("Algorithm", ""),
        digest_algorithm=metodo_digest.get("Algorithm", ""),
    )


def build_signed_despatch(data: GreRemitenteData, private_key_pem, certificate_pem) -> bytes:
    return sign_despatch_xml(build_despatch_xml(data), private_key_pem, certificate_pem)


# ---------------------------------------------------------------------------
# ZIP / base64 / SHA-256
# ---------------------------------------------------------------------------


def package_gre(xml_bytes: bytes, data: GreRemitenteData) -> GrePackage:
    """Empaqueta el XML en ZIP y calcula nombre, base64 y SHA-256 exactos."""

    validate_gre_data(data)
    if not isinstance(xml_bytes, (bytes, bytearray)) or not xml_bytes:
        raise GreValidationError("El XML a empaquetar es obligatorio")
    xml_bytes = bytes(xml_bytes)
    if xml_bytes.startswith(b"\xef\xbb\xbf"):
        raise GreValidationError("El XML no debe incluir BOM UTF-8")
    try:
        root = _parse_secure_xml(xml_bytes)
    except SunatXmlError as exc:
        raise GreValidationError(str(exc)) from None
    if _local_name(root) != "DespatchAdvice":
        raise GreValidationError("El XML a empaquetar no es un DespatchAdvice")

    esperado = _document_id(_serie(data.serie), _numero(data.numero))
    id_el = root.find(_qn(CBC_NS, "ID"))
    if id_el is None or (id_el.text or "").strip() != esperado:
        raise GreValidationError("El identificador del XML no coincide con la serie/número de la GRE")

    base = f"{_ruc(data.emisor.ruc, 'emisor.ruc')}-{GRE_REMITENTE_TIPO}-{_serie(data.serie)}-{_numero(data.numero):08d}"
    nombre_zip = f"{base}.zip"
    nombre_xml = f"{base}.xml"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archivo:
        info = zipfile.ZipInfo(nombre_xml, date_time=(2020, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        archivo.writestr(info, xml_bytes)
    zip_bytes = buffer.getvalue()
    return GrePackage(
        filename=nombre_zip,
        xml_filename=nombre_xml,
        zip_bytes=zip_bytes,
        base64_content=base64.b64encode(zip_bytes).decode("ascii"),
        sha256_hex=hashlib.sha256(zip_bytes).hexdigest(),
    )


def prepare_gre(data: GreRemitenteData, private_key_pem, certificate_pem) -> PreparedGre:
    """Prepara XML, firma y paquete localmente; no envía ni reserva correlativo."""

    xml_bytes = build_despatch_xml(data)
    signed = sign_despatch_xml(xml_bytes, private_key_pem, certificate_pem)
    package = package_gre(signed, data)
    return PreparedGre(data=data, xml_bytes=xml_bytes, signed_xml_bytes=signed, package=package)


# ---------------------------------------------------------------------------
# XSD estructural local (OASIS; no es validación tributaria SUNAT)
# ---------------------------------------------------------------------------


def validate_against_xsd(xml_bytes: bytes, xsd_path) -> None:
    """Valida contra un XSD local suministrado. No descarga esquemas."""

    path = Path(xsd_path)
    if not path.is_file():
        raise XsdValidationError("Esquema XSD local no disponible")
    parser = etree.XMLParser(no_network=True, resolve_entities=False, load_dtd=False)
    try:
        schema_doc = etree.parse(str(path), parser=parser)
        schema = etree.XMLSchema(schema_doc)
    except (etree.XMLSchemaParseError, etree.XMLSyntaxError, OSError):
        raise XsdValidationError("No se pudo cargar el esquema XSD local") from None
    try:
        doc = _parse_secure_xml(xml_bytes)
    except SunatXmlError as exc:
        raise XsdValidationError(str(exc)) from None
    if not schema.validate(doc):
        detalles = "; ".join(f"línea {e.line}: {e.message}" for e in list(schema.error_log)[:5])
        raise XsdValidationError(f"XML no válido según XSD estructural: {detalles}")


# ---------------------------------------------------------------------------
# Parser XML/CDR seguro
# ---------------------------------------------------------------------------


def _local_name(element) -> str:
    tag = element.tag
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""


def _parse_secure_xml(data: bytes) -> etree._Element:
    if not isinstance(data, (bytes, bytearray)):
        raise SunatXmlError("El XML debe ser bytes")
    raw = bytes(data)
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SunatXmlError("XML con DTD o entidades externas no permitido")
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
    )
    try:
        root = etree.fromstring(raw, parser=parser)
    except (etree.XMLSyntaxError, ValueError):
        raise SunatXmlError("XML inválido") from None
    info = root.getroottree().docinfo
    if info.doctype or info.internalDTD is not None or info.externalDTD is not None:
        raise SunatXmlError("XML con DTD no permitido")
    entity_type = getattr(etree, "_Entity", None)
    if entity_type is not None:
        for node in root.iter():
            if isinstance(node, entity_type):
                raise SunatXmlError("XML con entidades no permitido")
    return root


def _direct_child(element, name: str):
    for child in element:
        if _local_name(child) == name:
            return child
    return None


def _element_text(element) -> str:
    return (element.text or "").strip()


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _expected_identity(expected_ruc, expected_tipo, expected_serie, expected_numero) -> tuple[str, str, str]:
    try:
        ruc = _ruc(expected_ruc, "expected_ruc")
    except GreValidationError as exc:
        raise CdrError(str(exc)) from None
    if expected_tipo != GRE_REMITENTE_TIPO:
        raise CdrError("Solo se valida CDR para GRE remitente tipo 09")
    try:
        serie = _serie(expected_serie)
    except GreValidationError as exc:
        raise CdrError(str(exc)) from None
    if isinstance(expected_numero, bool) or not isinstance(expected_numero, int) or not 1 <= expected_numero <= 99999999:
        raise CdrError("Número de comprobante esperado inválido")
    return ruc, serie, _document_id(serie, expected_numero)


def _decode_cdr_payload(cdr) -> bytes:
    if isinstance(cdr, str):
        if cdr.lstrip().startswith("<"):
            return cdr.lstrip().encode("utf-8")
        try:
            raw = cdr.encode("ascii")
        except UnicodeEncodeError:
            raise CdrError("Base64 del CDR inválido") from None
        return _decode_cdr_base64(raw)
    if isinstance(cdr, (bytes, bytearray, memoryview)):
        raw = bytes(cdr)
        stripped = raw.lstrip()
        if stripped.startswith(b"PK") or stripped.startswith(b"<"):
            return raw
        return _decode_cdr_base64(raw)
    raise CdrError("CDR ausente o de tipo inválido")


def _decode_cdr_base64(raw: bytes) -> bytes:
    compact = b"".join(raw.split())
    limite = (MAX_CDR_XML_BYTES * 4 // 3) + 8
    if len(compact) > limite:
        raise CdrError("Base64 del CDR excede el tamaño permitido")
    try:
        return base64.b64decode(compact, validate=True)
    except Exception:
        raise CdrError("Base64 del CDR inválido") from None


def _extract_cdr_xml(payload: bytes) -> bytes:
    if not payload:
        raise CdrError("CDR vacío")
    stripped = payload
    if stripped.startswith(b"\xef\xbb\xbf"):
        stripped = stripped[3:]
    stripped = stripped.lstrip()
    if stripped.startswith(b"<"):
        if len(stripped) > MAX_CDR_XML_BYTES:
            raise CdrError("XML del CDR excede el tamaño permitido")
        return stripped
    if len(payload) > MAX_CDR_ZIP_BYTES:
        raise CdrError("ZIP del CDR excede el tamaño permitido")
    if not stripped.startswith(b"PK"):
        raise CdrError("El CDR no es un ZIP válido")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_CDR_ENTRIES:
                raise CdrError("El ZIP del CDR contiene demasiadas entradas")
            total = 0
            candidatos = []
            for info in infos:
                if info.flag_bits & 0x1:
                    raise CdrError("El ZIP del CDR no debe estar cifrado")
                nombre = info.filename or ""
                normalized = nombre.replace("\\", "/")
                if normalized.startswith("/") or ".." in normalized.split("/"):
                    raise CdrError("El ZIP del CDR contiene una ruta no permitida")
                if info.is_dir():
                    continue
                total += info.file_size
                if info.file_size > MAX_CDR_XML_BYTES:
                    raise CdrError("Una entrada del CDR excede el tamaño permitido")
                if total > MAX_CDR_TOTAL_UNCOMPRESSED:
                    raise CdrError("El contenido del CDR excede el tamaño permitido")
                if info.file_size and info.compress_size == 0:
                    raise CdrError("Relación de compresión del CDR no permitida")
                if info.compress_size and info.file_size / info.compress_size > MAX_CDR_COMPRESSION_RATIO:
                    raise CdrError("Posible ZIP bomb en el CDR")
                if normalized.lower().endswith(".xml"):
                    candidatos.append(info)
            if len(candidatos) != 1:
                raise CdrError("El ZIP del CDR debe contener exactamente un XML")
            info = candidatos[0]
            with zf.open(info) as entrada:
                data = entrada.read(MAX_CDR_XML_BYTES + 1)
            if len(data) > MAX_CDR_XML_BYTES or len(data) != info.file_size:
                raise CdrError("XML del CDR corrupto o truncado")
            return data
    except CdrError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, OSError,
            NotImplementedError, EOFError):
        raise CdrError("ZIP del CDR corrupto") from None


def parse_cdr(cdr, *, expected_ruc, expected_tipo: str = GRE_REMITENTE_TIPO,
              expected_serie, expected_numero) -> CdrResult:
    """Analiza un CDR (base64 de ZIP/XML) con límites y sin red.

    Solo acepta ``ResponseCode`` igual a ``0`` y un ``DocumentReference`` que
    corresponda al tipo/serie/número esperados. Códigos distintos se observan
    y permanecen no aceptados. Texto como «procesado» nunca acepta.
    """

    ruc, _serie_esperada, document_id = _expected_identity(
        expected_ruc, expected_tipo, expected_serie, expected_numero
    )
    payload = _decode_cdr_payload(cdr)
    xml_bytes = _extract_cdr_xml(payload)
    try:
        root = _parse_secure_xml(xml_bytes)
    except SunatXmlError as exc:
        raise CdrError(str(exc)) from None
    if _local_name(root) != "ApplicationResponse":
        raise CdrError("El CDR no es un ApplicationResponse")

    documentos = [element for element in root.iter() if _local_name(element) == "DocumentResponse"]
    if not documentos:
        raise CdrError("El CDR no contiene DocumentResponse")

    respuestas = []
    codigos = []
    for documento in documentos:
        respuesta = _direct_child(documento, "Response")
        if respuesta is None:
            raise CdrError("El CDR contiene un DocumentResponse sin Response")
        codigo_el = _direct_child(respuesta, "ResponseCode")
        if codigo_el is None or not _element_text(codigo_el):
            raise CdrError("El CDR no contiene ResponseCode")
        codigos.append(_element_text(codigo_el))

        referencias = [child for child in documento if _local_name(child) == "DocumentReference"]
        if not referencias:
            raise CdrError("El CDR no contiene DocumentReference del comprobante")
        for referencia in referencias:
            id_el = _direct_child(referencia, "ID")
            tipo_el = _direct_child(referencia, "DocumentTypeCode")
            if (id_el is None or _element_text(id_el) != document_id
                    or tipo_el is None or _element_text(tipo_el) != expected_tipo):
                raise CdrError("El CDR no corresponde al comprobante esperado")

        referencia_respuesta = _direct_child(respuesta, "ReferenceID")
        if (referencia_respuesta is not None and _element_text(referencia_respuesta)
                and _element_text(referencia_respuesta) != document_id):
            raise CdrError("El CDR referencia otro comprobante")
        respuestas.append(respuesta)

    if len(set(codigos)) != 1:
        raise CdrError("El CDR contiene respuestas ambiguas")
    codigo = codigos[0]
    if not codigo.isdigit():
        raise CdrError("El ResponseCode del CDR no es numérico")

    receptores = [el for el in root.iter() if _local_name(el) == "ReceiverParty"]
    if not receptores:
        raise CdrError("El CDR no identifica al receptor")
    receptor_ok = False
    for party in receptores:
        for identificacion in party.iter():
            if _local_name(identificacion) != "ID":
                continue
            texto = _element_text(identificacion)
            if re.fullmatch(r"\d{11}", texto):
                if texto != ruc:
                    raise CdrError("El CDR pertenece a otro receptor")
                receptor_ok = True
    if not receptor_ok:
        raise CdrError("El CDR no identifica el RUC del receptor")

    observaciones = []
    for respuesta in respuestas:
        descripcion = _direct_child(respuesta, "Description")
        if descripcion is not None:
            observaciones.append(_element_text(descripcion))
    for linea in (el for el in root.iter() if _local_name(el) == "LineResponse"):
        respuesta = _direct_child(linea, "Response")
        if respuesta is not None:
            descripcion = _direct_child(respuesta, "Description")
            if descripcion is not None:
                observaciones.append(_element_text(descripcion))
    notas = [_element_text(el) for el in root.iter() if _local_name(el) == "Note"]

    if codigo == "0":
        estado = CDR_ACEPTADO
        aceptado = True
    elif codigo == "98":
        estado = CDR_PENDIENTE
        aceptado = False
    elif codigo == "99":
        estado = CDR_ERROR
        aceptado = False
    else:
        estado = CDR_RECHAZADO
        aceptado = False

    return CdrResult(
        estado=estado,
        accepted=aceptado,
        response_code=codigo,
        document_id=document_id,
        observations=_unique(observaciones),
        notes=_unique(notas),
        cdr_xml=xml_bytes,
    )


def interpret_ticket_status(codigo, cdr=None, *, expected_ruc, expected_tipo: str = GRE_REMITENTE_TIPO,
                            expected_serie, expected_numero) -> TicketResult:
    """Interpreta el código de ticket sin aceptar tickets ni texto como CDR.

    98 => pendiente, 99 => error, 0 => exige CDR válido del mismo documento y
    ResponseCode 0. Cualquier otro valor (incluido «procesado») no acepta.
    """

    code = str(codigo).strip() if codigo is not None else ""
    if code == "98":
        return TicketResult(estado=CDR_PENDIENTE, accepted=False, ticket_code=code,
                            message="Ticket en proceso; no es aceptación")
    if code == "99":
        return TicketResult(estado=CDR_ERROR, accepted=False, ticket_code=code,
                            message="Ticket con error; no es aceptación")
    if code != "0":
        return TicketResult(estado=CDR_ERROR, accepted=False, ticket_code=code,
                            message="Código de ticket no reconocido; no se acepta")
    if cdr is None:
        return TicketResult(estado=CDR_ERROR, accepted=False, ticket_code=code,
                            message="CDR ausente; no se puede aceptar")
    try:
        resultado = parse_cdr(
            cdr,
            expected_ruc=expected_ruc,
            expected_tipo=expected_tipo,
            expected_serie=expected_serie,
            expected_numero=expected_numero,
        )
    except CdrError as exc:
        return TicketResult(estado=CDR_ERROR, accepted=False, ticket_code=code,
                            message=str(exc))
    if resultado.accepted:
        return TicketResult(estado=CDR_ACEPTADO, accepted=True, ticket_code=code,
                            cdr=resultado, message="CDR válido con ResponseCode 0")
    return TicketResult(estado=resultado.estado, accepted=False, ticket_code=code,
                        cdr=resultado, message="CDR no aceptado")
