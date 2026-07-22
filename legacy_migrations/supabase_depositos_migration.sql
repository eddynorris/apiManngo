-- =====================================================
-- MIGRACIÓN SUPABASE: Campos de Depósito en Pagos
-- =====================================================
-- Este script agrega campos para rastrear depósitos bancarios
-- en la tabla pagos de Supabase PostgreSQL

-- Verificar si las columnas ya existen antes de agregarlas
DO $$
BEGIN
    -- Agregar columna monto_depositado si no existe
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='pagos' AND column_name='monto_depositado') THEN
        ALTER TABLE pagos ADD COLUMN monto_depositado DECIMAL(10,2) DEFAULT 0.00;
        RAISE NOTICE 'Columna monto_depositado agregada exitosamente';
    ELSE
        RAISE NOTICE 'Columna monto_depositado ya existe';
    END IF;

    -- Agregar columna depositado si no existe
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='pagos' AND column_name='depositado') THEN
        ALTER TABLE pagos ADD COLUMN depositado BOOLEAN DEFAULT FALSE;
        RAISE NOTICE 'Columna depositado agregada exitosamente';
    ELSE
        RAISE NOTICE 'Columna depositado ya existe';
    END IF;

    -- Agregar columna fecha_deposito si no existe
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='pagos' AND column_name='fecha_deposito') THEN
        ALTER TABLE pagos ADD COLUMN fecha_deposito TIMESTAMP;
        RAISE NOTICE 'Columna fecha_deposito agregada exitosamente';
    ELSE
        RAISE NOTICE 'Columna fecha_deposito ya existe';
    END IF;
END $$;

-- Agregar restricciones de validación
DO $$
BEGIN
    -- Restricción: monto_depositado no puede ser negativo
    IF NOT EXISTS (SELECT 1 FROM information_schema.check_constraints 
                   WHERE constraint_name='pagos_monto_depositado_no_negativo') THEN
        ALTER TABLE pagos ADD CONSTRAINT pagos_monto_depositado_no_negativo 
        CHECK (monto_depositado >= 0);
        RAISE NOTICE 'Restricción monto_depositado_no_negativo agregada';
    END IF;

    -- Restricción: monto_depositado no puede exceder el monto total
    IF NOT EXISTS (SELECT 1 FROM information_schema.check_constraints 
                   WHERE constraint_name='pagos_monto_depositado_valido') THEN
        ALTER TABLE pagos ADD CONSTRAINT pagos_monto_depositado_valido 
        CHECK (monto_depositado <= monto);
        RAISE NOTICE 'Restricción monto_depositado_valido agregada';
    END IF;

    -- Restricción: si depositado es true, debe tener fecha_deposito
    IF NOT EXISTS (SELECT 1 FROM information_schema.check_constraints 
                   WHERE constraint_name='pagos_deposito_con_fecha') THEN
        ALTER TABLE pagos ADD CONSTRAINT pagos_deposito_con_fecha 
        CHECK (NOT depositado OR fecha_deposito IS NOT NULL);
        RAISE NOTICE 'Restricción deposito_con_fecha agregada';
    END IF;
END $$;

-- Crear índices para mejorar rendimiento
CREATE INDEX IF NOT EXISTS idx_pagos_depositado ON pagos(depositado);
CREATE INDEX IF NOT EXISTS idx_pagos_fecha_deposito ON pagos(fecha_deposito);
CREATE INDEX IF NOT EXISTS idx_pagos_monto_depositado ON pagos(monto_depositado);

-- Comentarios en las columnas para documentación
COMMENT ON COLUMN pagos.monto_depositado IS 'Monto real depositado en cuenta corporativa';
COMMENT ON COLUMN pagos.depositado IS 'Indica si el pago fue depositado en cuenta corporativa';
COMMENT ON COLUMN pagos.fecha_deposito IS 'Fecha y hora del depósito bancario';

-- =====================================================
-- FUNCIONES ÚTILES PARA CONSULTAS
-- =====================================================

-- Función para calcular monto en gerencia (dinero retenido)
CREATE OR REPLACE FUNCTION calcular_monto_en_gerencia(pago_monto DECIMAL, pago_monto_depositado DECIMAL)
RETURNS DECIMAL AS $$
BEGIN
    RETURN COALESCE(pago_monto, 0) - COALESCE(pago_monto_depositado, 0);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Vista para obtener resumen de depósitos
CREATE OR REPLACE VIEW vista_resumen_depositos AS
SELECT 
    COUNT(*) as total_pagos,
    COUNT(CASE WHEN depositado = true THEN 1 END) as pagos_depositados,
    COUNT(CASE WHEN depositado = false OR depositado IS NULL THEN 1 END) as pagos_pendientes,
    COALESCE(SUM(monto), 0) as monto_total_pagos,
    COALESCE(SUM(monto_depositado), 0) as monto_total_depositado,
    COALESCE(SUM(calcular_monto_en_gerencia(monto, monto_depositado)), 0) as monto_total_en_gerencia
FROM pagos;

-- Vista detallada de pagos con información de depósito
CREATE OR REPLACE VIEW vista_pagos_depositos AS
SELECT 
    p.id,
    p.venta_id,
    p.usuario_id,
    p.monto,
    p.monto_depositado,
    calcular_monto_en_gerencia(p.monto, p.monto_depositado) as monto_en_gerencia,
    p.depositado,
    p.fecha_deposito,
    p.fecha,
    p.metodo_pago,
    p.referencia,
    p.created_at,
    p.updated_at,
    -- Información adicional de la venta
    v.numero_venta,
    v.cliente_id,
    v.total as venta_total
FROM pagos p
LEFT JOIN ventas v ON p.venta_id = v.id;

-- =====================================================
-- CONSULTAS DE EJEMPLO ÚTILES
-- =====================================================

-- 1. Resumen general de depósitos
-- SELECT * FROM vista_resumen_depositos;

-- 2. Pagos pendientes de depósito
-- SELECT * FROM vista_pagos_depositos WHERE depositado = false OR depositado IS NULL;

-- 3. Pagos con dinero retenido en gerencia
-- SELECT * FROM vista_pagos_depositos WHERE monto_en_gerencia > 0;

-- 4. Depósitos realizados en un rango de fechas
-- SELECT * FROM vista_pagos_depositos 
-- WHERE depositado = true 
-- AND fecha_deposito BETWEEN '2024-01-01' AND '2024-12-31';

-- 5. Total de dinero en gerencia por usuario
-- SELECT 
--     usuario_id,
--     SUM(monto_en_gerencia) as total_en_gerencia
-- FROM vista_pagos_depositos 
-- GROUP BY usuario_id 
-- HAVING SUM(monto_en_gerencia) > 0;

-- =====================================================
-- DATOS DE EJEMPLO (OPCIONAL - DESCOMENTA SI NECESITAS)
-- =====================================================

-- Actualizar registros existentes con valores por defecto
-- UPDATE pagos SET 
--     monto_depositado = 0.00,
--     depositado = false
-- WHERE monto_depositado IS NULL OR depositado IS NULL;

-- =====================================================
-- VERIFICACIÓN FINAL
-- =====================================================

-- Verificar que las columnas se crearon correctamente
SELECT 
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
    constraint_name,
    check_clause
FROM information_schema.check_constraints 
WHERE constraint_name LIKE 'pagos_%deposito%'
OR constraint_name LIKE 'pagos_monto_depositado%';

RAISE NOTICE '✅ Migración completada exitosamente. Revisa los resultados de las consultas de verificación.';