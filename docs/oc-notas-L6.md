# Notas de implementación — Lote L6 (A09, A12, A13, A26)

## Alcance ejecutado

Solo L6: validación real de archivos (A09), sustitución segura de comprobantes con limpieza diferida (A12), actualización de dependencias/Dockerfile (A13 y A26) y correcciones de compatibilidad asociadas (logger, `.gitignore`, `.dockerignore`). No se ejecutó ningún lote siguiente.

No se usó producción, PostgreSQL remoto, dotenv real, Supabase/Storage real, Gemini, Telegram ni SUNAT. No se hicieron `git add`, commit, push, reset, clean ni stash. No se editaron `.env`, `.env.local` ni `.env.production`.

## Archivos modificados/creados

- `utils/file_handlers.py`: lectura acotada, detección por bytes reales, whitelist explícita de JPEG/PNG para Pillow, límites de bytes/píxeles, validación de PDF y conversión segura a WebP.
- `models.py`: nueva tabla durable `storage_cleanup_tasks`.
- `migrations/versions/a1b2c3d4e5f6_add_storage_cleanup_tasks.py`: migración de la tabla anterior (no aplicada).
- `services/storage_cleanup_service.py` (nuevo): cola durable, compensación de subidas, procesamiento posterior al commit y reintentos.
- `services/pago_service.py`: subida primero; programación del borrado anterior en la misma transacción; sin borrados inmediatos antes del commit.
- `resources/pago_resource.py`: commit con finalización de Storage en alta, edición, borrado, lote y depósitos; compensación en errores.
- `resources/venta_resource.py`: usa el mismo finalizador para comprobantes adjuntos en alta/edición de venta.
- `utils/logger_config.py`: eliminado el acceso incompatible a `app.config['ENV']`; conserva configuración explícita (`ENV`/`FLASK_ENV`) y cae a `FLASK_ENV`.
- `requirements.txt`: versiones de `docs/DEPENDENCIAS_VERIFICADAS.md`; `Flask-Marshmallow` se mantiene en `1.3.0` con `marshmallow==3.26.2`.
- `Dockerfile`: las dos etapas usan `python:3.12-slim`.
- `.dockerignore`: excluye dotenv reales, dumps/SQL, certificados privados, venvs y logs; mantiene `.env.example`.
- `.gitignore`: deja de ignorar `tests/` y `migrations/` completos; excluye solo artefactos, datos privados, dumps, certificados y logs. No se hizo `git add`.
- `tests/test_upload_validation.py`, `tests/test_comprobante_replacement_failures.py`, `tests/test_logger_environment.py`, `tests/test_dependency_pins.py`: regresiones nuevas.
- `docs/oc-notas-L6.md`: este registro.

No se modificaron tests originales ni se añadieron `skip`/`xfail`.

## A09 · Validación de archivos

Comportamiento implementado:

1. Lee el stream una sola vez con límite absoluto; no confía en `filename`, extensión ni `Content-Type`.
2. Detecta el formato real por firma: JPEG (`FF D8 FF`), PNG (firma PNG) y PDF (`%PDF-`).
3. Exige coincidencia entre formato real y extensión declarada.
4. Para imágenes usa `Image.open(..., formats=('JPEG', 'PNG'))`, comprueba dimensiones antes de cargar y luego `verify()`/`load()`; un PSD u otro formato renombrado no entra a la autodetección.
5. Límites por defecto: 10 MiB por imagen, 25 MiB por PDF y 25 000 000 píxeles. Son configurables por `MAX_IMAGE_UPLOAD_BYTES`, `MAX_PDF_UPLOAD_BYTES` y `MAX_IMAGE_PIXELS`.
6. PDF: exige cabecera `%PDF-` y marcador `%%EOF` en la cola; no se parsea ni se ejecuta ningún payload. Sigue subiéndose sin conversión, como antes.
7. Solo entonces convierte JPEG/PNG válidos a WebP y sube. Los errores de decodificación/validación devuelven `None`, sin subir nada.

Reproducción real antes de reparar (mismo comando permitido, solo este archivo):

- `5 failed, 6 passed` en `tests/test_upload_validation.py`.
- Fallaron: extensión/contenido no coincidente, MIME declarado no confiable, límite de bytes, límite de píxeles y PDF truncado.
- Resultado después de la reparación: `11 passed`.

No se ejecutaron imágenes diseñadas para corrupción de memoria.

## A12 · Sustitución de comprobantes y limpieza durable

Protocolo implementado:

1. Al reemplazar, se sube primero el nuevo archivo y se registra su clave.
2. La referencia nueva y una tarea `storage_cleanup_tasks` para la clave anterior se escriben en la **misma transacción SQL**.
3. El commit de negocio ocurre antes de cualquier borrado. Solo después se procesan las tareas pendientes.
4. Si el commit falla: rollback conserva la referencia anterior; la subida nueva no confirmada se compensa con borrado. Si esa compensación falla, se persiste una tarea de limpieza en una transacción nueva.
5. Si el borrado del anterior falla, la tarea permanece con `attempts`, `last_error` y `next_attempt_at`; no se declara limpieza exitosa.
6. Si la clave anterior sigue referenciada por otro pago, no se programa el borrado; además, el procesador vuelve a comprobar referencias justo antes de borrar. Una tarea retenida por referencia compartida no se elimina.
7. Alta, edición, borrado, pagos batch y depósitos usan el mismo finalizador. Los servicios de pago no confirman SQL por su cuenta para cerrar Storage; la ruta confirma y luego procesa la cola.

