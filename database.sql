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

-- Creación de tablas
CREATE TABLE almacenes (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) NOT NULL,
    direccion TEXT,
    ciudad VARCHAR(100)
);

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) UNIQUE NOT NULL,
    password VARCHAR(256) NOT NULL,
    rol VARCHAR(20) NOT NULL DEFAULT 'usuario' CHECK (rol IN ('admin', 'gerente', 'usuario')),
    almacen_id INTEGER REFERENCES almacenes(id) ON DELETE SET NULL
);

CREATE TABLE productos (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) UNIQUE NOT NULL,
    descripcion TEXT,
    precio_compra NUMERIC(12,2) NOT NULL,
    activo BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
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
    UNIQUE (producto_id, nombre)
);
CREATE INDEX idx_presentaciones_tipo ON presentaciones_producto(tipo);

CREATE TABLE proveedores (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) UNIQUE NOT NULL,
    telefono VARCHAR(20),
    direccion TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE lotes (
    id SERIAL PRIMARY KEY,
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    proveedor_id INTEGER REFERENCES proveedores(id) ON DELETE SET NULL,
    peso_humedo_kg NUMERIC(10,2) NOT NULL,
    peso_seco_kg NUMERIC(10,2),
    cantidad_disponible_kg NUMERIC(10,2),
    fecha_ingreso TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE clientes (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) NOT NULL,
    telefono VARCHAR(20),
    direccion TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    frecuencia_compra_dias INTEGER,
    ultima_fecha_compra TIMESTAMP WITH TIME ZONE
);

CREATE TABLE inventario (
    id SERIAL PRIMARY KEY,
    presentacion_id INTEGER NOT NULL REFERENCES presentaciones_producto(id) ON DELETE CASCADE,
    almacen_id INTEGER NOT NULL REFERENCES almacenes(id) ON DELETE CASCADE,
    lote_id INTEGER REFERENCES lotes(id) ON DELETE SET NULL,
    cantidad INTEGER NOT NULL DEFAULT 0 CHECK (cantidad >= 0),
    stock_minimo INTEGER NOT NULL DEFAULT 10,
    ultima_actualizacion TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (presentacion_id, almacen_id)
);
CREATE INDEX idx_inventario_almacen ON inventario(almacen_id, presentacion_id);

CREATE TABLE ventas (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    almacen_id INTEGER NOT NULL REFERENCES almacenes(id) ON DELETE CASCADE,
    fecha TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    total NUMERIC(12,2) NOT NULL CHECK (total > 0),
    tipo_pago VARCHAR(10) NOT NULL CHECK (tipo_pago IN ('contado', 'credito')),
    estado_pago VARCHAR(15) DEFAULT 'pendiente' CHECK (estado_pago IN ('pendiente', 'parcial', 'pagado')),
    consumo_diario_kg NUMERIC(10,2)
);

CREATE TABLE venta_detalles (
    id SERIAL PRIMARY KEY,
    venta_id INTEGER NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    presentacion_id INTEGER NOT NULL REFERENCES presentaciones_producto(id) ON DELETE CASCADE,
    cantidad INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario NUMERIC(12,2) NOT NULL
);

CREATE TABLE mermas (
    id SERIAL PRIMARY KEY,
    lote_id INTEGER NOT NULL REFERENCES lotes(id) ON DELETE CASCADE,
    cantidad_kg NUMERIC(10,2) NOT NULL CHECK (cantidad_kg > 0),
    convertido_a_briquetas BOOLEAN DEFAULT false,
    fecha_registro TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    usuario_id INTEGER REFERENCES users(id)
);

CREATE TABLE pagos (
    id SERIAL PRIMARY KEY,
    venta_id INTEGER NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    monto NUMERIC(12,2) NOT NULL CHECK (monto > 0),
    fecha TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metodo_pago VARCHAR(20) NOT NULL CHECK (metodo_pago IN ('efectivo', 'transferencia', 'tarjeta')),
    referencia VARCHAR(50),
    usuario_id INTEGER REFERENCES users(id),
    url_comprobante VARCHAR(255)
);

CREATE TABLE movimientos (
    id SERIAL PRIMARY KEY,
    tipo VARCHAR(10) NOT NULL CHECK (tipo IN ('entrada', 'salida')),
    presentacion_id INTEGER NOT NULL REFERENCES presentaciones_producto(id) ON DELETE CASCADE,
    lote_id INTEGER REFERENCES lotes(id) ON DELETE SET NULL,
    usuario_id INTEGER REFERENCES users(id),
    cantidad NUMERIC(12,2) NOT NULL CHECK (cantidad > 0),
    fecha TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    motivo VARCHAR(255)
);

