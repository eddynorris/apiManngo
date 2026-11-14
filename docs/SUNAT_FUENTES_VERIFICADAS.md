# SUNAT: fuentes revisadas y contrato para implementación local

## Alcance

Revisión documental de la integración del proyecto. Ninguna emisión, autenticación, consulta de ticket, Storage ni BD remota se ha ejecutado. Las pruebas de implementación deben ser locales, con transporte HTTP falso y certificado sintético. Esto NO certifica habilitación fiscal, aceptación por SUNAT ni cumplimiento de todas las reglas del emisor.

## Correcciones al skill y al código

- No confundir OSE con PSE ni decidir el sistema del emisor por una regla universal de «75 UIT B2B». La R.S. 044-2019 consultada contiene el caso de principales contribuyentes y 300 UIT; la obligación concreta exige verificar designación, periodos y normativa aplicable del RUC antes de activación.[3]
- El importe de una boleta no determina por sí solo que deba enviarse individualmente por `sendBill`. SUNAT documenta boleta individual o resumen diario; la identificación del adquirente y la modalidad de envío son decisiones diferentes. No implementar la regla antigua «≥700 => sendBill».[4]
- Una configuración XAdES que haya pasado una factura beta no demuestra que XAdES 1.3.2 sea el único formato legal, ni que todo XMLDSig sea inválido. La guía oficial describe XMLDSig y XAdES; mantener una configuración compatible, probar firma/digest localmente y no extrapolar un rechazo a todas las firmas.[6]
- `encoding='UTF-8'` no agrega BOM por ser Windows. Evitar explícitamente UTF-8-SIG/BOM y no modificar el documento después de firmarlo.
- No se ha confirmado un servicio público beta oficial para la GRE REST nueva. No inventar `gre-test.sunat.gob.pe`, no usar credenciales MODDATOS en producción ni enrutar un ambiente llamado beta al host de producción. La consulta de documentación pública es distinta a probar los endpoints fiscales. El manual GRE publicado por el portal no pudo recuperarse en esta revisión: no presentar el contrato completo siguiente como comprobación primaria actual.

## Contrato técnico GRE disponible (fuente secundaria, identificado como tal)

El OpenAPI de Greenter enlaza al manual de SUNAT y describe los endpoints y el ZIP base64 + SHA-256. Es una referencia de implementación, no una resolución ni prueba de aceptación real.[5]

```text
TOKEN POST https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/
  grant_type=password
  scope=https://api-cpe.sunat.gob.pe
  username=RUC+USUARIO_SOL
ENVÍO POST https://api-cpe.sunat.gob.pe/v1/contribuyente/gem/comprobantes/{nombre-sin-ext}
  {"archivo":{"nomArchivo":"RUC-09-T001-N.zip","arcGreZip":"BASE64","hashZip":"SHA256_HEX"}}
CONSULTA GET https://api-cpe.sunat.gob.pe/v1/contribuyente/gem/comprobantes/envios/{ticket}
  codRespuesta=98 => en proceso; 99 => error; 0 => procesado (leer CDR).
```

El contrato declara nombre de ZIP RUC-TIPO-SERIE-NÚMERO, `numTicket`, `arcCdr` e `indCdrGenerado`; un ticket no es una aceptación y deben interpretarse también los códigos del CDR.[5]

## Reglas de implementación que son decisiones de seguridad del proyecto

