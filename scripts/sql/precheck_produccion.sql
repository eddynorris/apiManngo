-- Verificación previa al despliegue F3: SOLO LECTURA (no crea, no modifica, no borra nada).
-- Ejecutar en el SQL Editor de Supabase (o con psql) sobre la BD de producción y pegar el resultado.
-- No devuelve datos personales: solo existencia de objetos y conteos.
--
-- Sirve para decidir con qué revisión de Alembic hay que hacer `flask db stamp` antes de `flask db upgrade`:
-- se estampa la ÚLTIMA revisión cuyas marcas estén todas en "si" siguiendo el orden de la cadena.

WITH marcas AS (
    -- 0. ¿Alembic ya gestiona esta BD?
    SELECT 0 AS orden, '-' AS revision, 'tabla alembic_version' AS marca,
           (to_regclass('public.alembic_version') IS NOT NULL) AS presente
    UNION ALL
    -- 1. 2d09ea8fe981 add_db_improvements
    SELECT 1, '2d09ea8fe981', 'columna movimientos.venta_id',
           EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'movimientos' AND column_name = 'venta_id')
    UNION ALL
    SELECT 1, '2d09ea8fe981', 'constraint uq_inventario_compuesto',
           EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_inventario_compuesto')
    UNION ALL
    SELECT 1, '2d09ea8fe981', 'check ck_lotes_kg_no_negativo',
           EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_lotes_kg_no_negativo')
    UNION ALL
    SELECT 1, '2d09ea8fe981', 'índice idx_ventas_almacen_fecha',
           (to_regclass('public.idx_ventas_almacen_fecha') IS NOT NULL)
    UNION ALL
    SELECT 1, '2d09ea8fe981', 'venta_detalles.cantidad es numeric',
           EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'venta_detalles'
                     AND column_name = 'cantidad' AND data_type = 'numeric')
    UNION ALL
    -- 2. 6efd542ce540 add_telegram_updates_table
    SELECT 2, '6efd542ce540', 'tabla telegram_updates',
           (to_regclass('public.telegram_updates') IS NOT NULL)
    UNION ALL
    -- 3. a1b2c3d4e5f6 add_storage_cleanup_tasks
    SELECT 3, 'a1b2c3d4e5f6', 'tabla storage_cleanup_tasks',
           (to_regclass('public.storage_cleanup_tasks') IS NOT NULL)
    UNION ALL
    -- 4. b7c1d2e3f4a5 add_user_auth_version
    SELECT 4, 'b7c1d2e3f4a5', 'columna users.auth_version',
           EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'auth_version')
    UNION ALL
    -- 5. c4d5e6f7a8b9 add_movimiento_scopes
    SELECT 5, 'c4d5e6f7a8b9', 'tabla movimiento_scopes',
           (to_regclass('public.movimiento_scopes') IS NOT NULL)
    UNION ALL
    -- 6. f1a2b3c4d5e6 add_pagos_venta_index (cualquier índice cuya primera columna sea pagos.venta_id)
    SELECT 6, 'f1a2b3c4d5e6', 'índice sobre pagos(venta_id)',
           EXISTS (SELECT 1 FROM pg_index i
                   JOIN pg_class t ON t.oid = i.indrelid AND t.relname = 'pagos'
                   JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = i.indkey[0]
                   WHERE a.attname = 'venta_id')
    UNION ALL
    -- 7. e8f9a0b1c2d3 telegram_updates_inbox
    SELECT 7, 'e8f9a0b1c2d3', 'columna telegram_updates.estado',
           EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'telegram_updates' AND column_name = 'estado')
)
SELECT orden, revision, marca, CASE WHEN presente THEN 'si' ELSE 'NO' END AS presente
FROM marcas
ORDER BY orden, marca;

-- Revisión registrada por Alembic y riesgos de datos para migraciones pendientes (solo conteos).
-- query_to_xml solo se evalúa si la tabla existe, así la consulta no falla en una BD sin esas tablas.
SELECT 'alembic_version.version_num' AS dato,
       CASE WHEN to_regclass('public.alembic_version') IS NOT NULL THEN
           (xpath('/row/v/text()', query_to_xml('SELECT string_agg(version_num, '','') AS v FROM alembic_version', false, true, '')))[1]::text
       END AS valor
UNION ALL
SELECT 'inventario duplicado (presentacion, almacen, lote)',
       (SELECT count(*) FROM (SELECT presentacion_id, almacen_id, lote_id FROM inventario
                              GROUP BY 1, 2, 3 HAVING count(*) > 1) d)::text
UNION ALL
SELECT 'inventario duplicado sin lote (presentacion, almacen)',
       (SELECT count(*) FROM (SELECT presentacion_id, almacen_id FROM inventario WHERE lote_id IS NULL
                              GROUP BY 1, 2 HAVING count(*) > 1) d)::text
UNION ALL
SELECT 'lotes con cantidad_disponible_kg negativa',
       (SELECT count(*) FROM lotes WHERE cantidad_disponible_kg < 0)::text
UNION ALL
SELECT 'filas en telegram_updates',
       CASE WHEN to_regclass('public.telegram_updates') IS NOT NULL THEN
           (xpath('/row/c/text()', query_to_xml('SELECT count(*) AS c FROM telegram_updates', false, true, '')))[1]::text
       END
UNION ALL
SELECT 'usuarios', (SELECT count(*) FROM users)::text
UNION ALL
SELECT 'version de PostgreSQL', current_setting('server_version');
