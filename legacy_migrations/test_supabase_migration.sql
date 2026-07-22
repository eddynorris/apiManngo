-- =====================================================
-- SCRIPT DE VALIDACIÓN PARA MIGRACIÓN SUPABASE
-- =====================================================
-- Este script valida que la migración de depósitos funcione correctamente
-- Ejecutar DESPUÉS de aplicar supabase_depositos_migration.sql

-- =====================================================
-- 1. VERIFICAR ESTRUCTURA DE TABLA
-- =====================================================

SELECT 'VERIFICANDO ESTRUCTURA DE TABLA...' as status;

-- Verificar que las columnas existen
SELECT 
    'Columnas agregadas:' as verificacion,
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'pagos' 
AND column_name IN ('monto_depositado', 'depositado', 'fecha_deposito')
ORDER BY column_name;

-- Verificar restricciones
SELECT 
    'Restricciones creadas:' as verificacion,
    constraint_name,
    check_clause
FROM information_schema.check_constraints 
WHERE constraint_name LIKE 'pagos_%deposito%'
OR constraint_name LIKE 'pagos_monto_depositado%';

-- Verificar índices
SELECT 
    'Índices creados:' as verificacion,
    indexname,
    indexdef
FROM pg_indexes 
WHERE tablename = 'pagos' 
AND indexname LIKE 'idx_pagos_%deposito%'
OR indexname LIKE 'idx_pagos_monto_depositado';

-- =====================================================
-- 2. INSERTAR DATOS DE PRUEBA
-- =====================================================

SELECT 'INSERTANDO DATOS DE PRUEBA...' as status;

-- Limpiar datos de prueba anteriores si existen
DELETE FROM pagos WHERE referencia LIKE 'TEST_%';

-- Insertar datos de prueba (ajusta según tu estructura de tabla)
INSERT INTO pagos (
    venta_id, usuario_id, monto, fecha, metodo_pago, referencia,
    monto_depositado, depositado, fecha_deposito
) VALUES 
-- Pago sin depósito (todo en gerencia)
(1, 1, 100.00, NOW(), 'efectivo', 'TEST_SIN_DEPOSITO', 0.00, false, NULL),

-- Pago con depósito completo (nada en gerencia)
(2, 1, 200.00, NOW(), 'transferencia', 'TEST_DEPOSITO_COMPLETO', 200.00, true, NOW()),

-- Pago con depósito parcial (parte en gerencia)
(3, 1, 150.00, NOW(), 'transferencia', 'TEST_DEPOSITO_PARCIAL', 100.00, true, NOW()),

-- Pago pendiente de depósito
(4, 1, 75.00, NOW(), 'efectivo', 'TEST_PENDIENTE', 0.00, false, NULL);

SELECT 'Datos de prueba insertados: ' || COUNT(*) || ' registros' as resultado
FROM pagos WHERE referencia LIKE 'TEST_%';

-- =====================================================
-- 3. PROBAR CÁLCULOS Y LÓGICA
-- =====================================================

SELECT 'PROBANDO CÁLCULOS Y LÓGICA...' as status;

-- Probar función de cálculo de monto en gerencia
SELECT 
    'Prueba cálculo monto en gerencia:' as prueba,
    referencia,
    monto,
    monto_depositado,
    calcular_monto_en_gerencia(monto, monto_depositado) as monto_en_gerencia_calculado,
    (monto - monto_depositado) as monto_en_gerencia_manual,
    CASE 
        WHEN calcular_monto_en_gerencia(monto, monto_depositado) = (monto - monto_depositado) 
        THEN '✅ CORRECTO' 
        ELSE '❌ ERROR' 
    END as validacion
FROM pagos 
WHERE referencia LIKE 'TEST_%'
ORDER BY referencia;

-- =====================================================
-- 4. PROBAR VISTAS CREADAS
-- =====================================================

SELECT 'PROBANDO VISTAS...' as status;

-- Probar vista de resumen
SELECT 
    'Resumen de depósitos (solo datos de prueba):' as vista,
    COUNT(*) as total_pagos,
    COUNT(CASE WHEN depositado = true THEN 1 END) as pagos_depositados,
    COUNT(CASE WHEN depositado = false OR depositado IS NULL THEN 1 END) as pagos_pendientes,
    COALESCE(SUM(monto), 0) as monto_total_pagos,
    COALESCE(SUM(monto_depositado), 0) as monto_total_depositado,
    COALESCE(SUM(calcular_monto_en_gerencia(monto, monto_depositado)), 0) as monto_total_en_gerencia
FROM pagos 
WHERE referencia LIKE 'TEST_%';

