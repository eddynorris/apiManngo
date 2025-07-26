# API Manngo - Visión General

Esta documentación proporciona una visión general completa de la API de Manngo, diseñada para la gestión de inventario, ventas, clientes y operaciones relacionadas con la comercialización de productos.

## 1. URL Base

La URL base para todas las peticiones a la API es `https://manngo.lat/`.
Si se configura un `Base Path Mapping` en AWS API Gateway o una regla en un Application Load Balancer (ALB) para usar `/api`, la URL base será `https://manngo.lat/api/`.

## 2. Autenticación

La API utiliza JSON Web Tokens (JWT) para la autenticación.
Para acceder a la mayoría de los endpoints protegidos, se debe incluir un token de acceso válido en el encabezado `Authorization` con el prefijo `Bearer`.

**Endpoint de Login:**
*   **POST** `/auth`
    *   **Request Body:**
        ```json
        {
            "username": "tu_nombre_de_usuario",
            "password": "tu_contraseña"
        }
        ```
    *   **Response (Éxito):**
        ```json
        {
            "access_token": "tu_token_jwt",
            "token_type": "Bearer",
            "expires_in": 28800, // Duración en segundos (8 horas por defecto, 12 para admins)
            "user": {
                "id": 1,
                "username": "usuario_ejemplo",
                "rol": "usuario",
                "almacen_id": 1,
                "almacen_nombre": "Almacén Principal"
            }
        }
        ```
    *   **Response (Error - Credenciales Inválidas):**
        ```json
        {
            "message": "Credenciales inválidas"
        }
        ```

## 3. Autorización (Roles y Permisos)

La API implementa un sistema de autorización basado en roles, verificando los permisos en el backend. El `rol` y el `almacen_id` del usuario se incluyen en el token JWT y se utilizan para controlar el acceso a los recursos.

**Roles Definidos:**
*   `admin`: Acceso completo a todas las funcionalidades y datos.
*   `gerente`: Acceso a la mayoría de las funcionalidades de gestión, pero con algunas restricciones de eliminación o configuración global (ej. usuarios).
*   `usuario`: Acceso a funcionalidades operativas básicas relacionadas con su almacén asignado (ej. ventas, movimientos de inventario).

**Decoradores de Autorización Comunes:**
*   `@jwt_required()`: Requiere un token JWT válido.
*   `@rol_requerido('rol1', 'rol2')`: Restringe el acceso a los roles especificados. Por ejemplo, `@rol_requerido('admin', 'gerente')` permite el acceso solo a administradores y gerentes.
*   `@mismo_almacen_o_admin`: Para usuarios no-admin, asegura que solo puedan acceder o modificar recursos asociados a su `almacen_id`. Los administradores tienen acceso irrestricto.

## 4. Patrones Comunes de la API

### Manejo de Errores
La API utiliza un decorador `@handle_db_errors` para gestionar errores de base de datos y un manejo de errores global en `app.py` para errores 404, 405 y 500. Las respuestas de error generalmente incluyen un mensaje descriptivo y un código de estado HTTP adecuado.

**Ejemplo de Respuesta de Error:**
```json
{
    "error": "Mensaje de error descriptivo",
    "details": "Detalles técnicos (solo en entorno de desarrollo)"
}
```

### Paginación
La mayoría de los endpoints que devuelven listas de recursos implementan paginación. Los parámetros de query comunes son:
*   `page`: Número de página (por defecto: 1).
*   `per_page`: Cantidad de elementos por página (por defecto: 10, máximo: `MAX_ITEMS_PER_PAGE` definido en `common.py`).

**Ejemplo de Respuesta Paginada:**
```json
{
    "data": [...], // Array de recursos
    "pagination": {
        "total": 100, // Número total de elementos disponibles
        "page": 1,    // Página actual
        "per_page": 10, // Elementos por página
        "pages": 10   // Número total de páginas
    }
}
```

### Subida de Archivos
Algunos endpoints permiten la subida de archivos (ej. comprobantes de pagos, fotos de presentaciones). Estos esperan `multipart/form-data` y manejan la subida a AWS S3, devolviendo URLs pre-firmadas para el acceso temporal a los archivos.

### Ordenación de Resultados
Varios endpoints `GET` para listar recursos soportan ordenación dinámica mediante los parámetros de query:
*   `sort_by`: Nombre del campo por el que ordenar (ej. `fecha`, `nombre`, `total`).
*   `sort_order`: Dirección de la ordenación (`asc` para ascendente, `desc` para descendente). Por defecto es `desc`.

## 5. Endpoints de la API

A continuación se detallan todos los endpoints de la API con sus esquemas de request/response completos. Todos los endpoints requieren autenticación JWT excepto donde se especifique lo contrario.

