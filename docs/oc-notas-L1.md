# Lote L1 · Autorización end-to-end de ventas, pedidos y detalles

**Estado:** implementado y verificado localmente con el runner offline. El padre mantiene la verificación independiente en Docker.

**Alcance respetado:** solo L1 (A01, A02 y superficie análoga de pedidos/detalles). No se desarrollaron cambios de contrato A25, pagos/depósitos (L2), transferencias/producción (L3), revocación de tokens ni manejo de dinero. No se usó producción, BD remota, dotenv real, Supabase/Storage/Gemini/Telegram/SUNAT reales. No se modificaron `.env` reales ni tests previos.

## Archivos modificados/creados

- `services/authorization.py` (nuevo): política central reutilizable. Resuelve el actor contra `Users` vigente en BD; solo el rol exacto `admin` cruza almacenes/propietarios. Los demás requieren `almacen_id` coincidente y, en venta/pedido existente, ser su vendedor. Los IDs solo se aceptan como entero positivo auténtico o string de dígitos ASCII que represente un entero positivo; se rechazan bool, float/Decimal integrales o no, cero, negativos, signos, whitespace, puntos y formatos inválidos sin truncar ni reparar. `autorizar_almacen` valida el ID destino antes del atajo de admin.
- `common.py`: helper `obtener_payload_normalizado()` que unifica JSON y multipart y normaliza IDs antes de autorizar.
- `services/venta_service.py`: `crear_venta`, `actualizar_venta`, `eliminar_venta` y `eliminar_ventas_en_lote` aceptan `actor_id` opcional. **El único endpoint HTTP de ventas (`resources/venta_resource.py`) siempre pasa el actor**; las llamadas internas de Telegram omiten `actor_id`, por lo que en esas llamadas el servicio no aplica autorización de objeto (comportamiento interno legítimo, no una autorización de servicio). Se autoriza la venta real antes de revertir stock/pagos; se autoriza el nuevo almacén si el payload cambia destino y el descuento FIFO usa el almacén destino.
- `resources/venta_resource.py`: GET de venta usa la política real; POST/PUT/DELETE delegan autorización al servicio; batch normaliza IDs y valida todo antes de borrar; HTTPException se re-lanza antes del `except Exception`.
- `resources/pedido_resource.py`: GET individual, PUT, DELETE y conversión autorizan el pedido real; POST/PUT autorizan almacén destino; lista no-admin se limita a almacén y vendedor propios.
- `resources/ventadetalle_resource.py`: GET/POST/DELETE autorizan la venta padre real. No se alteró el contrato de rutas/IDs de A25.
- `tests/test_venta_authorization.py` (nuevo): 58 regresiones de autorización, formatos y parseo estricto de IDs.

## Regresiones cubiertas

- Admin, usuario A/B (mismo almacén) y usuario C (otro almacén).
- GET/PUT/DELETE de venta ajena en el mismo almacén y en otro almacén: 403 y sin efectos persistidos (venta, detalles, stock y pagos relecturados).
- Venta propia, cambio de destino por no-admin rechazado y cambio de destino por admin descontando stock del almacén nuevo.
- Alta JSON y multipart contra almacén ajeno: 403 sin venta ni stock modificado; alta multipart en almacén propio: 201.
- IDs string/int en POST, PUT y batch; batch mixto propio/ajeno rechazado completo sin borrar la propia; batch con ID inexistente 404 sin mutación.
- Pedido ajeno: GET/PUT/DELETE/conversión 403 sin efectos; lista oculta pedidos ajenos.
- Detalle de venta ajena: GET/POST 403 sin nuevos detalles ni stock.
- Recurso inexistente: 404, no 500. Las HTTPException no caen en el `except Exception` genérico.
- Parseo estricto: rechazo de bool, float `1.0`/`1.9`, Decimal, cero, negativos, strings con signo/whitespace/punto/texto; aceptación de enteros positivos y strings de dígitos como `'1'`, `'25'`, `'01'`. `autorizar_almacen` rechaza destino inválido incluso para admin.

## Pruebas ejecutadas (resultado real)

Comando permitido, desde la raíz con el venv del proyecto:

```powershell
.\venv\Scripts\python.exe scripts/run_tests_offline.py --no-bytecode --no-cache --disable-warnings -q tests/test_venta_authorization.py
```

Resultado real: `58 passed, 41 warnings in 42.32s`.

Suite completa:

```powershell
.\venv\Scripts\python.exe scripts/run_tests_offline.py --no-bytecode --no-cache --disable-warnings -q
```

Resultado real: `195 passed, 2 failed, 239 warnings in 129.03s`. Los 2 fallos son de `tests/test_reporte_produccion_entradas.py` (`total_unidades` 150 vs 230 y traslados 0 vs 1), ajenos a L1 y reproducibles ejecutando solo ese módulo; no se modificaron ni suavizaron. Las 58 regresiones L1 y el resto de la suite pasan.

## Reproducción y límites

- No se ejecutó la suite nueva antes de aplicar la reparación en esta sesión. La reproducción de A01/A02 documentada en el plan maestro se tomó como evidencia de partida; los tests nuevos exigen ahora 403/404 y ausencia de efectos, por lo que fallarían con la conducta anterior (PUT/DELETE ajenos 200 y alta multipart 201). No se inventa un resultado previo no ejecutado.
- SQLite no verifica locks ni concurrencia de PostgreSQL; la autorización de objeto y la validación batch sí quedaron probadas en el runner aislado.
- No se añadieron dependencias ni `skip`/`xfail`. No se tocó dinero ni revocación de tokens.
- Recordatorio: el usuario debe rotar/cambiar sus secretos reales en los `.env` fuera de este lote; no se leyeron ni editaron.
