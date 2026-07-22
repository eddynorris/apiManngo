-- Migration: Add 'compra' to chk_tipo_operacion constraint on movimientos table
-- This fixes the CheckViolation error when creating a compra_insumo movement

-- Step 1: Drop the old constraint
ALTER TABLE movimientos DROP CONSTRAINT IF EXISTS chk_tipo_operacion;

-- Step 2: Re-create with 'compra' included  
ALTER TABLE movimientos ADD CONSTRAINT chk_tipo_operacion 
    CHECK (
        tipo_operacion IN ('produccion', 'venta', 'ajuste', 'merma', 'transferencia', 'ensamblaje', 'compra') 
        OR tipo_operacion IS NULL
    );