CREATE TABLE gastos (
    id SERIAL PRIMARY KEY,
    descripcion TEXT NOT NULL,
    monto NUMERIC(12,2) NOT NULL CHECK (monto > 0),
    fecha DATE DEFAULT CURRENT_DATE,
    categoria VARCHAR(50) NOT NULL CHECK (categoria IN ('logistica', 'personal', 'otros')),
    almacen_id INTEGER REFERENCES almacenes(id),
    usuario_id INTEGER REFERENCES users(id)
);

CREATE TABLE pedidos (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    almacen_id INTEGER NOT NULL REFERENCES almacenes(id) ON DELETE CASCADE,
    vendedor_id INTEGER REFERENCES users(id),
    fecha_creacion TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    fecha_entrega TIMESTAMP WITH TIME ZONE NOT NULL,
    estado VARCHAR(20) DEFAULT 'programado' CHECK (estado IN ('programado', 'confirmado', 'entregado', 'cancelado')),
    notas TEXT
);

CREATE TABLE pedido_detalles (
    id SERIAL PRIMARY KEY,
    pedido_id INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    presentacion_id INTEGER NOT NULL REFERENCES presentaciones_producto(id) ON DELETE CASCADE,
    cantidad INTEGER NOT NULL CHECK (cantidad > 0),
    precio_estimado NUMERIC(12,2) NOT NULL
);

-- Datos iniciales para empezar a usar el sistema

-- Insertar almacenes
INSERT INTO almacenes (nombre, direccion, ciudad) VALUES
('Planta', 'Km 384 Colcabamba', 'Calicocha'),
('Almacen Abancay', 'Av. Tamburco', 'Abancay'),
('Almacen Andahuaylas', 'Av. Peru', 'Andahuaylas');

-- Insertar usuario admin (contraseña: admin123)
INSERT INTO users (username, password, rol) VALUES
('admin', 'pbkdf2:sha256:150000$CZvVg5zN$b8bca4d3c58992e1cf7d5ce66ece1b22714d0e0eb8b11be7875d7919bc90d85e', 'admin');

-- Insertar productos
INSERT INTO productos (nombre, descripcion, precio_compra, activo) VALUES
('Carbón Vegetal Premium', 'Carbón vegetal de alta calidad para parrillas', 35.00, true),
('Briquetas de Carbón', 'Briquetas compactadas de carbón vegetal', 40.00, true),
('Carbón para Restaurantes', 'Carbón vegetal para uso en restaurantes', 30.00, true);

-- Insertar presentaciones
INSERT INTO presentaciones_producto (producto_id, nombre, capacidad_kg, tipo, precio_venta, activo) VALUES
(1, 'Saco de 30kg', 30.0, 'bruto', 87.00, true),
(1, 'Saco de 20kg', 20.0, 'bruto', 58.00, true),
(1, 'Bolsa de 10kg', 10.0, 'procesado', 30.00, true),
(1, 'Bolsa de 5kg', 5.0, 'procesado', 15.00, true),
(2, 'Bolsa de briquetas 5kg', 5.0, 'briqueta', 22.00, true),
(2, 'Bolsa de briquetas 4kg', 4.0, 'briqueta', 16.50, true),
(3, 'Saco Restaurante 25kg', 25.0, 'bruto', 75.00, true);

-- Insertar proveedores
INSERT INTO proveedores (nombre, telefono, direccion) VALUES
('Carbonera del Sur', '+51987654321', 'Av. Los Pinos 123, Abancay'),
('Maderas y Carbones', '+51987654322', 'Jr. Libertad 456, Andahuaylas'),
('Distribuidora Forestal', '+51987654323', 'Calle Principal 789, Calicocha');

-- Insertar clientes
INSERT INTO clientes (nombre, telefono, direccion) VALUES
('Pollo Loko', '+51987654321', 'Av. Principal 123'),
('Polleria Mauris', '+51987654322', 'Calle Los Olivos 456'),
('Polleria Ricas Brasas', '+51987654323', 'Jr. Libertad 789'),
('Mateus Restaurant', '+51987654324', 'Av. Sol 101'),
('Carboleña del Olivo', '+51987654325', 'Calle Paz 202'),
('Polleria La Fogata', '+51987654326', 'Av. Luna 303'),
('Maria Mayorista', '+51987654327', 'Jr. Estrella 404'),
('Dcarmen', '+51987654328', 'Av. Marte 505'),
('Polleria Gael', '+51987654329', 'Calle Tierra 606');