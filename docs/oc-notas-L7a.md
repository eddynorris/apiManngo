# Lote L7a · Fechas estrictas y trazabilidad de voz

**Estado:** fuente y tests entregados. **NO EJECUTADOS** por el agente (shell/Python no habilitado en este encargo). El padre validará en Docker. No se declaran resultados `passed`.

## Alcance y archivos propios

- `utils/date_parser.py`: helpers de Telegram dejan de devolver "hoy" ante fecha presente inválida y lanzan `ValueError`. Fecha ausente (`None`/vacío) usa hoy Perú; `YYYY-MM-DD` representa el día calendario de Perú. Se agregó `parse_fecha_operacion` estricto: ISO con zona conserva el instante; ISO u hora ingenua se interpreta en `America/Lima`; fecha-día es medianoche Perú; se devuelve UTC para almacenar. No se agregó `DD/MM/YYYY` porque no hay contrato previo que lo justifique.
- `resources/transaccion_resource.py`: una `fecha` explícita inválida, nula o vacía responde 400 antes de tocar stock/ventas/pagos/gastos/movimientos. Campo ausente usa el instante actual. El gasto asociado usa el día calendario Perú.
- `resources/compra_insumo_resource.py`: misma semántica estricta de fecha; inválida responde 400 sin inventario, movimiento ni gasto. Se quitó `detalle: str(e)` de la respuesta 500.
- `resources/voice_resource.py`: `ComandoVozLog` se guarda y confirma en una `Session` propia, independiente de la sesión del request, sin depender de otra operación comercial y sin confirmar cambios ajenos.
- Telegram: sin cambios netos en handlers; la fecha de lote sigue usando el helper. El helper devuelve la fecha-día en zona Perú y la BD con zona la almacena en UTC.
- `tests/test_historical_dates.py` (nuevo): fechas inválidas con cero efectos, ausente, nulo/vacío, límites medianoche Lima/UTC, fecha diaria de compra, ISO con zona, Telegram y persistencia del día Perú.
- `tests/test_voice_log_persistence.py` (nuevo): log de voz leído con una sesión ORM independiente tras rollback de la sesión del request; doble de Gemini, sin audio ni IA real.

## Convención de fechas

- Entrada ISO con zona: se respeta el instante y se almacena en UTC.
- Fecha-día `YYYY-MM-DD` y hora ingenua de operación: se interpretan en `America/Lima`.
- Campo ausente: puede usar ahora.
- Campo presente nulo, vacío o inválido: se rechaza con 400/`ValueError`.
- `to_peru_time` legado no se modificó para no romper reportes.

## Voz

- El log de auditoría se persiste una sola vez, en su propia transacción, aunque la petición no ejecute ninguna operación de base de datos.
- La prueba usa una sesión real independiente y un doble local de Gemini. En SQLite en memoria el pool puede compartir conexión; no se afirma aislamiento de locks. En PostgreSQL usa otra conexión del pool.

## Migraciones

- Ninguna. No hubo cambios de esquema.

## Resultados

- **NO EJECUTADOS por el agente.** Sin `pytest`, sin conexiones PostgreSQL y sin Gemini/Telegram reales.
- Comando previsto para el padre:
  `python scripts/run_tests_offline.py --no-bytecode --no-cache -q tests/test_historical_dates.py tests/test_voice_log_persistence.py tests/test_compra_insumo_resource.py tests/test_pago_validation.py`
- Recordatorio: rotar/cambiar secretos reales en `.env` fuera de este lote.