### Autenticación

#### POST /auth (Login de usuario)
**Descripción:** Autentica a un usuario y devuelve un token JWT válido para acceder a los endpoints protegidos.

**Request:**
```json
{
    "username": "string",
    "password": "string"
}
```

**Response (200 - Éxito):**
```json
{
    "access_token": "string",
    "token_type": "Bearer",
    "expires_in": 28800,
    "user": {
        "id": 1,
        "username": "string",
        "rol": "admin|gerente|usuario",
        "almacen_id": 1,
        "almacen_nombre": "string"
    }
}
```

**Response (401 - Error):**
```json
{
    "message": "Credenciales inválidas"
}
```

---

### Usuarios

#### GET /usuarios (Obtener lista de usuarios, solo `admin`)
**Parámetros de query:**
- `page` (int): Número de página (default: 1)
- `per_page` (int): Elementos por página (default: 10)
- `sort_by` (string): Campo de ordenación
- `sort_order` (string): asc/desc (default: desc)

**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "username": "string",
            "rol": "admin|gerente|usuario",
            "almacen_id": 1,
            "almacen": {
                "id": 1,
                "nombre": "string"
            },
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00"
        }
    ],
    "pagination": {
        "total": 100,
        "page": 1,
        "per_page": 10,
        "pages": 10
    }
}
```

#### GET /usuarios/<int:user_id> (Obtener detalles de un usuario, solo `admin`)
**Response (200):**
```json
{
    "id": 1,
    "username": "string",
    "rol": "admin|gerente|usuario",
    "almacen_id": 1,
    "almacen": {
        "id": 1,
        "nombre": "string"
    },
    "created_at": "2023-01-01T00:00:00+00:00",
    "updated_at": "2023-01-01T00:00:00+00:00"
}
```

#### POST /usuarios (Crear nuevo usuario, solo `admin`)
**Request:**
```json
{
    "username": "string",
    "password": "string",
    "rol": "admin|gerente|usuario",
    "almacen_id": 1
}
```

**Response (201):**
```json
{
    "id": 1,
    "username": "string",
    "rol": "admin|gerente|usuario",
    "almacen_id": 1,
    "almacen": {
        "id": 1,
        "nombre": "string"
    },
    "created_at": "2023-01-01T00:00:00+00:00",
    "updated_at": "2023-01-01T00:00:00+00:00"
}
```

#### PUT /usuarios/<int:user_id> (Actualizar usuario existente, solo `admin`)
**Request:**
```json
{
    "username": "string",
    "password": "string",
    "rol": "admin|gerente|usuario",
    "almacen_id": 1
}
```

**Response (200):** *Mismo esquema que POST*

#### DELETE /usuarios/<int:user_id> (Eliminar usuario, solo `admin`)
**Response (204):** *Sin contenido*

---

### Productos

#### GET /productos (Obtener lista de productos)
**Parámetros de query:**
- `page`, `per_page`, `sort_by`, `sort_order` (paginación estándar)

**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "nombre": "string",
            "descripcion": "string",
            "precio_compra": "0.00",
            "activo": true,
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00",
            "presentaciones": [
                {
                    "id": 1,
                    "nombre": "string",
                    "capacidad_kg": "0.00",
                    "tipo": "bruto|procesado|merma|briqueta|detalle",
                    "precio_venta": "0.00",
                    "activo": true,
                    "url_foto": "string"
                }
            ]
        }
    ],
    "pagination": {...}
}
```

#### GET /productos/<int:producto_id> (Obtener detalles de un producto)
**Response (200):** *Mismo esquema que el objeto individual en GET /productos*

#### POST /productos (Crear nuevo producto, `admin`, `gerente`)
**Request:**
```json
{
    "nombre": "string",
    "descripcion": "string",
    "precio_compra": "0.00",
    "activo": true
}
```

**Response (201):** *Mismo esquema que GET producto individual*

#### PUT /productos/<int:producto_id> (Actualizar producto, `admin`, `gerente`)
**Request:** *Mismo esquema que POST*
**Response (200):** *Mismo esquema que GET producto individual*

#### DELETE /productos/<int:producto_id> (Eliminar producto, `admin`, `gerente`)
**Response (204):** *Sin contenido*

---

### Presentaciones de Producto

#### GET /presentaciones (Obtener lista de presentaciones)
**Parámetros de query:**
- `page`, `per_page`, `sort_by`, `sort_order` (paginación estándar)

