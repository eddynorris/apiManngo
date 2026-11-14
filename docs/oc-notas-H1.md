# Notas de implementación — Lote H1 (harness PostgreSQL real)

**Estado:** H1 (sesión/DSN) verificado por el padre en PostgreSQL real: **419 passed, 1 failed, 0 skips en 50 s**. El bloqueo de DDL y la colección del DSN quedaron resueltos. El único fallo era la guarda de red, corregido en H1.1. **Esta sesión no ejecutó pytest** porque Bash no está habilitado; no se declaran resultados de pruebas propios. El padre verifica el cierre en Docker/PostgreSQL real.

**Alcance respetado:** en H1 se editaron solo `tests/conftest.py`, `tests/test_pg_session_lifecycle.py` (nuevo) y este documento. En H1.1 se editó además `scripts/offline_test_env.py` como único cambio de la guarda, y se amplió el test nuevo. No se editaron `app.py`, `common.py`, `models.py`, `schemas.py`, recursos, servicios, negocio, tests originales, aserciones, RLS ni `tests/test_venta_pago_auto.py`. No se tocaron `.env`, secretos, Docker ni producción. `tests/test_test_environment_safety.py` quedó intacto, incluida su aserción.

## Problema observado por el padre

La suite completa en PostgreSQL real arrancó con fallos más 24 errores de RLS y quedó esperando un `DROP TABLE movimientos`. `pg_stat_activity` mostraba otra conexión `idle in transaction` con un `SELECT` sobre `movimientos` bloqueando el DDL. En SQLite no se evidencia porque no hay locks entre conexiones.

Además, `tests/test_venta_pago_auto.py` escribe `DATABASE_URL=sqlite:///:memory:` durante la colección, mientras el engine real de la app sigue apuntando a PostgreSQL. Eso hacía fallar la validación de `_require_pg_url` (exige `DATABASE_URL == MANNGO_LOCAL_POSTGRES_URL`) sin que el engine hubiese cambiado.

## Archivos modificados

- `tests/conftest.py`
  - Captura temprana del DSN ya validado por `prepare_test_environment()` y del modo PostgreSQL opt-in.
  - `pytest_collection_finish` restaura, solo en modo PostgreSQL opt-in, el snapshot validado si otro módulo lo sobrescribió durante la colección.
  - La restauración **revalida** el snapshot con la misma autorización de loopback del runner (no fuerza `allow_local_postgres`) y exige que la configuración del engine y la URL real del engine sean PostgreSQL loopback desechables (`manngo_test*`) e iguales al snapshot. Si no coinciden o son remotas, falla en vez de restaurar una URL no validada. En SQLite no toca el entorno.
  - Hook `pytest_runtest_teardown` con `tryfirst=True`: hace rollback y `remove()` de la sesión activa **antes** de que pytest finalice los fixtures. Así los teardowns que ejecutan `db.drop_all()`/DDL no encuentran una conexión propia en `idle in transaction`.
  - `_clean_active_db_session` garantiza `remove()` incluso si el `rollback()` falla.
- `tests/test_pg_session_lifecycle.py` (nuevo)
  - Regresión de orden: un fixture crea una tabla sonda, deja un INSERT sin commit, y en su teardown (que ejecuta DDL) verifica que la sesión anterior fue removida y que el INSERT fue revertido antes del DDL. Sin el hook, ese teardown falla.
  - Regresión de colección: comprueba que el DSN validado del conftest sigue vigente después de la colección (que incluye el clobber de `DATABASE_URL` hecho por el test original), sin modificar ese test.
  - Pruebas unitarias del restaurador: reemplaza un clobber SQLite en modo PG, rechaza snapshots remotos (sin filtrar credenciales), rechaza config/engine que no coinciden con el snapshot y no actúa fuera del modo PostgreSQL.
  - H1.1: regresiones de whitelist de red con dobles de las llamadas originales de socket: endpoint loopback/puerto validado permitido; otro puerto, host remoto, UNIX y UDP bloqueados; `install_offline_guards` no amplía permisos después de la primera instalación.
- `scripts/offline_test_env.py` (H1.1)
  - El permiso loopback deja de ser "cualquier puerto local". Ahora se deriva únicamente del puerto de `DATABASE_URL` y `MANNGO_LOCAL_POSTGRES_URL`, ambos validados como PostgreSQL loopback desechable y coincidentes. El puerto se congela en la primera instalación.
  - `install_offline_guards(None)` conserva la política vigente; `False` cierra el permiso; `True` exige DSN seguro y no amplía endpoints ya instalados.
  - `sendto`/UDP se bloquea siempre; también remotos, otros puertos y direcciones UNIX.
