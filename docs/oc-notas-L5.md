# Notas L5 — Identidad Telegram y contención de release (A07/A08)

## Estado

- **A07: locallyfixed** (`locally_fixed`) en fuente y tests. Pendiente de verificación independiente del padre en Docker.
- **A08: mitigated_release_disabled**. No se declara funcional ni “fixed”: la entrega de Telegram queda deshabilitada en esta versión. No se implementó una cola a medias ni fiscalización ficticia.
- **No ejecutado por este agente:** no hay resultados reales de pytest. El entorno de esta sesión no expone shell; no se lanzó app, pytest, Docker, migraciones, PostgreSQL, Telegram real, Gemini ni SUNAT.

## Alcance respetado

- Solo se editaron `telegram/router.py`, `resources/telegram_webhook_resource.py`, los tests nuevos permitidos y este documento.
- No se tocaron `models.py`, `app.py`, `common.py`, `telegram/handlers/venta.py`, `services/` ni el arreglo L4 de vinculación por ID. El contrato de `/telegram/vincular` se mantiene para unit tests, pero no implica entrega operativa.
- No se usó producción, dotenv real, secretos reales, red de negocio, MCP, Storage, Gemini/Telegram/SUNAT reales, ni git commit/push/reset/clean/stash. No se modificó ningún `.env`.

## A07 · Identidad personal en Telegram

Cambios en `telegram/router.py`:

1. Se añadió una validación común de identidad privada: `chat.type == 'private'`, `from.id` y `chat.id` enteros positivos, y `from.id == chat.id`.
2. `handle_message` valida antes de leer texto, resolver usuario, intentar vinculación, invocar Gemini o cualquier manejador. Grupos, supergrupos, canales, chats negativos, remitentes ausentes o distintos del chat se rechazan sin resolver actor ni actuar.
3. `handle_callback_query` usa `callback_query.from.id` comparado contra `callback_query.message.chat.id`; no usa el autor del mensaje del bot (`message.from`). Exige el mismo chat privado positivo y descarta callbacks sin `message` o sin remitente.
4. La búsqueda de usuario vinculado sigue siendo la canónica por `Users.query.filter_by(telegram_chat_id=<remitente positivo>)`. No se añadió ninguna inferencia de rol `admin` por grupo ni por chat compartido.
5. Los rechazos no envían mensajes a grupos ni ejecutan manejadores; solo registran una advertencia genérica sin IDs sensibles.

Cobertura nueva en `tests/test_telegram_actor_identity.py` (dobles locales, sin red):

- mensajes de grupo/supergrupo, chat negativo, remitente ausente, remitente distinto o `chat.type` ausente: no resuelven actor y no llaman a Telegram;
- callback de grupo, chat negativo, remitente ausente, remitente distinto o sin mensaje: no resuelven actor, no escriben y no ejecutan cancelación;
- caso positivo de mensaje privado: la consulta se hace por el ID remitente positivo;
- caso positivo de callback: se usa `callback.from` y no `message.from` (autor bot);
- regresión con BD local desechable: un callback de grupo no modifica `telegram_context` de un usuario vinculado a ese chat negativo; un mensaje de grupo no envía nada.

## A08 · Contención explícita de release

Cambios en `resources/telegram_webhook_resource.py`:

1. `TelegramWebhookResource.post` devuelve **503** con `{"error": "IntegrationUnavailable"}` inmediatamente, para **todos** los updates.
2. La respuesta 503 ocurre antes de validar secretos de URL/cabecera, deduplicar `update_id`, tocar la BD, encolar o invocar `TelegramRouter`. No se puede habilitar con un flag y no se devuelve un 200 ficticio de “duplicado”.
3. Se eliminó el registro del token de URL en rechazos. La respuesta no contiene secretos. La advertencia es genérica y no incluye token ni cabecera.
4. Se conservan `executor`, `_process_update_async` y `_marcar_update_procesado` únicamente como **legado inactivo documentado**: `post` no los usa. No hay `executor.submit` en el flujo HTTP. Reactivarlos exige cola durable, idempotencia de efectos de negocio, estados y workers; no forma parte de este lote.
5. `/telegram/vincular` no se modificó; su contrato de código sigue disponible para unit tests, pero no habilita entrega real. La documentación/texto de operación del bot queda inactiva.

Cobertura nueva en `tests/test_telegram_release_gate.py`:

- 503 para mensajes, callbacks, grupos, updates vacíos y update repetido, incluso con `TELEGRAM_WEBHOOK_SECRET`/`TELEGRAM_BOT_SECRET_TOKEN` sintéticos correctos y `TELEGRAM_ENABLED=true`;
- token de URL inválido también recibe 503, no 403 con log de secreto;
- cero llamadas a deduplicación, router, executor y método asíncrono legado;
- cero filas nuevas en `telegram_updates` y cero efectos de escritura;
- los secretos sintéticos no aparecen ni en la respuesta ni en `caplog`.

## Comandos previstos para el padre (no ejecutados aquí)

```bash
python scripts/run_tests_offline.py --no-bytecode --no-cache -q \
  tests/test_telegram_actor_identity.py tests/test_telegram_release_gate.py \
  tests/test_telegram_link_identity.py
```

Sugerencia adicional: repetir la suite afectada en Docker con PostgreSQL local desechable, sin variables reales.

## Bloqueos y límites

- **A08 no es una reparación funcional.** Es una contención de release: el bot no debe habilitarse hasta implementar cola durable, idempotencia y workers. Cualquier cambio futuro debe reemplazar el 503, no saltárselo con un flag.
- SQLite local no valida locks, workers ni semántica PostgreSQL; la contención no depende de ello porque no hay escritura.
- No se hizo smoke test externo de Telegram ni se intentó contacto con la API real.
- Los resultados de tests de este documento son **no ejecutados**; quedan a verificación del padre.
- Recordatorio: los secretos reales siguen fuera de este lote y deben rotarse/cambiarse por el usuario donde corresponda.