**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "producto_id": 1,
            "nombre": "string",
            "capacidad_kg": "0.00",
            "tipo": "bruto|procesado|merma|briqueta|detalle",
            "precio_venta": "0.00",
            "activo": true,
            "url_foto": "string",
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00",
            "producto": {
                "id": 1,
                "nombre": "string"
            }
        }
    ],
    "pagination": {...}
}
```

#### GET /presentaciones/<int:presentacion_id> (Obtener detalles de una presentación)
**Response (200):** *Mismo esquema que el objeto individual en GET /presentaciones*

#### POST /presentaciones (Crear nueva presentación, `admin`, `gerente`)
**Content-Type:** `multipart/form-data` (para foto) o `application/json`

**Request (JSON):**
```json
{
    "producto_id": 1,
    "nombre": "string",
    "capacidad_kg": "0.00",
    "tipo": "bruto|procesado|merma|briqueta|detalle",
    "precio_venta": "0.00",
    "activo": true
}
```

**Request (multipart/form-data):**
- `producto_id`: 1
- `nombre`: string
- `capacidad_kg`: 0.00
- `tipo`: bruto|procesado|merma|briqueta|detalle
- `precio_venta`: 0.00
- `activo`: true
- `foto`: file (optional)

**Response (201):** *Mismo esquema que GET presentación individual*

#### PUT /presentaciones/<int:presentacion_id> (Actualizar presentación, `admin`, `gerente`)
**Request:** *Mismo esquema que POST*
**Response (200):** *Mismo esquema que GET presentación individual*

#### DELETE /presentaciones/<int:presentacion_id> (Eliminar presentación, `admin`)
**Response (204):** *Sin contenido*

---

### Almacenes

#### GET /almacenes (Obtener lista de almacenes)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "nombre": "string",
            "direccion": "string",
            "ciudad": "string",
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00"
        }
    ],
    "pagination": {...}
}
```

#### GET /almacenes/<int:almacen_id> (Obtener detalles de un almacén)
**Response (200):** *Mismo esquema que el objeto individual en GET /almacenes*

#### POST /almacenes (Crear nuevo almacén, `admin`, `gerente`)
**Request:**
```json
{
    "nombre": "string",
    "direccion": "string",
    "ciudad": "string"
}
```

**Response (201):** *Mismo esquema que GET almacén individual*

#### PUT /almacenes/<int:almacen_id> (Actualizar almacén, `admin`, `gerente`)
**Request:** *Mismo esquema que POST*
**Response (200):** *Mismo esquema que GET almacén individual*

#### DELETE /almacenes/<int:almacen_id> (Eliminar almacén, `admin`)
**Response (204):** *Sin contenido*

---

### Clientes

#### GET /clientes (Obtener lista de clientes)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "nombre": "string",
            "telefono": "string",
            "direccion": "string",
            "ciudad": "string",
            "frecuencia_compra_dias": 30,
            "ultima_fecha_compra": "2023-01-01",
            "saldo_pendiente": "0.00",
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00"
        }
    ],
    "pagination": {...}
}
```

#### GET /clientes/<int:cliente_id> (Obtener detalles de un cliente)
**Response (200):** *Mismo esquema que el objeto individual en GET /clientes*

#### POST /clientes (Crear nuevo cliente, `admin`, `gerente`, `usuario`)
**Request:**
```json
{
    "nombre": "string",
    "telefono": "string",
    "direccion": "string",
    "ciudad": "string",
    "frecuencia_compra_dias": 30
}
```

**Response (201):** *Mismo esquema que GET cliente individual*

#### PUT /clientes/<int:cliente_id> (Actualizar cliente, `admin`, `gerente`, `usuario`)
**Request:** *Mismo esquema que POST*
**Response (200):** *Mismo esquema que GET cliente individual*

#### DELETE /clientes/<int:cliente_id> (Eliminar cliente, `admin`, `gerente`)
**Response (204):** *Sin contenido*

---

### Proveedores

#### GET /proveedores (Obtener lista de proveedores)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "nombre": "string",
            "telefono": "string",
            "direccion": "string",
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00"
        }
    ],
    "pagination": {...}
}
```

#### GET /proveedores/<int:proveedor_id> (Obtener detalles de un proveedor)
**Response (200):** *Mismo esquema que el objeto individual en GET /proveedores*

#### POST /proveedores (Crear nuevo proveedor, `admin`, `gerente`)
**Request:**
```json
{
    "nombre": "string",
    "telefono": "string",
    "direccion": "string"
}
```

**Response (201):** *Mismo esquema que GET proveedor individual*

#### PUT /proveedores/<int:proveedor_id> (Actualizar proveedor, `admin`, `gerente`)
**Request:** *Mismo esquema que POST*
**Response (200):** *Mismo esquema que GET proveedor individual*

#### DELETE /proveedores/<int:proveedor_id> (Eliminar proveedor, `admin`, `gerente`)
**Response (204):** *Sin contenido*