- `docs/oc-notas-H1.md`: este registro.

## Decisiones

- **No se simuló el lock ni se desactivaron FKs:** la corrección es de ciclo de vida de sesión. El hook no crea locks artificiales ni cambia constraints.
- **No se toca la colección del test original:** `test_venta_pago_auto.py` sigue escribiendo `DATABASE_URL` al importarse. El conftest recupera el DSN validado después de la colección, comparándolo con la configuración y la URL reales del engine.
- **No se restaura una URL remota:** el valor restaurado es siempre el snapshot que pasó las guardas al inicio; se revalida antes de asignarlo.
- **SQLite conserva su comportamiento:** la restauración está limitada a `environment_status.database_kind == 'postgresql-loopback-disposable'`; en SQLite no se modifica `DATABASE_URL`.
- **Datos y aserciones:** el hook corre en fase de teardown, después de que terminaron los asserts del test. Solo hace rollback de transacciones no confirmadas y libera la sesión; no borra datos confirmados.

## Comandos y resultados

**No ejecutado por restricción:** Bash no está habilitado en esta sesión. No se ejecutó `pytest`, el runner offline ni Docker, y no se declaran resultados inventados.

Comandos que el padre debe ejecutar para verificar (sin red de negocio):

```bash
scripts/run_tests_offline.py --no-bytecode --no-cache --allow-local-postgres \
  --ignore=tests/test_sunat_xml.py
```

Con `MANNGO_RUN_PG_TESTS=1`, `MANNGO_LOCAL_POSTGRES_URL` y `DATABASE_URL` apuntando a la misma base loopback desechable `manngo_test*`. El bloque RLS queda para el final, como estaba previsto.

## Límites y pendientes

- SQLite no puede demostrar el bloqueo de DDL de PostgreSQL; la regresión de orden sí verifica que la sesión se elimina antes del teardown que ejecuta DDL, y el padre debe confirmar que desaparece el `idle in transaction` en `pg_stat_activity`.
- La restauración presupone que el runner/conftest arrancaron con un PostgreSQL loopback desechable ya validado. Si el snapshot no fuese válido, la colección falla explícitamente en vez de continuar con un DSN mezclado.
- No se modificaron tests originales ni RLS; cualquier fallo de negocio ajeno al harness queda fuera de este lote.

## Corrección H1.1 — guarda de red por endpoint

**Resultado real del padre tras H1:** `419 passed, 1 failed, 0 skips in 50s`. El bloqueo de DDL desapareció y la colección restauró el DSN correctamente.

**Único fallo:** `tests/test_test_environment_safety.py::test_socket_connections_are_blocked`. `install_offline_guards()` y `connect("127.0.0.1", 9)` debían lanzar `OfflineNetworkBlocked`, pero se recibía `ConnectionRefused` porque el flag `allow_loopback` habilitaba **todos** los puertos loopback.

**Reparación mínima aplicada solo en `scripts/offline_test_env.py` y tests nuevos:**

- El estado de la guarda ahora incluye `allowed_loopback_ports`, derivado exclusivamente del puerto de `DATABASE_URL` y `MANNGO_LOCAL_POSTGRES_URL`, ambos validados como PostgreSQL loopback desechable y coincidentes entre sí.
- El permiso se congela en la primera instalación: llamadas posteriores con `True` no agregan endpoints aunque otro módulo haya cambiado el entorno. Esto evita que un clobber tardío de `DATABASE_URL` a SQLite amplíe o redirija el permiso.
- `install_offline_guards(None)` conserva la política vigente sin ampliar; `False` cierra el permiso; `True` exige DSN seguro y falla si no lo es.
- Solo se permite TCP a host loopback en el puerto validado. Se bloquean siempre UDP/`sendto`, hosts remotos, otros puertos (`127.0.0.1:9`) y direcciones UNIX. No se habilitan Redis, HTTP, modelos ni sockets externos.
- Se conservan las funciones originales de socket para el caso permitido, sin abrir red en los tests: los casos nuevos usan dobles de `_original_socket_connect`, `_original_socket_connect_ex`, `_original_create_connection` y `_original_socket_sendto`.
- No se editó `tests/test_test_environment_safety.py` ni se cambió su aserción. No se modificó `tests/conftest.py` en H1.1. El bloque RLS sigue intacto y último.

**Resultado propio:** no ejecutado por restricción (Bash no habilitado). El padre debe confirmar que el caso `127.0.0.1:9` pasa a `OfflineNetworkBlocked` y que PostgreSQL loopback sigue conectando por su puerto validado en Docker.
