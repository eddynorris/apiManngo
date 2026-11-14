# Runbook F3: desplegar la rama `consolidacion/auditoria-2026-10` en producción

**Quién:** Eddy ejecuta cada paso. Los agentes no tocan producción, pero pueden revisar las salidas que se les pasen (sin secretos).
**Cuándo:** en una ventana de poco uso (por la noche en Lima), con unos 60–90 minutos libres.
**Qué cambia:** el código de la auditoría, Python 3.12 con el lock, Telegram con bandeja duradera, Redis obligatorio y 6 migraciones nuevas.
**Regla de oro:** las migraciones van **antes** del merge a `main`. El código nuevo lee columnas nuevas (`users.auth_version`, `telegram_updates.estado`) y fallaría con el esquema viejo. Al revés no hay problema: el código actual de `main` funciona con el esquema migrado (ver § Rollback).

> En los comandos, `<ref>` es el identificador del proyecto de Supabase y `<servicio-api>` el nombre del servicio web en Railway.
> Las contraseñas se leen con `read -s`, así no quedan en el historial. Nunca pegues un secreto en un chat con un agente.

---

## 0. Antes de empezar (checklist)
- [ ] El CI de la rama está en verde (jobs `test` e `integration`).
- [ ] Docker funciona en WSL (`docker ps`).
- [ ] Tienes a mano la contraseña de la BD (Supabase → Project Settings → Database) y acceso a Railway.
- [ ] Aviso a quien use el bot o el front: «mantenimiento de ~30 min».
- [ ] Anota el deployment actual en Railway (debería ser `9bdfd37`): es el punto de vuelta atrás.

## 1. Abrir el PR (sin fusionar)
- [ ] En GitHub, abre un PR `consolidacion/auditoria-2026-10` → `main` y revisa el diff y que el CI esté en verde.
- [ ] **No lo fusiones todavía.** Fusionar es desplegar.

## 2. Redis en Railway
1. En el proyecto: **+ New → Database → Add Redis**. Queda en red privada; no actives el proxy TCP público.
2. En `<servicio-api>` → **Variables**, añade:
   ```
   LIMITER_STORAGE_URI=${{Redis.REDIS_URL}}
   ```
3. Si Railway propone redesplegar, adelante: el código actual de `main` funciona igual con esa variable.

- [ ] Redis creado y variable referenciada (no copiada a mano).

## 3. Respaldo de la base de datos (obligatorio)
Hazlo desde WSL con el pooler en **modo sesión** de Supabase (puerto **5432** del host `*.pooler.supabase.com`, usuario `postgres.<ref>`). El puerto 6543, el del modo transacción, no sirve para `pg_dump`.
```bash
mkdir -p ~/backups-manngo && chmod 700 ~/backups-manngo
read -s -p "Contraseña BD: " PGPASSWORD; echo; export PGPASSWORD
docker run --rm -e PGPASSWORD -v ~/backups-manngo:/backup postgres:17-alpine \
  pg_dump "host=aws-0-us-east-2.pooler.supabase.com port=5432 dbname=postgres user=postgres.<ref> sslmode=require" \
  --schema=public --format=custom --no-owner --no-privileges \
  --file=/backup/manngo_$(date +%Y%m%d_%H%M).dump
ls -lh ~/backups-manngo
```
**Prueba de restauración** en un PostgreSQL local desechable (nunca en producción):
```bash
docker run -d --rm --name manngo-restore -e POSTGRES_PASSWORD=solo-local -p 127.0.0.1:55433:5432 postgres:17-alpine
sleep 5
docker exec -i manngo-restore pg_restore -U postgres -d postgres --no-owner --no-privileges < ~/backups-manngo/<archivo>.dump
docker exec manngo-restore psql -U postgres -Atc "SELECT 'ventas', count(*) FROM ventas UNION ALL SELECT 'pagos', count(*) FROM pagos UNION ALL SELECT 'inventario', count(*) FROM inventario"
```
Algunos avisos sobre roles o políticas de Supabase son normales. Lo que importa es que los conteos coincidan con producción.
**No borres todavía el contenedor `manngo-restore`:** en el paso 5 se ensayan ahí las migraciones con los datos reales.

- [ ] Dump creado (fuera del repo) y restauración local con conteos correctos.
- [ ] Si tu plan de Supabase tiene backups diarios, confirma también que el último existe.

## 4. Verificación previa del esquema (solo lectura)
En **Supabase → SQL Editor**, pega y ejecuta `scripts/sql/precheck_produccion.sql`. No modifica nada.
- [ ] Copia las dos tablas de resultado. Solo contienen marcas y conteos, sin datos personales. Si quieres, pásaselas a Claude para que las revise.