---

### Lotes

#### GET /lotes (Obtener lista de lotes)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "producto_id": 1,
            "proveedor_id": 1,
            "descripcion": "string",
            "peso_humedo_kg": "0.00",
            "peso_seco_kg": "0.00",
            "cantidad_disponible_kg": "0.00",
            "fecha_ingreso": "2023-01-01T00:00:00+00:00",
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00",
            "producto": {
                "id": 1,
                "nombre": "string"
            },
            "proveedor": {
                "id": 1,
                "nombre": "string"
            }
        }
    ],
    "pagination": {...}
}
```

#### GET /lotes/<int:lote_id> (Obtener detalles de un lote)
**Response (200):** *Mismo esquema que el objeto individual en GET /lotes*

#### POST /lotes (Crear nuevo lote, `admin`, `gerente`)
**Request:**
```json
{
    "producto_id": 1,
    "proveedor_id": 1,
    "descripcion": "string",
    "peso_humedo_kg": "0.00",
    "peso_seco_kg": "0.00",
    "cantidad_disponible_kg": "0.00",
    "fecha_ingreso": "2023-01-01T00:00:00+00:00"
}
```

**Response (201):** *Mismo esquema que GET lote individual*

#### PUT /lotes/<int:lote_id> (Actualizar lote, `admin`, `gerente`)
**Request:** *Mismo esquema que POST*
**Response (200):** *Mismo esquema que GET lote individual*

#### DELETE /lotes/<int:lote_id> (Eliminar lote, `admin`, `gerente`)
**Response (204):** *Sin contenido*

---

### Inventario

#### GET /inventarios (Obtener lista de inventario)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "presentacion_id": 1,
            "almacen_id": 1,
            "lote_id": 1,
            "cantidad": 100,
            "stock_minimo": 10,
            "created_at": "2023-01-01T00:00:00+00:00",
            "ultima_actualizacion": "2023-01-01T00:00:00+00:00",
            "presentacion": {
                "id": 1,
                "nombre": "string",
                "capacidad_kg": "0.00"
            },
            "almacen": {
                "id": 1,
                "nombre": "string"
            },
            "lote": {
                "id": 1,
                "descripcion": "string",
                "cantidad_disponible_kg": "0.00"
            }
        }
    ],
    "pagination": {...}
}
```

#### GET /inventarios/<int:inventario_id> (Obtener detalles de un registro de inventario)
**Response (200):** *Mismo esquema que el objeto individual en GET /inventarios*

#### POST /inventarios (Crear uno o múltiples registros de inventario, `@mismo_almacen_o_admin`)
**Request (un registro):**
```json
{
    "presentacion_id": 1,
    "almacen_id": 1,
    "lote_id": 1,
    "cantidad": 100,
    "stock_minimo": 10
}
```

**Request (múltiples registros):**
```json
[
    {
        "presentacion_id": 1,
        "almacen_id": 1,
        "lote_id": 1,
        "cantidad": 100,
        "stock_minimo": 10
    },
    {
        "presentacion_id": 2,
        "almacen_id": 1,
        "lote_id": 1,
        "cantidad": 50,
        "stock_minimo": 5
    }
]
```

**Response (201):** *Mismo esquema que GET inventario individual o array de inventarios*

#### PUT /inventarios/<int:inventario_id> (Actualizar registro de inventario individual, `@mismo_almacen_o_admin`)
**Request:** *Mismo esquema que POST individual*
**Response (200):** *Mismo esquema que GET inventario individual*

#### PUT /inventarios (Actualizar múltiples registros de inventario, `@mismo_almacen_o_admin`)
**Descripción:** Permite actualizar múltiples registros de inventario en una sola solicitud. Cada objeto debe incluir su `id` para identificar qué registro actualizar.

**Request (múltiples registros):**
```json
[
    {
        "id": 1,
        "cantidad": 150,
        "stock_minimo": 15,
        "lote_id": 2,
        "motivo": "Ajuste por inventario físico"
    },
    {
        "id": 2,
        "cantidad": 75,
        "stock_minimo": 10,
        "motivo": "Corrección de stock"
    }
]
```

**Response (200):** *Array de inventarios actualizados*

#### DELETE /inventarios/<int:inventario_id> (Eliminar registro de inventario, `@mismo_almacen_o_admin`)
**Response (204):** *Sin contenido*

---

### Movimientos de Inventario

