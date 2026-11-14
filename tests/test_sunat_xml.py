"""Pruebas locales de preparación GRE y lectura segura de CDR.

No hay red, Flask, ORM ni dotenv. El certificado es sintético y generado en
memoria por la propia prueba. Los XSD OASIS son solo validación estructural.
"""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from dataclasses import replace
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from services import sunat_xml as sx


UBL_NS = sx.UBL_NS
CBC_NS = sx.CBC_NS
CAC_NS = sx.CAC_NS
EXT_NS = sx.EXT_NS
DS_NS = sx.DS_NS

XSD_PATH = Path(__file__).resolve().parent / "fixtures" / "ubl21_xsd" / "maindoc" / "UBL-DespatchAdvice-2.1.xsd"


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
                descripcion="Carbón & <leña> ñandú",
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
        .not_valid_before(datetime(2020, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2035, 1, 1, tzinfo=timezone.utc))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return key, cert, key_pem, cert_pem


def _cdr_xml(response_code="0", doc_id="T001-00000001", doc_type="09",
             ruc="20999999999", description="El comprobante ha sido aceptado",
             notes=("Nota sintética de prueba",)) -> bytes:
    notas = "".join(f"<cbc:Note>{note}</cbc:Note>" for note in notes)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ApplicationResponse xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"
 xmlns:cac="{CAC_NS}" xmlns:cbc="{CBC_NS}">
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
  {notas}
