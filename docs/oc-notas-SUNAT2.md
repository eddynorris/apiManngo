# Notas SUNAT2 — transporte GRE remitente seguro

## Alcance

- Subfase SUNAT2: reemplazo del transporte falso (`/gemini/despachos`, `gre-test`) por el contrato REST documentado para GRE remitente tipo `09`, modalidad privada `02`.
- Sin producción, sin beta fiscal, sin dotenv real, sin credenciales/certificados reales, sin red, sin instalación de dependencias, sin commits y sin persistencia.
- Solo se modificaron `services/sunat_service.py`, `tests/test_sunat_transport.py` y este archivo.
- `services/sunat_xml.py` se usó como interfaz estable de solo lectura: `PreparedGre`, `GrePackage`, `package_gre`, `build_despatch_xml`, `interpret_ticket_status`, `validate_gre_data` y sus errores/namespaces. No se reescribió XML, firma ni XSD.

## Interfaz estable para la siguiente capa durable

- `SunatConfig`: `ambiente`, `envio_habilitado`, `client_id`, `client_secret`, `ruc`, `usuario_sol`, `clave_sol`, `timeout_seconds`.
  - `SunatConfig.from_env()` lee `SUNAT_AMBIENTE` (default `local`), `SUNAT_ENVIO_HABILITADO` (default `false`), `MANNGO_ID_SUNAT`, `MANNGO_CLAVE_SUNAT`, `SUNAT_RUC`, `SUNAT_USUARIO_SOL` y `SUNAT_CLAVE_SOL`.
  - Los secretos no se recortan ni transforman silenciosamente; se rechazan valores ausentes.
- `SunatService(config=None, http=None, clock=None)`: construcción sin red. `http` requiere `post(url, **kwargs)`/`get(url, **kwargs)`; `clock` requiere `monotonic()`.
- `obtener_access_token() -> str`
- `emitir_guia_remision(prepared: PreparedGre) -> GreEnvioResult`
- `consultar_estado_ticket(ticket, expected: GreRemitenteData) -> TicketResult`
- `GreEnvioResult`: `estado` (`"enviado"`), `document_id`, `filename`, `ticket`, `http_status`, `accepted=False`.
- Errores públicos: `SunatError`, `SunatConfigError`, `SunatDisabledError`, `SunatPreparationError`, `SunatAuthError`, `SunatTokenError`, `SunatResponseError`, `SunatProtocolError`, `SunatTransportError`, `SunatTransmissionUncertain`.
- Se conserva la instancia global `sunat_service = SunatService()`; su creación solo lee configuración y no contacta la red.

La siguiente capa durable debe conservar documento, ticket, estados, CDR/observaciones y la marca de envío incierto. Este módulo no asigna correlativos, no persiste y no decide reintentos.

## Decisiones implementadas

- Cierre por defecto: ninguna llamada HTTP (ni OAuth) si `ambiente != "produccion"` o `envio_habilitado != true` o faltan credenciales. `beta` y ambientes desconocidos fallan cerrados; no se usa `gre-test` ni se inventa otro host.
- Endpoints fijos:
  - Token: `POST https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/`
  - Envío: `POST https://api-cpe.sunat.gob.pe/v1/contribuyente/gem/comprobantes/{nombre_sin_ext}`
  - Consulta: `GET https://api-cpe.sunat.gob.pe/v1/contribuyente/gem/comprobantes/envios/{ticket}`
- OAuth: `grant_type=password`, `scope=https://api-cpe.sunat.gob.pe`, `username=RUC+USUARIO_SOL`, credenciales del cliente en formulario.
- Envío: JSON exclusivamente `{"archivo": {"nomArchivo", "arcGreZip", "hashZip"}}`; no se acepta ni se construye JSON de negocio.
- Verificación local previa a OAuth/POST: tipo `09`, modalidad `02`, RUC del documento igual al configurado, `prepared.xml_bytes` igual al XML unsigned esperado y comparación canónica del contenido completo del firmado contra ese unsigned. Para comparar se retira únicamente `ext:UBLExtensions` del árbol parseado; los bytes firmados originales no se modifican y son los que se empaquetan/envían. El parser local rechaza DTD, entidades externas y red. Después se recomputa `package_gre` (ZIP, XML interno, nombre, base64 y SHA-256). Cualquier incoherencia lanza `SunatPreparationError` sin HTTP.
- La comparación de contenido no valida criptográficamente la firma ni la cadena de confianza/CA del certificado; esa integridad y confianza quedan documentadas para la siguiente capa durable.
- Token cacheado con `monotonic`, token no vacío y `expires_in` entero finito positivo (no booleano, no texto, no fallback `3600`). Margen controlado para vigencias cortas.
- HTTP propio sin proxy heredado (`Session.trust_env=False`), `allow_redirects=False`, timeout finito y sin reintento automático de POST.
- `200/201` de envío exige `numTicket` no vacío; se devuelve `estado="enviado"` y `accepted=False`. Un ticket no es aceptación.
- Timeout o fallo de transporte durante el POST lanza `SunatTransmissionUncertain`; no se reintenta. Después del POST, `5xx`, redirect, estado ilegible, `2xx` sin ticket, JSON roto o `numTicket` ausente/vacío también son resultado incierto (`SunatTransmissionUncertain`), no ausencia de emisión. `422` conserva un error de respuesta definitivo y `401` conserva `SunatAuthError`; un fallo OAuth ocurre antes de enviar la GRE.
- Respuestas públicas redactadas: no se incluyen cuerpos, `desError`, tokens, claves ni excepciones crudas. En `422` solo se expone un código numérico acotado cuando existe.
- Consulta: `98` pendiente, `99` error. `0` solo acepta si `interpret_ticket_status` recibe un CDR válido del mismo RUC/tipo/serie/número con `ResponseCode=0`. CDR ausente, corrupto, ajeno, código desconocido o texto como «procesado» nunca aceptan. El `CdrResult` conserva XML, observaciones y notas.
- La consulta exige `expected: GreRemitenteData` validado y con RUC igual al configurado; no permite clasificar un CDR ajeno como propio.

