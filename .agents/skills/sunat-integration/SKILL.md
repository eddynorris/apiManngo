---
name: sunat-integration
description: Guía de integración con APIs de SUNAT (GRE REST JSON, CPE SOAP XML y SIRE Compras/Ventas) y envoltorios privados (APISUNAT) para facturación electrónica en el backend de Flask y el bot de Telegram.
---

# Guía de Integración Técnica con SUNAT y APISUNAT

Este documento compila y organiza de manera óptima las especificaciones técnicas oficiales de la **SUNAT** (incluyendo el módulo de compras SIRE y el envío directo de Guías de Remisión) junto con la integración de APIs de terceros (como **APISUNAT**).

---

## 1. Guía de Remisión Electrónica (GRE) - Envío Directo a SUNAT (REST API)

Para emitir Guías de Remisión (GRE) de forma gratuita directamente a la SUNAT usando JSON, se utiliza la API REST oficial de SUNAT.

### A. Obtención de Credenciales
1. Ingresa a **SUNAT Operaciones en Línea (SOL)** con tu Clave SOL.
2. Registra una nueva aplicación API en la sección **Credenciales de API SUNAT**.
3. Obtendrás un `client_id` y `client_secret`.

### B. Flujo de Autenticación (OAuth 2.0)
Antes de realizar cualquier envío, debes solicitar un token de acceso temporal mediante una petición POST (autenticación básica del cliente + credenciales de usuario SOL):

*   **URL de Seguridad (Producción):** `https://api-seguridad.sunat.gob.pe/v1/clientessol/<client_id>/oauth2/token/`
*   **Método:** `POST`
*   **Cabeceras (Headers):**
    *   `Content-Type: application/x-www-form-urlencoded`
*   **Cuerpo (Body - urlencoded):**
    *   `grant_type`: `password`
    *   `scope`: `https://api-cpe.sunat.gob.pe`
    *   `client_id`: `<Tu client_id>`
    *   `client_secret`: `<Tu client_secret>`
    *   `username`: `<RUC + Usuario SOL de Emisor>`
    *   `password`: `<Clave SOL de Emisor>`

El servicio te responderá un JSON con el campo `access_token` y su tiempo de expiración.

### C. Estructura JSON para Envío de GRE (Dispatches)
Una vez obtenido el `access_token`, se envía la guía mediante un POST:

*   **URL (Producción):** `https://api-cpe.sunat.gob.pe/v1/contribuyente/gemini/despachos`
*   **Cabeceras (Headers):**
    *   `Authorization: Bearer <access_token>`
    *   `Content-Type: application/json`
*   **Cuerpo (JSON simplificado según estándar SUNAT):**
```json
{
  "serie": "T001",
  "numero": 12,
  "fechaEmision": "2026-07-14T08:00:00Z",
  "motivoTraslado": "01",
  "modalidadTransporte": "02",
  "unidadMedidaPeso": "KGM",
  "pesoBrutoTotal": 450.00,
  "remitente": {
    "numeroDocumento": "20601234567",
    "tipoDocumento": "6",
    "nombre": "EMPRESA EMISORA S.A.C."
  },
  "destinatario": {
    "numeroDocumento": "20509876543",
    "tipoDocumento": "6",
    "nombre": "CLIENTE DESTINO S.A.C."
  },
  "puntoPartida": {
    "direccion": "Av. Las Briquetas 123",
    "ubigeo": "030101"
  },
  "puntoLlegada": {
    "direccion": "Jr. Planta Andahuaylas 456",
    "ubigeo": "030102"
  },
  "detalles": [
    {
      "codigo": "P001",
      "descripcion": "Saco de carbón vegetal 20kg",
      "cantidad": 20.00,
      "unidadMedida": "NIU"
    },
    {
      "codigo": "P002",
      "descripcion": "Bolsa de briquetas 10kg",
      "cantidad": 5.00,
      "unidadMedida": "NIU"
    }
  ],
  "chofer": {
    "tipoDocumento": "1",
    "numeroDocumento": "44556677",
    "licencia": "Q44556677",
    "nombre": "JUAN PEREZ"
  },
  "vehiculo": {
    "placa": "ABC-123"
  }
}
```

---

## 2. Emisión de Facturas y Boletas (CPE SOAP XML)

La SUNAT exige que las Facturas y Boletas se emitan en formato XML firmadas digitalmente bajo el estándar UBL (Universal Business Language).