**Cómo decidir el siguiente paso:**
| Resultado | Qué hacer en el paso 5 |
|---|---|
| `tabla alembic_version` = **si** | **No** hagas `stamp`. `version_num` dice dónde está la BD; ejecuta `upgrade` directamente |
| Sin `alembic_version`; `movimientos.venta_id`, `uq_inventario_compuesto`, `cantidad es numeric` y `tabla telegram_updates` en **si**; el resto en **NO** | `flask db stamp 6efd542ce540` y después `upgrade`. **Es el caso más probable**, ensayado en local con el esquema de `main` |
| Igual que el anterior pero con `ck_lotes_kg_no_negativo` y `idx_ventas_almacen_fecha` en **NO** | También vale `stamp 6efd542ce540`. Esas dos marcas solo existen si alguien ejecutó la migración; `models.py` no las define. Es una mejora opcional que se puede añadir aparte |
| Sin `alembic_version` y `movimientos.venta_id` o `uq_inventario_compuesto` en **NO** | **Para.** La migración `2d09ea8fe981` hace cambios grandes (constraint único de inventario, JSONB→JSON, CHECK de lotes). Hay que revisarla con los conteos de riesgo antes de aplicarla |
| Marcas mezcladas (p. ej. `auth_version` en **si** pero `storage_cleanup_tasks` en **NO**) | **Para** y revísalo antes de seguir |
| `inventario duplicado …` > 0 o `lotes … negativa` > 0, con `2d09ea8fe981` pendiente | **Para**: `2d09ea8fe981` fallaría. Primero hay que corregir esos datos |

## 5. Migraciones (con el código viejo aún en producción)
Desde WSL, en un clon limpio de la rama (sin `.env`):
```bash
git clone --branch consolidacion/auditoria-2026-10 https://github.com/eddynorris/apiManngo /tmp/manngo-deploy
cd /tmp/manngo-deploy && ls .env 2>/dev/null && echo "¡hay .env, para!"
python3 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.lock
export FLASK_ENV=development   # solo para ejecutar la CLI: no arranca servidor ni crea tablas
export JWT_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"   # efímera, solo para el arranque de la CLI
```

**5a. Ensayo sobre la copia restaurada (paso 3), con datos reales y sin riesgo:**
```bash
export DATABASE_URL="postgresql://postgres:solo-local@127.0.0.1:55433/postgres"
flask --app app db current
flask --app app db stamp <REV>     # SOLO si el paso 4 lo indica (p. ej. 6efd542ce540)
flask --app app db upgrade         # si falla aquí, para: producción sigue intacta
flask --app app db current         # debe decir: e8f9a0b1c2d3 (head)
docker exec -i manngo-restore psql -U postgres -P pager=off < scripts/sql/precheck_produccion.sql   # todo en "si"
docker rm -f manngo-restore
```
- [ ] El ensayo termina sin errores. Si falla, pásale el error a Claude antes de tocar producción.

**5b. Producción** (los mismos comandos, ahora con la BD real):
```bash
read -s -p "Contraseña BD: " DB_PASS; echo
export DATABASE_URL="postgresql://postgres.<ref>:${DB_PASS}@aws-0-us-east-2.pooler.supabase.com:5432/postgres?sslmode=require"
flask --app app db current
flask --app app db stamp <REV>     # SOLO si el paso 4 lo indica (la misma <REV> del ensayo)
flask --app app db upgrade
flask --app app db current         # debe decir: e8f9a0b1c2d3 (head)
unset DB_PASS DATABASE_URL PGPASSWORD
```
- [ ] Repite el paso 4: todas las marcas en **si** y `version_num` = `e8f9a0b1c2d3`.
- [ ] Comprueba que el bot y el front actuales siguen funcionando: sigue corriendo el código de `main` sobre el esquema nuevo.

> La vista previa offline (`flask db upgrade --sql`) **no funciona** con estas migraciones: `f1a2b3c4d5e6` inspecciona los índices reales. El ensayo 5a la sustituye con ventaja, porque prueba las migraciones con los datos reales.

## 6. Variables del servicio API en Railway
En `<servicio-api>` → Variables. Compara con `.env.example`, que ya lista todo lo que el código lee.
- [ ] `FLASK_ENV=production`
- [ ] `DATABASE_URL`: la de siempre. La app puede usar el pooler en modo transacción (6543); el lock por chat de Telegram es por transacción y funciona ahí.
- [ ] `JWT_SECRET_KEY` de 32 caracteres o más (la misma de ahora, para no cerrar sesiones).
- [ ] `ALLOWED_ORIGINS` con el dominio del front (sin `*`).
- [ ] `LIMITER_STORAGE_URI=${{Redis.REDIS_URL}}` (paso 2).
- [ ] `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` y `TELEGRAM_WEBHOOK_SECRET` (ya existen). `TELEGRAM_BOT_SECRET_TOKEN` solo si registraste el webhook con `secret_token`; el código lo exige únicamente si la variable está definida.
- [ ] `GOOGLE_API_KEY`, `SUPABASE_URL` y `SUPABASE_KEY` como ahora.
- [ ] `SUNAT_ENVIO_HABILITADO` ausente o `false`.
- [ ] Opcional, con la CLI de Railway: `railway run python scripts/deployment_preflight.py --from-process-env --production`. No imprime valores y no se conecta a nada.