## Pruebas escritas

`tests/test_sunat_transport.py` cubre, con `FakeHTTP`/`FakeClock` y certificado sintético:

- Control positivo de configuración explícita de producción + envío habilitado + credenciales sintéticas: URL, formulario OAuth, headers, timeout, `allow_redirects=False`, JSON de archivo exacto y ticket devuelto sin aceptación.
- `local`, `beta`, ambiente desconocido, envío deshabilitado y credenciales ausentes/incompletas: cero llamadas HTTP.
- Constructor sin credenciales locales, instancia global import-safe y sesión propia con `trust_env=False`.
- Cache de token con reloj monotónico y margen; vigencias inválidas/ausentes/booleanas/texto sin fallback ni envío.
- OAuth `401` redactado sin GRE enviada; JSON roto; `422` con solo código permitido; timeout de envío con una única llamada y `SunatTransmissionUncertain`.
- Envío con `5xx`, redirect, estado ilegible, `204`, JSON roto o sin `numTicket`: `SunatTransmissionUncertain`, sin resultado exitoso y con un máximo de un POST de emisión.
- Rechazo previo a HTTP de hash, base64, ZIP, XML unsigned, RUC y tipo de documento incoherentes. Regresión específica: mismo serie/número y `DocumentID`, pero firmado con distinta cantidad o RUC, con paquete/hash coherente con `prepared.data`, se rechaza antes de cualquier HTTP.
- Consulta `98`, `99`, `0` con CDR correcto; CDR ausente, vacío, corrupto, ajeno por documento/tipo/RUC, rechazo `2326`, código desconocido y ticket inválido o eco distinto. Las respuestas GET usan la cola `get_responses` de `FakeHTTP`, no la cola POST.

## Ejecución y límites reales

- **No ejecutado**: las pruebas no se corrieron en este encargo. Requieren `lxml`, `cryptography` y `signxml`, y el contrato indica ejecutarlas en el entorno Docker del padre, no instalar dependencias localmente.
- Comando previsto, no ejecutado: `python scripts/run_tests_offline.py tests/test_sunat_transport.py -v` dentro del entorno Docker con `lxml`/`signxml`.
- El padre informó previamente **59 tests XML/firma/XSD aprobados** con `signxml` 5.2; esos tests no se reejecutaron aquí.
- Ejecución real del padre en Docker antes de esta corrección: **111 passed, 12 failed** en XML+transporte. Los 12 fallos fueron consultas: los tests llenaban la cola POST de `FakeHTTP` con `[token, query]`, dejando vacía la cola GET. Se corrigieron los fixtures propios a `post_responses=[token]` y `get_responses=[query]`; no se convirtió GET en POST ni se relajaron aserciones.
- La corrección de esta ronda **no se ejecutó** localmente; queda para la ejecución Docker del padre.
- No se declaran resultados de ejecución no obtenidos.
- Límites: no hay persistencia, correlativos, idempotencia, migraciones ni conciliación de envíos inciertos (corresponden a la siguiente capa). No se confirmó un servicio beta GRE oficial; por eso beta falla cerrado.
- `telegram/handlers/guia_sunat.py` sigue usando el contrato JSON antiguo y no se modificó por estar fuera de propiedad de este lote; deberá adaptarse cuando la capa durable consuma esta interfaz.
- Recordatorio de cierre: el usuario debe cambiar sus secretos reales fuera del repositorio; este lote no leyó ni escribió dotenv, certificados ni credenciales reales.
