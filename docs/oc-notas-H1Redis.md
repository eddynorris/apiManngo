# Notas de implementación — L8 · Guarda local de Redis compartido (harness)

**Estado:** implementado en fuente y tests nuevos. **No ejecutado** por este agente (sin shell/red en esta sesión); no se declaran resultados de pytest. El padre arranca Redis local, ejecuta L8 con los flags y verifica en Docker.

**Alcance respetado:** solo el harness de pruebas. No se modificaron `tests/conftest.py`, tests existentes, código de aplicación, recursos, modelos, servicios, dependencias, Docker ni `.env`. No se usó producción, Redis/PostgreSQL reales desde este agente, dotenv real ni red de negocio. La conexión real de verificación queda para el padre.

## Archivos modificados/creados

- `scripts/offline_test_env.py`:
  - Constantes reservadas `MANNGO_ALLOW_LOCAL_REDIS_TESTS`, `MANNGO_LOCAL_REDIS_URL` y valor por defecto `redis://127.0.0.1:6379/0`.
  - `validate_testing_redis_url(...)`: exige autorización explícita, esquema `redis`, host loopback literal, puerto 1..65535 y base numérica; rechaza query de override de endpoint/base, UNIX, remotos y `rediss`. Los errores son genéricos y no incluyen credenciales.
  - `_validated_redis_ports(...)` y `_validated_loopback_ports(...)`: Redis solo se valida junto a PostgreSQL loopback desechable ya validado; `REDIS_URL` convencional no autoriza nada.
  - `prepare_test_environment(..., allow_local_redis=None)`: `None` preserva el opt-in del runner (necesario cuando conftest lo invoca sin flags), `False` cierra/elimina el flag y `True` exige PostgreSQL local validado y URI Redis válido.
  - `install_offline_guards(..., allow_local_redis=None, env=None)`: en la primera instalación congela únicamente los puertos TCP de PostgreSQL validado y, si la autorización Redis es explícita y válida, el puerto exacto de `MANNGO_LOCAL_REDIS_URL`. Después de instalado no amplía puertos ni por cambio tardío de entorno. `False` cierra Redis y conserva PostgreSQL; el cierre total sigue limpiando ambos.
  - Se conservan los bloqueos HTTP/SDK, UDP/`sendto`, UNIX, remotos y el caso `127.0.0.1:9`.
- `scripts/run_tests_offline.py`: nuevo flag `--allow-local-redis`, rechazado con código 2 si falta `--allow-local-postgres` (antes de importar app/pytest). Se pasa la autorización a preparación e instalación.
- `tests/test_local_redis_guard.py` (nuevo): regresiones de URI aceptado/rechazado sin filtrar credenciales, flag None/False/True, URI reservado frente a `REDIS_URL`, puerto adicional solo con permiso explícito, congelamiento/no ampliación tras instalación, dobles de socket original (sin conexiones reales), `localhost:9` y UDP bloqueados, y rechazo del runner sin PostgreSQL.
- `docs/oc-notas-H1Redis.md`: este registro.

## Contratos conservados

- `prepare_test_environment` e `install_offline_guards` agregan parámetros opcionales de solo keyword; las firmas y llamadas previas siguen funcionando.
- `EnvironmentStatus` conserva sus cuatro campos y añade `allow_local_redis: bool = False` al final.
- `clear_integration_environment` sigue borrando Redis real (`REDIS_*`, `LIMITER_*`) y ahora documenta/verifica que no borra las variables reservadas `MANNGO_LOCAL_*` ni el flag de autorización.
- El test H1 de PostgreSQL (`H1_POSTGRESONLY`) sigue con solo el puerto 5432 cuando no se pidió Redis.

## Pruebas

- **No ejecutadas** por este agente: la sesión no tiene Bash habilitado. No se declaran `passed` ni resultados inventados.
- Comando previsto para el padre, con PostgreSQL local desechable, Redis local y las dos URLs coincidentes:
  `python scripts/run_tests_offline.py --no-bytecode --no-cache --allow-local-postgres --allow-local-redis -q tests/test_local_redis_guard.py tests/test_pg_session_lifecycle.py tests/test_test_environment_safety.py`
- Para L8 completo, el padre añade el resto de su selección de tests y verifica desde fuera el socket Redis del puerto exacto de `MANNGO_LOCAL_REDIS_URL`. No se conecta a Redis desde los tests unitarios: usan dobles de las funciones originales de socket.

## Límites

- El permiso es de proceso y por puerto exacto; no sustituye un cortafuegos ni valida la disponibilidad real de Redis. La verificación real es del padre.
- No se modificó la clave de Flask-Limiter ni el código de aplicación; este lote solo habilita el harness para que L8 pueda probar contadores compartidos.
- Recordatorio: rotar/cambiar secretos reales en los `.env` fuera de este lote.
