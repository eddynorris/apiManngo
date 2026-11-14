# Notas de implementación — Lote L9 (ayuda de carga histórica)

**Estado:** dry-run local entregado; NO es una carga terminada ni una importación autorizada.

**Alcance respetado:** módulo puro, sin app/ORM/servicios de negocio, sin dotenv, sin red y sin base de datos. No se construyó un importador general, ni frontend, ni se cargaron datos reales. No se editó `app.py`, `models.py`, `common.py` ni servicios. No se usó producción, Supabase, Storage, Gemini, Telegram ni SUNAT.

## Archivos

- `scripts/prepare_historical_load.py` (nuevo): CLI de planificación dry-run sobre JSON explícito.
- `tests/test_historical_load_plan.py` (nuevo): fixture sintético identificado `SYNTHETIC_FIXTURE_L9_EXPLICIT_TWO_LOADS`, con dos cargas y conciliación.
- `docs/oc-notas-L9.md` (este archivo): solo la sección del lote L9.

## Qué valida el planificador

- Catálogos suministrados explícitamente: almacenes, presentaciones con unidad, clientes y proveedores. No inventa referencias.
- `source_ref` obligatorio y único; `operation_id` estable.
- Fechas ISO `YYYY-MM-DD` obligatorias, sin sustitución por la fecha actual; periodo `start/end` explícito.
- Cantidades finitas y positivas; unidad coherente con el catálogo; kg derivado de `kg_por_unidad` y comprobado si se declara.
- Tipos: `initial_balance`, `purchase`, `production`, `distribution`, `sale`.
- Ventas individuales (una presentación por venta). Huecos de cliente, cantidad, precio o fecha quedan en `pendientes`; no se crea cliente genérico ni se inventan precios/pesos/fechas.
- Conciliación por almacén/presentación/unidad: saldo inicial + compras + producción + transferencias entrada − transferencias salida − ventas − consumos = calculado; se compara con `declared_final_stock` si fue suministrado.
- Efectos separados para evitar doble conteo: saldo inicial no es compra/gasto; compra crea stock y gasto una sola vez; producción transforma sin duplicar el gasto; distribución es neutra entre almacenes; venta no crea pago automático.
- Si una venta requiere existencias cuya primera entrada es posterior, reporta `INCOMPATIBILIDAD_HISTORICA`; no reordena ni altera FIFO en secreto.
- Salida determinista y repetible. No existe opción `apply` ni conexión a BD. El plan queda en `estado: planificado_no_ejecutado`; no se crea marcador de "realizado" antes de una operación real.

## Diferencia obligatoria de objetivos

- **Regularización inicial:** documenta existencias/saldos a una fecha de corte. No reconstruye el kardex cronológico ni el FIFO histórico.
- **Reconstrucción cronológica:** ordena operaciones por fecha real y verifica disponibilidad a la fecha de cada venta. Requiere fechas y secuencia reales; las incompatibilidades se reportan, no se corrigen en silencio.

No son equivalentes y el planificador solo ayuda a decidir; no ejecuta ninguna de las dos.

## Pendiente del usuario (bloqueos reales)

- Periodo real (inicio/fin) y fuente de las ventas: aún no suministrados.
- Dos cargas reales: proveedor, fechas, pesos/unidades y documentos: aún no suministrados.
- Inventario/insumos/producción/transferencias/ventas/pagos ya registrados: no inventariados.
- Nivel de trazabilidad exigido y responsable autorizado de la carga.
- Copia desechable, backup/restauración y autorización nueva para cualquier ejecución real.

## Corrección acotada tras verificación Docker del padre

- **Falso positivo corregido:** el test de datos insuficientes buscaba el substring `cliente generico` en todo el JSON; el diagnóstico correcto `No se usa un cliente generico` lo hacía fallar sin probar una fabricación de cliente. Ahora se verifica estructuralmente: `VENTA-1` no está en operaciones aceptadas, sí está en `pendientes`, el payload de entrada queda inmutable y los campos ausentes (`cliente`, `cantidad`, `precio_unitario`) no se inyectan. Solo `VENTA-2`/`CL2` aparece como venta aceptada y ese cliente pertenece al catálogo suministrado.
- **Sobrescritura de fuente corregida:** `--output` igual a `--input` (incluida ruta normalizada o symlink resuelto) se rechaza con exit 2 y JSON de error seguro, sin tocar bytes.
- **Salida existente corregida:** el plan se crea con `os.open(..., O_CREAT | O_EXCL)`, sin comprobar-existe-y-luego-escribir. Un archivo existente produce exit 2 y conserva sus bytes; no hay `--force` ni `apply`.
- La escritura de un plan nuevo explícito sigue permitida, es determinista y no sobrescribe. Errores de lectura UTF-8/OSError y de creación/escritura se reportan como JSON accionable sin traceback.
- Pruebas añadidas para: mismo archivo, ruta normalizada equivalente, archivo existente, symlink (o existente como fallback si el sistema no permite crear symlinks), salida nueva determinista y entrada UTF-8 inválida.

## Pruebas

- Archivo de pruebas actualizado, pero **no ejecutado en esta sesión**: no hay shell/Bash disponible. El padre ejecutará la verificación real en Docker.
- Comando previsto para el padre:
  `venv/Scripts/python.exe scripts/run_tests_offline.py --no-bytecode --no-cache --disable-warnings -q tests/test_historical_load_plan.py`
- No se declara resultado de pytest como obtenido. No se usaron `skip`/`xfail` ni se relajaron aserciones originales del usuario.
- Limitación: son pruebas de un planificador puro; no validan PostgreSQL, locks, autorización HTTP ni una carga real. El caso symlink se ejecuta si el sistema permite crearlo; si no, se comprueba el rechazo de la ruta existente.

## Recordatorio

El usuario debe rotar/cambiar sus secretos reales en sus `.env` fuera de este lote; este lote no leyó ni editó esos archivos.
