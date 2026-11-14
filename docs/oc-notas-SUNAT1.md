# Notas SUNAT1 — XML/firma/empaquetado/CDR local

## Alcance
- Subfase SUNAT1: módulo puro de preparación de GRE remitente UBL 2.1 (tipo `09`, modalidad `02`) y lectura segura de CDR.
- Sin Flask, SQLAlchemy, dotenv, HTTP, red fiscal, base de datos ni datos reales.
- No se modificaron `models.py`, `services/sunat_service.py`, rutas, `resources/__init__.py`, Telegram, `.env` ni dependencias.
- La firma local con certificado sintético no acredita aceptación SUNAT.

## Archivos
- `services/sunat_xml.py`: implementación nueva.
- `tests/test_sunat_xml.py`: regresiones locales nuevas.
- `docs/oc-notas-SUNAT1.md`: estas notas.

## API pública
- Datos: `EmisorGre`, `DestinatarioGre`, `BienGre`, `TransportePrivadoGre`, `GreRemitenteData`, `SigningMaterial`, `SignatureInfo`, `GrePackage`, `PreparedGre`, `CdrResult`, `TicketResult`.
- Errores: `SunatXmlError`, `GreValidationError`, `GreUnsupportedError`, `GreSignatureError`, `XsdValidationError`, `CdrError`.
- Funciones: `validate_gre_data`, `build_despatch_xml`, `load_pkcs12`, `sign_despatch_xml`, `verify_despatch_signature`, `build_signed_despatch`, `package_gre`, `prepare_gre`, `validate_against_xsd`, `parse_cdr`, `interpret_ticket_status`.

## Decisiones implementadas
- Validación explícita: no se completan valores por defecto. Se rechazan campos vacíos, RUC/DNI/ubigeo inválidos, placa/licencia/dirección/nombre ausentes o de relleno, cantidades/pesos no finitos o no positivos.
- XML: `DespatchAdvice` UBL 2.1 sin `UBLExtensions` en el documento no firmado (así el unsigned es estructuralmente válido); el firmador inserta `ext:UBLExtensions` como primer hijo. `Shipment` lleva `cbc:ID` técnico antes de `HandlingCode`. Placa en `cac:TransportMeans/cac:RoadTransport/cbc:LicensePlateID`; `DespatchLine` con `OrderLineReference/cbc:LineID` obligatorio.
- Firma: XMLDSig enveloped RSA/SHA-256 y exc-c14n, referencia de documento completo `URI=""`. Se usa el placeholder oficial `ds:Signature Id="placeholder"` dentro de `ext:ExtensionContent` antes de firmar; signxml lo reemplaza en sitio y lo excisa para el digest. Un `signature_annotator` fija el `Id` real `SignatureSP` antes de `SignatureValue`; `cac:Signature/cbc:URI` apunta a `#SignatureSP`. No se mueve ni se altera la firma después de crearla. Se verifica localmente antes de devolver bytes y se rechaza cualquier alteración posterior.
- Paquete: ZIP con un XML, nombre `RUC-09-Tserie-numero.zip`, base64 y SHA-256 calculados sobre los bytes exactos del ZIP.
- CDR: acepta base64 de ZIP/XML o XML directo; límites de tamaño, entradas, compresión y rutas; sin DTD/entidades/red. Exige `ResponseCode` `0` y coincidencia de tipo/serie/número y RUC receptor; `98` queda pendiente, `99` error y otros códigos rechazados. Texto como «procesado» nunca acepta.
- Tickets: `98` pendiente, `99` error, `0` solo acepta con CDR válido; cualquier otro valor no acepta.
- XSD: `validate_against_xsd` usa el XSD OASIS local suministrado como control estructural. No es validación tributaria SUNAT.

## Corrección tras ejecución del padre
- El padre ejecutó la suite en Docker real con `signxml` 5.2, `lxml` 6.1.3 y `cryptography` 50.0.2: **50 passed, 8 failed** (7 de firma y 1 de XSD). No se inventan resultados posteriores.
- Causa de los 7 fallos de firma: `reference_uri=""` no representa el documento entero en SignXML 5.2; el digest se intentaba resolver como `#` sin Id. Además se firmaba en la raíz y luego se movía la firma, lo que viola el contrato de firma.
- Corrección: construir el unsigned sin `UBLExtensions`, insertar el placeholder `ds:Signature Id="placeholder"` en `ExtensionContent` antes de firmar, omitir `reference_uri` (genera `URI=""`), fijar `Id="SignatureSP"` con `signature_annotators` antes de `SignatureValue` y verificar sin mover ni modificar la firma.
- Causa del fallo XSD: `Shipment` empezaba en `HandlingCode` y el XSD exige `cbc:ID` primero; además, un `ExtensionContent` vacío en el unsigned no era válido. Corrección: `Shipment/cbc:ID` técnico igual al ID del documento y unsigned sin `UBLExtensions`.
- Se conservaron las 58 regresiones y se añadieron comprobaciones de URI `#SignatureSP` contra el `Id` real, firma única, permanencia en `ExtensionContent` y digest RSA/SHA-256 verificable.
- Segunda ejecución del padre: **50 passed, 9 failed**. Los 9 fallos fueron `TypeError` porque `XMLSigner.__init__` de SignXML 5.2 no acepta el keyword `signature_annotators`. Corrección: instanciar `XMLSigner` sin ese keyword y luego hacer `signer.signature_annotators.append(_annotate_signature)`, conservando el annotator por defecto `_add_key_info` y sin reemplazar la lista.

## Evidencia y límites
- XSD leídos desde `tests/fixtures/ubl21_xsd/` (OASIS UBL 2.1, solo estructura).
- Pruebas escritas para escape/Unicode, campos explícitos, mutadores inválidos, firma/digest, PFX sintético, tampering, ZIP/base64/hash, XSD y CDR (aceptado, rechazado, ajeno, corrupto, XXE y límites).
- La suite corregida NO se ejecutó en esta corrección local, incluida la segunda. Debe ejecutarse en el entorno Docker del padre, donde están disponibles `lxml` y `signxml` 5.2. No se declaran resultados no obtenidos.
- `requirements.txt` no incluye `lxml` ni `signxml`; no se instalaron dependencias localmente.

## Pendientes fuera de SUNAT1
- Transporte HTTP, credenciales, persistencia, migraciones, rutas y conciliación de estados.
- Reparar el host beta inventado en el `services/sunat_service.py` existente en una fase posterior.
- Ampliar validaciones tributarias específicas de SUNAT sin confundirlas con el XSD OASIS.