</ApplicationResponse>""".encode("utf-8")


def _zip_b64(xml_bytes: bytes, name="R-20999999999-09-T001-00000001.xml") -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, xml_bytes)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _parse_cdr(cdr, **overrides):
    kwargs = dict(
        expected_ruc="20999999999",
        expected_tipo="09",
        expected_serie="T001",
        expected_numero=1,
    )
    kwargs.update(overrides)
    return sx.parse_cdr(cdr, **kwargs)


# ---------------------------------------------------------------------------
# Construcción / validación
# ---------------------------------------------------------------------------


def test_build_escapes_xml_and_preserves_unicode():
    data = _data()
    xml_bytes = sx.build_despatch_xml(data)
    root = etree.fromstring(xml_bytes)

    assert root.tag == f"{{{UBL_NS}}}DespatchAdvice"
    assert not xml_bytes.startswith(b"\xef\xbb\xbf")
    assert b"&amp;" in xml_bytes and b"&lt;" in xml_bytes
    assert "Carbón & <leña> ñandú".encode("utf-8") not in xml_bytes

    descripcion = root.find(f".//{{{CAC_NS}}}Item/{{{CBC_NS}}}Description")
    assert descripcion is not None
    assert descripcion.text == "Carbón & <leña> ñandú"


def test_build_contains_explicit_gre_fields():
    root = etree.fromstring(sx.build_despatch_xml(_data()))

    def text(path):
        element = root.find(path)
        return None if element is None else element.text

    assert text(f"{{{CBC_NS}}}UBLVersionID") == "2.1"
    assert text(f"{{{CBC_NS}}}CustomizationID") == "2.0"
    assert text(f"{{{CBC_NS}}}ID") == "T001-00000001"
    assert text(f"{{{CBC_NS}}}IssueDate") == "2026-10-22"
    assert text(f"{{{CBC_NS}}}IssueTime") == "09:30:00"
    assert text(f"{{{CBC_NS}}}DespatchAdviceTypeCode") == "09"
    assert text(f".//{{{CBC_NS}}}HandlingCode") == "01"
    assert text(f".//{{{CBC_NS}}}TransportModeCode") == "02"
    assert text(f".//{{{CBC_NS}}}LicensePlateID") == "TST-001"
    assert text(f".//{{{CAC_NS}}}IdentityDocumentReference/{{{CBC_NS}}}ID") == "Q00000001"
    assert text(f".//{{{CBC_NS}}}DeliveredQuantity") == "10"
    assert text(f".//{{{CBC_NS}}}GrossWeightMeasure") == "250.5"

    supplier_id = root.find(f".//{{{CAC_NS}}}DespatchSupplierParty//{{{CBC_NS}}}ID")
    assert supplier_id is not None and supplier_id.text == "20999999999"
    assert supplier_id.get("schemeID") == "6"
    customer_id = root.find(f".//{{{CAC_NS}}}DeliveryCustomerParty//{{{CBC_NS}}}ID")
    assert customer_id is not None and customer_id.text == "20999999998"

    lineas = root.findall(f"{{{CAC_NS}}}DespatchLine")
    assert len(lineas) == 1
    assert lineas[0].find(f"{{{CAC_NS}}}OrderLineReference/{{{CBC_NS}}}LineID").text == "1"
    assert lineas[0].find(f"{{{CAC_NS}}}Item/{{{CAC_NS}}}SellersItemIdentification/{{{CBC_NS}}}ID").text == "CARBON-01"

    assert root.find(f"{{{EXT_NS}}}UBLExtensions") is None
    firma_ubl = root.find(f"{{{CAC_NS}}}Signature")
    assert firma_ubl is not None
    assert firma_ubl.find(f"{{{CBC_NS}}}ID").text == sx.SIGNATURE_ID
    uri_ubl = firma_ubl.find(
        f"{{{CAC_NS}}}DigitalSignatureAttachment/{{{CAC_NS}}}ExternalReference/{{{CBC_NS}}}URI"
    )
    assert uri_ubl.text == f"#{sx.SIGNATURE_ID}"
    envio = root.find(f"{{{CAC_NS}}}Shipment")
    assert envio[0].tag == f"{{{CBC_NS}}}ID"
    assert envio[0].text == "T001-00000001"


def test_build_multiple_lines_uses_explicit_values():
    data = _data(bienes=(
        sx.BienGre("A-1", "Bien uno", "NIU", Decimal("2"), Decimal("10")),
        sx.BienGre("B-2", "Bien dos", "KGM", Decimal("3.5"), Decimal("4.25")),
    ))
    root = etree.fromstring(sx.build_despatch_xml(data))
    lineas = root.findall(f"{{{CAC_NS}}}DespatchLine")
    assert [line.find(f"{{{CBC_NS}}}ID").text for line in lineas] == ["1", "2"]
    assert lineas[1].find(f"{{{CBC_NS}}}DeliveredQuantity").get("unitCode") == "KGM"
    assert root.find(f".//{{{CBC_NS}}}GrossWeightMeasure").text == "14.25"


def _mutators():
    return {
        "sin_razon_social_emisor": lambda d: replace(d, emisor=replace(d.emisor, razon_social="")),
        "sin_razon_social_destino": lambda d: replace(d, destinatario=replace(d.destinatario, razon_social="")),
        "sin_direccion_partida": lambda d: replace(d, partida_direccion=""),
        "sin_placa": lambda d: replace(d, transporte=replace(d.transporte, placa="")),
        "sin_licencia": lambda d: replace(d, transporte=replace(d.transporte, conductor_licencia="")),
        "sin_conductor": lambda d: replace(d, transporte=replace(d.transporte, conductor_nombre="")),
        "ruc_invalido": lambda d: replace(d, emisor=replace(d.emisor, ruc="123")),
        "documento_destino_invalido": lambda d: replace(d, destinatario=replace(d.destinatario, numero_documento="123")),
        "ubigeo_invalido": lambda d: replace(d, partida_ubigeo="123"),
        "serie_invalida": lambda d: replace(d, serie="X001"),
        "serie_minuscula": lambda d: replace(d, serie="t001"),
        "numero_cero": lambda d: replace(d, numero=0),
        "numero_mayor": lambda d: replace(d, numero=100000000),
        "numero_booleano": lambda d: replace(d, numero=True),
        "fecha_datetime": lambda d: replace(d, fecha_emision=datetime(2026, 10, 22, 9, 30)),
        "motivo_invalido": lambda d: replace(d, motivo_traslado="99"),
        "tipo_no_soportado": lambda d: replace(d, tipo_documento="31"),
        "modalidad_no_soportada": lambda d: replace(d, modalidad_traslado="01"),
        "sin_bienes": lambda d: replace(d, bienes=()),
        "cantidad_cero": lambda d: replace(d, bienes=(replace(d.bienes[0], cantidad=Decimal("0")),)),
        "cantidad_negativa": lambda d: replace(d, bienes=(replace(d.bienes[0], cantidad=Decimal("-1")),)),
        "cantidad_nan": lambda d: replace(d, bienes=(replace(d.bienes[0], cantidad=Decimal("NaN")),)),
        "cantidad_infinita": lambda d: replace(d, bienes=(replace(d.bienes[0], cantidad=Decimal("Infinity")),)),
        "cantidad_texto": lambda d: replace(d, bienes=(replace(d.bienes[0], cantidad="abc"),)),
        "peso_cero": lambda d: replace(d, bienes=(replace(d.bienes[0], peso_kg=Decimal("0")),)),
        "peso_nan": lambda d: replace(d, bienes=(replace(d.bienes[0], peso_kg=Decimal("NaN")),)),
        "unidad_vacia": lambda d: replace(d, bienes=(replace(d.bienes[0], unidad=""),)),
        "descripcion_vacia": lambda d: replace(d, bienes=(replace(d.bienes[0], descripcion=""),)),
        "codigo_vacio": lambda d: replace(d, bienes=(replace(d.bienes[0], codigo=""),)),
        "placeholder_emisor": lambda d: replace(d, emisor=replace(d.emisor, razon_social="Cliente Externo")),
        "placeholder_licencia": lambda d: replace(d, transporte=replace(d.transporte, conductor_licencia="Q+DNI")),
        "placeholder_placa": lambda d: replace(d, transporte=replace(d.transporte, placa="Sin placa")),
    }


@pytest.mark.parametrize("name", sorted(_mutators()))
def test_rejects_incomplete_or_invalid_data(name):
    mutator = _mutators()[name]
    with pytest.raises(sx.GreValidationError):
        sx.build_despatch_xml(mutator(_data()))


def test_validate_gre_data_accepts_explicit_valid_data():
    assert sx.validate_gre_data(_data()) is None


# ---------------------------------------------------------------------------
# Firma / digest
# ---------------------------------------------------------------------------


def test_signature_is_in_extension_content_and_verifies(signing_material):
    _, _, key_pem, cert_pem = signing_material
    signed = sx.build_signed_despatch(_data(), key_pem, cert_pem)

    assert signed.startswith(b"<?xml")
    assert not signed.startswith(b"\xef\xbb\xbf")
    root = etree.fromstring(signed)
    extension = root.find(f".//{{{EXT_NS}}}ExtensionContent")
    signature = extension.find(f".//{{{DS_NS}}}Signature")
    assert signature is not None
    assert root.find(f"{{{DS_NS}}}Signature") is None
    assert signature.getparent().tag == f"{{{EXT_NS}}}ExtensionContent"
    assert signature.get("Id") == sx.SIGNATURE_ID
    assert signature.find(f".//{{{DS_NS}}}SignatureMethod").get("Algorithm").endswith("rsa-sha256")
    assert "sha256" in signature.find(f".//{{{DS_NS}}}DigestMethod").get("Algorithm")
    assert len(root.findall(f".//{{{DS_NS}}}Signature")) == 1

    uri_ubl = root.find(
        f".//{{{CAC_NS}}}Signature/{{{CAC_NS}}}DigitalSignatureAttachment/"
        f"{{{CAC_NS}}}ExternalReference/{{{CBC_NS}}}URI"
    )
    assert uri_ubl is not None
    assert uri_ubl.text == f"#{signature.get('Id')}"

    info = sx.verify_despatch_signature(signed, cert_pem)
    assert info.reference_uri == ""
    assert info.digest_value
    assert "sha256" in info.digest_algorithm


def test_signature_stays_in_extension_with_real_id_and_unique_reference(signing_material):
    _, _, key_pem, cert_pem = signing_material
    signed = sx.build_signed_despatch(_data(), key_pem, cert_pem)
    root = etree.fromstring(signed)
    firmas = root.findall(f".//{{{DS_NS}}}Signature")
    assert len(firmas) == 1
    firma = firmas[0]
    assert firma.get("Id") == sx.SIGNATURE_ID
    assert firma.getparent().tag == f"{{{EXT_NS}}}ExtensionContent"
    assert root.find(f"{{{DS_NS}}}Signature") is None

    uri_ubl = root.find(
        f".//{{{CAC_NS}}}Signature/{{{CAC_NS}}}DigitalSignatureAttachment/"
        f"{{{CAC_NS}}}ExternalReference/{{{CBC_NS}}}URI"
    )
    assert uri_ubl.text == f"#{firma.get('Id')}"

    reserialized = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    info = sx.verify_despatch_signature(reserialized, cert_pem)
    assert info.reference_uri == ""
    assert info.signature_algorithm.endswith("rsa-sha256")
    assert info.digest_algorithm.endswith("sha256")


def test_digest_value_is_present_and_sha256(signing_material):
    _, _, key_pem, cert_pem = signing_material
    signed = sx.build_signed_despatch(_data(), key_pem, cert_pem)
    info = sx.verify_despatch_signature(signed, cert_pem)
    raw_digest = base64.b64decode(info.digest_value, validate=True)
    assert len(raw_digest) == hashlib.sha256().digest_size
    assert info.digest_algorithm.endswith("sha256")


def test_tampering_after_signature_is_rejected(signing_material):
    _, _, key_pem, cert_pem = signing_material
    signed = sx.build_signed_despatch(_data(), key_pem, cert_pem)
    assert b"CONDUCTOR DE PRUEBA" in signed
    tampered = signed.replace(b"CONDUCTOR DE PRUEBA", b"CONDUCTOR DE PRUEBX")
    assert tampered != signed
    with pytest.raises(sx.GreSignatureError):
        sx.verify_despatch_signature(tampered, cert_pem)


def test_pfx_signing_material_is_local_and_password_protected(signing_material):
    key, cert, _, _ = signing_material
    password = b"clave-sintetica"
    pfx = pkcs12.serialize_key_and_certificates(
        name=b"prueba-sunat-local",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )
    material = sx.load_pkcs12(pfx, password)
    assert b"PRIVATE KEY" in material.private_key_pem
    assert b"CERTIFICATE" in material.certificate_pem
    signed = sx.build_signed_despatch(_data(), material.private_key_pem, material.certificate_pem)
    info = sx.verify_despatch_signature(signed, material.certificate_pem)
    assert info.digest_value

    with pytest.raises(sx.GreSignatureError):
        sx.load_pkcs12(pfx, b"clave-incorrecta")


# ---------------------------------------------------------------------------
# ZIP / base64 / SHA-256
# ---------------------------------------------------------------------------


def test_package_name_zip_base64_and_sha256_are_coherent(signing_material):
    _, _, key_pem, cert_pem = signing_material
    signed = sx.build_signed_despatch(_data(), key_pem, cert_pem)
    package = sx.package_gre(signed, _data())

    assert package.filename == "20999999999-09-T001-00000001.zip"
    assert package.xml_filename == "20999999999-09-T001-00000001.xml"
    assert package.base64_content == base64.b64encode(package.zip_bytes).decode("ascii")
    assert package.sha256_hex == hashlib.sha256(package.zip_bytes).hexdigest()
    with zipfile.ZipFile(io.BytesIO(package.zip_bytes)) as zf:
        assert zf.namelist() == [package.xml_filename]
        assert zf.read(package.xml_filename) == signed


def test_package_rejects_xml_from_other_document(signing_material):
    _, _, key_pem, cert_pem = signing_material
    otro = _data(numero=2)
    signed = sx.build_signed_despatch(otro, key_pem, cert_pem)
    with pytest.raises(sx.GreValidationError):
        sx.package_gre(signed, _data(numero=1))


def test_price_prepare_gre_is_local(signing_material):
    _, _, key_pem, cert_pem = signing_material
    prepared = sx.prepare_gre(_data(), key_pem, cert_pem)
    assert prepared.xml_bytes.startswith(b"<?xml")
    assert prepared.xml_bytes != prepared.signed_xml_bytes
    assert b"<ds:Signature" in prepared.signed_xml_bytes
    assert prepared.package.sha256_hex == hashlib.sha256(prepared.package.zip_bytes).hexdigest()
    assert prepared.package.filename.endswith("-09-T001-00000001.zip")


# ---------------------------------------------------------------------------
# XSD estructural OASIS (no regla SUNAT)
# ---------------------------------------------------------------------------


def test_xsd_oasis_structural_validation(signing_material):
    _, _, key_pem, cert_pem = signing_material
    unsigned = sx.build_despatch_xml(_data())
    sx.validate_against_xsd(unsigned, XSD_PATH)
    signed = sx.build_signed_despatch(_data(), key_pem, cert_pem)
    sx.validate_against_xsd(signed, XSD_PATH)


def test_xsd_rejects_structurally_invalid_document():
    malformed = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<DespatchAdvice xmlns="urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2"/>'
    )
    with pytest.raises(sx.XsdValidationError):
        sx.validate_against_xsd(malformed, XSD_PATH)


# ---------------------------------------------------------------------------
# CDR
# ---------------------------------------------------------------------------


def test_cdr_accepted_only_with_matching_identity_and_zero():
    result = _parse_cdr(_zip_b64(_cdr_xml()))
    assert result.accepted is True
    assert result.estado == sx.CDR_ACEPTADO
    assert result.response_code == "0"
    assert result.document_id == "T001-00000001"
    assert "El comprobante ha sido aceptado" in result.observations
    assert "Nota sintética de prueba" in result.notes
    assert result.cdr_xml.startswith(b"<?xml")


def test_cdr_rejection_code_is_observed_not_accepted():
    result = _parse_cdr(_zip_b64(_cdr_xml(
        response_code="2326",
        description="procesado pero rechazado",
        notes=("se conserva",),
    )))
    assert result.accepted is False
    assert result.estado == sx.CDR_RECHAZADO
    assert result.response_code == "2326"
    assert "procesado pero rechazado" in result.observations
    assert "se conserva" in result.notes


@pytest.mark.parametrize("code,estado", [("98", sx.CDR_PENDIENTE), ("99", sx.CDR_ERROR)])
def test_cdr_pending_or_error_codes_are_not_accepted(code, estado):
    result = _parse_cdr(_zip_b64(_cdr_xml(response_code=code)))
    assert result.accepted is False
    assert result.estado == estado


@pytest.mark.parametrize("kwargs", [
    {"doc_id": "T001-00000002"},
    {"doc_type": "31"},
    {"ruc": "20999999997"},
])
def test_cdr_foreign_identity_is_rejected(kwargs):
    with pytest.raises(sx.CdrError):
        _parse_cdr(_zip_b64(_cdr_xml(**kwargs)))


def test_cdr_wrong_root_is_rejected():
    with pytest.raises(sx.CdrError):
        _parse_cdr(_zip_b64(b'<DespatchAdvice xmlns="urn:example"/>'))


def test_cdr_invalid_base64_and_corrupt_zip_are_rejected():
    with pytest.raises(sx.CdrError):
        _parse_cdr("!!!no-es-base64!!!")
    with pytest.raises(sx.CdrError):
        _parse_cdr(base64.b64encode(b"no-es-un-zip").decode("ascii"))


def test_cdr_xxe_is_rejected_without_expansion():
    xxe = b"""<?xml version="1.0"?>