### A. Endpoints SOAP Oficiales (SUNAT)
*   **Beta/Pruebas:** `https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService`
*   **Producción (Envío de comprobantes):** `https://e-facturacion.sunat.gob.pe/ol-ti-itcpfegem/billService`
*   **Producción (Consultas CDR/Estado):** `https://e-facturacion.sunat.gob.pe/ol-it-wsconscpegem/billService`

### B. Requerimientos Clave
1.  **Certificado Digital (Firma Electrónica)**: Archivo `.pfx` o `.p12` utilizado para firmar el nodo del XML (`Signature`).
2.  **Conversión a ZIP**: El XML firmado se comprime en formato `.zip` antes de enviarse en la solicitud SOAP (`sendBill`).
3.  **Procesamiento de Respuesta (CDR)**: El servicio SOAP responde con un archivo ZIP que contiene el XML del **CDR (Constancia de Recepción)**, el cual debes descomprimir y validar para comprobar si fue **Aceptado** o **Rechazado** por SUNAT.

---

## 3. Integración con Terceros (APISUNAT.com)

Si deseas evitar la complejidad del XML SOAP y del Certificado Digital, se puede utilizar un PSE intermedio como **APISUNAT**.

### A. Endpoint REST de Emisión
*   **Método:** `POST`
*   **URL:** `https://api.apisunat.com/v1/documentos` (o URL del sandbox provisto)
*   **Cabeceras (Headers):**
    *   `Authorization: Bearer <API_KEY>`
    *   `Content-Type: application/json`

### B. Payload JSON de Ejemplo (Emisión de Comprobante)
```json
{
  "tipoDoc": "01",
  "serie": "F001",
  "correlativo": 102,
  "fechaEmision": "2026-07-14",
  "formaPago": {
    "moneda": "PEN",
    "tipo": "Contado"
  },
  "emisor": {
    "ruc": "20601234567",
    "razonSocial": "EMPRESA DE CARBON SAC",
    "direccion": "Av. Principal 100",
    "ubigeo": "150101"
  },
  "receptor": {
    "tipoDoc": "6",
    "numDoc": "20123456789",
    "razonSocial": "CLIENTE PRUEBA SAC",
    "direccion": "Av. Lima 200"
  },
  "detalles": [
    {
      "cantidad": 10,
      "unidadMedida": "NIU",
      "descripcion": "Saco de carbón vegetal 20kg",
      "valorUnitario": 20.00,
      "precioUnitario": 23.60,
      "subtotal": 200.00,
      "igv": 36.00,
      "total": 236.00,
      "tipoAfectacion": "10"
    }
  ],
  "totalVenta": 236.00,
  "totalIgv": 36.00,
  "totalSubtotal": 200.00
}
```

---

## 4. API SUNAT SIRE (Sistema Integrado de Registros Electrónicos)

El **SIRE Compras** y **SIRE Ventas** son APIs REST de SUNAT para consultar las propuestas de compras y ventas de los contribuyentes.

### A. Endpoint de Consultas de SIRE
*   **Base URL (SIRE):** `https://api-sire.sunat.gob.pe/v1/contribuyente/mige/sire`
*   **Endpoints Clave (SIRE Compras):**
    *   `GET /compras/propuesta/periodo/{periodo}/comprobantes` - Obtiene los comprobantes de la propuesta de SUNAT.
    *   `POST /compras/propuesta/periodo/{periodo}/aceptar` - Acepta la propuesta de compras generada.
    *   `POST /compras/propuesta/periodo/{periodo}/reemplazar` - Sube un archivo `.txt` comprimido en `.zip` usando el protocolo **TUS.io** para reemplazar la propuesta.

### B. Protocolo de Carga de Archivos TUS (SIRE)
Para enviar archivos pesados (como el reemplazo de la propuesta mediante txt), SUNAT implementa el protocolo abierto de reanudación de archivos **TUS (tus.io)**:
1.  **Petición inicial (POST)**: Informa el tamaño y nombre del archivo ZIP. SUNAT responde con un encabezado `Location` que contiene la URL exclusiva para la subida del archivo.
2.  **Petición de carga (PATCH)**: Sube los fragmentos binarios del ZIP a la URL asignada.
3.  **Petición final (POST de procesamiento)**: Indica a SUNAT que inicie la lectura e importación del archivo cargado.
