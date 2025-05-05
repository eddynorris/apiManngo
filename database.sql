-- Database: manngo_db

CREATE DATABASE "manngo_db"
    WITH
    OWNER = postgres
    ENCODING = 'UTF8'
    LC_COLLATE = 'Spanish_Spain.1252'
    LC_CTYPE = 'Spanish_Spain.1252'
    LOCALE_PROVIDER = 'libc'
    TABLESPACE = pg_default
    CONNECTION LIMIT = -1
    IS_TEMPLATE = False;

-- Eliminación de tablas en orden para evitar conflictos con foreign keys
DROP TABLE IF EXISTS pedido_detalles CASCADE;
DROP TABLE IF EXISTS pedidos CASCADE;
DROP TABLE IF EXISTS movimientos CASCADE;
DROP TABLE IF EXISTS gastos CASCADE;
DROP TABLE IF EXISTS pagos CASCADE;
DROP TABLE IF EXISTS venta_detalles CASCADE;
DROP TABLE IF EXISTS ventas CASCADE;
DROP TABLE IF EXISTS inventario CASCADE;
DROP TABLE IF EXISTS mermas CASCADE;
DROP TABLE IF EXISTS lotes CASCADE;
DROP TABLE IF EXISTS presentaciones_producto CASCADE;
DROP TABLE IF EXISTS productos CASCADE;
DROP TABLE IF EXISTS clientes CASCADE;
DROP TABLE IF EXISTS proveedores CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS almacenes CASCADE;
DROP TABLE IF EXISTS depositos_bancarios CASCADE;