<!DOCTYPE ApplicationResponse [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<ApplicationResponse xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2">
  <cbc:ID xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">&xxe;</cbc:ID>
</ApplicationResponse>"""
    with pytest.raises(sx.CdrError):
        _parse_cdr(_zip_b64(xxe))


def test_cdr_zip_limits_and_single_xml_selection():
    with pytest.raises(sx.CdrError):
        _parse_cdr(base64.b64encode(b"PK\x03\x04basura").decode("ascii"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("leeme.txt", "sin xml")
    with pytest.raises(sx.CdrError):
        _parse_cdr(base64.b64encode(buffer.getvalue()).decode("ascii"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.xml", _cdr_xml())
        zf.writestr("b.xml", _cdr_xml())
    with pytest.raises(sx.CdrError):
        _parse_cdr(base64.b64encode(buffer.getvalue()).decode("ascii"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../evil.xml", _cdr_xml())
    with pytest.raises(sx.CdrError):
        _parse_cdr(base64.b64encode(buffer.getvalue()).decode("ascii"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for index in range(sx.MAX_CDR_ENTRIES):
            zf.writestr(f"extra-{index}.txt", "x")
        zf.writestr("cdr.xml", _cdr_xml())
    with pytest.raises(sx.CdrError):
        _parse_cdr(base64.b64encode(buffer.getvalue()).decode("ascii"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("cdr.xml", b"<ApplicationResponse>" + b"A" * (sx.MAX_CDR_XML_BYTES + 10) + b"</ApplicationResponse>")
    with pytest.raises(sx.CdrError):
        _parse_cdr(base64.b64encode(buffer.getvalue()).decode("ascii"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("cdr.xml", b"<ApplicationResponse>" + b"A" * (1024 * 1024) + b"</ApplicationResponse>")
    with pytest.raises(sx.CdrError):
        _parse_cdr(base64.b64encode(buffer.getvalue()).decode("ascii"))


# ---------------------------------------------------------------------------
# Ticket: 98/99/texto nunca aceptan
# ---------------------------------------------------------------------------


def test_ticket_without_valid_cdr_never_accepts():
    kwargs = dict(expected_ruc="20999999999", expected_serie="T001", expected_numero=1)
    assert sx.interpret_ticket_status("98", None, **kwargs).estado == sx.CDR_PENDIENTE
    assert sx.interpret_ticket_status("98", None, **kwargs).accepted is False
    assert sx.interpret_ticket_status("99", None, **kwargs).estado == sx.CDR_ERROR
    assert sx.interpret_ticket_status("99", None, **kwargs).accepted is False
    assert sx.interpret_ticket_status("0", None, **kwargs).accepted is False
    assert sx.interpret_ticket_status("0", None, **kwargs).estado == sx.CDR_ERROR
    assert sx.interpret_ticket_status("procesado", _zip_b64(_cdr_xml()), **kwargs).accepted is False
    assert sx.interpret_ticket_status("procesado", _zip_b64(_cdr_xml()), **kwargs).estado == sx.CDR_ERROR


def test_ticket_accepts_only_zero_with_matching_cdr():
    kwargs = dict(expected_ruc="20999999999", expected_serie="T001", expected_numero=1)
    accepted = sx.interpret_ticket_status("0", _zip_b64(_cdr_xml()), **kwargs)
    assert accepted.accepted is True
    assert accepted.estado == sx.CDR_ACEPTADO
    assert accepted.cdr is not None

    rejected = sx.interpret_ticket_status("0", _zip_b64(_cdr_xml(response_code="2326")), **kwargs)
    assert rejected.accepted is False
    assert rejected.estado == sx.CDR_RECHAZADO

    foreign = sx.interpret_ticket_status("0", _zip_b64(_cdr_xml(doc_id="T001-00000009")), **kwargs)
    assert foreign.accepted is False
    assert foreign.estado == sx.CDR_ERROR

    still_pending = sx.interpret_ticket_status("98", _zip_b64(_cdr_xml()), **kwargs)
    assert still_pending.accepted is False
    assert still_pending.estado == sx.CDR_PENDIENTE
