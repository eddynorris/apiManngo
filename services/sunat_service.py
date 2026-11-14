"""Transporte HTTP seguro para GRE remitente (SUNAT).

Interfaz pública estable para la siguiente capa durable:

- ``SunatConfig``: configuración explícita o leída de las claves ENV
  existentes (``MANNGO_ID_SUNAT``, ``MANNGO_CLAVE_SUNAT``, ``SUNAT_RUC``,
  ``SUNAT_USUARIO_SOL``, ``SUNAT_CLAVE_SOL``).
- ``SunatService(config=None, http=None, clock=None)``: construir nunca
  contacta la red. ``http`` debe exponer ``post(url, **kwargs)`` y
  ``get(url, **kwargs)``; ``clock`` debe exponer ``monotonic()``.
- ``obtener_access_token() -> str``
- ``emitir_guia_remision(prepared: PreparedGre) -> GreEnvioResult``
- ``consultar_estado_ticket(ticket, expected: GreRemitenteData) -> TicketResult``

El servicio NO construye XML ni acepta JSON de negocio plano: solo transporta
un paquete preparado por ``services.sunat_xml``. Por defecto el ambiente es
``local`` y el envío está deshabilitado, por lo que no existe ninguna llamada
HTTP (ni OAuth) hasta que se configure explícitamente ``produccion``,
``SUNAT_ENVIO_HABILITADO=true`` y credenciales completas. Beta u otros
ambientes fallan cerrados; no se inventan hosts alternativos.

Un HTTP 200/201 con ``numTicket`` solo significa "recibido para proceso", no
aceptación. La aceptación únicamente proviene de un CDR válido, del mismo
documento, con ``ResponseCode=0``, interpretado por
``services.sunat_xml.interpret_ticket_status``.

Este módulo no registra cuerpos, tokens, claves ni excepciones crudas. Las
pruebas usan transporte falso; ninguna prueba de este archivo contacta SUNAT.
"""

from __future__ import annotations

import base64
import hashlib
import math
import os
import re
import time
from dataclasses import dataclass
from typing import Mapping

import requests
from lxml import etree

from services.sunat_xml import (
    DS_NS,
    EXT_NS,
    GRE_REMITENTE_TIPO,
    GRE_TRANSPORTE_PRIVADO_MODALIDAD,
    UBL_NS,
    GrePackage,
    GreRemitenteData,
    PreparedGre,
    SunatXmlError,
    TicketResult,
    build_despatch_xml,
    interpret_ticket_status,
    package_gre,
    validate_gre_data,
)

TOKEN_URL_TEMPLATE = "https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/"
API_COMPROBANTES_URL = "https://api-cpe.sunat.gob.pe/v1/contribuyente/gem/comprobantes"
OAUTH_SCOPE = "https://api-cpe.sunat.gob.pe"
TOKEN_MARGIN_SECONDS = 300
MAX_TICKET_LENGTH = 200
DEFAULT_TIMEOUT_SECONDS = 15.0
SEND_UNCERTAIN_MESSAGE = (
    "No se pudo confirmar si SUNAT recibió la guía; no reintentar automáticamente"
)

_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9._~-]+")
_RUC_RE = re.compile(r"\d{11}")
_TICKET_RE = re.compile(r"[A-Za-z0-9._~-]+")
_SAFE_ERROR_CODE_RE = re.compile(r"\d{1,6}")


class SunatError(Exception):
    """Error público y redactado de la capa SUNAT."""


class SunatConfigError(SunatError):
    """Configuración incompleta o inválida."""


class SunatDisabledError(SunatConfigError):
    """El envío no está habilitado explícitamente para producción."""


class SunatPreparationError(SunatError):
    """El documento preparado o la identidad esperada no son coherentes."""


class SunatAuthError(SunatError):
    """Falló la autenticación OAuth2; no se envió ninguna GRE."""


class SunatTokenError(SunatAuthError):
    """La respuesta del token no cumple el contrato mínimo."""


class SunatResponseError(SunatError):
    """SUNAT devolvió una respuesta HTTP/JSON no utilizable."""


class SunatProtocolError(SunatResponseError):
    """La petición o la respuesta no respetan el contrato esperado."""


class SunatTransportError(SunatError):
    """No fue posible completar una operación de transporte."""


class SunatTransmissionUncertain(SunatTransportError):
    """El envío de la GRE pudo haberse recibido; no se debe reintentar solo."""


