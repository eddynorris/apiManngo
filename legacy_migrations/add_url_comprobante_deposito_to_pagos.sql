-- Migración: Agregar campo url_comprobante_deposito a la tabla pagos
-- Fecha: 2024-12-19
-- Descripción: Agrega el campo url_comprobante_deposito para almacenar la URL del comprobante de depósito bancario

ALTER TABLE pagos 
ADD COLUMN url_comprobante_deposito VARCHAR(255);

-- Agregar comentario al campo
COMMENT ON COLUMN pagos.url_comprobante_deposito IS 'URL del comprobante de depósito bancario almacenado en S3';