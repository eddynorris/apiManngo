# Documentación de Endpoints - API Flask Manngo

Esta documentación detalla todos los endpoints disponibles en la API Flask Manngo, organizados por módulos funcionales.

## Tabla de Contenidos

1. [Autenticación](#autenticación)
2. [Gestión de Usuarios](#gestión-de-usuarios)
3. [Almacenes](#almacenes)
4. [Productos y Presentaciones](#productos-y-presentaciones)
5. [Clientes](#clientes)
6. [Inventario y Lotes](#inventario-y-lotes)
7. [Movimientos](#movimientos)
8. [Producción](#producción)
9. [Recetas](#recetas)
10. [Ventas](#ventas)
11. [Pagos](#pagos)
12. [Pedidos](#pedidos)
13. [Gastos](#gastos)
14. [Proveedores](#proveedores)
15. [Transferencias](#transferencias)
16. [Reportes](#reportes)
17. [Dashboard](#dashboard)
18. [Depósitos Bancarios](#depósitos-bancarios)
19. [Mermas](#mermas)
20. [Chat IA](#chat-ia)

---

## Autenticación

### POST `/api/auth`
**Descripción**: Autenticación de usuarios y generación de tokens JWT.

**Request Body**:
```json
{
  "username": "string",
  "password": "string"
}
```

**Response (200)**:
```json
{
  "access_token": "jwt_token_string",
  "user": {
    "id": 1,
    "username": "usuario",
    "rol": "admin",
    "almacen_id": 1,
    "almacen_nombre": "Almacén Principal"
  }
}
```

**Flujo**: Valida credenciales contra la base de datos, genera token JWT con claims adicionales (rol, almacén) y retorna información del usuario autenticado.

---

## Gestión de Usuarios

### GET `/api/users`
**Descripción**: Lista usuarios con paginación y filtros (solo admins).

**Query Parameters**:
- `page`: Número de página (default: 1)
- `per_page`: Elementos por página (max: 50)
- `username`: Filtro por nombre de usuario
- `rol`: Filtro por rol

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "username": "admin",
      "rol": "admin",
      "almacen_id": 1,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "pagination": {
    "total": 10,
    "page": 1,
    "per_page": 10,
    "pages": 1
  }
}
```

### GET `/api/users/{id}`
**Descripción**: Obtiene detalles de un usuario específico (solo admins).

**Response (200)**:
```json
{
  "id": 1,
  "username": "admin",
  "rol": "admin",
  "almacen_id": 1,
  "almacen": {
    "id": 1,
    "nombre": "Almacén Principal"
  },
  "created_at": "2024-01-01T00:00:00Z"
}
```

### POST `/api/users`
**Descripción**: Crea un nuevo usuario (solo admins).

**Request Body**:
```json
{
  "username": "nuevo_usuario",
  "password": "password123",
  "rol": "vendedor",
  "almacen_id": 1
}
```

**Response (201)**:
```json
{
  "id": 2,
  "username": "nuevo_usuario",
  "rol": "vendedor",
  "almacen_id": 1,
  "created_at": "2024-01-01T00:00:00Z"
}
```

**Flujo**: Valida datos, hashea contraseña, verifica que el almacén existe y crea el usuario con validaciones de rol.

---

## Almacenes

### GET `/api/almacenes`
**Descripción**: Lista almacenes con paginación y filtros.

**Query Parameters**:
- `page`: Número de página
- `per_page`: Elementos por página
- `nombre`: Filtro por nombre

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "nombre": "Almacén Principal",
      "direccion": "Calle 123",
      "telefono": "123456789",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "pagination": {
    "total": 5,
    "page": 1,
    "per_page": 10,
    "pages": 1
  }
}
```

### GET `/api/almacenes/{id}`
**Descripción**: Obtiene detalles de un almacén específico.

### POST `/api/almacenes`
**Descripción**: Crea un nuevo almacén (admin/gerente).

**Request Body**:
```json
{
  "nombre": "Nuevo Almacén",
  "direccion": "Dirección del almacén",
  "telefono": "987654321"
}
```

### PUT `/api/almacenes/{id}`
**Descripción**: Actualiza un almacén existente (admin/gerente).

### DELETE `/api/almacenes/{id}`
**Descripción**: Elimina un almacén (solo si no tiene usuarios asociados).

**Flujo**: Los almacenes son centros de operación. Cada usuario está asignado a un almacén y las operaciones se filtran por almacén según permisos.

---

## Productos y Presentaciones

### GET `/api/productos`
**Descripción**: Lista productos con paginación.

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "nombre": "Briqueta Carbón",
      "descripcion": "Briqueta de carbón vegetal",
      "presentaciones": [
        {
          "id": 1,
          "nombre": "Briqueta 1kg",
          "capacidad_kg": 1.0,
          "precio_venta": 5000.00,
          "tipo": "briqueta"
        }
      ]
    }
  ],
  "pagination": {...}
}
```

### GET `/api/productos/{id}`
**Descripción**: Obtiene detalles de un producto específico.

### POST `/api/productos`
**Descripción**: Crea un nuevo producto (admin/gerente).

### GET `/api/presentaciones`
**Descripción**: Lista presentaciones con URLs pre-firmadas para fotos.

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "nombre": "Briqueta 1kg",
      "capacidad_kg": 1.0,
      "precio_venta": 5000.00,
      "tipo": "briqueta",
      "url_foto": "https://presigned-url-s3...",
      "producto": {
        "id": 1,
        "nombre": "Briqueta Carbón"
      }
    }
  ]
}
```

**Flujo**: Los productos tienen múltiples presentaciones. Las presentaciones definen capacidad, precio y tipo (briqueta, materia_prima, insumo).

---

## Clientes

### GET `/api/clientes`
**Descripción**: Lista clientes con paginación y filtros.

**Query Parameters**:
- `nombre`: Filtro por nombre
- `telefono`: Filtro por teléfono
- `ciudad`: Filtro por ciudad
- `page`, `per_page`: Paginación

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "nombre": "Cliente Ejemplo",
      "telefono": "123456789",
      "direccion": "Calle 123",
      "ciudad": "Bogotá",
      "saldo_pendiente": 0.00,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "pagination": {...}
}
```

### POST `/api/clientes`
**Descripción**: Crea un nuevo cliente.

**Request Body**:
```json
{
  "nombre": "Nuevo Cliente",
  "telefono": "987654321",
  "direccion": "Nueva Dirección",
  "ciudad": "Medellín"
}
```

### GET `/api/clientes/export`
**Descripción**: Exporta lista de clientes en formato Excel.

### GET `/api/clientes/proyeccion`
**Descripción**: Obtiene proyección de ventas por cliente.

**Flujo**: Los clientes pueden tener saldo pendiente por ventas a crédito. Se integran con el sistema de ventas y pagos.

---

## Inventario y Lotes

### GET `/api/inventario`
**Descripción**: Lista inventario con filtros y paginación.

**Query Parameters**:
- `almacen_id`: Filtro por almacén
- `presentacion_id`: Filtro por presentación
- `stock_bajo`: Solo items con stock bajo (boolean)

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "presentacion_id": 1,
      "almacen_id": 1,
      "cantidad": 100.0,
      "stock_minimo": 10,
      "presentacion": {
        "nombre": "Briqueta 1kg",
        "precio_venta": 5000.00
      },
      "almacen": {
        "nombre": "Almacén Principal"
      }
    }
  ]
}
```

### GET `/api/inventario/global`
**Descripción**: Reporte global de inventario consolidado.

### GET `/api/lotes`
**Descripción**: Lista lotes de materia prima con filtros.

**Query Parameters**:
- `proveedor_id`: Filtro por proveedor
- `producto_id`: Filtro por producto
- `disponible`: Solo lotes con stock disponible

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "descripcion": "Lote Carbón Enero 2024",
      "cantidad_inicial_kg": 1000.0,
      "cantidad_disponible_kg": 800.0,
      "precio_kg": 1500.00,
      "fecha_vencimiento": "2024-12-31",
      "proveedor": {
        "nombre": "Proveedor ABC"
      }
    }
  ]
}
```

### POST `/api/lotes`
**Descripción**: Crea un nuevo lote de materia prima.

**Request Body**:
```json
{
  "descripcion": "Nuevo Lote",
  "cantidad_inicial_kg": 500.0,
  "precio_kg": 1600.00,
  "fecha_vencimiento": "2024-12-31",
  "proveedor_id": 1,
  "producto_id": 1
}
```

**Flujo**: Los lotes representan materia prima comprada. Se consumen en producción y se rastrea su disponibilidad.

---

## Movimientos

### GET `/api/movimientos`
**Descripción**: Lista movimientos de inventario con filtros.

**Query Parameters**:
- `tipo`: entrada/salida
- `tipo_operacion`: venta/ensamblaje/transferencia/ajuste
- `fecha_inicio`, `fecha_fin`: Rango de fechas
- `almacen_id`: Filtro por almacén

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "tipo": "entrada",
      "tipo_operacion": "ensamblaje",
      "cantidad": 50.0,
      "descripcion": "Producción de briquetas",
      "fecha": "2024-01-01T10:00:00Z",
      "presentacion": {
        "nombre": "Briqueta 1kg"
      },
      "usuario": {
        "username": "operador1"
      }
    }
  ]
}
```

### POST `/api/movimientos`
**Descripción**: Registra un movimiento de inventario.

**Request Body**:
```json
{
  "tipo": "entrada",
  "tipo_operacion": "ajuste",
  "presentacion_id": 1,
  "cantidad": 10.0,
  "descripcion": "Ajuste de inventario",
  "almacen_id": 1
}
```

**Flujo**: Los movimientos registran todos los cambios de inventario. Se crean automáticamente en ventas, producción y transferencias.

---

## Producción

### POST `/api/produccion`
**Descripción**: Ejecuta producción basada en recetas.

**Request Body**:
```json
{
  "almacen_id": 1,
  "presentacion_id": 1,
  "cantidad_a_producir": 100,
  "lotes_seleccionados": [
    {
      "componente_presentacion_id": 2,
      "lote_id": 1
    }
  ],
  "lote_destino_id": null
}
```

**Response (200)**:
```json
{
  "mensaje": "Producción realizada con éxito",
  "id_operacion": "abc12345",
  "entradas_realizadas": [
    {
      "presentacion": "Briqueta 1kg",
      "cantidad": 100,
      "lote_id": 2
    }
  ],
  "salidas_realizadas": [
    {
      "tipo": "materia_prima",
      "lote_descripcion": "Carbón Enero",
      "cantidad_kg": 150.0
    }
  ]
}
```

### POST `/api/produccion/ensamblaje`
**Descripción**: Endpoint interno para ensamblaje directo (usado por producción).

**Flujo**: La producción consume materia prima e insumos según recetas para crear productos terminados. Actualiza inventario automáticamente.

---

## Recetas

### GET `/api/recetas`
**Descripción**: Lista recetas con paginación.

**Query Parameters**:
- `presentacion_id`: Filtro por presentación final

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "nombre": "Receta Briqueta 1kg",
      "presentacion_id": 1,
      "componentes": [
        {
          "componente_presentacion_id": 2,
          "cantidad_necesaria": 1.5,
          "tipo_consumo": "materia_prima",
          "componente_presentacion": {
            "nombre": "Carbón Vegetal"
          }
        }
      ]
    }
  ]
}
```

### POST `/api/recetas`
**Descripción**: Crea una nueva receta (admin/gerente).

**Request Body**:
```json
{
  "presentacion_id": 1,
  "nombre": "Nueva Receta",
  "descripcion": "Descripción de la receta",
  "componentes": [
    {
      "componente_presentacion_id": 2,
      "cantidad_necesaria": 1.5,
      "tipo_consumo": "materia_prima"
    }
  ]
}
```

**Flujo**: Las recetas definen qué materiales y en qué cantidades se necesitan para producir un producto final.

---

## Ventas

### GET `/api/ventas`
**Descripción**: Lista ventas con filtros y URLs pre-firmadas.

**Query Parameters**:
- `cliente_id`: Filtro por cliente
- `almacen_id`: Filtro por almacén
- `estado_pago`: pendiente/parcial/pagado
- `fecha_inicio`, `fecha_fin`: Rango de fechas

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "cliente_id": 1,
      "almacen_id": 1,
      "total": 50000.00,
      "estado_pago": "pagado",
      "fecha": "2024-01-01T10:00:00Z",
      "cliente": {
        "nombre": "Cliente ABC"
      },
      "detalles": [
        {
          "presentacion_id": 1,
          "cantidad": 10,
          "precio_unitario": 5000.00,
          "presentacion": {
            "nombre": "Briqueta 1kg",
            "url_foto": "https://presigned-url..."
          }
        }
      ]
    }
  ]
}
```

### POST `/api/ventas`
**Descripción**: Crea una nueva venta.

**Request Body**:
```json
{
  "cliente_id": 1,
  "almacen_id": 1,
  "detalles": [
    {
      "presentacion_id": 1,
      "cantidad": 10,
      "precio_unitario": 5000.00,
      "lote_id": 1
    }
  ],
  "observaciones": "Venta al contado"
}
```

### GET `/api/ventas/{id}/detalles`
**Descripción**: Obtiene detalles de una venta específica.

### POST `/api/ventas/{id}/detalles`
**Descripción**: Agrega un detalle a una venta existente.

### DELETE `/api/ventas/detalles/{detalle_id}`
**Descripción**: Elimina un detalle de venta (revierte stock).

**Flujo**: Las ventas consumen inventario, pueden ser al contado o crédito, y se integran con el sistema de pagos.

---

## Pagos

### GET `/api/pagos`
**Descripción**: Lista pagos con filtros.

**Query Parameters**:
- `venta_id`: Filtro por venta
- `metodo_pago`: efectivo/transferencia/tarjeta
- `fecha_inicio`, `fecha_fin`: Rango de fechas

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "venta_id": 1,
      "monto": 25000.00,
      "metodo_pago": "efectivo",
      "fecha_pago": "2024-01-01T10:00:00Z",
      "referencia": null,
      "venta": {
        "id": 1,
        "total": 50000.00,
        "cliente": {
          "nombre": "Cliente ABC"
        }
      }
    }
  ]
}
```

### POST `/api/pagos`
**Descripción**: Registra un nuevo pago.

**Request Body**:
```json
{
  "venta_id": 1,
  "monto": 25000.00,
  "metodo_pago": "transferencia",
  "referencia": "TRF123456"
}
```

### GET `/api/pagos/venta/{venta_id}`
**Descripción**: Lista pagos de una venta específica.

### GET `/api/pagos/cierre-caja`
**Descripción**: Obtiene datos para cierre de caja.

**Response (200)**:
```json
{
  "fecha": "2024-01-01",
  "total_efectivo": 150000.00,
  "total_transferencias": 75000.00,
  "total_tarjetas": 25000.00,
  "total_general": 250000.00,
  "numero_transacciones": 15
}
```

**Flujo**: Los pagos se asocian a ventas y pueden ser parciales. El sistema calcula automáticamente el estado de pago de las ventas.

---

## Pedidos

### GET `/api/pedidos`
**Descripción**: Lista pedidos con filtros y ordenación.

**Query Parameters**:
- `cliente_id`: Filtro por cliente
- `estado`: pendiente/confirmado/entregado/cancelado
- `fecha_inicio`, `fecha_fin`: Rango de fechas de entrega
- `sort_by`: fecha_creacion/fecha_entrega/cliente_nombre
- `sort_order`: asc/desc

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "cliente_id": 1,
      "almacen_id": 1,
      "estado": "confirmado",
      "fecha_entrega": "2024-01-05T00:00:00Z",
      "total": 100000.00,
      "cliente": {
        "nombre": "Cliente ABC"
      },
      "detalles": [
        {
          "presentacion_id": 1,
          "cantidad": 20,
          "precio_unitario": 5000.00
        }
      ]
    }
  ]
}
```

### POST `/api/pedidos`
**Descripción**: Crea un nuevo pedido.

### POST `/api/pedidos/{id}/convertir`
**Descripción**: Convierte un pedido en venta.

**Response (200)**:
```json
{
  "mensaje": "Pedido convertido a venta exitosamente",
  "venta_id": 15,
  "pedido_id": 1,
  "total": 100000.00
}
```

### GET `/api/pedidos/form-data`
**Descripción**: Obtiene datos para formularios (clientes, almacenes, presentaciones).

**Flujo**: Los pedidos son pre-ventas que se pueden convertir en ventas reales. Permiten planificar entregas futuras.

---

## Gastos

### GET `/api/gastos`
**Descripción**: Lista gastos con filtros.

**Query Parameters**:
- `categoria`: Filtro por categoría
- `fecha_inicio`, `fecha_fin`: Rango de fechas
- `almacen_id`: Filtro por almacén

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "descripcion": "Compra de combustible",
      "monto": 50000.00,
      "categoria": "operativo",
      "fecha": "2024-01-01T00:00:00Z",
      "almacen": {
        "nombre": "Almacén Principal"
      }
    }
  ]
}
```

### POST `/api/gastos`
**Descripción**: Registra un nuevo gasto.

**Request Body**:
```json
{
  "descripcion": "Nuevo gasto",
  "monto": 25000.00,
  "categoria": "administrativo",
  "almacen_id": 1
}
```

### GET `/api/gastos/export`
**Descripción**: Exporta gastos en formato Excel.

**Flujo**: Los gastos se categorizan y se asocian a almacenes para control financiero.

---

## Proveedores

### GET `/api/proveedores`
**Descripción**: Lista proveedores con filtros.

**Query Parameters**:
- `nombre`: Filtro por nombre
- `ciudad`: Filtro por ciudad

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "nombre": "Proveedor ABC",
      "telefono": "123456789",
      "direccion": "Calle 123",
      "email": "proveedor@email.com",
      "lotes": [
        {
          "id": 1,
          "descripcion": "Lote Carbón Enero"
        }
      ]
    }
  ]
}
```

### POST `/api/proveedores`
**Descripción**: Registra un nuevo proveedor (admin/gerente).

### PUT `/api/proveedores/{id}`
**Descripción**: Actualiza datos de proveedor.

### DELETE `/api/proveedores/{id}`
**Descripción**: Elimina proveedor (solo si no tiene lotes asociados).

**Flujo**: Los proveedores suministran materia prima que se registra en lotes.

---

## Transferencias

### GET `/api/transferencias`
**Descripción**: Lista almacenes disponibles para transferencias.

**Response (200)**:
```json
{
  "almacenes": [
    {
      "id": 1,
      "nombre": "Almacén Principal",
      "direccion": "Calle 123"
    }
  ]
}
```

### POST `/api/transferencias`
**Descripción**: Ejecuta transferencia entre almacenes.

**Request Body**:
```json
{
  "almacen_origen_id": 1,
  "almacen_destino_id": 2,
  "transferencias": [
    {
      "presentacion_id": 1,
      "cantidad": 50
    }
  ]
}
```

**Response (200)**:
```json
{
  "mensaje": "Transferencia realizada con éxito",
  "id_operacion": "abc12345",
  "almacen_origen": "Almacén Principal",
  "almacen_destino": "Almacén Secundario",
  "transferencias_realizadas": [
    {
      "presentacion": "Briqueta 1kg",
      "cantidad": 50
    }
  ]
}
```

**Flujo**: Las transferencias mueven inventario entre almacenes, creando movimientos de salida en origen y entrada en destino.

---

## Reportes

### GET `/api/reportes/produccion-briquetas`
**Descripción**: Reporte de producción de briquetas por período.

**Query Parameters**:
- `fecha_inicio`, `fecha_fin`: Rango de fechas (default: último mes)
- `almacen_id`: Filtro por almacén
- `presentacion_id`: Filtro por tipo de briqueta
- `periodo`: dia/semana/mes (agrupación)

**Response (200)**:
```json
{
  "resumen": {
    "total_unidades_producidas": 1000,
    "total_kg_producidos": 1000.0,
    "numero_producciones": 10
  },
  "detalle_por_presentacion": [
    {
      "presentacion_id": 1,
      "presentacion_nombre": "Briqueta 1kg",
      "unidades_producidas": 500,
      "kg_producidos": 500.0,
      "numero_producciones": 5
    }
  ]
}
```

### GET `/api/reportes/produccion-general`
**Descripción**: Reporte general de producción (todos los productos).

### GET `/api/reportes/ventas-presentacion`
**Descripción**: Reporte de ventas por presentación.

**Query Parameters**:
- `fecha_inicio`, `fecha_fin`: Rango de fechas
- `almacen_id`: Filtro por almacén
- `lote_id`: Filtro por lote específico

**Response (200)**:
```json
[
  {
    "presentacion_id": 1,
    "presentacion_nombre": "Briqueta 1kg",
    "unidades_vendidas": 200,
    "total_vendido": "1000000.00"
  }
]
```

### GET `/api/reportes/resumen-financiero`
**Descripción**: Resumen financiero con ventas, gastos y ganancias.

**Response (200)**:
```json
{
  "total_ventas": 2500000.00,
  "total_gastos": 500000.00,
  "ganancia_neta": 2000000.00,
  "numero_ventas": 50,
  "numero_gastos": 15
}
```

**Flujo**: Los reportes consolidan información de diferentes módulos para análisis de negocio.

---

## Dashboard

### GET `/api/dashboard`
**Descripción**: Datos consolidados para dashboard con alertas (solo admin).

**Response (200)**:
```json
{
  "alertas_stock_bajo": [
    {
      "presentacion_id": 1,
      "nombre": "Briqueta 1kg",
      "cantidad": 5.0,
      "stock_minimo": 10,
      "almacen_nombre": "Almacén Principal"
    }
  ],
  "alertas_lotes_bajos": [
    {
      "lote_id": 1,
      "descripcion": "Carbón Enero",
      "cantidad_disponible_kg": 100.0
    }
  ],
  "clientes_saldo_pendiente": [
    {
      "cliente_id": 1,
      "cliente_nombre": "Cliente ABC",
      "saldo_pendiente": 150000.00,
      "numero_ventas_pendientes": 3
    }
  ]
}
```

**Flujo**: Consolida alertas de stock bajo, lotes con poca cantidad y clientes con saldo pendiente.

---

## Depósitos Bancarios

### GET `/api/depositos-bancarios`
**Descripción**: Lista depósitos bancarios con filtros.

**Query Parameters**:
- `almacen_id`: Filtro por almacén
- `fecha_desde`, `fecha_hasta`: Rango de fechas
- `sort_by`: fecha_deposito/monto_depositado/almacen_nombre
- `sort_order`: asc/desc

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "monto_depositado": 500000.00,
      "fecha_deposito": "2024-01-01",
      "referencia_bancaria": "DEP123456",
      "almacen_id": 1,
      "comprobante_url": "https://presigned-url..."
    }
  ]
}
```

### POST `/api/depositos-bancarios`
**Descripción**: Registra un nuevo depósito bancario.

**Request Body** (multipart/form-data):
```
monto_depositado: 500000.00
fecha_deposito: 2024-01-01
referencia_bancaria: DEP123456
almacen_id: 1
comprobante: [archivo]
```

**Flujo**: Registra depósitos bancarios con comprobantes digitales almacenados en S3.

---

## Mermas

### GET `/api/mermas`
**Descripción**: Lista mermas con filtros.

**Query Parameters**:
- `lote_id`: Filtro por lote
- `convertido_a_briquetas`: Filtro por estado de conversión

**Response (200)**:
```json
{
  "data": [
    {
      "id": 1,
      "lote_id": 1,
      "cantidad_kg": 50.0,
      "convertido_a_briquetas": false,
      "fecha_registro": "2024-01-01T10:00:00Z",
      "lote": {
        "descripcion": "Carbón Enero"
      }
    }
  ]
}
```

### POST `/api/mermas`
**Descripción**: Registra una nueva merma (admin/gerente).

**Request Body**:
```json
{
  "lote_id": 1,
  "cantidad_kg": 25.0
}
```

**Flujo**: Las mermas registran pérdidas de materia prima que reducen la cantidad disponible en lotes.

---

## Chat IA

### POST `/api/chat`
**Descripción**: Consulta al asistente IA con contexto de documentación.

**Request Body**:
```json
{
  "question": "¿Cómo crear una venta?"
}
```

**Response (200)**:
```json
{
  "answer": "Para crear una venta, debes enviar una petición POST a /api/ventas con los detalles de los productos..."
}
```

**Flujo**: Utiliza embeddings para buscar documentación relevante y genera respuestas contextuales con Gemini AI.

---

## Notas Importantes

### Autenticación
- Todos los endpoints (excepto `/api/auth` y `/api/chat`) requieren token JWT
- El token se envía en el header: `Authorization: Bearer <token>`

### Permisos por Rol
- **admin**: Acceso completo a todos los endpoints
- **gerente**: Acceso a operaciones de gestión (crear/editar productos, usuarios, etc.)
- **vendedor**: Acceso limitado a ventas, consultas y operaciones de su almacén

### Filtros por Almacén
- Los usuarios no-admin solo ven datos de su almacén asignado
- Los admin/gerente pueden filtrar por cualquier almacén

### Paginación
- Parámetros estándar: `page` (default: 1), `per_page` (max: 50)
- Respuesta incluye objeto `pagination` con metadatos

### Manejo de Archivos
- Las imágenes se almacenan en S3 con URLs pre-firmadas
- Los comprobantes se suben como multipart/form-data

### Transaccionalidad
- Las operaciones complejas (ventas, producción, transferencias) son transaccionales
- En caso de error, se revierten todos los cambios

Esta documentación cubre todos los endpoints principales de la API Flask Manngo, proporcionando una guía completa para su integración y uso.