Cobertura nueva en `tests/test_comprobante_replacement_failures.py` (10 casos):

- orden subida nueva → commit → borrado anterior, comprobando la referencia nueva dentro del callback de borrado;
- mismo orden con un Storage falso que registra `upload` y `delete`, sin tocar red real;
- commit fallido: referencia anterior conservada, subida nueva compensada, sin tarea huérfana;
- commit y compensación fallidos: se conserva una tarea durable para borrar la subida nueva;
- fallo de subida: anterior intacto;
- fallo de borrado: tarea durable con intento y error; reintento exitoso la elimina;
- clave compartida por dos pagos: no se borra;
- borrado de pago: el archivo se elimina solo después del commit;
- fallo de validación en batch: se compensa el comprobante subido;
- depósito: se ve la referencia nueva antes de borrar la anterior.

Todas usan dobles locales que registran orden y simulan fallos. No se tocó Storage real.

## A13 y A26 · Dependencias y despliegue

- `requirements.txt` quedó con las versiones verificadas del documento: Flask 3.1.3, Werkzeug 3.1.9, Flask-Cors 6.0.5, Flask-JWT-Extended 4.7.4, Flask-Limiter 4.1.1, Flask-SQLAlchemy 3.1.1, Pillow 13.0.0, gunicorn 26.2.0, cryptography 50.0.2, PyJWT 2.15.1, python-dotenv 1.2.4, requests 2.34.2, Jinja2 3.1.6, setuptools 84.2.1, marshmallow 3.26.2, PyYAML 6.0.3 y openpyxl 3.1.5. Se mantiene `Flask-Marshmallow==1.3.0`; no se usó `1.5.0`.
- Se añadieron `lxml==6.1.3` y `signxml==5.2.0` como preparación estructural de GRE, según el documento. No se emitió ni validó fiscalmente nada.
- No se creó ni inventó `requirements.lock` ni hashes. El lock real resuelto por el padre queda pendiente de incorporación.
- `tests/test_dependency_pins.py` comprueba los pins críticos y que el Dockerfile use Python 3.12.
- La auditoría del padre no se interpreta aquí como ausencia general de vulnerabilidades.

## Pruebas ejecutadas (resultados reales)

Comando permitido, desde la raíz:

`venv/Scripts/python.exe scripts/run_tests_offline.py --no-bytecode --no-cache --disable-warnings -q ...`

- L6 focalizado (nuevos + batch + autorización de ventas): **95 passed**.
- Suite con `--ignore=tests/test_sunat_xml.py`: **234 passed, 2 failed, 16 skipped**. Ninguno de los 16 skipped pertenece a los archivos nuevos de L6.
- Los 2 fallos son ajenos a L6 y reproducen igual al ejecutar solo `tests/test_reporte_produccion_entradas.py`: `test_reporte_entradas_completo` espera 230 unidades y recibe 150; `test_reporte_entradas_filtro_tipo_operacion` espera 1 traslado y recibe 0. No se editó ese código ni su test porque no pertenece a este lote.
- Sin `--ignore`, la suite se detiene en colección de `tests/test_sunat_xml.py` porque el venv local no tiene `lxml`. No se instaló nada.
- El intérprete local del venv resolvió stdlib bajo Python313 según la traza del error; no se ejecutó aquí la matriz Python 3.12. El padre ya reportó la matriz 3.12 con 139 tests en el entorno candidato.
- No se ejecutó Docker build, `python app.py`, `flask`, migraciones ni conexiones PostgreSQL/Storage/SUNAT.

## Bloqueos y límites

- `requirements.lock` con hashes no está en el repo; no se inventó.
- `tests/test_sunat_xml.py` no puede colectarse en el venv local por falta de `lxml`; debe verificarse en el entorno/Docker del padre con las dependencias del lock.
- Persisten 2 fallos previos/ajenos en `tests/test_reporte_produccion_entradas.py`; no se tocaron.
- SQLite no valida carreras de referencia entre transacciones ni locks PostgreSQL. La comprobación de clave compartida es una defensa lógica dentro de la transacción, no una prueba de concurrencia real.
- La migración `a1b2c3d4e5f6` no se aplicó a ninguna base.
- El procesamiento de la cola ocurre tras las operaciones de pago/venta. Un reintento periódico externo para tareas que fallen repetidamente queda pendiente de operación; no se simuló un worker real.
- Recordatorio: los secretos reales del usuario siguen fuera de este lote y deben rotarse/cambiarse por el usuario cuando corresponda.