@dataclass(frozen=True)
class SunatConfig:
    """Configuración inmutable del transporte GRE.

    Los secretos se conservan exactamente como fueron suministrados; no se
    recortan ni transforman silenciosamente.
    """

    ambiente: str = "local"
    envio_habilitado: bool = False
    client_id: str | None = None
    client_secret: str | None = None
    ruc: str | None = None
    usuario_sol: str | None = None
    clave_sol: str | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SunatConfig":
        env = os.environ if environ is None else environ
        ambiente = str(env.get("SUNAT_AMBIENTE", "local")).strip().lower() or "local"
        return cls(
            ambiente=ambiente,
            envio_habilitado=_parse_env_flag(env.get("SUNAT_ENVIO_HABILITADO")),
            client_id=env.get("MANNGO_ID_SUNAT"),
            client_secret=env.get("MANNGO_CLAVE_SUNAT"),
            ruc=env.get("SUNAT_RUC"),
            usuario_sol=env.get("SUNAT_USUARIO_SOL"),
            clave_sol=env.get("SUNAT_CLAVE_SOL"),
        )


@dataclass(frozen=True)
class GreEnvioResult:
    """Resultado estable de un envío recibido, todavía NO aceptado.

    ``estado`` es ``"enviado"`` y ``accepted`` siempre es ``False``. El
    consumidor debe conservar ``ticket`` y consultar el CDR en la siguiente
    capa.
    """

    estado: str
    document_id: str
    filename: str
    ticket: str
    http_status: int
    accepted: bool = False


def _parse_env_flag(value) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _safe_error_code(response) -> str | None:
    """Devuelve solo un código numérico acotado; nunca mensajes ni cuerpos."""

    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    candidates = []
    for key in ("codigo", "code", "cod", "errorCode", "error_code", "numError"):
        if key in payload:
            candidates.append(payload[key])
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("codigo", "code", "cod", "errorCode", "error_code"):
            if key in error:
                candidates.append(error[key])

    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, str)):
            text = str(value).strip()
            if _SAFE_ERROR_CODE_RE.fullmatch(text):
                return text
    return None


def _status_code(response) -> int:
    status = getattr(response, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int):
        raise SunatResponseError("SUNAT devolvió una respuesta HTTP inválida")
    return status


def _json_object(response) -> dict:
    try:
        payload = response.json()
    except Exception:
        raise SunatResponseError("SUNAT devolvió una respuesta JSON inválida") from None
    if not isinstance(payload, dict):
        raise SunatResponseError("SUNAT devolvió una respuesta JSON inesperada")
    return payload


def _raise_http_error(response, operation: str) -> None:
    status = _status_code(response)
    code = _safe_error_code(response)
    detail = f" (código {code})" if code else ""
    if status in (401, 403):
        raise SunatAuthError(f"SUNAT rechazó la autenticación HTTP {status}{detail}")
    raise SunatResponseError(f"SUNAT devolvió HTTP {status}{detail} en {operation}")


def _valid_timeout(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SunatConfigError("El timeout de SUNAT debe ser un número finito positivo")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise SunatConfigError("El timeout de SUNAT debe ser un número finito positivo")
    return timeout


def _valid_client_id(value: str | None) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or not _CLIENT_ID_RE.fullmatch(value)
    ):
        raise SunatConfigError("El client_id de SUNAT es inválido")
    return value


def _valid_ruc(value: str | None) -> str:
    if not isinstance(value, str) or not _RUC_RE.fullmatch(value) or value == "0" * 11:
        raise SunatConfigError("El RUC de SUNAT es inválido")
    return value


def _document_id(data: GreRemitenteData) -> str:
    return f"{data.serie}-{int(data.numero):08d}"


