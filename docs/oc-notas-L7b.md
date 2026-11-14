# Lote L7b · Contrato HTTP global y detalles de venta (A19/A23/A25)

**Estado:** implementado en fuente y tests. **No ejecutado** por restricción del encargo (sin shell); no hay resultados reales de pytest. El padre mantiene la verificación independiente en Docker.

**Alcance respetado:** solo `common.py`, `resources/ventadetalle_resource.py`, tests nuevos y estas notas. No se editaron `app.py`, auth, ventas, pagos, movimientos, `services/venta_service.py`, `services/authorization.py`, `schemas.py` ni otros recursos para hacer pasar tests. No se usó producción, dotenv, red, secretos, SQL remoto, MCP ni commits. No se abordó L8.

## Archivos modificados/creados

- `common.py`: `handle_db_errors` detecta respuestas 5xx devueltas como tupla o `Response`; hace rollback, reemplaza el cuerpo por `{"message": "Error interno del servidor", "error_id": ...}`, conserva el status 5xx y los headers necesarios (descarta `Content-Type` y `Content-Length` obsoletos del cuerpo anterior) y registra solo método, ruta, status y `error_id` en el logger del servidor. Las excepciones HTTP 4xx, `ValidationError` y los 400/409 devueltos por el handler no se filtran. Los 5xx por excepción no capturada ya se sanitizaban y conservan ese comportamiento con rollback.
- `resources/ventadetalle_resource.py`: GET usa el schema plural sobre la lista. POST carga el schema con `load_instance=True` y lee atributos de la instancia, no índices de diccionario. La venta se toma siempre de la URL; un `venta_id` distinto en el payload se rechaza sin mutar. POST y DELETE delegan en `VentaService.actualizar_venta` con el actor real, por lo que el stock se recalcula por FIFO/movimientos dentro de la misma transacción y no se escribe inventario directamente. DELETE usa la ruta real `/ventas/<venta_id>/detalles`, exige `detalle_id` por body o query, autoriza la venta antes de leer/mutar, rechaza detalles de otra venta y rechaza dejar la venta sin líneas. Si el nuevo total queda por debajo de los pagos existentes, el servicio lanza 409 y la ruta revierte.
- `tests/test_http_error_contract.py` (nuevo): rutas sintéticas decoradas con `handle_db_errors` para 400/401/403/404/409/413/429/500, tupla 500, `Response` 503 y excepción no capturada. Cada caso 5xx inserta una fila con sentinela y confirma rollback; el JSON no contiene la sentinela y conserva `error_id` y headers útiles.
- `tests/test_venta_detalle_contract.py` (nuevo): GET/POST/PUT/DELETE ajenos según rutas reales, casos válidos con efecto real en venta, detalle, stock y pagos; detalle de otra venta, última línea, falta de `detalle_id`, recurso inexistente, stock insuficiente y saldo de pagos incoherente.
- `docs/oc-notas-L7b.md` (nuevo): este registro.

## Decisiones y límites

- **Sin API nueva:** la ruta registrada sigue siendo solo `/ventas/<int:venta_id>/detalles`. La variante histórica `/venta_detalles/<detalle_id>` aparece en documentación antigua pero no está registrada; no se añadió para no inventar contrato. DELETE sobre la ruta real recibe `detalle_id` en body o query.
- **Autorización antes de parsear/mutar DELETE:** el path de la venta se carga y autoriza antes de exigir `detalle_id`, de modo que un usuario no autorizado recibe 403 y no puede sondear detalles.
- **Atomicidad de detalles:** reutiliza `VentaService.actualizar_venta`; no se duplican descuentos FIFO ni se omiten movimientos. El costo es que actualizar detalles recrea las líneas y recalcula lotes por FIFO, comportamiento ya existente del servicio.
- **Sanitización acotada:** `handle_db_errors` no captura `HTTPException` 5xx lanzadas explícitamente (las re-lanza como antes); este lote sanea respuestas 5xx devueltas por handlers y excepciones no capturadas. No se cambiaron otros recursos que devuelven `str(e)` en 500; su saneamiento depende de que la respuesta pase por este decorador.
- **No ejecutado:** no se corrió pytest, app, migraciones ni contenedores. SQLite no prueba locks de PostgreSQL.
- Comando previsto por el padre: `python scripts/run_tests_offline.py --no-bytecode --no-cache -q tests/test_http_error_contract.py tests/test_venta_detalle_contract.py tests/test_venta_authorization.py`.
- Recordatorio: rotar/cambiar secretos reales en los `.env` fuera de este lote; no se leyeron ni editaron.
