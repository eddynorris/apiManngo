-- Migración para agregar campos de trazabilidad de producción a la tabla movimientos
-- Fecha: 2024
-- Descripción: Agrega campos para mejorar el control y trazabilidad de operaciones de producción

-- Agregar columnas para trazabilidad de producción
ALTER TABLE movimientos 
ADD COLUMN tipo_operacion VARCHAR(20),
ADD COLUMN lote_origen_id INTEGER,
ADD COLUMN cantidad_kg_procesados NUMERIC(10,2),
ADD COLUMN eficiencia_conversion NUMERIC(5,2),
ADD COLUMN turno_produccion VARCHAR(10);

-- Agregar foreign key constraint para lote_origen_id
ALTER TABLE movimientos 
ADD CONSTRAINT fk_movimientos_lote_origen 
FOREIGN KEY (lote_origen_id) REFERENCES lotes(id) ON DELETE SET NULL;

-- Agregar constraint para tipo_operacion (valores válidos)
ALTER TABLE movimientos 
ADD CONSTRAINT chk_tipo_operacion 
CHECK (tipo_operacion IN ('produccion', 'venta', 'ajuste', 'merma', 'transferencia'));

-- Agregar constraint para turno_produccion (valores válidos)
ALTER TABLE movimientos 
ADD CONSTRAINT chk_turno_produccion 
CHECK (turno_produccion IN ('mañana', 'tarde', 'noche'));

-- Agregar constraint para eficiencia_conversion (0-100%)
ALTER TABLE movimientos 
ADD CONSTRAINT chk_eficiencia_conversion 
CHECK (eficiencia_conversion >= 0 AND eficiencia_conversion <= 100);

-- Agregar constraint para cantidad_kg_procesados (debe ser positiva)
ALTER TABLE movimientos 
ADD CONSTRAINT chk_cantidad_kg_procesados 
CHECK (cantidad_kg_procesados >= 0);

-- Crear índices para mejorar el rendimiento de consultas
CREATE INDEX idx_movimientos_tipo_operacion ON movimientos(tipo_operacion);
CREATE INDEX idx_movimientos_lote_origen_id ON movimientos(lote_origen_id);
CREATE INDEX idx_movimientos_turno_produccion ON movimientos(turno_produccion);

-- Agregar comentarios para documentación
COMMENT ON COLUMN movimientos.tipo_operacion IS 'Tipo de operación: produccion, venta, ajuste, merma, transferencia';
COMMENT ON COLUMN movimientos.lote_origen_id IS 'ID del lote de origen para operaciones de conversión/producción';
COMMENT ON COLUMN movimientos.cantidad_kg_procesados IS 'Kilogramos de materia prima utilizados en la producción';
COMMENT ON COLUMN movimientos.eficiencia_conversion IS 'Porcentaje de eficiencia en la conversión (0-100%)';
COMMENT ON COLUMN movimientos.turno_produccion IS 'Turno de producción: mañana, tarde, noche';

-- Script completado
-- Para ejecutar: psql -d tu_base_de_datos -f add_production_fields_to_movimientos.sql