def _qualified(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _parse_secure_xml(data) -> etree._Element:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise SunatPreparationError("El XML de la guía es obligatorio")
    raw = bytes(data)
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SunatPreparationError("El XML de la guía no debe contener DTD ni entidades")

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
        raise SunatPreparationError("El XML de la guía no es válido") from None

    info = root.getroottree().docinfo
    if info.doctype or info.internalDTD is not None or info.externalDTD is not None:
        raise SunatPreparationError("El XML de la guía no debe contener DTD")
    entity_type = getattr(etree, "_Entity", None)
    if entity_type is not None:
        for node in root.iter():
            if isinstance(node, entity_type):
                raise SunatPreparationError("El XML de la guía no debe contener entidades")
    return root


def _canonical_without_extensions(xml_bytes, *, require_signature: bool) -> bytes:
    """Canoniza el documento retirando únicamente ``ext:UBLExtensions``.

    La firma XMLDSig vive dentro de esa extensión. Este control compara el
    contenido de negocio, no valida la cadena de confianza del certificado.
    """

    root = _parse_secure_xml(xml_bytes)
    if root.tag != _qualified(UBL_NS, "DespatchAdvice"):
        raise SunatPreparationError("El XML de la guía no es un DespatchAdvice")

    extension_tag = _qualified(EXT_NS, "UBLExtensions")
    extensions = [child for child in root if child.tag == extension_tag]
    if require_signature:
        if len(extensions) != 1:
            raise SunatPreparationError(
                "El XML firmado no contiene exactamente un UBLExtensions"
            )
        extension = extensions[0]
        if not any(
            node.tag == _qualified(DS_NS, "Signature") for node in extension.iter()
        ):
            raise SunatPreparationError("El XML firmado no contiene una firma XMLDSig")
        root.remove(extension)
    elif extensions:
        raise SunatPreparationError("El XML sin firmar no debe contener UBLExtensions")

    try:
        return etree.tostring(
            root, method="c14n", exclusive=True, with_comments=False
        )
    except (etree.LxmlError, ValueError):
        raise SunatPreparationError("No se pudo comparar el XML de la guía") from None


def _send_status_is_uncertain(status: int) -> bool:
    if 400 <= status < 500 and status not in (408, 429):
        return False
    return status not in (200, 201)


class SunatService:
    """Cliente REST GRE remitente con transporte inyectable y red opt-in."""

    def __init__(self, config: SunatConfig | None = None, http=None, clock=None):
        self.config = config if config is not None else SunatConfig.from_env()
        if not isinstance(self.config, SunatConfig):
            raise TypeError("config debe ser SunatConfig")
        self._http = http
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_margin = 0.0

    # ------------------------------------------------------------------
    # Infraestructura
    # ------------------------------------------------------------------

    def _http_client(self):
        if self._http is None:
            session = requests.Session()
            session.trust_env = False
            self._http = session
        return self._http

    def _now(self) -> float:
        clock = self._clock
        if clock is None:
            return time.monotonic()
        try:
            value = clock.monotonic() if hasattr(clock, "monotonic") else clock()
            value = float(value)
        except Exception:
            raise SunatConfigError("El reloj inyectado no es válido") from None
        if not math.isfinite(value):
            raise SunatConfigError("El reloj inyectado no es válido")
        return value

    def _ensure_envio_habilitado(self) -> None:
        config = self.config
        if config.ambiente != "produccion":
            raise SunatDisabledError(
                "SUNAT: el envío solo está permitido en ambiente produccion explícito"
            )
        if config.envio_habilitado is not True:
            raise SunatDisabledError(
                "SUNAT: el envío está deshabilitado (SUNAT_ENVIO_HABILITADO=false)"
            )
        required = (
            config.client_id,
            config.client_secret,
            config.ruc,
            config.usuario_sol,
            config.clave_sol,
        )
        if any(not isinstance(value, str) or value == "" for value in required):
            raise SunatConfigError("Faltan credenciales SUNAT requeridas")
        _valid_client_id(config.client_id)
        _valid_ruc(config.ruc)
        _valid_timeout(config.timeout_seconds)

    def _verify_prepared(self, prepared: PreparedGre) -> GrePackage:
        if not isinstance(prepared, PreparedGre):
            raise SunatPreparationError("Se requiere un PreparedGre")
        data = prepared.data
        if not isinstance(data, GreRemitenteData):
            raise SunatPreparationError("Los datos de la guía preparada no son válidos")
        try:
            if data.tipo_documento != GRE_REMITENTE_TIPO:
                raise SunatPreparationError("Solo se soporta GRE remitente tipo 09")
            if data.modalidad_traslado != GRE_TRANSPORTE_PRIVADO_MODALIDAD:
                raise SunatPreparationError("Solo se soporta transporte privado modalidad 02")
            if data.emisor.ruc != self.config.ruc:
                raise SunatPreparationError(
                    "El RUC de la guía no coincide con el RUC configurado"
                )
            xml = prepared.xml_bytes
            if not isinstance(xml, (bytes, bytearray)) or not xml:
                raise SunatPreparationError("El XML de la guía es obligatorio")
            expected_xml = build_despatch_xml(data)
            if bytes(xml) != expected_xml:
                raise SunatPreparationError("El XML de la guía no corresponde a sus datos")

            signed = prepared.signed_xml_bytes
            if not isinstance(signed, (bytes, bytearray)) or not signed:
                raise SunatPreparationError("El XML firmado de la guía es obligatorio")
            signed_bytes = bytes(signed)
            expected_content = _canonical_without_extensions(
                expected_xml, require_signature=False
            )
            signed_content = _canonical_without_extensions(
                signed_bytes, require_signature=True
            )
            if expected_content != signed_content:
                raise SunatPreparationError(
                    "El contenido firmado no coincide con los datos de la guía"
                )

            package = prepared.package
            if not isinstance(package, GrePackage):
                raise SunatPreparationError("El paquete de la guía es obligatorio")
            recomputed = package_gre(signed_bytes, data)
        except SunatError:
            raise
        except Exception:
            raise SunatPreparationError(
                "La guía preparada no es válida para su envío"
            ) from None

        if (
            package.filename != recomputed.filename
            or package.xml_filename != recomputed.xml_filename
            or package.zip_bytes != recomputed.zip_bytes
            or package.base64_content != recomputed.base64_content
            or package.sha256_hex != recomputed.sha256_hex
        ):
            raise SunatPreparationError(
                "El paquete de la guía no es coherente con el XML firmado"
            )
        if package.sha256_hex != hashlib.sha256(package.zip_bytes).hexdigest():
            raise SunatPreparationError("El SHA-256 del paquete no es coherente")
        if package.base64_content != base64.b64encode(package.zip_bytes).decode("ascii"):
            raise SunatPreparationError("El base64 del paquete no es coherente")
        return package

    def _validate_expected(self, expected: GreRemitenteData) -> None:
        if not isinstance(expected, GreRemitenteData):
            raise SunatPreparationError("Se requieren datos GreRemitenteData de referencia")
        try:
            validate_gre_data(expected)
        except SunatXmlError:
            raise SunatPreparationError("La identidad de la guía esperada no es válida") from None
        if expected.emisor.ruc != self.config.ruc:
            raise SunatPreparationError(
                "El RUC esperado no coincide con el RUC configurado"
            )

    @staticmethod
    def _valid_ticket(ticket) -> str:
        if not isinstance(ticket, str):
            raise SunatProtocolError("El ticket de SUNAT debe ser texto")
        normalized = ticket.strip()
        if (
            not normalized
            or normalized in {".", ".."}
            or len(normalized) > MAX_TICKET_LENGTH
            or not _TICKET_RE.fullmatch(normalized)
        ):
            raise SunatProtocolError("El ticket de SUNAT no es válido")
        return normalized

    # ------------------------------------------------------------------
    # OAuth2
    # ------------------------------------------------------------------

    def _token_from_cache_or_http(self) -> str:
        now = self._now()
        if (
            self._token is not None
            and self._token_expires_at > 0
            and now < self._token_expires_at - self._token_margin
        ):
            return self._token

        self._token = None
        self._token_expires_at = 0.0
        self._token_margin = 0.0

        config = self.config
        url = TOKEN_URL_TEMPLATE.format(client_id=_valid_client_id(config.client_id))
        form = {
            "grant_type": "password",
            "scope": OAUTH_SCOPE,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "username": f"{config.ruc}{config.usuario_sol}",
            "password": config.clave_sol,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        try:
            response = self._http_client().post(
                url,
                data=form,
                headers=headers,
                timeout=_valid_timeout(config.timeout_seconds),
                allow_redirects=False,
            )
        except Exception:
            raise SunatTransportError(
                "No se pudo contactar al servicio de autenticación de SUNAT"
            ) from None

        status = _status_code(response)
        if status != 200:
            _raise_http_error(response, "autenticación")

        payload = _json_object(response)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise SunatTokenError("SUNAT no devolvió un token de acceso válido")

        expires_in = payload.get("expires_in")
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, int)
            or expires_in <= 0
        ):
            raise SunatTokenError("SUNAT devolvió una vigencia de token inválida")

        issued_at = self._now()
        self._token = token
        self._token_expires_at = issued_at + expires_in
        self._token_margin = min(TOKEN_MARGIN_SECONDS, expires_in // 10)
        return token

    def obtener_access_token(self) -> str:
        """Obtiene (o reutiliza) un token OAuth2 sin exponer secretos."""

        self._ensure_envio_habilitado()
        return self._token_from_cache_or_http()

    # ------------------------------------------------------------------
    # Envío
    # ------------------------------------------------------------------

    def emitir_guia_remision(self, prepared: PreparedGre) -> GreEnvioResult:
        """Envía una GRE remitente ya preparada y devuelve su ticket.

        Un ticket no es aceptación. Si el transporte falla durante el POST, o
        la respuesta posterior no permite saber si SUNAT recibió la guía
        (5xx, redirect, estado ilegible, JSON roto o sin ticket), se lanza
        ``SunatTransmissionUncertain`` y el llamador no debe reemitir ni
        reintentar automáticamente. Un fallo de autenticación ocurre antes de
        enviar la GRE y se informa como ``SunatAuthError``.
        """

        self._ensure_envio_habilitado()
        package = self._verify_prepared(prepared)
        token = self.obtener_access_token()

        nombre_sin_ext = package.filename[:-4] if package.filename.endswith(".zip") else package.filename
        url = f"{API_COMPROBANTES_URL}/{nombre_sin_ext}"
        body = {
            "archivo": {
                "nomArchivo": package.filename,
                "arcGreZip": package.base64_content,
                "hashZip": package.sha256_hex,
            }
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = self._http_client().post(
                url,
                json=body,
                headers=headers,
                timeout=_valid_timeout(self.config.timeout_seconds),
                allow_redirects=False,
            )
        except Exception:
            raise SunatTransmissionUncertain(SEND_UNCERTAIN_MESSAGE) from None

        try:
            status = _status_code(response)
        except SunatResponseError:
            raise SunatTransmissionUncertain(SEND_UNCERTAIN_MESSAGE) from None

        if _send_status_is_uncertain(status):
            raise SunatTransmissionUncertain(SEND_UNCERTAIN_MESSAGE)
        if status not in (200, 201):
            _raise_http_error(response, "envío de GRE")

        try:
            payload = _json_object(response)
        except SunatResponseError:
            raise SunatTransmissionUncertain(SEND_UNCERTAIN_MESSAGE) from None
        ticket = payload.get("numTicket")
        if not isinstance(ticket, str) or not ticket.strip():
            raise SunatTransmissionUncertain(SEND_UNCERTAIN_MESSAGE)

        data = prepared.data
        return GreEnvioResult(
            estado="enviado",
            document_id=_document_id(data),
            filename=package.filename,
            ticket=ticket.strip(),
            http_status=status,
            accepted=False,
        )

    # ------------------------------------------------------------------
    # Consulta de ticket / CDR
    # ------------------------------------------------------------------

    def consultar_estado_ticket(
        self, ticket, expected: GreRemitenteData
    ) -> TicketResult:
        """Consulta el ticket y clasifica solo con CDR válido del documento.

        ``98`` queda pendiente, ``99`` es error y ``0`` exige un CDR del mismo
        RUC/tipo/serie/número con ``ResponseCode=0``. CDR ausente, corrupto,
        ajeno o un código desconocido nunca se marcan como aceptados. El
        ``CdrResult`` conserva observaciones y notas para la siguiente capa.
        """

        self._ensure_envio_habilitado()
        valid_ticket = self._valid_ticket(ticket)
        self._validate_expected(expected)

        token = self.obtener_access_token()
        url = f"{API_COMPROBANTES_URL}/envios/{valid_ticket}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        try:
            response = self._http_client().get(
                url,
                headers=headers,
                timeout=_valid_timeout(self.config.timeout_seconds),
                allow_redirects=False,
            )
        except Exception:
            raise SunatTransportError(
                "No se pudo consultar el estado del ticket en SUNAT"
            ) from None

        status = _status_code(response)
        if status != 200:
            _raise_http_error(response, "consulta de ticket")

        payload = _json_object(response)
        echoed_ticket = payload.get("numTicket")
        if echoed_ticket is not None and str(echoed_ticket) != valid_ticket:
            raise SunatProtocolError("SUNAT devolvió un ticket distinto al consultado")

        response_code = payload.get("codRespuesta")
        if response_code is None:
            response_code = payload.get("codigo")
        cdr = payload.get("arcCdr")
        if cdr is None:
            cdr = payload.get("cdr")

        return interpret_ticket_status(
            response_code,
            cdr,
            expected_ruc=self.config.ruc,
            expected_tipo=GRE_REMITENTE_TIPO,
            expected_serie=expected.serie,
            expected_numero=expected.numero,
        )


sunat_service = SunatService()