#### GET /movimientos (Obtener lista de movimientos)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "tipo": "entrada|salida",
            "presentacion_id": 1,
            "lote_id": 1,
            "usuario_id": 1,
            "cantidad": "100.00",
            "fecha": "2023-01-01T00:00:00+00:00",
            "motivo": "string",
            "total_kg": "500.00",
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00",
            "presentacion": {
                "id": 1,
                "nombre": "string",
                "capacidad_kg": "5.00"
            },
            "lote": {
                "id": 1,
                "cantidad_disponible_kg": "1000.00",
                "descripcion": "string"
            },
            "usuario": {
                "id": 1,
                "username": "string"
            }
        }
    ],
    "pagination": {...}
}
```

#### GET /movimientos/<int:movimiento_id> (Obtener detalles de un movimiento)
**Response (200):** *Mismo esquema que el objeto individual en GET /movimientos*

#### POST /movimientos (Registrar nuevo movimiento y actualizar inventario)
**Request:**
```json
{
    "tipo": "entrada|salida",
    "presentacion_id": 1,
    "lote_id": 1,
    "usuario_id": 1,
    "cantidad": "100.00",
    "fecha": "2023-01-01T00:00:00+00:00",
    "motivo": "string"
}
```

**Response (201):** *Mismo esquema que GET movimiento individual*

#### DELETE /movimientos/<int:movimiento_id> (Eliminar movimiento y revertir inventario)
**Response (204):** *Sin contenido*

---

### Ventas

#### GET /ventas (Obtener lista de ventas)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "cliente_id": 1,
            "almacen_id": 1,
            "vendedor_id": 1,
            "fecha": "2023-01-01T00:00:00+00:00",
            "total": "100.00",
            "tipo_pago": "contado|credito",
            "estado_pago": "pendiente|parcial|pagado",
            "consumo_diario_kg": "10.00",
            "saldo_pendiente": "0.00",
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00",
            "cliente": {
                "id": 1,
                "nombre": "string"
            },
            "almacen": {
                "id": 1,
                "nombre": "string"
            },
            "vendedor": {
                "id": 1,
                "username": "string"
            },
            "detalles": [
                {
                    "id": 1,
                    "presentacion_id": 1,
                    "cantidad": 10,
                    "precio_unitario": "10.00",
                    "total_linea": "100.00",
                    "created_at": "2023-01-01T00:00:00+00:00",
                    "updated_at": "2023-01-01T00:00:00+00:00",
                    "presentacion": {
                        "id": 1,
                        "nombre": "string",
                        "precio_venta": "10.00",
                        "url_foto": "string"
                    }
                }
            ]
        }
    ],
    "pagination": {...}
}
```

#### GET /ventas/<int:venta_id> (Obtener detalles de una venta)
**Response (200):** *Mismo esquema que el objeto individual en GET /ventas*

#### POST /ventas (Crear nueva venta, `@mismo_almacen_o_admin`)
**Request:**
```json
{
    "cliente_id": 1,
    "almacen_id": 1,
    "vendedor_id": 1,
    "fecha": "2023-01-01T00:00:00+00:00",
    "total": "100.00",
    "tipo_pago": "contado|credito",
    "estado_pago": "pendiente|parcial|pagado",
    "consumo_diario_kg": "10.00",
    "detalles": [
        {
            "presentacion_id": 1,
            "cantidad": 10,
            "precio_unitario": "10.00"
        }
    ]
}
```

**Response (201):** *Mismo esquema que GET venta individual*

#### PUT /ventas/<int:venta_id> (Actualizar venta, `@mismo_almacen_o_admin`)
**Nota:** Los campos `detalles` y `almacen_id` son inmutables
**Request:**
```json
{
    "cliente_id": 1,
    "vendedor_id": 1,
    "fecha": "2023-01-01T00:00:00+00:00",
    "total": "100.00",
    "tipo_pago": "contado|credito",
    "estado_pago": "pendiente|parcial|pagado",
    "consumo_diario_kg": "10.00"
}
```

**Response (200):** *Mismo esquema que GET venta individual*

#### DELETE /ventas/<int:venta_id> (Eliminar venta y revertir inventario, `@mismo_almacen_o_admin`)
**Response (204):** *Sin contenido*

---

### Detalles de Venta