-- Creación de tablas
CREATE TABLE almacenes (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) NOT NULL,
    direccion TEXT,
    ciudad VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) UNIQUE NOT NULL,
    password VARCHAR(256) NOT NULL,
    rol VARCHAR(20) NOT NULL DEFAULT 'usuario' CHECK (rol IN ('admin', 'gerente', 'usuario')),
    almacen_id INTEGER REFERENCES almacenes(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE productos (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) UNIQUE NOT NULL,
    descripcion TEXT,
    precio_compra NUMERIC(12,2) NOT NULL,
    activo BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE presentaciones_producto (
    id SERIAL PRIMARY KEY,
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    nombre VARCHAR(100) NOT NULL,
    capacidad_kg NUMERIC(10,2) NOT NULL,
    tipo VARCHAR(20) NOT NULL CHECK (tipo IN ('bruto', 'procesado', 'merma', 'briqueta', 'detalle')),
    precio_venta NUMERIC(12,2) NOT NULL,
    activo BOOLEAN DEFAULT true,
    url_foto VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (producto_id, nombre)
);
CREATE INDEX idx_presentaciones_tipo ON presentaciones_producto(tipo);

CREATE TABLE proveedores (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) UNIQUE NOT NULL,
    telefono VARCHAR(20),
    direccion TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE lotes (
    id SERIAL PRIMARY KEY,
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    proveedor_id INTEGER REFERENCES proveedores(id) ON DELETE SET NULL,
    descripcion VARCHAR(255),
    peso_humedo_kg NUMERIC(10,2) NOT NULL,
    peso_seco_kg NUMERIC(10,2),
    cantidad_disponible_kg NUMERIC(10,2),
    fecha_ingreso TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE clientes (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) NOT NULL,
    telefono VARCHAR(20),
    direccion TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    frecuencia_compra_dias INTEGER,
    ultima_fecha_compra TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE inventario (
    id SERIAL PRIMARY KEY,
    presentacion_id INTEGER NOT NULL REFERENCES presentaciones_producto(id) ON DELETE CASCADE,
    almacen_id INTEGER NOT NULL REFERENCES almacenes(id) ON DELETE CASCADE,
    lote_id INTEGER REFERENCES lotes(id) ON DELETE SET NULL,
    cantidad INTEGER NOT NULL DEFAULT 0 CHECK (cantidad >= 0),
    stock_minimo INTEGER NOT NULL DEFAULT 10,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ultima_actualizacion TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (presentacion_id, almacen_id)
);
CREATE INDEX idx_inventario_almacen ON inventario(almacen_id, presentacion_id);

CREATE TABLE ventas (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    almacen_id INTEGER NOT NULL REFERENCES almacenes(id) ON DELETE CASCADE,
    vendedor_id INTEGER REFERENCES users(id),
    fecha TIMESTAMP WITH TIME ZONE,
    total NUMERIC(12,2) NOT NULL CHECK (total > 0),
    tipo_pago VARCHAR(10) NOT NULL CHECK (tipo_pago IN ('contado', 'credito')),
    estado_pago VARCHAR(15) DEFAULT 'pendiente' CHECK (estado_pago IN ('pendiente', 'parcial', 'pagado')),
    consumo_diario_kg NUMERIC(10,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE venta_detalles (
    id SERIAL PRIMARY KEY,
    venta_id INTEGER NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    presentacion_id INTEGER NOT NULL REFERENCES presentaciones_producto(id) ON DELETE CASCADE,
    cantidad INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario NUMERIC(12,2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE mermas (
    id SERIAL PRIMARY KEY,
    lote_id INTEGER NOT NULL REFERENCES lotes(id) ON DELETE CASCADE,
    cantidad_kg NUMERIC(10,2) NOT NULL CHECK (cantidad_kg > 0),
    convertido_a_briquetas BOOLEAN DEFAULT false,
    fecha_registro TIMESTAMP WITH TIME ZONE,
    usuario_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE pagos (
    id SERIAL PRIMARY KEY,
    venta_id INTEGER NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    monto NUMERIC(12,2) NOT NULL CHECK (monto > 0),
    fecha TIMESTAMP WITH TIME ZONE,
    metodo_pago VARCHAR(20) NOT NULL CHECK (metodo_pago IN ('efectivo', 'transferencia', 'tarjeta')),
    referencia VARCHAR(50),
    usuario_id INTEGER REFERENCES users(id),
    url_comprobante VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE movimientos (
    id SERIAL PRIMARY KEY,
    tipo VARCHAR(10) NOT NULL CHECK (tipo IN ('entrada', 'salida')),
    presentacion_id INTEGER NOT NULL REFERENCES presentaciones_producto(id) ON DELETE CASCADE,
    lote_id INTEGER REFERENCES lotes(id) ON DELETE SET NULL,
    usuario_id INTEGER REFERENCES users(id),
    cantidad NUMERIC(12,2) NOT NULL CHECK (cantidad > 0),
    fecha TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    motivo VARCHAR(255),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE gastos (
    id SERIAL PRIMARY KEY,
    descripcion TEXT NOT NULL,
    monto NUMERIC(12,2) NOT NULL CHECK (monto > 0),
    fecha DATE,
    categoria VARCHAR(50) NOT NULL CHECK (categoria IN ('logistica', 'personal', 'otros')),
    almacen_id INTEGER REFERENCES almacenes(id),
    usuario_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE pedidos (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    almacen_id INTEGER NOT NULL REFERENCES almacenes(id) ON DELETE CASCADE,
    vendedor_id INTEGER REFERENCES users(id),
    fecha_creacion TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    fecha_entrega TIMESTAMP WITH TIME ZONE NOT NULL,
    estado VARCHAR(20) DEFAULT 'programado' CHECK (estado IN ('programado', 'confirmado', 'entregado', 'cancelado')),
    notas TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE pedido_detalles (
    id SERIAL PRIMARY KEY,
    pedido_id INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    presentacion_id INTEGER NOT NULL REFERENCES presentaciones_producto(id) ON DELETE CASCADE,
    cantidad INTEGER NOT NULL CHECK (cantidad > 0),
    precio_estimado NUMERIC(12,2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Nueva tabla para gestionar depósitos bancarios del efectivo acumulado
CREATE TABLE depositos_bancarios (
    id SERIAL PRIMARY KEY,
    fecha_deposito TIMESTAMP WITH TIME ZONE NOT NULL,
    monto_depositado NUMERIC(12,2) NOT NULL CHECK (monto_depositado > 0),
    almacen_id INTEGER REFERENCES almacenes(id) ON DELETE SET NULL,
    usuario_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    referencia_bancaria VARCHAR(100),
    url_comprobante_deposito VARCHAR(255),
    notas TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Función para actualizar automáticamente la columna updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = now();
   RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers para cada tabla con columna updated_at
CREATE TRIGGER update_almacenes_updated_at BEFORE UPDATE ON almacenes FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_productos_updated_at BEFORE UPDATE ON productos FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_presentaciones_producto_updated_at BEFORE UPDATE ON presentaciones_producto FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_proveedores_updated_at BEFORE UPDATE ON proveedores FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_lotes_updated_at BEFORE UPDATE ON lotes FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_clientes_updated_at BEFORE UPDATE ON clientes FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_ventas_updated_at BEFORE UPDATE ON ventas FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_venta_detalles_updated_at BEFORE UPDATE ON venta_detalles FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_mermas_updated_at BEFORE UPDATE ON mermas FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_pagos_updated_at BEFORE UPDATE ON pagos FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_movimientos_updated_at BEFORE UPDATE ON movimientos FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_gastos_updated_at BEFORE UPDATE ON gastos FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_pedidos_updated_at BEFORE UPDATE ON pedidos FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_pedido_detalles_updated_at BEFORE UPDATE ON pedido_detalles FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_depositos_bancarios_updated_at BEFORE UPDATE ON depositos_bancarios FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Datos iniciales para empezar a usar el sistema

-- Insertar almacenes
INSERT INTO almacenes (nombre, direccion, ciudad) VALUES
('Planta', 'Km 384 Colcabamba', 'Calicocha'),
('Almacen Abancay', 'Av. Tamburco', 'Abancay'), 
('Almacen Andahuaylas', 'Av. Peru', 'Andahuaylas');


-- Insertar productos
INSERT INTO productos (nombre, descripcion, precio_compra, activo) VALUES
('Carbón Vegetal Premium', 'Carbón vegetal de alta calidad para parrillas', 35.00, true),
('Briquetas de Carbón', 'Briquetas compactadas de carbón vegetal', 40.00, true),
('Carbón para Restaurantes', 'Carbón vegetal para uso en restaurantes', 30.00, true);

-- Insertar presentaciones
INSERT INTO presentaciones_producto (producto_id, nombre, capacidad_kg, tipo, precio_venta, activo) VALUES
(13, 'Saco de 30kg', 30.0, 'procesado', 87.00, true),
(13, 'Saco de 20kg', 20.0, 'procesado', 58.00, true),
(13, 'Bolsa de 10kg', 10.0, 'procesado', 30.00, true),
(13, 'Bolsa de 5kg', 5.0, 'procesado', 15.00, true),
(13, 'Bolsa de carbon Fogo de Chao 5kg', 5.0, 'procesado', 22.00, true),
(14, 'Bolsa de briquetas 4kg', 4.0, 'briqueta', 16.50, true),
(15, 'Saco Restaurante 25kg', 25.0, 'bruto', 75.00, true);

-- Insertar proveedores
INSERT INTO proveedores (nombre, telefono, direccion) VALUES
('Pucallpa Chana', NULL, NULL, NOW(), NOW());

-- Insertar clientes
INSERT INTO clientes (nombre, telefono, direccion, ciudad, frecuencia_compra_dias, ultima_fecha_compra, created_at, updated_at) VALUES
('Pollo Loko', '51964413506', NULL, 'Abancay', 10, '2025-03-12', NOW(), NOW()),
('Polleria Mauris', '51964848422', NULL, 'Abancay', 10, '2025-03-12', NOW(), NOW()),
('Polleria Ricas Brasas', '51946218841', NULL, 'Abancay', 15, NULL, NOW(), NOW()),
('Mateus Restaurant', '51987493896', NULL, 'Abancay', 15, NULL, NOW(), NOW()),
('Carboleña del Olivo', '51966910806', NULL, 'Abancay', 7, '2025-02-21', NOW(), NOW()),
('Polleria La Fogata', '51949872517', NULL, 'Abancay', 15, '2025-02-21', NOW(), NOW()),
('Corporacion ILHAN Dennis', NULL, NULL, 'Abancay', NULL, NULL, NOW(), NOW()),
('Dcarmen', '51925109358', NULL, 'Abancay', 15, NULL, NOW(), NOW()),
('Polleria Gael', '51921796437', NULL, 'Abancay', 15, NULL, NOW(), NOW()),
('Mary Pacuri', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Lito Huaman', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Ibar Huaman', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()), -- Ciudad corregida
('El viejito polleria', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Super Gallo', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Chucila', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()), -- Ciudad corregida
('Zorro', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Chino', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Carbon y fuego', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()), -- Ciudad corregida
('Sabor y fuego focchots', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('El hornito', NULL, NULL, 'Abancay', NULL, NULL, NOW(), NOW()),
('Don Genaro', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()), -- Ciudad corregida
('Kevin La casa del pollo', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Otros', NULL, NULL, 'Planta', NULL, NULL, NOW(), NOW()), -- Asociado a Planta?
('Ccerari Polleria Casinchihua', NULL, NULL, 'Casinchihua', NULL, NULL, NOW(), NOW()),
('Oregano', NULL, NULL, 'Abancay', NULL, NULL, NOW(), NOW()),
('Paola Tamburco', NULL, NULL, 'Abancay', NULL, NULL, NOW(), NOW()),
('Polleria Bentos', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()), -- Ciudad corregida
('Yermerson Hot Drill', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('La granja', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()), -- Ciudad corregida
('Sabores Andinos', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()), -- Ciudad corregida
('Tuc Tuc Polleria', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Don bras polleria', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Santa cecilia', NULL, NULL, 'Abancay', NULL, NULL, NOW(), NOW()),
('Añañao', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Polleria Lorenzo', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()),
('Polleria Edward', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW()), -- Ciudad corregida
('Polleria Sumaq', NULL, NULL, 'Andahuaylas', NULL, NULL, NOW(), NOW());

INSERT INTO lotes (producto_id, proveedor_id, descripcion, peso_humedo_kg, peso_seco_kg, cantidad_disponible_kg, fecha_ingreso, created_at, updated_at) VALUES
(
    (SELECT id FROM productos WHERE nombre = 'Carbon vegetal'), -- ID del producto JSON
    (SELECT id FROM proveedores WHERE nombre = 'Pucallpa Chana'), -- ID del proveedor JSON
    'Lote nro 1',
    25000.00,
    24800.00,
    22100.00,
    '2025-04-10 00:00:00',
    NOW(),
    NOW()
);

-- 8. Insertar Gastos (Desde PDF, usuario_id = 1)
INSERT INTO gastos (descripcion, monto, fecha, categoria, almacen_id, usuario_id, created_at, updated_at) VALUES
('Adelanto de sueldo a willy', 600.00, '2025-02-23', 'personal', NULL, 1, NOW(), NOW()),
('Descargador para abancay', 70.00, '2025-02-23', 'logistica', (SELECT id FROM almacenes WHERE nombre = 'Almacen Abancay'), 1, NOW(), NOW()),
('Pago a papa por traer 50 sacos de 20 y 50 bolsas de 10, tambien entregarlo todo', 50.00, '2025-02-21', 'logistica', NULL, 1, NOW(), NOW()),
('Pago a wilfredo por los pasajes de vuelta de la planta', 48.00, '2025-02-24', 'logistica', (SELECT id FROM almacenes WHERE nombre = 'Planta'), 1, NOW(), NOW()),
('Gaseosa de 3litros', 12.50, '2025-02-23', 'otros', NULL, 1, NOW(), NOW()),
('Pasaje para cobrar al pollo loko', 10.00, '2025-02-22', 'logistica', NULL, 1, NOW(), NOW()),
('14 soles a Jhon para su almuerzo y taxy', 14.00, '2025-02-22', 'personal', NULL, 1, NOW(), NOW()),
('Taxy para entregas y cobros', 40.00, '2025-02-21', 'logistica', NULL, 1, NOW(), NOW()),
('Combustible para el viaje a andahuaylas', 203.00, '2025-02-21', 'logistica', (SELECT id FROM almacenes WHERE nombre = 'Almacen Andahuaylas'), 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Descargo para andahuaylas', 50.00, '2025-02-22', 'logistica', (SELECT id FROM almacenes WHERE nombre = 'Almacen Andahuaylas'), 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Cochera', 10.00, '2025-02-21', 'otros', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Viaticos (Desayuno, almuerzo y cena) de la descarga para andahuaylas', 30.00, '2025-02-21', 'personal', (SELECT id FROM almacenes WHERE nombre = 'Almacen Andahuaylas'), 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Peaje de la planta a abancay', 4.00, '2025-02-21', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Envio de carbon de la planta a abancay en el furgon, combustible', 200.00, '2025-02-23', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Peaje', 15.80, '2025-02-23', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Gaseoson', 23.50, '2025-02-23', 'otros', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Viajes', 24.00, '2025-02-24', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Viaje de la planta a andahuaylas, combustible (sobro del viaje a abancay)', 108.70, '2025-02-24', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Combustible de vuelta se tanqueo el furgon', 193.50, '2025-02-24', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Peaje de vuelta', 7.90, '2025-02-24', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Viaticos', 30.00, '2025-02-24', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Descargue del carbon.', 50.00, '2025-02-24', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Pago a estibadores', 448.00, '2025-02-20', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Cena para los estibadores', 70.00, '2025-02-19', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Desayuno de los estibadores', 70.00, '2025-02-20', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('2 chambers del olivo (Pasaje y sueldo)', 94.00, '2025-02-20', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Almuerzo y Cena del personal', 80.00, '2025-02-20', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('2 chambers del olivo (sueldo)', 140.00, '2025-02-21', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Almuerzo personal', 40.00, '2025-02-21', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('4 Chambers Sueldo (vinieron 3 uno se fue)', 280.00, '2025-02-22', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Almuerzo y cena(Se cocino)', 30.00, '2025-02-22', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('4 Chambers Sueldo', 280.00, '2025-02-23', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('60 soles a wilfredo por entregar al mauris y pollo loko', 60.00, '2025-03-12', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('10 soles a william para la moto', 10.00, '2025-03-12', 'logistica', NULL, 1, NOW(), NOW()),
('Sueldo de willy restante', 1400.00, '2025-03-12', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Deuda al primo por su trabajo de antes que llegue la carga', 400.00, '2025-03-12', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obudulia es user 1
('Adelanto a yenifer', 100.00, '2025-03-13', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obudlia es user 1
('Adelantao a willy, pago de sus servicios', 253.30, '2025-03-03', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Combustible', 201.50, NULL, 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('13 galones para el viaje a andahuaylas', 201.50, '2025-03-12', 'logistica', (SELECT id FROM almacenes WHERE nombre = 'Almacen Andahuaylas'), 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Combustible viaje a andahuaylas de vuelta', 80.00, '2025-03-12', 'logistica', (SELECT id FROM almacenes WHERE nombre = 'Almacen Andahuaylas'), 1, NOW(), NOW()), -- Asumiendo willy es user 1?
('Descargue del carbon', 50.00, '2025-03-12', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Pago al transportista del trailer', 2000.00, '2025-02-22', 'logistica', NULL, 1, NOW(), NOW()),
('Descargue de wilfredo al oregano', 40.00, '2025-03-14', 'logistica', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Pago al primo sueldo', 420.00, '2025-03-04', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Pago al primo sueldo', 400.00, '2025-03-14', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Pago sunat', 180.00, '2025-03-04', 'otros', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Pago a wilfredo por entregar al dcarmen', 10.00, '2025-03-19', 'logistica', NULL, 1, NOW(), NOW()),
('Pago a wilfredo por entregar al olivo', 12.00, '2025-03-17', 'logistica', NULL, 1, NOW(), NOW()),
('Pago a motocar para llevar al pollo loko y don bras', 50.00, '2025-03-21', 'logistica', NULL, 1, NOW(), NOW()),
('William llevo en taxy al mauris 6 sacos', 20.00, '2025-03-23', 'logistica', NULL, 1, NOW(), NOW()),
('Se pago su semana al primo', 420.00, '2025-03-19', 'personal', NULL, 1, NOW(), NOW()), -- Asumiendo Obdulia es user 1
('Pago a wilfredo por entregar al pollo loko', 35.00, '2025-03-31', 'logistica', NULL, 1, NOW(), NOW()),
('Pago', 165.00, '2025-03-28', 'otros', NULL, 1, NOW(), NOW()), -- Usuario '2' no mapeado, asignado a 1
('Wilfredo pago mauris', 30.00, '2025-04-01', 'logistica', NULL, 1, NOW(), NOW()),
('Wilfredo Pago pollo loko', 35.00, '2025-04-10', 'logistica', NULL, 1, NOW(), NOW()),
('Papa pedido', 25.00, '2025-04-08', 'otros', NULL, 1, NOW(), NOW()),
('Wilfredo pago por llevar al olivo', 15.00, '2025-04-02', 'logistica', NULL, 1, NOW(), NOW()),
('Pago a moto para llevar la Mauris', 25.00, '2025-03-26', 'logistica', NULL, 1, NOW(), NOW()),
('Pago a moto para llevar al pollo loko', 40.00, '2025-04-19', 'logistica', NULL, 1, NOW(), NOW()),
('Pago a papa para llevar a dcarmen', 10.00, '2025-04-11', 'logistica', NULL, 1, NOW(), NOW()),
('Pago a moto para llevar al mauris', 30.00, '2025-04-22', 'logistica', NULL, 1, NOW(), NOW()),
('Pago al taxy para llevar al mateus', 10.00, '2025-04-17', 'logistica', NULL, 1, NOW(), NOW()),
('Pago a gary para llevar al mauris', 20.00, '2025-05-02', 'logistica', NULL, 1, NOW(), NOW()),
('Gasto para llevar las bolsas de 10 al oregano', 20.00, '2025-05-01', 'logistica', NULL, 1, NOW(), NOW()),
('Pago a luis por la semana de trabajo', 420.00, '2025-05-01', 'personal', NULL, 1, NOW(), NOW()); -- Asumiendo Obdulia es user 1

-- Venta 3: Cliente 'Carboleña del Olivo' (ID: 5), Vendedor 6, Fecha '2025-03-11'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(5, 14, 6, '2025-03-11 00:00:00', 348.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 6, 58.00, NOW(), NOW());

-- Venta 4: Cliente 'Carboleña del Olivo' (ID: 5), Vendedor 6, Fecha '2025-03-19'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(5, 14, 6, '2025-03-19 00:00:00', 290.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 5, 58.00, NOW(), NOW());

-- Venta 5: Cliente 'Carboleña del Olivo' (ID: 5), Vendedor 6, Fecha '2025-03-26'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(5, 14, 6, '2025-03-26 00:00:00', 348.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 6, 58.00, NOW(), NOW());

-- Venta 6: Cliente 'Carboleña del Olivo' (ID: 5), Vendedor 6, Fecha '2025-04-02'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(5, 14, 6, '2025-04-02 00:00:00', 348.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 6, 58.00, NOW(), NOW());

-- Venta 7: Cliente 'Carboleña del Olivo' (ID: 5), Vendedor 6, Fecha '2025-04-29'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(5, 14, 6, '2025-04-29 00:00:00', 348.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 6, 58.00, NOW(), NOW());

-- Venta 8: Cliente 'Carbon y fuego' (ID: 18), Vendedor 6, Fecha '2025-03-30'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(18, 15, 6, '2025-03-30 00:00:00', 580.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW());

-- Venta 9: Cliente 'Ccerari Polleria Casinchihua' (ID: 24), Vendedor 6, Fecha '2025-02-23'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(24, 13, 6, '2025-02-23 00:00:00', 116.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 2, 58.00, NOW(), NOW());

-- Venta 10: Cliente 'Ccerari Polleria Casinchihua' (ID: 24), Vendedor 6, Fecha '2025-03-12'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(24, 13, 6, '2025-03-12 00:00:00', 116.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 2, 58.00, NOW(), NOW());

-- Venta 11: Cliente 'Chino' (ID: 17), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(17, 15, 6, '2025-02-21 00:00:00', 580.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW());

-- Venta 12: Cliente 'Chino' (ID: 17), Vendedor 6, Fecha '2025-02-24'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(17, 15, 6, '2025-02-24 00:00:00', 2320.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 40, 58.00, NOW(), NOW());

-- Venta 13: Cliente 'Chucila' (ID: 15), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(15, 15, 6, '2025-02-21 00:00:00', 580.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW());

-- Venta 14: Cliente 'Chucila' (ID: 15), Vendedor 6, Fecha '2025-02-24'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(15, 15, 6, '2025-02-24 00:00:00', 580.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW());

-- Venta 15: Cliente 'Chucila' (ID: 15), Vendedor 6, Fecha '2025-03-24'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(15, 15, 6, '2025-03-24 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 16: Cliente 'Corporacion ILHAN Dennis' (ID: 7), Vendedor 6, Fecha '2025-03-03'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(7, 14, 6, '2025-03-03 00:00:00', 1740.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 20, 58.00, NOW(), NOW()),
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de 10kg'), 20, 29.00, NOW(), NOW());

-- Venta 17: Cliente 'Dcarmen' (ID: 8), Vendedor 6, Fecha '2025-02-25'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(8, 14, 6, '2025-02-25 00:00:00', 232.00, 'contado', 'pagado', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 4, 58.00, NOW(), NOW());

-- Venta 18: Cliente 'Dcarmen' (ID: 8), Vendedor 6, Fecha '2025-03-17'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(8, 14, 6, '2025-03-17 00:00:00', 232.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 4, 58.00, NOW(), NOW());

-- Venta 19: Cliente 'Dcarmen' (ID: 8), Vendedor 6, Fecha '2025-04-11'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(8, 14, 6, '2025-04-11 00:00:00', 232.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 4, 58.00, NOW(), NOW());

-- Venta 20: Cliente 'Don Genaro' (ID: 21), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(21, 15, 6, '2025-02-21 00:00:00', 232.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 4, 58.00, NOW(), NOW());

-- Venta 21: Cliente 'Don Genaro' (ID: 21), Vendedor 6, Fecha '2025-03-20'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(21, 15, 6, '2025-03-20 00:00:00', 232.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 4, 58.00, NOW(), NOW());

-- Venta 22: Cliente 'Don Genaro' (ID: 21), Vendedor 6, Fecha '2025-03-22'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(21, 15, 6, '2025-03-22 00:00:00', 348.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 3, 58.00, NOW(), NOW()),
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 30kg'), 2, 87.00, NOW(), NOW());

-- Venta 23: Cliente 'Don bras polleria' (ID: 32), Vendedor 6, Fecha '2025-03-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(32, 14, 6, '2025-03-21 00:00:00', 156.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 3, 52.00, NOW(), NOW());

-- Venta 24: Cliente 'El hornito' (ID: 20), Vendedor 6, Fecha '2025-03-12'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(20, 15, 6, '2025-03-12 00:00:00', 116.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 2, 58.00, NOW(), NOW());

-- Venta 25: Cliente 'El viejito polleria' (ID: 13), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(13, 15, 6, '2025-02-21 00:00:00', 580.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW());

-- Venta 26: Cliente 'El viejito polleria' (ID: 13), Vendedor 6, Fecha '2025-02-24'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(13, 15, 6, '2025-02-24 00:00:00', 1160.00, 'contado', 'pagado', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 30kg'), 10, 87.00, NOW(), NOW()),
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 5, 58.00, NOW(), NOW());

-- Venta 27: Cliente 'El viejito polleria' (ID: 13), Vendedor 6, Fecha '2025-03-29'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(13, 15, 6, '2025-03-29 00:00:00', 1740.00, 'contado', 'pagado', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 30, 58.00, NOW(), NOW());

-- Venta 28: Cliente 'Ibar Huaman' (ID: 12), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(12, 15, 6, '2025-02-21 00:00:00', 2030.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de 10kg'), 25, 29.00, NOW(), NOW()),
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 5kg'), 30, 14.50, NOW(), NOW());

-- Venta 29: Cliente 'Ibar Huaman' (ID: 12), Vendedor 6, Fecha '2025-02-24'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(12, 15, 6, '2025-02-24 00:00:00', 2320.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de 10kg'), 80, 29.00, NOW(), NOW());

-- Venta 30: Cliente 'Kevin La casa del pollo' (ID: 22), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(
    22, -- ID del cliente
    15,
    6, -- vendedor_id cambiado a 6
    '2025-02-21 00:00:00',
    174.00,
    'contado',
    'pendiente',
    NOW(),
    NOW()
);
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(
    currval(pg_get_serial_sequence('ventas', 'id')),
    (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'),
    3,
    58.00,
    NOW(),
    NOW()
);
-- Venta 31: Cliente 'Kevin La casa del pollo' (ID: 22), Vendedor 6, Fecha '2025-03-07'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(22, 15, 6, '2025-03-07 00:00:00', 174.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 3, 58.00, NOW(), NOW());

-- Venta 32: Cliente 'La granja' (ID: 29), Vendedor 6, Fecha '2025-03-12'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(29, 15, 6, '2025-03-12 00:00:00', 580.00, 'contado', 'pagado', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW());

-- Venta 33: Cliente 'Lito Huaman' (ID: 11), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(11, 15, 6, '2025-02-21 00:00:00', 1740.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW()),
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de 10kg'), 25, 29.00, NOW(), NOW()),
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 5kg'), 30, 14.50, NOW(), NOW());

-- Venta 34: Cliente 'Lito Huaman' (ID: 11), Vendedor 6, Fecha '2025-02-24'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(11, 15, 6, '2025-02-24 00:00:00', 1798.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de 10kg'), 40, 29.00, NOW(), NOW()),
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 5kg'), 44, 14.50, NOW(), NOW());

-- Venta 35: Cliente 'Mary Pacuri' (ID: 10), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(10, 15, 6, '2025-02-21 00:00:00', 1595.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW()),
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de 10kg'), 20, 29.00, NOW(), NOW()),
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 5kg'), 30, 14.50, NOW(), NOW());

-- Venta 36: Cliente 'Mateus Restaurant' (ID: 4), Vendedor 6, Fecha '2025-02-25'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(4, 14, 6, '2025-02-25 00:00:00', 116.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 2, 58.00, NOW(), NOW());

-- Venta 37: Cliente 'Mateus Restaurant' (ID: 4), Vendedor 6, Fecha '2025-03-16'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(4, 14, 6, '2025-03-16 00:00:00', 116.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 2, 58.00, NOW(), NOW());

-- Venta 38: Cliente 'Mateus Restaurant' (ID: 4), Vendedor 6, Fecha '2025-03-29'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(4, 14, 6, '2025-03-29 00:00:00', 136.00, 'contado', 'pagado', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 2, 68.00, NOW(), NOW());

-- Venta 39: Cliente 'Mateus Restaurant' (ID: 4), Vendedor 6, Fecha '2025-04-17'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(4, 14, 6, '2025-04-17 00:00:00', 870.00, 'contado', 'pagado', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 40: Cliente 'Oregano' (ID: 25), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(25, 14, 6, '2025-02-21 00:00:00', 1450.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de 10kg'), 50, 29.00, NOW(), NOW());

-- Venta 41: Cliente 'Oregano' (ID: 25), Vendedor 6, Fecha '2025-03-14'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(25, 14, 6, '2025-03-14 00:00:00', 1740.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de 10kg'), 60, 29.00, NOW(), NOW());

-- Venta 42: Cliente 'Otros' (ID: 23), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(23, 15, 6, '2025-02-21 00:00:00', 174.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 3, 58.00, NOW(), NOW());

-- Venta 43: Cliente 'Otros' (ID: 23), Vendedor 6, Fecha '2025-03-01'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(23, 14, 6, '2025-03-01 00:00:00', 58.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 1, 58.00, NOW(), NOW());

-- Venta 44: Cliente 'Otros' (ID: 23), Vendedor 6, Fecha '2025-03-23'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(23, 15, 6, '2025-03-23 00:00:00', 58.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 1, 58.00, NOW(), NOW());

-- Venta 45: Cliente 'Paola Tamburco' (ID: 26), Vendedor 6, Fecha '2025-02-25'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(26, 14, 6, '2025-02-25 00:00:00', 58.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 1, 58.00, NOW(), NOW());

-- Venta 46: Cliente 'Paola Tamburco' (ID: 26), Vendedor 6, Fecha '2025-03-26'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(26, 14, 6, '2025-03-26 00:00:00', 58.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 1, 58.00, NOW(), NOW());

-- Venta 47: Cliente 'Polleria Edward' (ID: 36), Vendedor 6, Fecha '2025-03-25'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(36, 15, 6, '2025-03-25 00:00:00', 29.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de 10kg'), 1, 29.00, NOW(), NOW());

-- Venta 48: Cliente 'Polleria La Fogata' (ID: 6), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(6, 14, 6, '2025-02-21 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 49: Cliente 'Polleria La Fogata' (ID: 6), Vendedor 6, Fecha '2025-04-29'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(6, 14, 6, '2025-04-29 00:00:00', 290.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 5, 58.00, NOW(), NOW());

-- Venta 50: Cliente 'Polleria Lorenzo' (ID: 35), Vendedor 6, Fecha '2025-03-22'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(35, 15, 6, '2025-03-22 00:00:00', 58.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 1, 58.00, NOW(), NOW());

-- Venta 51: Cliente 'Polleria Lorenzo' (ID: 35), Vendedor 6, Fecha '2025-03-25'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(35, 15, 6, '2025-03-25 00:00:00', 58.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 1, 58.00, NOW(), NOW());

-- Venta 52: Cliente 'Polleria Mauris' (ID: 2), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(2, 13, 6, '2025-02-21 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 53: Cliente 'Polleria Mauris' (ID: 2), Vendedor 6, Fecha '2025-03-03'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(2, 14, 6, '2025-03-03 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 54: Cliente 'Polleria Mauris' (ID: 2), Vendedor 6, Fecha '2025-03-12'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(2, 14, 6, '2025-03-12 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 55: Cliente 'Polleria Mauris' (ID: 2), Vendedor 6, Fecha '2025-03-23'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(2, 14, 6, '2025-03-23 00:00:00', 348.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 6, 58.00, NOW(), NOW());

-- Venta 56: Cliente 'Polleria Mauris' (ID: 2), Vendedor 6, Fecha '2025-03-26'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(2, 14, 6, '2025-03-26 00:00:00', 522.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 9, 58.00, NOW(), NOW());

-- Venta 57: Cliente 'Polleria Mauris' (ID: 2), Vendedor 6, Fecha '2025-04-01'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(2, 14, 6, '2025-04-01 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 58: Cliente 'Polleria Mauris' (ID: 2), Vendedor 6, Fecha '2025-04-12'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(2, 14, 6, '2025-04-12 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 59: Cliente 'Polleria Mauris' (ID: 2), Vendedor 6, Fecha '2025-04-22'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(2, 14, 6, '2025-04-22 00:00:00', 812.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 14, 58.00, NOW(), NOW());

-- Venta 60: Cliente 'Polleria Mauris' (ID: 2), Vendedor 6, Fecha '2025-05-02'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(2, 14, 6, '2025-05-02 00:00:00', 522.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 9, 58.00, NOW(), NOW());

-- Venta 61: Cliente 'Polleria Sumaq' (ID: 37), Vendedor 6, Fecha '2025-03-22'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(37, 15, 6, '2025-03-22 00:00:00', 116.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 2, 58.00, NOW(), NOW());

-- Venta 62: Cliente 'Pollo Loko' (ID: 1), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(1, 14, 6, '2025-02-21 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 63: Cliente 'Pollo Loko' (ID: 1), Vendedor 6, Fecha '2025-03-02'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(1, 14, 6, '2025-03-02 00:00:00', 136.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 2, 68.00, NOW(), NOW());

-- Venta 64: Cliente 'Pollo Loko' (ID: 1), Vendedor 6, Fecha '2025-03-03'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(1, 14, 6, '2025-03-03 00:00:00', 754.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 13, 58.00, NOW(), NOW());

-- Venta 65: Cliente 'Pollo Loko' (ID: 1), Vendedor 6, Fecha '2025-03-12'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(1, 14, 6, '2025-03-12 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 66: Cliente 'Pollo Loko' (ID: 1), Vendedor 6, Fecha '2025-03-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(1, 14, 6, '2025-03-21 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 67: Cliente 'Pollo Loko' (ID: 1), Vendedor 6, Fecha '2025-03-31'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(1, 14, 6, '2025-03-31 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 68: Cliente 'Pollo Loko' (ID: 1), Vendedor 6, Fecha '2025-04-10'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(1, 14, 6, '2025-04-10 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 69: Cliente 'Pollo Loko' (ID: 1), Vendedor 6, Fecha '2025-04-19'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(1, 14, 6, '2025-04-19 00:00:00', 870.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 15, 58.00, NOW(), NOW());

-- Venta 70: Cliente 'Pollo Loko' (ID: 1), Vendedor 6, Fecha '2025-04-29'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(1, 14, 6, '2025-04-29 00:00:00', 580.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW());

-- Venta 71: Cliente 'Sabor y fuego focchots' (ID: 19), Vendedor 6, Fecha '2025-03-08'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(
    19, -- ID del cliente
    15,
    6, -- vendedor_id cambiado a 6
    '2025-03-08 00:00:00',
    290.00,
    'contado',
    'pendiente',
    NOW(),
    NOW()
);
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(
    currval(pg_get_serial_sequence('ventas', 'id')),
    (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'),
    5,
    58.00,
    NOW(),
    NOW()
);
-- Venta 72: Cliente 'Sabores Andinos' (ID: 30), Vendedor 6, Fecha '2025-03-06'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(30, 15, 6, '2025-03-06 00:00:00', 116.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 2, 58.00, NOW(), NOW());

-- Venta 73: Cliente 'Santa cecilia' (ID: 33), Vendedor 6, Fecha '2025-03-22'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(33, 14, 6, '2025-03-22 00:00:00', 183.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de briquetas Fogo de 4kg'), 6, 14.00, NOW(), NOW()),
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de carbon Fogo 3k'), 6, 16.50, NOW(), NOW());

-- Venta 74: Cliente 'Super Gallo' (ID: 14), Vendedor 6, Fecha '2025-02-24'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(14, 15, 6, '2025-02-24 00:00:00', 1450.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 25, 58.00, NOW(), NOW());

-- Venta 75: Cliente 'Super Gallo' (ID: 14), Vendedor 6, Fecha '2025-03-26'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(14, 15, 6, '2025-03-26 00:00:00', 1760.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 20, 88.00, NOW(), NOW());

-- Venta 76: Cliente 'Tuc Tuc Polleria' (ID: 31), Vendedor 6, Fecha '2025-03-12'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(31, 15, 6, '2025-03-12 00:00:00', 58.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 1, 58.00, NOW(), NOW());

-- Venta 77: Cliente 'Yermerson Hot Drill' (ID: 28), Vendedor 6, Fecha '2025-03-08'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(28, 15, 6, '2025-03-08 00:00:00', 290.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Bolsa de 10kg'), 10, 29.00, NOW(), NOW());

-- Venta 78: Cliente 'Zorro' (ID: 16), Vendedor 6, Fecha '2025-02-21'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(16, 15, 6, '2025-02-21 00:00:00', 580.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW());

-- Venta 79: Cliente 'Zorro' (ID: 16), Vendedor 6, Fecha '2025-03-26'
INSERT INTO ventas (cliente_id, almacen_id, vendedor_id, fecha, total, tipo_pago, estado_pago, created_at, updated_at) VALUES
(16, 15, 6, '2025-03-26 00:00:00', 580.00, 'contado', 'pendiente', NOW(), NOW()); -- vendedor_id cambiado a 6
INSERT INTO venta_detalles (venta_id, presentacion_id, cantidad, precio_unitario, created_at, updated_at) VALUES
(currval(pg_get_serial_sequence('ventas', 'id')), (SELECT id FROM presentaciones_producto WHERE nombre = 'Saco de 20kg'), 10, 58.00, NOW(), NOW());
