"""Pruebas locales del transporte GRE remitente (SUNAT).

No hay red real: todo el HTTP se inyecta con ``FakeHTTP``. El certificado y
las credenciales son sintéticos y se generan/definen en la propia prueba. No
se usan dotenv, base de datos, Flask ni credenciales reales.

Estas pruebas requieren ``lxml``, ``cryptography`` y ``signxml`` para preparar
la GRE firmada; por eso solo deben ejecutarse en el entorno de tests del padre.
"""

from __future__ import annotations

import base64
import io
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from services import sunat_service as ss
from services import sunat_xml as sx

TOKEN_URL = "https://api-seguridad.sunat.gob.pe/v1/clientessol/synthetic-client/oauth2/token/"
SEND_URL = "https://api-cpe.sunat.gob.pe/v1/contribuyente/gem/comprobantes/20999999999-09-T001-00000001"
QUERY_URL = "https://api-cpe.sunat.gob.pe/v1/contribuyente/gem/comprobantes/envios/TICKET-0001"
MISSING = object()


# ---------------------------------------------------------------------------
# Dobles y datos sintéticos
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("cuerpo crudo que no debe propagarse")
        return self._payload


class FakeHTTP:
    def __init__(self, post_responses=(), get_responses=()):
        self._posts = list(post_responses)
        self._gets = list(get_responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if not self._posts:
            raise AssertionError(f"POST inesperado a {url}")
        item = self._posts.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if not self._gets:
            raise AssertionError(f"GET inesperado a {url}")
        item = self._gets.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClock:
    def __init__(self, value=1000.0):
        self.value = value

    def monotonic(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def _config(**overrides):
    base = dict(
        ambiente="produccion",
        envio_habilitado=True,
        client_id="synthetic-client",
        client_secret="synthetic-secret",
        ruc="20999999999",
        usuario_sol="USOL",
        clave_sol="clave-sintetica",
        timeout_seconds=5.0,
    )
    base.update(overrides)
    return ss.SunatConfig(**base)


def _data(**overrides) -> sx.GreRemitenteData:
    base = dict(
        serie="T001",
        numero=1,
        fecha_emision=date(2026, 10, 22),
        hora_emision=time(9, 30, 0),
        fecha_inicio_traslado=date(2026, 10, 22),
        motivo_traslado="01",
        tipo_documento="09",
        modalidad_traslado="02",
        emisor=sx.EmisorGre(
            ruc="20999999999",
            razon_social="EMISOR SINTÉTICO DE PRUEBA S.A.C.",
            ubigeo="150101",
            direccion="AV. PRUEBA 123",
        ),
        destinatario=sx.DestinatarioGre(
            tipo_documento="6",
            numero_documento="20999999998",
            razon_social="DESTINATARIO SINTÉTICO S.A.C.",
            ubigeo="150102",
            direccion="CALLE PRUEBA 456",
        ),
        partida_ubigeo="150101",
        partida_direccion="AV. PRUEBA 123",
        llegada_ubigeo="150102",
        llegada_direccion="CALLE PRUEBA 456",
        transporte=sx.TransportePrivadoGre(
            placa="TST-001",
            conductor_tipo_documento="1",
            conductor_numero_documento="00000001",
            conductor_nombre="CONDUCTOR DE PRUEBA",
            conductor_licencia="Q00000001",
        ),
        bienes=(
            sx.BienGre(
                codigo="CARBON-01",
                descripcion="Carbón de prueba",
                unidad="NIU",
                cantidad=Decimal("10"),
                peso_kg=Decimal("250.5"),
            ),
        ),
    )
    base.update(overrides)
    return sx.GreRemitenteData(**base)


@pytest.fixture(scope="module")
def signing_material():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PE"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PRUEBA LOCAL SINTETICA"),
        x509.NameAttribute(NameOID.COMMON_NAME, "CERTIFICADO SINTETICO DE PRUEBA"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2035, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return key_pem, cert_pem


def _prepared(signing_material, data=None):
    key_pem, cert_pem = signing_material
    return sx.prepare_gre(data or _data(), key_pem, cert_pem)


def _token_response(token="TOKEN-SINTETICO", expires_in=3600):
    payload = {"access_token": token}
    if expires_in is not MISSING:
        payload["expires_in"] = expires_in
    return FakeResponse(200, payload)


def _send_response(ticket="TICKET-0001", status=201):
    return FakeResponse(status, {"numTicket": ticket})


def _query_response(code, cdr=MISSING, ticket="TICKET-0001"):
    payload = {"codRespuesta": code, "numTicket": ticket}
    if cdr is not MISSING:
        payload["arcCdr"] = cdr
    return FakeResponse(200, payload)


def _cdr_xml(response_code="0", doc_id="T001-00000001", doc_type="09",
             ruc="20999999999", description="El comprobante ha sido aceptado"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ApplicationResponse xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"
 xmlns:cac="{sx.CAC_NS}" xmlns:cbc="{sx.CBC_NS}">
  <cbc:UBLVersionID>2.0</cbc:UBLVersionID>
  <cbc:CustomizationID>1.0</cbc:CustomizationID>
  <cbc:ID>CDR-SINTETICO-1</cbc:ID>
  <cbc:IssueDate>2026-10-22</cbc:IssueDate>
  <cac:ReceiverParty>
    <cac:PartyIdentification><cbc:ID schemeID="6">{ruc}</cbc:ID></cac:PartyIdentification>
  </cac:ReceiverParty>
  <cac:DocumentResponse>
    <cac:Response>
      <cbc:ReferenceID>{doc_id}</cbc:ReferenceID>
      <cbc:ResponseCode>{response_code}</cbc:ResponseCode>
      <cbc:Description>{description}</cbc:Description>
    </cac:Response>
    <cac:DocumentReference>
      <cbc:ID>{doc_id}</cbc:ID>
      <cbc:DocumentTypeCode>{doc_type}</cbc:DocumentTypeCode>
    </cac:DocumentReference>
  </cac:DocumentResponse>
  <cbc:Note>Nota sintética conservada</cbc:Note>
</ApplicationResponse>""".encode()


def _zip_b64(xml_bytes):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("R-20999999999-09-T001-00000001.xml", xml_bytes)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _post_urls(http):
    return [url for method, url, _kwargs in http.calls if method == "POST"]


# ---------------------------------------------------------------------------
# Configuración cerrada y ausencia de HTTP
# ---------------------------------------------------------------------------


def test_default_config_is_local_and_disabled_without_http(monkeypatch):
    for key in (
        "SUNAT_AMBIENTE",
        "SUNAT_ENVIO_HABILITADO",
        "MANNGO_ID_SUNAT",
        "MANNGO_CLAVE_SUNAT",
        "SUNAT_RUC",
        "SUNAT_USUARIO_SOL",
        "SUNAT_CLAVE_SOL",
    ):
        monkeypatch.delenv(key, raising=False)

    http = FakeHTTP()
    service = ss.SunatService(http=http)
    assert service.config.ambiente == "local"
    assert service.config.envio_habilitado is False
    with pytest.raises(ss.SunatDisabledError):
        service.obtener_access_token()
    assert http.calls == []


@pytest.mark.parametrize("mutate", [
    lambda c: replace(c, ambiente="local"),
    lambda c: replace(c, ambiente="beta"),
    lambda c: replace(c, ambiente="desconocido"),
    lambda c: replace(c, envio_habilitado=False),
    lambda c: replace(c, client_id=None),
    lambda c: replace(c, client_secret=""),
    lambda c: replace(c, ruc=None),
    lambda c: replace(c, usuario_sol=None),
    lambda c: replace(c, clave_sol=None),
])
def test_closed_configuration_never_calls_http(signing_material, mutate):
    config = mutate(_config())
    http = FakeHTTP()
    service = ss.SunatService(config, http=http)
    with pytest.raises(ss.SunatConfigError):
        service.emitir_guia_remision(_prepared(signing_material))
    with pytest.raises(ss.SunatConfigError):
        service.consultar_estado_ticket("TICKET-0001", _data())
    assert http.calls == []


def test_env_config_preserves_secret_values_without_silent_normalization():
    config = ss.SunatConfig.from_env({
        "SUNAT_AMBIENTE": "PRODUCCION",
        "SUNAT_ENVIO_HABILITADO": "false",
        "MANNGO_ID_SUNAT": "synthetic-client",
        "MANNGO_CLAVE_SUNAT": " secret-client ",
        "SUNAT_RUC": "20999999999",
        "SUNAT_USUARIO_SOL": "USOL",
        "SUNAT_CLAVE_SOL": " secret-sol ",
    })
    assert config.ambiente == "produccion"
    assert config.envio_habilitado is False
    assert config.client_secret == " secret-client "
    assert config.clave_sol == " secret-sol "


def test_production_config_with_missing_credentials_does_not_break_constructor():
    config = replace(_config(), clave_sol="")
    service = ss.SunatService(config, http=FakeHTTP())
    assert service.config.ambiente == "produccion"
    with pytest.raises(ss.SunatConfigError):
        service.obtener_access_token()


def test_default_session_disables_inherited_proxy_configuration(monkeypatch):
    class DummySession:
        def __init__(self):
            self.trust_env = True

    monkeypatch.setattr(ss.requests, "Session", DummySession)
    service = ss.SunatService(_config())
    client = service._http_client()
    assert isinstance(client, DummySession)
    assert client.trust_env is False


# ---------------------------------------------------------------------------
# Contrato OAuth / envío correcto
# ---------------------------------------------------------------------------


def test_positive_send_uses_exact_contract_without_accepting(signing_material):
    prepared = _prepared(signing_material)
    http = FakeHTTP([_token_response(), _send_response("TICKET-0001", 201)])
    service = ss.SunatService(_config(), http=http)

    result = service.emitir_guia_remision(prepared)

    assert result.estado == "enviado"
    assert result.accepted is False
    assert result.ticket == "TICKET-0001"
    assert result.document_id == "T001-00000001"
    assert result.filename == prepared.package.filename
    assert result.http_status == 201
    assert len(http.calls) == 2

    token_method, token_url, token_kwargs = http.calls[0]
    assert token_method == "POST"
    assert token_url == TOKEN_URL
    assert token_kwargs["data"] == {
        "grant_type": "password",
        "scope": "https://api-cpe.sunat.gob.pe",
        "client_id": "synthetic-client",
        "client_secret": "synthetic-secret",
        "username": "20999999999USOL",
        "password": "clave-sintetica",
    }
    assert token_kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert token_kwargs["timeout"] == 5.0
    assert token_kwargs["allow_redirects"] is False

    send_method, send_url, send_kwargs = http.calls[1]
    assert send_method == "POST"
    assert send_url == SEND_URL
    assert send_kwargs["headers"]["Authorization"] == "Bearer TOKEN-SINTETICO"
    assert send_kwargs["headers"]["Content-Type"] == "application/json"
    assert send_kwargs["timeout"] == 5.0
    assert send_kwargs["allow_redirects"] is False
    assert send_kwargs["json"] == {
        "archivo": {
            "nomArchivo": prepared.package.filename,
            "arcGreZip": prepared.package.base64_content,
            "hashZip": prepared.package.sha256_hex,
        }
    }
    assert "serie" not in send_kwargs["json"]
    assert "detalles" not in send_kwargs["json"]


@pytest.mark.parametrize("status", [200, 201])
def test_send_accepts_only_nonempty_ticket_and_not_acceptance(signing_material, status):
    http = FakeHTTP([_token_response(), _send_response("TICKET-OK", status)])
    service = ss.SunatService(_config(), http=http)
    result = service.emitir_guia_remision(_prepared(signing_material))
    assert result.ticket == "TICKET-OK"
    assert result.accepted is False


@pytest.mark.parametrize("payload", [
    {},
    {"numTicket": ""},
    {"numTicket": "   "},
    {"numTicket": None},
    {"numTicket": 12345},
])
def test_send_without_ticket_is_uncertain_not_absence(signing_material, payload):
    http = FakeHTTP([_token_response(), FakeResponse(200, payload)])
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatTransmissionUncertain):
        service.emitir_guia_remision(_prepared(signing_material))
    sends = [url for url in _post_urls(http) if url == SEND_URL]
    assert len(sends) == 1


def test_token_is_cached_with_monotonic_clock_and_margin():
    clock = FakeClock(1000.0)
    http = FakeHTTP([
        _token_response("TOKEN-1", 3600),
        _token_response("TOKEN-2", 3600),
    ])
    service = ss.SunatService(_config(), http=http, clock=clock)

    assert service.obtener_access_token() == "TOKEN-1"
    assert service.obtener_access_token() == "TOKEN-1"
    assert len(http.calls) == 1

    clock.advance(3301)
    assert service.obtener_access_token() == "TOKEN-2"
    assert len(http.calls) == 2


# ---------------------------------------------------------------------------
# Fallos de autenticación / respuesta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expires_in", [MISSING, True, False, 0, -1, 1.5, "3600"])
def test_invalid_token_expiry_never_falls_back_or_sends(signing_material, expires_in):
    http = FakeHTTP([_token_response(expires_in=expires_in)])
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatTokenError):
        service.emitir_guia_remision(_prepared(signing_material))
    assert len(http.calls) == 1
    assert http.calls[0][1] == TOKEN_URL


@pytest.mark.parametrize("payload", [
    {"expires_in": 3600},
    {"access_token": "", "expires_in": 3600},
    {"access_token": "   ", "expires_in": 3600},
    {"access_token": None, "expires_in": 3600},
])
def test_empty_token_is_rejected_without_send(signing_material, payload):
    http = FakeHTTP([FakeResponse(200, payload)])
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatTokenError):
        service.emitir_guia_remision(_prepared(signing_material))
    assert len(http.calls) == 1


def test_oauth_401_is_redacted_and_sends_no_gre(signing_material):
    http = FakeHTTP([
        FakeResponse(401, {"error_description": "SECRETO-OAUTH", "access_token": "NO"}),
    ])
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatAuthError) as error:
        service.emitir_guia_remision(_prepared(signing_material))
    assert "SECRETO-OAUTH" not in str(error.value)
    assert len(http.calls) == 1
    assert http.calls[0][1] == TOKEN_URL


def test_oauth_malformed_json_is_redacted(signing_material):
    http = FakeHTTP([FakeResponse(200, json_error=True)])
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatResponseError) as error:
        service.emitir_guia_remision(_prepared(signing_material))
    assert "cuerpo crudo" not in str(error.value)
    assert len(http.calls) == 1


def test_oauth_timeout_is_typed_and_sends_no_gre(signing_material):
    http = FakeHTTP([requests.Timeout("SECRETO-OAUTH-TIMEOUT")])
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatTransportError) as error:
        service.emitir_guia_remision(_prepared(signing_material))
    assert "SECRETO-OAUTH-TIMEOUT" not in str(error.value)
    assert len(http.calls) == 1
    assert http.calls[0][1] == TOKEN_URL


def test_send_422_exposes_only_whitelisted_code(signing_material):
    http = FakeHTTP([
        _token_response(),
        FakeResponse(422, {
            "error": {"desError": "SECRETO-422", "code": "1234"},
            "message": "TEXTO-CRUDO",
        }),
    ])
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatResponseError) as error:
        service.emitir_guia_remision(_prepared(signing_material))
    message = str(error.value)
    assert "1234" in message
    assert "SECRETO-422" not in message
    assert "TEXTO-CRUDO" not in message
    assert len(http.calls) == 2


def test_send_malformed_json_is_uncertain_and_redacted(signing_material):
    http = FakeHTTP([_token_response(), FakeResponse(200, json_error=True)])
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatTransmissionUncertain) as error:
        service.emitir_guia_remision(_prepared(signing_material))
    assert "cuerpo crudo" not in str(error.value)
    sends = [url for url in _post_urls(http) if url == SEND_URL]
    assert len(sends) == 1


@pytest.mark.parametrize("response", [
    pytest.param(FakeResponse(500, {"error": {"desError": "SECRETO-500"}}), id="http-500"),
    pytest.param(FakeResponse(302, {"numTicket": "NO-VALIDO"}), id="redirect"),
    pytest.param(FakeResponse(status_code=None), id="status-ilegible"),
    pytest.param(FakeResponse(204, {}), id="sin-contenido"),
])
def test_send_unconfirmable_response_is_uncertain_with_single_post(
    signing_material, response
):
    http = FakeHTTP([_token_response(), response])
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatTransmissionUncertain) as error:
        service.emitir_guia_remision(_prepared(signing_material))
    assert "SECRETO-500" not in str(error.value)
    sends = [url for url in _post_urls(http) if url == SEND_URL]
    assert len(sends) == 1


def test_send_timeout_is_uncertain_and_not_retried(signing_material):
    http = FakeHTTP([
        _token_response(),
        requests.Timeout("SECRETO-TIMEOUT"),
    ])
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatTransmissionUncertain) as error:
        service.emitir_guia_remision(_prepared(signing_material))
    assert "SECRETO-TIMEOUT" not in str(error.value)
    sends = [url for url in _post_urls(http) if url == SEND_URL]
    assert len(sends) == 1


# ---------------------------------------------------------------------------
# Coherencia local antes de OAuth / envío
# ---------------------------------------------------------------------------


def _bad_hash(prepared):
    return replace(prepared, package=replace(prepared.package, sha256_hex="0" * 64))


def _bad_base64(prepared):
    return replace(prepared, package=replace(prepared.package, base64_content="AAAA"))


def _bad_zip(prepared):
    return replace(prepared, package=replace(prepared.package, zip_bytes=b"no-es-un-zip"))


def _bad_xml(prepared):
    return replace(prepared, xml_bytes=b"<otro/>")


@pytest.mark.parametrize("mutator", [_bad_hash, _bad_base64, _bad_zip, _bad_xml])
def test_incoherent_package_is_rejected_before_http(signing_material, mutator):
    http = FakeHTTP()
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatPreparationError):
        service.emitir_guia_remision(mutator(_prepared(signing_material)))
    assert http.calls == []


def test_ruc_mismatch_is_rejected_before_http(signing_material):
    http = FakeHTTP()
    service = ss.SunatService(_config(ruc="20999999998"), http=http)
    with pytest.raises(ss.SunatPreparationError):
        service.emitir_guia_remision(_prepared(signing_material))
    assert http.calls == []


@pytest.mark.parametrize("variant", [
    pytest.param(
        replace(_data(), bienes=(replace(_data().bienes[0], cantidad=Decimal("99")),)),
        id="cantidad-distinta",
    ),
    pytest.param(
        replace(_data(), emisor=replace(_data().emisor, ruc="20999999998")),
        id="ruc-distinto",
    ),
])
def test_signed_content_must_match_data_even_with_same_document_id(
    signing_material, variant
):
    key_pem, cert_pem = signing_material
    base = _prepared(signing_material)
    signed_variant = sx.build_signed_despatch(variant, key_pem, cert_pem)
    package = sx.package_gre(signed_variant, base.data)
    prepared = replace(base, signed_xml_bytes=signed_variant, package=package)

    http = FakeHTTP()
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatPreparationError):
        service.emitir_guia_remision(prepared)
    assert http.calls == []


def test_unsupported_document_type_is_rejected_before_http(signing_material):
    data = _data(tipo_documento="01")
    key_pem, cert_pem = signing_material
    prepared = sx.prepare_gre(_data(), key_pem, cert_pem)
    prepared = replace(prepared, data=data)
    http = FakeHTTP()
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatPreparationError):
        service.emitir_guia_remision(prepared)
    assert http.calls == []


# ---------------------------------------------------------------------------
# Consulta de ticket y CDR
# ---------------------------------------------------------------------------


def test_query_98_is_pending_and_never_accepted(signing_material):
    http = FakeHTTP(post_responses=[_token_response()], get_responses=[_query_response("98")])
    service = ss.SunatService(_config(), http=http)

    result = service.consultar_estado_ticket("TICKET-0001", _data())

    assert result.estado == sx.CDR_PENDIENTE
    assert result.accepted is False
    assert result.cdr is None
    assert len(http.calls) == 2
    method, url, kwargs = http.calls[1]
    assert method == "GET"
    assert url == QUERY_URL
    assert kwargs["headers"]["Authorization"] == "Bearer TOKEN-SINTETICO"
    assert kwargs["timeout"] == 5.0
    assert kwargs["allow_redirects"] is False


def test_query_99_is_error_and_never_accepted(signing_material):
    http = FakeHTTP(post_responses=[_token_response()], get_responses=[_query_response("99")])
    service = ss.SunatService(_config(), http=http)
    result = service.consultar_estado_ticket("TICKET-0001", _data())
    assert result.estado == sx.CDR_ERROR
    assert result.accepted is False


def test_query_zero_with_matching_cdr_accepts_and_preserves_cdr(signing_material):
    cdr = _zip_b64(_cdr_xml())
    http = FakeHTTP(post_responses=[_token_response()], get_responses=[_query_response("0", cdr)])
    service = ss.SunatService(_config(), http=http)

    result = service.consultar_estado_ticket("TICKET-0001", _data())

    assert result.accepted is True
    assert result.estado == sx.CDR_ACEPTADO
    assert result.cdr is not None
    assert result.cdr.response_code == "0"
    assert result.cdr.document_id == "T001-00000001"
    assert "El comprobante ha sido aceptado" in result.cdr.observations
    assert "Nota sintética conservada" in result.cdr.notes
    assert result.cdr.cdr_xml.startswith(b"<?xml")


@pytest.mark.parametrize("cdr", [
    pytest.param(MISSING, id="ausente"),
    pytest.param("", id="vacio"),
    pytest.param("!!!no-es-base64!!!", id="base64-invalido"),
    pytest.param(_zip_b64(_cdr_xml(doc_id="T001-00000009")), id="documento-ajeno"),
    pytest.param(_zip_b64(_cdr_xml(doc_type="31")), id="tipo-ajeno"),
    pytest.param(_zip_b64(_cdr_xml(ruc="20999999997")), id="ruc-ajeno"),
    pytest.param(_zip_b64(_cdr_xml(response_code="2326")), id="rechazo-2326"),
])
def test_query_zero_without_valid_matching_cdr_never_accepts(signing_material, cdr):
    http = FakeHTTP(post_responses=[_token_response()], get_responses=[_query_response("0", cdr)])
    service = ss.SunatService(_config(), http=http)
    result = service.consultar_estado_ticket("TICKET-0001", _data())
    assert result.accepted is False
    assert result.estado in {sx.CDR_ERROR, sx.CDR_RECHAZADO}


def test_query_unknown_code_never_accepts_even_with_valid_cdr(signing_material):
    http = FakeHTTP(
        post_responses=[_token_response()],
        get_responses=[_query_response("procesado", _zip_b64(_cdr_xml()))],
    )
    service = ss.SunatService(_config(), http=http)
    result = service.consultar_estado_ticket("TICKET-0001", _data())
    assert result.accepted is False
    assert result.estado == sx.CDR_ERROR


@pytest.mark.parametrize("ticket", ["", "   ", "../otro", None, True, "x" * 201])
def test_invalid_ticket_is_rejected_without_http(signing_material, ticket):
    http = FakeHTTP()
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatProtocolError):
        service.consultar_estado_ticket(ticket, _data())
    assert http.calls == []


def test_query_expected_ruc_mismatch_is_rejected_without_http():
    expected = replace(_data(), emisor=replace(_data().emisor, ruc="20999999998"))
    http = FakeHTTP()
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatPreparationError):
        service.consultar_estado_ticket("TICKET-0001", expected)
    assert http.calls == []


def test_query_ticket_echo_mismatch_is_rejected():
    http = FakeHTTP(
        post_responses=[_token_response()],
        get_responses=[
            FakeResponse(200, {"codRespuesta": "98", "numTicket": "OTRO-TICKET"}),
        ],
    )
    service = ss.SunatService(_config(), http=http)
    with pytest.raises(ss.SunatProtocolError):
        service.consultar_estado_ticket("TICKET-0001", _data())
