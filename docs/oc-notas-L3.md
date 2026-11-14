# Lote L3 · Transferencias, producción y reversión de movimientos (A04/A25)

**Estado:** implementado en fuente y tests. **No ejecutado** por restricción expresa del encargo: no se usó shell, red, BD real ni dotenv. No hay resultados de pytest, migraciones ni concurrencia que declarar. El padre mantiene la verificación independiente en Docker.

**Alcance respetado:** solo L3/A04 y la parte de A25 correspondiente a movimientos. No se tocaron `models.py`, `common.py`, `app.py`, `schemas.py`, `services/authorization.py`, auth/RLS ni tests existentes. No se modificaron inventarios actuales ni se borró historial. No se usó producción, Supabase/Storage, Gemini, Telegram, SUNAT, secretos ni commits.

## Archivos modificados/creados

- `models_stock_audit.py` (nuevo): modelo auxiliar `MovimientoScope` (`movimiento_scopes`). PK `movimiento_id` FK a `movimientos`, más `inventario_id` y `almacen_id` del registro real. Importado por `resources/movimiento_resource.py`, por lo que su metadata queda registrada antes de cualquier `db.create_all`.
- `migrations/versions/c4d5e6f7a8b9_add_movimiento_scopes.py` (nuevo): migración reversible con `down_revision = 'b7c1d2e3f4a5'` (head existente de L4 al revisar `migrations/versions`). Si L4 agrega otra revisión después, el padre debe reencadenar para no dejar dos heads.
- `resources/transferencia_resource.py`: autorización de ambos extremos antes de consultar/bloquear/mutar; normalización estricta de IDs; cantidades finitas y positivas; validación agregada por presentación para rechazar duplicados que sobrepasan stock; rollback y propagación de `HTTPException` sin convertirla en 500.
- `resources/produccion_resource.py`: IDs/cantidades estrictos; autorización del almacén de producción antes de consultar receta/bloquear/mutar; actor tomado de BD; `HTTPException` re-lanzada con rollback.
- `services/produccion_service.py`: normalización estricta de entradas/salidas, cantidades finitas positivas, autorización del almacén con el actor vigente antes de locks, demandas agregadas por insumo/lote para impedir stock negativo por líneas duplicadas.
- `resources/movimiento_resource.py`: POST manual corregido (sin `schema.load`/`to_dict()`); persiste `MovimientoScope` exacto en la misma transacción; requiere inventario real existente incluso para entradas. DELETE ya no usa `.first()` ni trunca stock a 0; solo revierte movimientos manuales nuevos con alcance verificable.
- `tests/test_stock_operation_authorization.py` (nuevo): autorización y atomicidad de transferencias/producción.
- `tests/test_movimiento_reversal_scope.py` (nuevo): alcance exacto, rechazo legacy/derivado y reversión segura.
- `docs/oc-notas-L3.md` (nuevo): este registro.

## Decisiones de autorización

- Se reutiliza la política existente `services.authorization.autorizar_almacen`. Solo el rol exacto `admin` vigente en BD puede cruzar almacenes. Los demás roles deben coincidir con su `almacen_id` real.
- Transferencias: se autorizan **origen y destino** antes de obtener inventarios con lock o mutar. Por tanto, un no-admin no puede transferir entre almacenes distintos; un admin válido conserva el acceso original.
- Producción y ensamblaje: se autoriza el `almacen_id` de la operación antes de locks/mutaciones. También se autoriza otra vez dentro del servicio de ensamblaje para defensa en profundidad.
- Los IDs se normalizan con `normalizar_ids` y aceptan enteros positivos o strings de dígitos ASCII equivalentes. Se rechazan bool, float, cero, negativos, signos, espacios y formatos mixtos.
- El actor de los movimientos se toma del usuario real resuelto en BD, no de un `usuario_id` del cuerpo.

## Contrato de movimientos manuales

- `POST /movimientos` exige `almacen_id`, `presentacion_id`, `tipo` (`entrada|salida`), `cantidad` finita y estrictamente positiva. `lote_id` puede ser nulo si el inventario es exactamente sin lote.
- Se busca el inventario exacto por `(presentacion_id, almacen_id, lote_id)` con `with_for_update()`. Si no existe, responde 409 sin crear movimiento ni mutar stock (no confirma una entrada sin inventario real).
- Si el inventario existe, en la misma transacción se crea el `Movimiento`, se crea `MovimientoScope` con `inventario_id`/`almacen_id` reales y se actualiza el inventario. No se permite `tipo_operacion` derivado (`venta`, `produccion`, `transferencia`, `compra`, etc.) en el alta manual; solo nulo o `ajuste`.
- `DELETE /movimientos/<id>`:
  - Movimiento derivado (`venta`, `produccion`, `ensamblaje`, `transferencia`, `compra`, `merma`): 409 con el flujo específico, aunque exista una fila de scope. No se borra una sola pierna.
  - Movimiento histórico sin `MovimientoScope` verificable: 409 sin efecto, incluso para admin. No se infiere almacén del actor ni se usa `.first()`.
  - Movimiento manual nuevo: autoriza el almacén guardado en el scope, bloquea movimiento/scope/inventario, compara presentación, lote y almacén y revierte. Si el inventario falta o no coincide: 409. Si una entrada no tiene stock suficiente para revertirse: 409 sin truncar a 0.
  - `Scope` y movimiento se eliminan en la misma transacción; el kardex histórico no se reescribe.