## 7. Desplegar
- [ ] Fusiona el PR en `main`. Railway construye con el `Dockerfile` (Python 3.12 e instalación desde el lock con hashes).
- [ ] En el log de build: `pip check` sin errores.
- [ ] En el log de deploy: `Starting gunicorn 26.2.0` y ningún error de `LIMITER_STORAGE_URI`.

## 8. Smoke test (10 min)
- [ ] `GET https://<dominio>/health` devuelve 200.
- [ ] El front permite iniciar sesión (las sesiones abiertas siguen valiendo), listar ventas y ver el dashboard.
- [ ] `GET /reportes/produccion-briquetas?periodo=dia` responde 200. Antes daba 500 siempre.
- [ ] Telegram: escribe `/start` al bot en privado y llega la bienvenida. Haz una consulta de stock.
- [ ] Logs de Railway: las peticiones al webhook aparecen como `POST /telegram/webhook/[oculto]`, sin el token.
- [ ] SQL Editor (solo lectura): `SELECT estado, count(*) FROM telegram_updates GROUP BY 1;` muestra filas `procesado` nuevas y ninguna `procesando` de hace más de 1 min.

## 9. Cron de Telegram (obligatorio)
Sin este cron, un mensaje que se quede a medias bloquea su chat. Railway debe tener un servicio que se ejecute y termine:
1. **+ New → GitHub Repo →** el mismo repo y la rama `main`. Nombre: `telegram-cron`.
2. Settings → **Cron Schedule:** `*/5 * * * *` (Railway usa UTC; aquí da igual).
3. Settings → **Custom Start Command:** `flask --app app telegram-pendientes`
4. Sin dominio público ni healthcheck HTTP.
5. Variables: las mismas que la API, por referencia (`DATABASE_URL=${{<servicio-api>.DATABASE_URL}}`, etc.) o con *Shared Variables*. Como mínimo: `FLASK_ENV`, `DATABASE_URL`, `JWT_SECRET_KEY`, `ALLOWED_ORIGINS`, `LIMITER_STORAGE_URI`, `TELEGRAM_BOT_TOKEN`.
- [ ] La primera ejecución imprime `telegram-pendientes: abandonados=0 procesados=0 errores=0 purgados=0` (o similar) y termina con código 0.

Este mismo mecanismo de cron servirá para los recordatorios de WhatsApp (F5).

## 10. Rotación de secretos (otro día, con el sistema estable)
Las notas de la auditoría recomiendan renovarlos. De menor a mayor impacto:
| Secreto | Impacto al rotarlo |
|---|---|
| `SUPABASE_KEY` (service key) | Ninguno si se cambia a la vez en Railway |
| Contraseña de la BD | Hay que actualizar `DATABASE_URL` en la API y en el cron |
| `TELEGRAM_WEBHOOK_SECRET` / `TELEGRAM_BOT_SECRET_TOKEN` | Hay que volver a registrar el webhook (`setWebhook` con la nueva URL y el `secret_token`) |
| `TELEGRAM_BOT_TOKEN` (BotFather → revoke) | Nuevo token y otra vez `setWebhook` |
| `JWT_SECRET_KEY` | **Cierra la sesión de todos** (vuelven a entrar) |
| `GOOGLE_API_KEY`, claves SUNAT | Según el proveedor |

## Rollback
- **Si algo falla tras el merge:** en Railway → Deployments, redespliega el deployment anterior (`9bdfd37`). **No hace falta tocar la BD.** Las 6 migraciones solo añaden cosas (tablas, columnas con default e índices) y el código viejo funciona sobre ellas: inserta en `telegram_updates` solo `update_id` y las columnas nuevas toman sus valores por defecto.
- **Si el fallo está en la migración (paso 5):** Alembic aplica cada revisión en una transacción. Si una falla, esa no queda a medias. Ejecuta `flask db current` para ver dónde quedó y revisa antes de reintentar.
- **Restaurar el dump** es el último recurso, ante una corrupción de datos y nunca por un fallo de código. Se decide con calma y con el dump del paso 3.

## Después del despliegue (pendiente de verificación en vivo)
- **A17 (índice de pagos):** queda verificado si el paso 4 marca **si** en `índice sobre pagos(venta_id)`.
- **A13 (gunicorn):** queda verificado con la versión del log de deploy. El proxy de Railway queda delante.
- **A26 (dependencias):** añadir `pip-audit` al CI (tarea aparte).
- **A10 (RLS de Supabase):** **no** forma parte de este despliegue. Es un cambio aparte que hay que revisar con `migrations/manual/20261022_backend_only_rls.sql` y los roles reales.