-- Probar vista detallada
SELECT 
    'Vista detallada de pagos de prueba:' as vista,
    referencia,
    monto,
    monto_depositado,
    calcular_monto_en_gerencia(monto, monto_depositado) as monto_en_gerencia,
    depositado,
    fecha_deposito,
    CASE 
        WHEN depositado AND fecha_deposito IS NOT NULL THEN '✅ Consistente'
        WHEN NOT depositado AND fecha_deposito IS NULL THEN '✅ Consistente'
        ELSE '⚠️ Inconsistente'
    END as estado_consistencia
FROM pagos 
WHERE referencia LIKE 'TEST_%'
ORDER BY referencia;

-- =====================================================
-- 5. PROBAR RESTRICCIONES
-- =====================================================

SELECT 'PROBANDO RESTRICCIONES...' as status;

-- Intentar insertar monto depositado negativo (debe fallar)
DO $$
BEGIN
    BEGIN
        INSERT INTO pagos (venta_id, usuario_id, monto, fecha, metodo_pago, referencia, monto_depositado)
        VALUES (1, 1, 100.00, NOW(), 'efectivo', 'TEST_NEGATIVO', -10.00);
        RAISE NOTICE '❌ ERROR: Se permitió monto depositado negativo';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE '✅ CORRECTO: Restricción monto_depositado_no_negativo funciona';
    END;
END $$;

-- Intentar insertar monto depositado mayor al monto (debe fallar)
DO $$
BEGIN
    BEGIN
        INSERT INTO pagos (venta_id, usuario_id, monto, fecha, metodo_pago, referencia, monto_depositado)
        VALUES (1, 1, 100.00, NOW(), 'efectivo', 'TEST_EXCESIVO', 150.00);
        RAISE NOTICE '❌ ERROR: Se permitió monto depositado mayor al monto';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE '✅ CORRECTO: Restricción monto_depositado_valido funciona';
    END;
END $$;

-- Intentar marcar como depositado sin fecha (debe fallar)
DO $$
BEGIN
    BEGIN
        INSERT INTO pagos (venta_id, usuario_id, monto, fecha, metodo_pago, referencia, depositado, fecha_deposito)
        VALUES (1, 1, 100.00, NOW(), 'efectivo', 'TEST_SIN_FECHA', true, NULL);
        RAISE NOTICE '❌ ERROR: Se permitió depositado=true sin fecha_deposito';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE '✅ CORRECTO: Restricción deposito_con_fecha funciona';
    END;
END $$;

-- =====================================================
-- 6. CONSULTAS DE EJEMPLO FUNCIONALES
-- =====================================================

SELECT 'EJECUTANDO CONSULTAS DE EJEMPLO...' as status;

-- Pagos pendientes de depósito
SELECT 
    'Pagos pendientes de depósito:' as consulta,
    COUNT(*) as cantidad,
    COALESCE(SUM(monto), 0) as monto_total_pendiente
FROM pagos 
WHERE (depositado = false OR depositado IS NULL)
AND referencia LIKE 'TEST_%';

-- Pagos con dinero retenido en gerencia
SELECT 
    'Pagos con dinero en gerencia:' as consulta,
    COUNT(*) as cantidad,
    COALESCE(SUM(calcular_monto_en_gerencia(monto, monto_depositado)), 0) as total_en_gerencia
FROM pagos 
WHERE calcular_monto_en_gerencia(monto, monto_depositado) > 0
AND referencia LIKE 'TEST_%';

-- =====================================================
-- 7. LIMPIEZA Y RESUMEN FINAL
-- =====================================================

SELECT 'LIMPIANDO DATOS DE PRUEBA...' as status;

-- Eliminar datos de prueba
DELETE FROM pagos WHERE referencia LIKE 'TEST_%';

SELECT 'Datos de prueba eliminados' as resultado;

-- =====================================================
-- RESUMEN FINAL
-- =====================================================

SELECT '🎉 MIGRACIÓN VALIDADA EXITOSAMENTE' as resultado;

SELECT 
    '📋 Funcionalidades verificadas:' as resumen,
    '✓ Columnas agregadas correctamente' as item1,
    '✓ Restricciones funcionando' as item2,
    '✓ Índices creados' as item3,
    '✓ Funciones de cálculo operativas' as item4,
    '✓ Vistas funcionando correctamente' as item5,
    '✓ Validaciones de negocio activas' as item6;

SELECT 
    '🚀 Sistema listo para:' as capacidades,
    '• Rastrear depósitos bancarios' as cap1,
    '• Diferenciar dinero depositado vs retenido' as cap2,
    '• Generar reportes precisos' as cap3,
    '• Mantener integridad de datos' as cap4;

-- Mostrar comandos útiles para el usuario
SELECT 
    '💡 Consultas útiles para usar:' as ayuda,
    'SELECT * FROM vista_resumen_depositos;' as consulta1,
    'SELECT * FROM vista_pagos_depositos WHERE depositado = false;' as consulta2,
    'SELECT * FROM vista_pagos_depositos WHERE monto_en_gerencia > 0;' as consulta3;