## Resultado de integración PostgreSQL del padre (preflight)

- **168 passed, 10 failed.** Un fallo de autorización pertenece a otro lote; los otros 9 son de estos tests: `KeyError: 1/2` al indexar `stock_env["lotes"][1/2]`.
- Causa: el `DELETE` del fixture deja la secuencia PostgreSQL avanzada, por lo que los almacenes reciben IDs autogenerados 5/6/7 aunque los tests usan 1/2/3. Es un defecto de fixture en PostgreSQL, **no evidencia de fallo de negocio ni de autorización**.
- Reparación mínima aplicada solo en los fixtures nuevos: los tres `Almacen` sintéticos de ambos tests se insertan con `id=1/2/3` explícito. No se reseteó ninguna secuencia, no se usó BD de producción, no se omitieron ni relajaron tests/asserts.
- Esta reparación **no fue ejecutada por este agente**; el padre la verifica en Docker/PostgreSQL aislado.

## Corrección final PG real (93 passed, 1 failed antes)

- **Antes de esta corrección:** 93 passed, 1 failed. El único fallo era `test_delete_movement_with_missing_inventory_returns_409`, que intentaba borrar un `Inventario` referenciado por el FK `RESTRICT` de `MovimientoScope` antes de llamar a la API; SQLite con FK off ocultaba el problema.
- **Reparación:** el test ya no borra el inventario real. Inyecta un fallo de lectura con `monkeypatch` sobre `sqlalchemy.orm.Query.one_or_none`, devolviendo `None` solo cuando la entidad es `Inventario`; el resto delega al original. El handler DELETE conserva su consulta `Inventario.query...one_or_none()` y el test mantiene el 409 y verifica inventario, movimiento y scope persistidos sin mutaciones. El monkeypatch se restaura antes de leer el ORM.
- **Test independiente:** `test_movimiento_scope_fk_blocks_real_inventory_delete` intenta el DELETE real en PostgreSQL y exige `IntegrityError`, rollback y fila existente. En SQLite intenta activar `PRAGMA foreign_keys=ON`; si queda activo, exige la misma protección; si no, valida el descriptor `ON DELETE RESTRICT` sin afirmar que SQLite aplique el FK. Sin skips.
- Esta corrección **no fue ejecutada por este agente**; el padre la verifica en Docker/PostgreSQL aislado.

## Pruebas añadidas (diseñadas; corrida del padre arriba)

- Almacenes 1/2/3 persistidos y usuarios de cada almacén, admin, gerente y usuario sin almacén.
- Transferencia no-admin: origen/destino ajenos, propio→ajeno y ajeno→propio: 403 sin stock ni movimientos. Admin cruza y conserva su conducta válida.
- Transferencia con IDs string/int, rechazo de bool/float/espacios, cantidades 0/negativas/NaN/Infinity/texto, duplicados que sobrepasan stock y fallo tardío de batch sin efectos parciales.
- Producción y ensamblaje: no-admin/gerente/sin almacén en almacén ajeno 403; admin cruza; IDs string funcionan; demandas duplicadas de insumo no dejan stock negativo.
- Movimiento manual: scope apunta al inventario/almacén exactos; mismo producto en varios almacenes/lotes solo revierte el objetivo; entrada y salida restauran el saldo exacto.
- DELETE legacy sin scope 409; derivados 409 con scope incluso admin; reversión insuficiente 409 sin clamp; inventario ausente o scope inconsistente 409; movimiento inexistente 404; POST sin inventario real 409; entradas/salidas inválidas no crean scope ni mutan stock.

## Verificación pendiente del padre

Comandos previstos con el runner ya existente, desde la raíz:

```bash
python scripts/run_tests_offline.py --no-bytecode --no-cache -q tests/test_stock_operation_authorization.py tests/test_movimiento_reversal_scope.py
```

No se ejecutó. El padre debe confirmar además que la suite completa no cambia aserciones previas y que la migración encadena sin múltiples heads.

## Límites y bloqueos

- **No ejecutado:** pytest, import de app, migraciones, `flask db`, build Docker, conexiones de BD, locks PostgreSQL y pruebas de concurrencia. No se inventan resultados.
- SQLite no comprueba `FOR UPDATE` ni concurrencia real; las pruebas nuevas usan el SQLite desechable del runner.
- La migración no se aplicó. `db.create_all()` de los tests crea la tabla por el import del modelo.
- **D1/imagen:** el Dockerfile ya incluye el `COPY` explícito de `models_stock_audit.py` (integración hecha por D1). Este lote no modificó el Dockerfile; falta confirmar que la imagen final construida lo contenga.
- No se rellenan scopes para movimientos históricos ni derivados. Esa ausencia es intencional: su reversión por DELETE queda prohibida con 409.
- **PG FK:** el escenario imposible con `ON DELETE RESTRICT` ya no borra el inventario; el 409 se prueba con lectura inyectada. El test independiente conserva la comprobación real de FK en PostgreSQL y la alternativa de descriptor en SQLite.
- Recordatorio: el usuario debe rotar/cambiar sus secretos reales en los `.env` fuera de este lote; no se leyeron ni editaron.
