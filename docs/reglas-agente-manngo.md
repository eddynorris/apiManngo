# Contrato de implementación Manngo — OpenCode

Autorización vigente: implementar el plan `.hermes/plans/2026-10-22_160910-manngo-auditoria-y-carga-historica.md` por lotes y preparar integración SUNAT realista, SIN usar jamás la base de producción para pruebas. Leer estas reglas al empezar cada encargo.

## Prohibiciones
- NUNCA conectar a la BD de producción: ni SELECT, health check, migrations, backups, importaciones ni pruebas. Solo SQLite desechable o PostgreSQL local expresamente aislado; bloquear host remoto antes de construir conexión.
- NO leer, editar, imprimir ni cargar `.env`, `.env.local`, `.env.production`, credenciales/certificados reales o dumps de datos reales. `.env.example` es plantilla editable sin valores privados. El usuario cambiará sus secretos después: recordárselo al cierre.
- NO invocar MCP, Supabase remoto, Storage real, Gemini real, Telegram real, SUNAT de producción o beta, ni emitir documentos fiscales de prueba. Los tests usan dobles locales y material criptográfico de prueba generado localmente.
- NO git commit/push/reset/clean/stash, no descartar cambios previos, no editar AGENTS.md ni este contrato, el plan maestro o archivos de otros proyectos. No descargar modelos. No introducir secretos o datos personales en tests/logs.
- No omitir ni aflojar tests previos para pasar. Storage real puede excluirse con una razón explícita. Las regresiones nuevas no llevan skip/xfail para esconder fallos.
- No activar stock negativo, quitar autorización/RLS/constraints ni inventar clientes, teléfonos, licencias, direcciones, pesos, montos, fechas o comprobantes para facilitar ingreso de datos.
- No ejecutar lotes no encargados ni agentes concurrentes que escriban los mismos archivos. Si falta una decisión, dejar un bloqueo concreto en notas, no inventarla.

## Ejecución y pruebas
1. El árbol está sucio desde antes de la auditoría: preservar sus modificaciones. Editar lo mínimo y mantener contratos compatibles cuando sea seguro.
2. L0: SOLO bootstrap/runner seguro y tests de seguridad del entorno. No lanzar app.py/flask/pytest normal, ni usar dotenv real. Inicialmente bash está deshabilitado; Hermes ejecuta pruebas después de revisar el runner.
3. Tras L0, toda ejecución de tests pasa por `scripts/run_tests_offline.py`. El runner limpia claves de integraciones, fuerza env testing y SQLite local, bloquea sockets y librerías HTTP antes de importar app; exige autorización explícita de loopback para integración PostgreSQL local. No basta que el conftest reemplace DATABASE_URL después de cargar dotenv.
4. Cada arreglo: test que falla antes, reparación mínima, test después, suite afectada y lectura persistida del resultado. Informar límites: SQLite no comprueba locks PostgreSQL ni un doble confirma producción.
5. Dependencias solo cuando se encarguen, en entorno de tests dedicado. Documentar versión y razón. No cambiar proveedor principal de Hermes ni configuración global de OpenCode.
6. Mantener `docs/oc-notas-manngo.md`: lote, archivos modificados, comandos/resultado real o 'no ejecutado por restricción', decisiones y bloqueos. Los resúmenes son auto-reportes hasta verificación del padre.
7. El padre es dueño del contrato, plan, cola de encargos, verificación independiente y referencias oficiales SUNAT. OpenCode es dueño de implementar fuente y tests del lote asignado.

## Aclaración Telegram/Gemini
El usuario retiró la API key de Gemini. No buscarla ni pedirla para tests. Probar lógica, autenticación, deduplicación y fallos con dobles. El smoke test real del bot se deja para el final y pendiente si no hay credenciales.

## SUNAT
No fiarse ciegamente del skill: su revisión oficial está en curso. GRE requiere XML UBL firmado, ZIP, base64/hash y contrato REST real; el JSON plano con /gemini/despachos actual no es ese contrato. No tomar ticket, 200, ni texto 'procesado' como aceptación. Preparación local por defecto y envío real deshabilitado. Credenciales, certificado y prueba fiscal real quedan bajo una futura activación humana explícita. SUNAT no es un mecanismo para emitir con fecha antigua ventas fuera de plazo.