1. `SUNAT_ENVIO_HABILITADO` false por defecto y modo `local` por defecto. Producción requiere ambas opciones explícitas y configuración completa; durante este encargo NUNCA activarlas ni enviar. Ambientes desconocidos o beta GRE no confirmada fallan cerrados; no convertirlos a prod silenciosamente.
2. No usar `/gemini`, `/despachos` ni JSON plano de una guía como sustituto de XML UBL `DespatchAdvice` firmado + ZIP. XML debe construirse con lxml/ElementTree, no interpolación sin escape. Validación de esquema cuando estén disponibles XSD locales; no llamar XSD-validado a un simple parseo.
3. Datos explícitos: emisor real, razón social, RUC/documento y nombre de destino, partida/llegada y ubigeos, fecha/hora de emisión y fecha de inicio del traslado, motivo, modalidad, placa y conductor/licencia cuando corresponda, bienes con unidad/cantidad/peso finitos positivos. Sin nombre «Cliente Externo», peso genérico 20 kg, DNI/placa/dirección/razón social de fallback ni licencia inventada como Q+DNI. Si falta un campo, error accionable sin enviar ni reservar un correlativo.
4. Serie GRE remitente comienza T y tiene 4 caracteres alfanuméricos; número 1..99999999 asignado en persistencia, no `int(time.time()) % ...`. El alcance inicial implementa GRE remitente con transporte privado; otros tipos/modalidades se rechazan como no soportados hasta estar implementados y verificados. No extrapolar T a GRE transportista.
5. Persistir documento, referencia idempotente, identidad de actor, estados y ticket. Identificador repetido y payload idéntico recupera la misma operación; distinto payload => 409. Timeout de envío => estado incierto para conciliación, no reemisión automática con otro número.
6. Separar preparado, enviado/recibido, en proceso, aceptado, rechazado y envío incierto. SOLO CDR válido que corresponda a tipo/serie/número del documento y `ResponseCode=0` permite aceptación; notas/observaciones no se borran. Códigos desconocidos, 98, CDR ausente/corrupto/ajeno, HTTP200 vacío y texto «procesado» no son aceptación.
7. Leer CDR con parser XML sin entidades externas/DTD/red, ZIP limitado en bytes/entradas/ratio y selección explícita del XML (saltar carpetas). No guardar secretos en XML ni responses/logs. Probar ZIP bomb benigno/límites, XML XXE, base64 inválido y SOAP Fault si se añade CPE.
8. El certificado de pruebas se genera localmente. Sin certificado real no se etiqueta una guía firmada como válida para producción. Clave del PFX nunca por chat ni logs; plantilla `.env.example` sin secretos.
9. Transporte HTTP inyectable con timeouts, sin redirects a hosts arbitrarios, errores públicos redactados y token cacheado solo por periodo válido. Pruebas con dobles que registran URL/payload; jamás red fiscal real.
10. No emitir documentos fiscales con fecha antigua para recuperar ventas históricas. Captura operativa y emisión fiscal son procesos distintos. Revisar con contador los comprobantes pendientes o regularizaciones; no inventar una solución tributaria.

## Pruebas de aceptación locales

- Rechazo de envío deshabilitado/ambiente desconocido antes de HTTP.
- Preparación explícita de GRE sin valores inventados, escape XML, firma y verificación del digest con certificado sintético, ZIP/nombre/base64/hash coherentes.
- Request OAuth/envío/ticket exactos capturados por transporte falso; 401/422/timeout/200 vacío.
- Ticket con 98 o sin CDR nunca aceptado. CDR de rechazo y CDR ajeno nunca aceptados. 0 + CDR correcto acepta y persiste el artefacto.
- Reintento/callback repetido no consume correlativo nuevo ni duplica documento; fallo SQL no deja documento/correlativo parcialmente confirmado.
- Telegram solo usa esta capa después de validar actor/contexto; smoke test real del bot al final y pendiente si no hay Gemini.

## Fuentes y límites

Ver lista generada al final. Las fuentes oficiales y la secundaria están diferenciadas arriba. La habilitación del emisor, normativa consolidada aplicable, certificado, correlativos iniciales y una emisión real con CDR quedan pendientes de activación humana; no bloquean las pruebas locales de código.

## Sources

[3] https://www.sunat.gob.pe/legislacion/superin/2019/044-2019.pdf
    > "cuyos ingresos anuales en el año anterior sean iguales o mayores a 300 UIT."
[4] https://cpe.sunat.gob.pe/informacion_general/operador_servicios_electronicos
    > "Boleta de venta (Individual o Resumen diario)."
[5] https://github.com/thegreenter/gre-api/blob/main/openapi.yaml
    > "Nombre del archivo zip enviado. Estructura: RRRRRRRRRRR-TT-SSSS-NNNNNNNN.zip"
[6] https://cpe.sunat.gob.pe/sites/default/files/inline-files/guia%2Bxml%2Bboleta%2Bversion%202-1%2B1%2B0_0_0%20%282%29.pdf
    > "Dentro de la firma electrónica en formato XML, existen diferentes “subtipos de formatos”, dentro de los cuales destacan por encima de todos el XML Dsig y la variante de este, el XML Advanced Electronic Signatures (XAdES)."