#### GET /ventas/<int:venta_id>/detalles (Obtener detalles de una venta específica)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "presentacion_id": 1,
            "cantidad": 10,
            "precio_unitario": "10.00",
            "total_linea": "100.00",
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00",
            "presentacion": {
                "id": 1,
                "nombre": "string",
                "precio_venta": "10.00",
                "url_foto": "string"
            }
        }
    ]
}
```

#### POST /ventas/<int:venta_id>/detalles (Agregar detalle a una venta existente, `@mismo_almacen_o_admin`)
**Request:**
```json
{
    "presentacion_id": 1,
    "cantidad": 10,
    "precio_unitario": "10.00"
}
```

**Response (201):** *Mismo esquema que el objeto individual en GET /ventas/venta_id/detalles*

#### DELETE /venta_detalles/<int:detalle_id> (Eliminar un detalle de venta, `@mismo_almacen_o_admin`)
**Response (204):** *Sin contenido*

---

### Pagos

#### GET /pagos (Obtener lista de pagos)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "venta_id": 1,
            "usuario_id": 1,
            "monto": "100.00",
            "fecha": "2023-01-01T00:00:00+00:00",
            "metodo_pago": "efectivo|deposito|transferencia|tarjeta|yape_plin|otro",
            "referencia": "string",
            "url_comprobante": "string",
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00",
            "venta": {
                "id": 1,
                "total": "100.00"
            },
            "usuario": {
                "id": 1,
                "username": "string"
            }
        }
    ],
    "pagination": {...}
}
```

#### GET /pagos/<int:pago_id> (Obtener detalles de un pago)
**Response (200):** *Mismo esquema que el objeto individual en GET /pagos*

#### GET /pagos/venta/<int:venta_id> (Obtener todos los pagos para una venta específica)
**Response (200):** *Mismo esquema que GET /pagos*

#### POST /pagos (Registrar nuevo pago)
**Content-Type:** `application/json` o `multipart/form-data`

**Request (JSON):**
```json
{
    "venta_id": 1,
    "usuario_id": 1,
    "monto": "100.00",
    "fecha": "2023-01-01T00:00:00+00:00",
    "metodo_pago": "efectivo|deposito|transferencia|tarjeta|yape_plin|otro",
    "referencia": "string"
}
```

**Request (multipart/form-data):**
- `venta_id`: 1
- `usuario_id`: 1
- `monto`: 100.00
- `fecha`: 2023-01-01T00:00:00+00:00
- `metodo_pago`: efectivo|deposito|transferencia|tarjeta|yape_plin|otro
- `referencia`: string
- `comprobante`: file (optional)

**Response (201):** *Mismo esquema que GET pago individual*

#### POST /pagos/batch (Registrar múltiples pagos con un solo comprobante)
**Content-Type:** `multipart/form-data`

**Request:**
- `pagos`: JSON string con array de pagos
- `comprobante`: file

**Ejemplo de pagos JSON:**
```json
[
    {
        "venta_id": 1,
        "usuario_id": 1,
        "monto": "50.00",
        "fecha": "2023-01-01T00:00:00+00:00",
        "metodo_pago": "transferencia",
        "referencia": "REF001"
    },
    {
        "venta_id": 2,
        "usuario_id": 1,
        "monto": "75.00",
        "fecha": "2023-01-01T00:00:00+00:00",
        "metodo_pago": "transferencia",
        "referencia": "REF002"
    }
]
```

**Response (201):** *Array de pagos creados*

#### PUT /pagos/<int:pago_id> (Actualizar pago existente)
**Request:** *Mismo esquema que POST /pagos*
**Response (200):** *Mismo esquema que GET pago individual*

#### DELETE /pagos/<int:pago_id> (Eliminar pago y su comprobante asociado)
**Response (204):** *Sin contenido*

---

### Gastos

#### GET /gastos (Obtener lista de gastos)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "descripcion": "string",
            "monto": "100.00",
            "fecha": "2023-01-01",
            "categoria": "logistica|personal|otros",
            "almacen_id": 1,
            "usuario_id": 1,
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00",
            "almacen": {
                "id": 1,
                "nombre": "string"
            },
            "usuario": {
                "id": 1,
                "username": "string"
            }
        }
    ],
    "pagination": {...}
}
```

#### GET /gastos/<int:gasto_id> (Obtener detalles de un gasto)
**Response (200):** *Mismo esquema que el objeto individual en GET /gastos*

#### POST /gastos (Registrar nuevo gasto)
**Request:**
```json
{
    "descripcion": "string",
    "monto": "100.00",
    "fecha": "2023-01-01",
    "categoria": "logistica|personal|otros",
    "almacen_id": 1,
    "usuario_id": 1
}
```

**Response (201):** *Mismo esquema que GET gasto individual*

#### PUT /gastos/<int:gasto_id> (Actualizar gasto existente)
**Request:** *Mismo esquema que POST*
**Response (200):** *Mismo esquema que GET gasto individual*

#### DELETE /gastos/<int:gasto_id> (Eliminar registro de gasto)
**Response (204):** *Sin contenido*

---

### Mermas

#### GET /mermas (Obtener lista de mermas)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "lote_id": 1,
            "cantidad_kg": "10.00",
            "convertido_a_briquetas": false,
            "fecha_registro": "2023-01-01T00:00:00+00:00",
            "usuario_id": 1,
            "created_at": "2023-01-01T00:00:00+00:00",
            "updated_at": "2023-01-01T00:00:00+00:00",
            "lote": {
                "id": 1,
                "cantidad_disponible_kg": "1000.00"
            },
            "usuario": {
                "id": 1,
                "username": "string"
            }
        }
    ],
    "pagination": {...}
}
```

