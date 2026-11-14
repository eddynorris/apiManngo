# Notas L2 · A03, A06 y A11

## Alcance

Lote limitado a pagos y depósitos:

- A03: autorización por almacén/propietario en lectura, edición, borrado, lotes y depósitos.
- A06: validación de montos finitos y estrictamente positivos en pagos, depósitos y pagos iniciales.
- A11: serialización de la validación de saldo/disponible con locks deterministas y regresión PostgreSQL opt-in.

No se modificó producción, integraciones reales, `.env`, migraciones ni otros lotes. No se instalaron dependencias.

## Cambios realizados

### Autorización

- `services/authorization.py`
  - `autorizar_venta_para_pago(actor_id, venta)`: admin libre; no admin exige `venta.almacen_id == actor.almacen_id`.
  - `autorizar_pago(actor_id, pago)`: además de la venta, no admin exige `pago.usuario_id == actor.id`.
  - El actor se resuelve contra la BD vigente; no se confía en `usuario_id`, `rol` ni `almacen_id` del cuerpo.
- `resources/pago_resource.py`
  - GET individual, PUT, DELETE, `GET /pagos/venta/<id>` y depósitos autorizan el recurso real.
  - POST y batch autorizan las ventas antes de subir archivos.
  - Se mantiene el filtro de propietario de la lista y de pagos por venta para no admin.
  - Un PUT que intenta cambiar `venta_id` devuelve 400.

### Validación monetaria

- `services/money.py`: `monto_decimal_positivo()` rechaza `None`, bool, texto inválido, `NaN`, `Infinity`, cero, negativos y montos fuera del máximo.
- `schemas.py`: `PagoSchema` valida `monto`.
- `services/pago_service.py`: create/update/batch revalidan el monto en la capa de servicio.
- `resources/pago_resource.py`: depósitos validan `monto_depositado` antes de mutar.
- `services/venta_service.py`: pagos iniciales negativos/no finitos se rechazan y el pago inicial no puede exceder el total.
- `resources/venta_resource.py`: montos iniciales no numéricos se rechazan con 400 antes de tocar stock.
- `resources/transaccion_resource.py`: pagos y gastos iniciales validados; suma de pagos no puede exceder el total.

### Mass assignment

`PagoSchema` marca como `dump_only`:

- `id`
- `usuario_id`
- `depositado`
- `monto_depositado`
- `fecha_deposito`
- `url_comprobante`
- `monto_en_gerencia`

El recurso elimina además esos campos del cuerpo antes de cargar el schema. En POST el propietario se toma del actor autenticado.

### Concurrencia y consistencia

- `services/pago_service.py`
  - `_lock_ventas()` y `_lock_pagos()` bloquean en orden determinista por ID con `with_for_update()`.
  - `_total_pagado()` relee `SUM(pagos)` dentro de la transacción.
  - create/update/delete/batch bloquean venta y pago antes de validar y mutar.
  - update rechaza con 409 bajar el monto por debajo de `monto_depositado`.
  - batch valida todo antes de subir archivos y bloquea todas las ventas ordenadas.
- `resources/pago_resource.py`
  - depósitos validan IDs duplicados y montos, autorizan, suben el archivo y luego bloquean ventas/pagos ordenados para revalidar el disponible.
- `services/venta_service.py`
  - actualizar el total de una venta por debajo de los pagos registrados devuelve 409 y revierte stock/cambios.

### Comprobantes

- Se mantiene la finalización de Storage de L6 (`commit_with_storage_finalization()`), el seguimiento de claves subidas y la compensación.
- En sustituciones, la clave anterior solo se programa para borrado después de validar y subir la nueva.

## Pruebas añadidas

- `tests/test_pago_authorization.py`
  - GET/PUT/DELETE ajenos, mismo almacén y almacén distinto.
  - POST en almacén propio con otro vendedor permitido, propietario tomado del actor.
  - campos de servidor ignorados.
  - listado y pagos por venta sin filtrar información ajena.
  - batch mixto rechazado atómicamente y batch propio con dueño correcto.
- `tests/test_pago_validation.py`
  - montos `-1`, `0`, `abc`, `NaN`, `Infinity`, `-Infinity`, `1e10000`.
  - sobrepago y saldo exacto.
  - multipart negativo.
  - updates inválidos y 409 por depósito mayor que el nuevo monto.
  - batch inválido y suma agregada superior al total.
  - depósitos: sobrepago, disponible exacto, IDs duplicados y montos inválidos.
  - transacción completa: pago inicial negativo, sobrepago sin cambio de stock y caso válido.
- `tests/test_deposito_authorization.py`
  - depósito de pago de otro almacén y de otro propietario del mismo almacén.
  - depósito propio y admin.
  - lote mixto sin aplicación parcial.
- `tests/integration/test_pago_concurrency_postgres.py`
  - dos hilos con conexiones reales, venta de 100 y dos pagos de 70.
  - exige que solo uno se cree y que `SUM(pagos) <= 100`.
  - opt-in con `MANNGO_RUN_PG_TESTS=1`, `MANNGO_LOCAL_POSTGRES_URL` y `--allow-local-postgres`.
  - no imprime la URL.

## Ajuste de test L6

`tests/test_comprobante_replacement_failures.py` se ajustó para la conducta más segura del batch: si la validación falla, no se sube ningún archivo. El test ahora verifica `save_file.assert_not_called()`, que no hay claves que borrar y que no quedan pagos ni tareas de limpieza. Es una aserción más estricta, no un debilitamiento.

## Verificación

- Se intentó ejecutar el runner permitido:
  `venv/Scripts/python.exe scripts/run_tests_offline.py --no-bytecode --no-cache --disable-warnings -q tests/test_pago_authorization.py`
- Resultado: bloqueado antes de recolectar tests por `ModuleNotFoundError: No module named 'lxml'` al importar `services/sunat_service.py` desde `telegram/handlers/transferencia.py` al cargar `resources/__init__.py`.
- No se instalaron dependencias ni se modificaron parsers/telegram para evitar dependencias nuevas en Windows.
- La verificación completa y la prueba PostgreSQL A11 quedan para el entorno Docker del padre.

## Evidencia A11 del padre

- Artefacto: `docs/verificacion/A11before-20261022-191125`.
- Antes: dos conexiones reales, dos `POST /pagos` de 70 sobre venta de 100 devolvieron 201 y `SUM(pagos)=140`.
- Esperado después: una 201 y otra 400/409; `SUM(pagos)<=100`.
- La sonda del padre usa barrera tras leer pagos solo si no hubo `FOR UPDATE` previo en esa conexión.

## Limitaciones / pendientes

- No se pudo ejecutar ningún test en este entorno Windows por falta de `lxml`.
- No se añadió constraint de base de datos para `monto > 0`; requiere revisar datos existentes y migración. La validación de aplicación ya es estricta.
- No se verificó en PostgreSQL local desde este entorno.
- El test L6 se adaptó en este lote; el padre debe confirmar que la suite completa lo acepta.
