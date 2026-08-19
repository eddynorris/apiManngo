---
name: sunat-integration
description: Guía canónica y técnica de integración con los Servicios Oficiales de la SUNAT del Perú (GRE API REST JSON, Facturación Electrónica CPE SOAP UBL 2.1, SIRE RVIE/RCE y Consulta Integrada de Validez CPE). Basada estrictamente en normativa y documentación oficial de SUNAT.
---

# Guía Canónica de Integración Oficial con SUNAT (Perú)

Esta guía consolida la documentación técnica, esquemas de datos, flujos de autenticación y especificaciones legales y tributarias oficiales de la **Superintendencia Nacional de Aduanas y de Administración Tributaria (SUNAT)** de la República del Perú.

---

## 1. Marco Normativo y Fuentes Oficiales de Referencia

| Ámbito | Base Legal Oficial | Portal / Fuente Oficial |
| :--- | :--- | :--- |
| **SEE - Del Contribuyente (UBL 2.1)** | R.S. N° 097-2012/SUNAT y modif. | [cpe.sunat.gob.pe](https://cpe.sunat.gob.pe) |
| **SEE - OSE (Operador de Servicios)** | D.L. N° 1314 / R.S. N° 117-2017/SUNAT | [orientacion.sunat.gob.pe](https://orientacion.sunat.gob.pe) |
| **Guías de Remisión (GRE)** | R.S. N° 123-2022/SUNAT y R.S. N° 255-2022/SUNAT | [cpe.sunat.gob.pe/guias_remision](https://cpe.sunat.gob.pe/informacion_general/guias_remision) |
| **SIRE (RVIE y RCE)** | R.S. N° 112-2021/SUNAT, R.S. 040-2022 y R.S. 204-2023/SUNAT | [cpe.sunat.gob.pe/sire](https://cpe.sunat.gob.pe/sire) |
| **Plazo Máximo de Envío (Facturas)** | R.S. N° 000003-2023/SUNAT (Hasta el 3° día calendario) | [Resolución 000003-2023](https://www.sunat.gob.pe) |
| **Forma de Pago y Cuotas** | R.S. N° 193-2020/SUNAT (Contado / Crédito con vencimientos) | [Resolución 193-2020](https://www.sunat.gob.pe) |
| **Catálogos y Anexos UBL** | Anexo VIII de la R.S. N° 097-2012/SUNAT | [Tablas de Catálogos SUNAT](https://cpe.sunat.gob.pe) |

---

## 2. Autenticación y Seguridad (OAuth 2.0 SUNAT)

SUNAT utiliza el protocolo **OAuth 2.0 (Password Credentials Grant)** para sus APIs REST (GRE, SIRE y Consulta de Validez CPE).

### A. Obtención de Credenciales en Clave SOL
1. Ingresar a **SUNAT Operaciones en Línea (SOL)** con RUC, Usuario y Clave SOL.
2. Ir a: *Empresas -> Comprobantes de Pago -> Certificado Digital / Credenciales de API SUNAT*.
3. Registrar la aplicación indicando el nombre de la app y la URL de retorno (puede ser `https://localhost` si es backend).
4. El sistema generará:
   - `client_id` (Identificador de Cliente)
   - `client_secret` (Clave Secreta de API)

### B. Endpoints de Autenticación (Token URL)

| Entorno | URL del Servicio de Token |
| :--- | :--- |
| **Producción** | `https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/` |
| **Beta / Test (GRE)** | `https://gre-test.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token` |

### C. Solicitud de Access Token (HTTP POST)
* **Headers:** `Content-Type: application/x-www-form-urlencoded`
* **Body (x-www-form-urlencoded):**
  ```http
  grant_type=password
  scope=https://api-cpe.sunat.gob.pe
  client_id={CLIENT_ID}
  client_secret={CLIENT_SECRET}
  username={RUC}{USUARIO_SOL}
  password={CLAVE_SOL}
  ```
  *(Nota: para SIRE el scope es `https://api-sire.sunat.gob.pe`; para Consulta CPE es `https://api.sunat.gob.pe/v1/contribuyente/contribuyentes`).*

* **Respuesta Exitosa (JSON - HTTP 200):**
  ```json
  {
    "access_token": "eyJhbGciOiJSUzI1NiIs...",
    "token_type": "bearer",
    "expires_in": 3600
  }
  ```

---

## 3. Guía de Remisión Electrónica (GRE) - API REST JSON Oficial

Regulada por la **R.S. N° 123-2022/SUNAT**. Los contribuyentes emiten directamente la GRE Remitente (Tipo 09) y GRE Transportista (Tipo 31) mediante el servicio REST de SUNAT.

### A. Endpoints Oficiales de GRE

| Acción | Método | Endpoint Producción | Endpoint Beta |
| :--- | :--- | :--- | :--- |
| **Envío de GRE** | `POST` | `https://api-cpe.sunat.gob.pe/v1/contribuyente/gemini/despachos` | `https://gre-test.sunat.gob.pe/v1/contribuyente/gemini/despachos` |
| **Consulta Ticket** | `GET` | `https://api-cpe.sunat.gob.pe/v1/contribuyente/gemini/despachos/consultas/ticket/{numTicket}` | `https://gre-test.sunat.gob.pe/v1/contribuyente/gemini/despachos/consultas/ticket/{numTicket}` |
| **Consulta Estado/CDR**| `GET` | `https://api-cpe.sunat.gob.pe/v1/contribuyente/gemini/despachos/consultas/{ruc}-{codComp}-{serie}-{numero}` | `https://gre-test.sunat.gob.pe/v1/contribuyente/gemini/despachos/consultas/{ruc}-{codComp}-{serie}-{numero}` |

### B. Payload JSON Oficial de GRE Remitente (Tipo 09)

```json
{
  "version": "2022",
  "tipoDocumento": "09",
  "serie": "T001",
  "numero": 1045,
  "fechaEmision": "2026-08-16T09:30:00Z",
  "horaEmision": "09:30:00",
  "motivoTraslado": "01",
  "descripcionMotivo": "VENTA DE MERCADERIA",
  "modalidadTransporte": "02",
  "fechaInicioTraslado": "2026-08-16",
  "pesoBrutoTotal": 450.00,
  "unidadMedidaPeso": "KGM",
  "numBultos": 25,
  "remitente": {
    "tipoDocumento": "6",
    "numeroDocumento": "20601234567",
    "nombre": "EMPRESA INDUSTRIAL MANNGO S.A.C."
  },
  "destinatario": {
    "tipoDocumento": "6",
    "numeroDocumento": "20509876543",
    "nombre": "COMERCIAL DISTRIBUIDORA DEL SUR S.A.C."
  },
  "puntoPartida": {
    "ubigeo": "030303",
    "direccion": "CARRETERA PANAMERICANA KM 384, COLCABAMBA, AYMARAES, APURIMAC",
    "codigoEstablecimiento": "0000"
  },
  "puntoLlegada": {
    "ubigeo": "030102",
    "direccion": "AV. CIRCUNVALACION 450, TAMBURCO, ABANCAY, APURIMAC",
    "codigoEstablecimiento": "0000"
  },
  "chofer": {
    "tipoDocumento": "1",
    "numeroDocumento": "45678901",
    "nombre": "JUAN CARLOS PEREZ GOMEZ",
    "apellido": "PEREZ GOMEZ",
    "licencia": "Q45678901"
  },
  "vehiculo": {
    "placa": "ABC123",
    "autorizacionEspecial": ""
  },
  "detalles": [
    {
      "codigo": "PROD-001",
      "descripcion": "SACO DE CARBON VEGETAL 20KG",
      "unidadMedida": "NIU",
      "cantidad": 20.0,
      "codigoProductoSunat": "10171500"
    },
    {
      "codigo": "PROD-002",
      "descripcion": "BOLSA DE BRIQUETAS DE CARBON 10KG",
      "unidadMedida": "NIU",
      "cantidad": 5.0,
      "codigoProductoSunat": "10171500"
    }
  ]
}
```

### C. Flujo de Estados y Respuesta de Ticket GRE
1. **Envío POST:** SUNAT responde con HTTP 200 y el campo `numTicket` (ej. `2026081600001234`).
2. **Polling de Consulta Ticket:** Se consulta el endpoint `consultas/ticket/{numTicket}`:
   - `codRespuesta = "0"`: Guía procesada y **Aceptada**.
   - `codRespuesta = "98"`: En proceso (reintentar con backoff).
   - `codRespuesta = "99"`: **Rechazada**. Viene acompañado del objeto `error` con `codError` y `desError`.
3. **Descarga de CDR (Constancia de Recepción):** Contiene el XML del CDR firmado por SUNAT que certifica la validez legal del traslado.

---

## 4. Facturación Electrónica (CPE SOAP UBL 2.1)

Aplica para **Facturas (01)**, **Boletas de Venta (03)**, **Notas de Crédito (07)** y **Notas de Débito (08)** bajo el estándar OASIS UBL 2.1 y la **R.S. N° 097-2012/SUNAT**.

### A. Endpoints SOAP Oficiales (WSDL `billService`)

| Servicio | Entorno | Endpoint SOAP Oficial |
| :--- | :--- | :--- |
| **Emisión CPE (Facturas, Boletas, NC, ND)** | **Producción** | `https://e-facturacion.sunat.gob.pe/ol-ti-itcpfegem/billService` |
| **Emisión CPE (Beta / Homologación)** | **Beta** | `https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService` |
| **Consultas de Estado y CDR** | **Producción** | `https://e-facturacion.sunat.gob.pe/ol-it-wsconscpegem/billService` |
| **Retenciones y Percepciones (CRE/CPE)** | **Producción** | `https://e-facturacion.sunat.gob.pe/ol-ti-itemision-otroscpe-gem/billService` |
| **Guías SOAP (Versión Histórica)** | **Producción** | `https://e-guiaremision.sunat.gob.pe/ol-ti-itemision-otroscpe-gem/billService` |

### B. Métodos del Servicio SOAP
1. `sendBill(fileName, contentFile)`: Envío síncrono de un comprobante (Factura o Nota vinculada a Factura).
   - `fileName`: Nombre del archivo ZIP (`{RUC}-{TIPO}-{SERIE}-{CORRELATIVO}.zip`).
   - `contentFile`: Archivo ZIP codificado en Base64 que contiene el XML firmado digitalmente.
   - Retorna: `applicationResponse` (CDR ZIP en Base64).
2. `sendSummary(fileName, contentFile)`: Envío asíncrono de Resúmenes Diarios de Boletas (`RC-YYYYMMDD-1.zip`) o Comunicaciones de Baja (`RA-YYYYMMDD-1.zip`).
   - Retorna: `ticket`.
3. `getStatus(ticket)`: Consulta del estado de procesamiento del ticket de Resumen Diario o Baja.
4. `getStatusCdr(rucComprobante, tipoComprobante, serieComprobante, numeroComprobante)`: Descarga el CDR de un comprobante emitido previamente.

### C. Reglas Críticas de Cumplimiento Legal (SUNAT)

1. **Plazo Máximo de Envío a SUNAT u OSE (R.S. 000003-2023/SUNAT):**
   - Las **Facturas Electrónicas** y sus **Notas de Crédito/Débito** deben enviarse a SUNAT o al OSE en un plazo máximo de **hasta tres (3) días calendario**, contados desde el día siguiente a la fecha de emisión.
   - Todo comprobante enviado fuera de este plazo es **RECHAZADO automáticamente** por SUNAT y carece de validez tributaria.

2. **Forma de Pago y Detalle de Cuotas (R.S. 193-2020/SUNAT):**
   - Debe declararse obligatoriamente `Contado` o `Crédito` en el XML (`cac:PaymentTerms`).
   - Si es **Crédito**, debe especificarse el `Monto Neto Pendiente de Pago` y el desglose de cada cuota con su número correlativo, monto a pagar y fecha de vencimiento (`PaymentDueDate`).

3. **Operaciones Sujetas al SPOT (Detracciones - D.L. N° 940):**
   - Para operaciones afectas al Sistema de Pago de Obligaciones Tributarias (SPOT), se debe incluir en el XML:
     - Código de Bien o Servicio Sujeto a Detracción (Catálogo 54).
     - Porcentaje de Detracción aplicable.
     - Monto total de la detracción en PEN.
     - Número de Cuenta Corriente del Banco de la Nación del emisor.

4. **Estructura de Firma Digital (XMLDSig):**
   - El XML debe estar firmado con certificado digital X.509 v3 (formato `.pfx` o `.p12`) emitido por una entidad de certificación acreditada ante INDECOPI.
   - Algoritmo de firma: `RSA-SHA256`.
   - La firma se inserta en el nodo `ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/ds:Signature`.
   - Se debe calcular y almacenar el `DigestValue` (Hash SHA-256) para la representación impresa (PDF) y el código QR.

---

## 5. Sistema Integrado de Registros Electrónicos (SIRE)

Regulado por la **R.S. N° 112-2021/SUNAT**, **R.S. N° 040-2022/SUNAT** y **R.S. N° 204-2023/SUNAT**. Reemplaza definitivamente al PLE y al Portal para el Registro de Ventas e Ingresos Electrónico (RVIE) y el Registro de Compras Electrónico (RCE).

### A. Endpoints Base Oficiales
* **Base URL:** `https://api-sire.sunat.gob.pe/v1/contribuyente/mige/sire`
* **Scope OAuth 2.0:** `https://api-sire.sunat.gob.pe`

### B. Módulo RVIE (Ventas)
* `GET /rvie/propuesta/periodo/{periodo}/comprobantes`: Consulta la propuesta de ventas generada por SUNAT (Parámetros: `numPagina`, `tamPagina`).
* `GET /rvie/propuesta/periodo/{periodo}/resumen`: Obtiene los totales acumulados de la propuesta de ventas.
* `POST /rvie/propuesta/periodo/{periodo}/aceptar`: Acepta formalmente la propuesta de RVIE emitida por SUNAT.
* `POST /rvie/propuesta/periodo/{periodo}/reemplazar`: Reemplaza la propuesta mediante archivo ZIP cargado vía protocolo TUS.
* `POST /rvie/resumen/periodo/{periodo}/generar`: Genera el registro preliminar de ventas.

### C. Módulo RCE (Compras)
* `GET /rce/propuesta/periodo/{periodo}/comprobantes`: Consulta los comprobantes de compras propuestos por SUNAT.
* `GET /rce/propuesta/periodo/{periodo}/resumen`: Resumen de bases imponibles y créditos fiscales propuestos.
* `POST /rce/propuesta/periodo/{periodo}/aceptar`: Acepta la propuesta de compras.
* `POST /rce/propuesta/periodo/{periodo}/complementar`: Añade comprobantes no incluidos en la propuesta de SUNAT (ej. recibos físicos de contingencia, DUAs de importación, notas de débito especiales).
* `POST /rce/propuesta/periodo/{periodo}/excluir`: Excluye comprobantes de la propuesta de compras (para postergar crédito fiscal).
* `POST /rce/propuesta/periodo/{periodo}/reemplazar`: Reemplaza íntegramente la propuesta de compras.
* `POST /rce/resumen/periodo/{periodo}/generar`: Genera el preliminar del registro de compras.

### D. Carga Masiva de Archivos mediante Protocolo TUS (Tus.io v1.0.0)
Para archivos de gran volumen (reemplazo o complementación en formato `.txt` comprimido en `.zip`):
1. **Creación de Subida (POST):**
   - Header: `Tus-Resumable: 1.0.0`
   - Header: `Upload-Length: {tamaño_en_bytes}`
   - Header: `Upload-Metadata: filename {base64_filename},filetype {base64_mimetype}`
   - SUNAT responde HTTP 201 con el header `Location: {upload_url}`.
2. **Subida de Chunks Binarios (PATCH):**
   - Endpoint: `{upload_url}`
   - Header: `Tus-Resumable: 1.0.0`
   - Header: `Upload-Offset: 0`
   - Header: `Content-Type: application/offset+octet-stream`
   - Body: Bytes binarios del archivo ZIP.
3. **Consulta de Ticket Masivo:**
   - `GET https://api-sire.sunat.gob.pe/v1/contribuyente/mige/sire/masivo/consulta/ticket/{numTicket}`

---

## 6. Consulta Integrada de Validez de Comprobantes de Pago (API REST)

Permite verificar en tiempo real si un comprobante de compra o venta existe, está aceptado por SUNAT y si el emisor se encuentra en estado ACTIVO y condición HABIDO.

* **Endpoint:** `POST https://api.sunat.gob.pe/v1/contribuyente/contribuyentes/{numRuc}/validarcpe`
* **Scope OAuth 2.0:** `https://api.sunat.gob.pe/v1/contribuyente/contribuyentes`
* **Payload JSON:**
  ```json
  {
    "numRuc": "20601234567",
    "codComp": "01",
    "numeroSerie": "F001",
    "numero": "1250",
    "fechaEmision": "16/08/2026",
    "monto": "1180.00"
  }
  ```
* **Respuesta de Validación:**
  - `data.estadoCp`:
    - `0`: NO EXISTE (Comprobante no informado o no registrado).
    - `1`: ACEPTADO (Comprobante válido y aceptado en SUNAT).
    - `2`: ANULADO (Comprobante comunicado de baja o anulado con NC).
    - `3`: AUTORIZADO (Comprobante con autorización de imprenta).
  - `data.estadoRuc`: `00` (Activo).
  - `data.condDomiRuc`: `00` (Habido).

---

## 7. Catálogos Oficiales de Códigos SUNAT (Anexo VIII)

| Catálogo | Descripción | Valores Más Frecuentes |
| :--- | :--- | :--- |
| **01** | Tipo de Comprobante | `01`: Factura, `03`: Boleta de Venta, `07`: Nota de Crédito, `08`: Nota de Débito, `09`: GRE Remitente, `20`: Retención, `31`: GRE Transportista, `40`: Percepción |
| **02** | Código de Moneda (ISO 4217) | `PEN`: Soles, `USD`: Dólares Americanos, `EUR`: Euros |
| **03** | Unidad de Medida (UN/ECE rec 20) | `NIU`: Unidad (Bienes), `ZZ`: Unidad de Servicio, `KGM`: Kilogramos, `TNE`: Toneladas, `LTR`: Litros, `BX`: Caja, `MTR`: Metros |
| **06** | Tipo de Doc. Identidad | `0`: Doc. Trib. No Domiciliado / Sin Documento, `1`: DNI, `4`: Carnet de Extranjería, `6`: RUC, `7`: Pasaporte, `A`: Cédula Diplomática |
| **07** | Tipo de Afectación al IGV | `10`: Gravado - Operación Onerosa (18% o 10%), `11`-`17`: Gravado Retiro/Bonificación/Premio, `20`: Exonerado - Operación Onerosa, `30`: Inafecto - Operación Onerosa, `40`: Exportación de Bienes/Servicios |
| **09** | Tipo de Nota de Crédito | `01`: Anulación de la operación, `02`: Anulación por error en el RUC, `03`: Corrección por error en la descripción, `04`: Descuento global, `05`: Descuento por ítem, `06`: Devolución total, `07`: Devolución por ítem, `08`: Bonificación, `09`: Disminución en el valor, `10`: Otros conceptos, `13`: Ajustes de importes y/o fechas de pago |
| **10** | Tipo de Nota de Débito | `01`: Intereses por mora, `02`: Aumento en el valor, `03`: Penalidades / otros conceptos |
| **12** | Códigos de Tributos | `1000`: IGV (VAT), `1016`: IVAP (VAT), `2000`: ISC (EXC), `7152`: ICBPER (Impuesto a las Bolsas Plásticas), `9995`: Exportación (FRE), `9996`: Gratuito (FRE), `9997`: Exonerado (VAT), `9998`: Inafecto (FRE), `9999`: Otros (OTH) |
| **17** | Tipo de Operación | `0101`: Venta Interna, `0102`: Exportación, `1001`: Sujeta a Detracción, `1002`: Detracción Recursos Hidrobiológicos, `1004`: Detracción Transporte de Carga, `2001`: Percepción |
| **18** | Modalidad de Traslado (GRE) | `01`: Transporte Público (Empresa de transporte con RUC), `02`: Transporte Privado (Vehículo y conductor propio/alquilado) |
| **20** | Motivo de Traslado (GRE) | `01`: Venta, `02`: Compra, `04`: Traslado entre establecimientos de la misma empresa, `08`: Importación, `09`: Exportación, `13`: Otros, `14`: Venta sujeta a confirmación del comprador, `18`: Emisor itinerante, `19`: Traslado a zona primaria |
| **51** | Tipo de Facturación / Operación | `0101`: Venta Interna, `0102`: Exportación, `0103`: No Domiciliados, `0104`: Anticipos, `0200`: Servicios |
| **54** | Códigos de Bienes/Servicios SPOT | `001`: Azúcar (10%), `004`: Recursos Hidrobiológicos (4%), `019`: Madera (4%), `020`: Oro (1.5%), `022`: Harina de pescado (4%), `025`: Fabricación de bienes por encargo (10%), `027`: Demás servicios gravados con el IGV (12%), `037`: Transporte de carga (4%) |

---

## 8. Tabla de Códigos de Error y Excepciones SUNAT

| Rango de Códigos | Tipo de Incidencia | Causa / Acción Correctiva |
| :--- | :--- | :--- |
| **0100 – 0199** | Error de Autenticación / Conexión | Credenciales SOL incorrectas, Client ID inactivo, usuario sin permisos en el portal SOL. |
| **1000 – 1999** | Excepciones del Servidor SUNAT | Problemas internos temporales de la plataforma SUNAT. Requiere reintento con backoff exponencial. |
| **2000 – 2999** | Errores de Validación de Estructura / Negocio | XML mal formado, schema XSD inválido, RUC no activo/habido, código de afectación incongruente, serie no autorizada. |
| **2371** | Envío fuera de plazo legal | La Factura superó los 3 días calendario posteriores a la fecha de emisión (R.S. 000003-2023). Debe emitirse con fecha actual. |
| **3000 – 3999** | Errores en Resúmenes Diarios / Bajas | Inconsistencias en los rangos de comprobantes informados o fecha de baja inválida. |
| **4000+** | Observaciones del CDR | El comprobante fue **Aceptado con Observaciones**. Se debe subsanar en futuras emisiones para evitar multas. |

---

## 9. Arquitectura y Buenas Prácticas de Implementación en Backend

1. **Gestión de Token OAuth en Memoria / Redis:**
   - Almacenar el `access_token` junto con `expires_at`.
   - Renovar proactivamente el token 5 minutos antes de su vencimiento para evitar rechazos HTTP 401 en operaciones críticas.
2. **Idempotencia y Correlatividad:**
   - Mantener el control de correlativos en transacciones atómicas (`SELECT ... FOR UPDATE`) en base de datos PostgreSQL para evitar huecos en la serie.
3. **Almacenamiento Confiable de XML y CDR:**
   - El XML firmado y el CDR ZIP devuelto por SUNAT deben resguardarse de forma inmutable en almacenamiento de objetos (Amazon S3 o Google Cloud Storage).
4. **Cola Asíncrona para GRE y Resúmenes Diarios:**
   - Utilizar tareas en background (Celery, RQ o colas en DB) para consultar los tickets de SUNAT (`status/{numTicket}`) de manera no bloqueante.
5. **Circuit Breaker y Logs de Auditoría:**
   - Si los endpoints de SUNAT responden HTTP 500/503 de forma consecutiva, activar mecanismo de contingencia para resguardar la emisión y reintentar automáticamente cuando el servicio se restablezca.