#### GET /mermas/<int:merma_id> (Obtener detalles de una merma)
**Response (200):** *Mismo esquema que el objeto individual en GET /mermas*

#### POST /mermas (Crear nueva merma, `admin`, `gerente`)
**Request:**
```json
{
    "lote_id": 1,
    "cantidad_kg": "10.00",
    "convertido_a_briquetas": false,
    "fecha_registro": "2023-01-01T00:00:00+00:00",
    "usuario_id": 1
}
```

**Response (201):** *Mismo esquema que GET merma individual*

#### PUT /mermas/<int:merma_id> (Actualizar merma existente, `admin`, `gerente`)
**Request:** *Mismo esquema que POST*
**Response (200):** *Mismo esquema que GET merma individual*

#### DELETE /mermas/<int:merma_id> (Eliminar merma, `admin`, `gerente`)
**Response (204):** *Sin contenido*

---

### Pedidos

#### GET /pedidos (Obtener lista de pedidos)
**Response (200):**
```json
{
    "data": [
        {
            "id": 1,
            "cliente_id": 1,
            "almacen_id": 1,
            "vendedor_id": 1,
            "fecha_creacion": "2023-01-01T00:00:00+00:00",
            "fecha_entrega": "2023-01-02T00:00:00+00:00",
            "estado": "programado|confirmado|entregado|cancelado",
            "notas": "string",
            "total_estimado": "100.00",
            "updated_at": "2023-01-01T00:00:00+00:00",
            "cliente": {
                "id": 1,
                "nombre": "string"
            },
            "almacen": {
                "id": 1,
                "nombre": "string"
            },
            "vendedor": {
                "id": 1,
                "username": "string"
            },
            "detalles": [
                {
                    "id": 1,
                    "presentacion_id": 1,
                    "cantidad": 10,
                    "precio_estimado": "10.00",
                    "created_at": "2023-01-01T00:00:00+00:00",
                    "updated_at": "2023-01-01T00:00:00+00:00",
                    "presentacion": {
                        "id": 1,
                        "nombre": "string",
                        "precio_venta": "10.00",
                        "url_foto": "string"
                    }
                }
            ]
        }
    ],
    "pagination": {...}
}
```

#### GET /pedidos/<int:pedido_id> (Obtener detalles de un pedido)
**Response (200):** *Mismo esquema que el objeto individual en GET /pedidos*

#### POST /pedidos (Crear nuevo pedido, `@mismo_almacen_o_admin`)
**Request:**
```json
{
    "cliente_id": 1,
    "almacen_id": 1,
    "vendedor_id": 1,
    "fecha_entrega": "2023-01-02T00:00:00+00:00",
    "estado": "programado|confirmado|entregado|cancelado",
    "notas": "string",
    "detalles": [
        {
            "presentacion_id": 1,
            "cantidad": 10,
            "precio_estimado": "10.00"
        }
    ]
}
```

**Response (201):** *Mismo esquema que GET pedido individual*

#### PUT /pedidos/<int:pedido_id> (Actualizar pedido existente, `@mismo_almacen_o_admin`)
**Nota:** No se puede modificar pedidos ya entregados
**Request:** *Mismo esquema que POST*
**Response (200):** *Mismo esquema que GET pedido individual*

#### DELETE /pedidos/<int:pedido_id> (Eliminar o marcar como cancelado, `@mismo_almacen_o_admin`)
**Nota:** No se puede eliminar pedidos ya entregados
**Response (204):** *Sin contenido*

#### POST /pedidos/<int:pedido_id>/convertir (Convertir un pedido en una venta real, `@mismo_almacen_o_admin`)
**Request:** *Opcional - puede incluir ajustes al pedido*
```json
{
    "fecha_venta": "2023-01-01T00:00:00+00:00",
    "tipo_pago": "contado|credito",
    "ajustes_detalles": [
        {
            "detalle_id": 1,
            "cantidad": 8,
            "precio_unitario": "12.00"
        }
    ]
}
```

**Response (201):** *Esquema de venta creada*

---

### Formularios de Pedido y Venta

#### GET /pedidos/form-data (Obtiene datos para formularios de pedido)
**Response (200):**
```json
{
    "clientes": [
        {
            "id": 1,
            "nombre": "string",
            "telefono": "string",
            "saldo_pendiente": "0.00"
        }
    ],
    "almacenes": [
        {
            "id": 1,
            "nombre": "string"
        }
    ],
    "presentaciones": [
        {
            "id": 1,
            "nombre": "string",
            "precio_venta": "10.00",
            "activo": true,
            "producto": {
                "id": 1,
                "nombre": "string"
            }
        }
    ]
}
```

#### GET /ventas/form-data (Obtiene datos para formularios de venta)
**Response (200):**
```json
{
    "clientes": [
        {
            "id": 1,
            "nombre": "string",
            "telefono": "string",
            "saldo_pendiente": "0.00"
        }
    ],
    "almacenes": [
        {
            "id": 1,
            "nombre": "string"
        }
    ],
    "presentaciones_con_stock": [
        {
            "id": 1,
            "nombre": "string",
            "precio_venta": "10.00",
            "stock_disponible": 100,
            "producto": {
                "id": 1,
                "nombre": "string"
            }
        }
    ]
}
```

---



---

### Dashboard

#### GET /dashboard (Endpoint consolidado para alertas del dashboard, solo `admin`)
**Response (200):**
```json
{
    "stock_bajo": [
        {
            "presentacion_id": 1,
            "presentacion_nombre": "string",
            "almacen_id": 1,
            "almacen_nombre": "string",
            "cantidad_actual": 5,
            "stock_minimo": 10
        }
    ],
    "ventas_pendientes": [
        {
            "venta_id": 1,
            "cliente_nombre": "string",
            "total": "100.00",
            "saldo_pendiente": "50.00",
            "dias_pendiente": 15
        }
    ],
    "alertas_lotes": [
        {
            "lote_id": 1,
            "descripcion": "string",
            "cantidad_disponible_kg": "10.00",
            "tipo_alerta": "stock_bajo"
        }
    ],
    "resumen_financiero": {
        "ventas_mes_actual": "10000.00",
        "gastos_mes_actual": "2000.00",
        "pagos_pendientes": "5000.00",
        "depositos_mes_actual": "8000.00"
    }
}
```

## 6. Modelos de Datos (SQLAlchemy)

Se proporciona un breve resumen de los modelos de datos principales de la base de datos. Para detalles completos sobre atributos, tipos y relaciones, consulte `models.py`.

*   **Users**: Representa a los usuarios del sistema con roles y relación a almacenes.
*   **Producto**: Información general de los productos (ej. "Carbón Vegetal Premium").
*   **PresentacionProducto**: Detalles de cómo se presenta un producto (ej. "Bolsa 5kg"). Incluye `capacidad_kg`, `precio_venta` y `url_foto`.
*   **Lote**: Registra lotes de productos con información de proveedor, pesos y disponibilidad en kg.
*   **Almacen**: Define los diferentes almacenes de la empresa.
*   **Inventario**: Mantiene el stock de `PresentacionProducto` en cada `Almacen`.
*   **Venta**: Representa una transacción de venta con cliente, almacén, vendedor, total y estado de pago.
*   **VentaDetalle**: Los ítems individuales dentro de una `Venta`, con cantidad y precio unitario.
*   **Merma**: Registra las mermas (pérdidas) de lotes.
*   **Proveedor**: Información de los proveedores.
*   **Cliente**: Datos de los clientes, incluyendo saldo pendiente calculado.
*   **Pago**: Registra los pagos recibidos para las ventas, con método de pago, referencia y `url_comprobante`.
*   **Movimiento**: Registra entradas y salidas de inventario, asociadas a `PresentacionProducto`, `Lote` y `Usuario`.
*   **Gasto**: Registra los gastos operativos por categoría, almacén y usuario.
*   **Pedido**: Representa un pedido de cliente con fecha de creación/entrega, estado y detalles.
*   **PedidoDetalle**: Los ítems individuales dentro de un `Pedido`.
*   **DepositoBancario**: Registra depósitos de dinero en el banco, con monto, fecha, almacén, usuario y `url_comprobante_deposito`.

## 7. Schemas (Marshmallow)

Se utilizan esquemas Marshmallow (en `schemas.py`) para la serialización (convertir objetos de base de datos a JSON) y deserialización (convertir JSON a objetos de base de datos) de los modelos. Los esquemas definen la estructura de los datos que se envían y reciben a través de la API.

Cada modelo (`Users`, `Producto`, `PresentacionProducto`, etc.) tiene un esquema singular (ej. `UserSchema`) y un esquema para listas (ej. `UsersSchema(many=True)`). Campos como `Decimal` a menudo se representan como `String` en el JSON para evitar problemas de precisión en JavaScript.

Para detalles específicos de cada esquema, consulte el archivo `schemas.py`. 