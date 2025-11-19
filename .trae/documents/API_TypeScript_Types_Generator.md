# Generador de Tipos TypeScript para API Flask Manngo

## 1. Objetivo del Proyecto

Crear un generador automático de tipos TypeScript y definiciones de API para la aplicación Flask Manngo, similar a la funcionalidad de `supabase gen types typescript --linked > types/supabase.ts`. Este generador analizará el código fuente de Python y generará una "fuente de verdad" completa para el desarrollo frontend.

### Propósito Principal
- **Automatización**: Generar automáticamente tipos TypeScript desde el código Python
- **Consistencia**: Mantener sincronización entre backend y frontend
- **Productividad**: Reducir errores de tipado y acelerar el desarrollo
- **Documentación**: Crear una referencia completa de la API

## 2. Análisis Requerido

### 2.1 Archivos Fuente a Analizar

#### Modelos de Datos (`models.py`)
- **Propósito**: Extraer definiciones de entidades de base de datos
- **Información a extraer**:
  - Nombres de tablas y campos
  - Tipos de datos SQLAlchemy → TypeScript
  - Relaciones entre entidades
  - Constraints y validaciones
  - Propiedades calculadas

#### Esquemas de Serialización (`schemas.py`)
- **Propósito**: Definir estructura de datos de entrada/salida de API
- **Información a extraer**:
  - Campos serializables
  - Validaciones de Marshmallow
  - Campos opcionales vs requeridos
  - Transformaciones de datos
  - Esquemas anidados

#### Recursos de API (`resources/*.py`)
- **Propósito**: Mapear endpoints, métodos HTTP y parámetros
- **Archivos a analizar**:
  - `almacen_resource.py`
  - `auth_resource.py`
  - `chat_resource.py`
  - `cliente_resource.py`
  - `dashboard_resource.py`
  - `deposito_bancario_resource.py`
  - `gasto_resource.py`
  - `inventario_resource.py`
  - `lote_resource.py`
  - `merma_resource.py`
  - `movimiento_resource.py`
  - `pago_resource.py`
  - `pedido_resource.py`
  - `presentacion_resource.py`
  - `produccion_resource.py`
  - `producto_resource.py`
  - `proveedor_resource.py`
  - `receta_resource.py`
  - `reporte_financiero_resource.py`
  - `reporte_produccion_resource.py`
  - `transferencia_resource.py`
  - `user_resource.py`
  - `venta_resource.py`
  - `ventadetalle_resource.py`

### 2.2 Información a Extraer de Recursos

#### Definición de Endpoints
- **Rutas**: Patrones de URL y parámetros de ruta
- **Métodos HTTP**: GET, POST, PUT, DELETE
- **Autenticación**: Decoradores `@jwt_required`
- **Autorización**: Decoradores `@rol_requerido`
- **Rate Limiting**: Configuraciones de límites

#### Parámetros de Request
- **Query Parameters**: Filtros, paginación, ordenamiento
- **Request Body**: Estructura de datos de entrada
- **Form Data**: Archivos y campos multipart
- **Headers**: Headers requeridos

#### Tipos de Response
- **Success Responses**: Estructura de datos de salida
- **Error Responses**: Códigos de error y mensajes
- **Paginación**: Metadata de paginación
- **Validaciones**: Errores de validación

## 3. Estructura de Salida

### 3.1 Interfaces TypeScript para Modelos

```typescript
// Ejemplo de salida esperada
export interface Usuario {
  id: number;
  username: string;
  email: string;
  rol: 'admin' | 'operador' | 'vendedor';
  activo: boolean;
  fecha_creacion: string; // ISO datetime
  fecha_actualizacion: string; // ISO datetime
}

export interface Producto {
  id: number;
  nombre: string;
  descripcion?: string;
  categoria: string;
  activo: boolean;
  fecha_creacion: string;
  fecha_actualizacion: string;
  // Relaciones
  presentaciones?: PresentacionProducto[];
}
```

### 3.2 Tipos para Requests/Responses

```typescript
// Request Types
export interface CreateUsuarioRequest {
  username: string;
  email: string;
  password: string;
  rol: 'admin' | 'operador' | 'vendedor';
}

export interface UpdateUsuarioRequest {
  username?: string;
  email?: string;
  rol?: 'admin' | 'operador' | 'vendedor';
  activo?: boolean;
}

// Response Types
export interface UsuarioResponse {
  data: Usuario;
  message?: string;
}

export interface UsuariosListResponse {
  data: Usuario[];
  pagination: {
    page: number;
    per_page: number;
    total: number;
    pages: number;
  };
}
```

### 3.3 Enums y Constantes

```typescript
export enum UserRole {
  ADMIN = 'admin',
  OPERADOR = 'operador',
  VENDEDOR = 'vendedor'
}

export enum MetodoPago {
  EFECTIVO = 'efectivo',
  TRANSFERENCIA = 'transferencia',
  TARJETA = 'tarjeta'
}

export const API_ENDPOINTS = {
  AUTH: '/auth',
  USUARIOS: '/usuarios',
  PRODUCTOS: '/productos',
  // ... más endpoints
} as const;
```

### 3.4 Definiciones de API

```typescript
export interface APIEndpoint {
  path: string;
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  auth_required: boolean;
  roles_required?: UserRole[];
  query_params?: Record<string, string>;
  request_body?: any;
  response_type: any;
}

export const API_DEFINITIONS: Record<string, APIEndpoint> = {
  GET_USUARIOS: {
    path: '/usuarios',
    method: 'GET',
    auth_required: true,
    roles_required: [UserRole.ADMIN],
    query_params: {
      page: 'number',
      per_page: 'number',
      username: 'string',
      rol: 'string'
    },
    response_type: 'UsuariosListResponse'
  },
  // ... más definiciones
};
```

## 4. Tecnologías y Herramientas

### 4.1 Análisis de Código Python

#### AST (Abstract Syntax Tree)
- **Librería**: `ast` module de Python
- **Propósito**: Parsear código Python de forma programática
- **Uso**: Extraer definiciones de clases, métodos, decoradores

#### Inspección de Módulos
- **Librería**: `inspect` module
- **Propósito**: Obtener información de objetos en runtime
- **Uso**: Analizar esquemas Marshmallow y modelos SQLAlchemy

#### Análisis de Tipos
- **Librería**: `typing` module
- **Propósito**: Mapear tipos Python a TypeScript
- **Mapeo**:
  - `str` → `string`
  - `int` → `number`
  - `float` → `number`
  - `bool` → `boolean`
  - `datetime` → `string` (ISO format)
  - `Optional[T]` → `T | null`
  - `List[T]` → `T[]`

### 4.2 Generación de TypeScript

#### Estructura de Archivos
```
types/
├── index.ts          # Exportaciones principales
├── models.ts         # Interfaces de modelos
├── requests.ts       # Tipos de request
├── responses.ts      # Tipos de response
├── enums.ts          # Enumeraciones
├── api.ts           # Definiciones de endpoints
└── utils.ts         # Utilidades y helpers
```

#### Plantillas de Código
- **Template Engine**: Jinja2 para generar código TypeScript
- **Formateo**: Prettier para formatear código generado
- **Validación**: TypeScript compiler para verificar sintaxis

### 4.3 Automatización

#### Script Principal
```python
# generate_types.py
import ast
import inspect
from typing import Dict, List, Any
from jinja2 import Template

class TypeScriptGenerator:
    def __init__(self, source_dir: str, output_dir: str):
        self.source_dir = source_dir
        self.output_dir = output_dir
    
    def analyze_models(self) -> Dict[str, Any]:
        """Analizar models.py y extraer definiciones"""
        pass
    
    def analyze_schemas(self) -> Dict[str, Any]:
        """Analizar schemas.py y extraer validaciones"""
        pass
    
    def analyze_resources(self) -> Dict[str, Any]:
        """Analizar resources/*.py y extraer endpoints"""
        pass
    
    def generate_types(self) -> None:
        """Generar archivos TypeScript"""
        pass
```

#### Integración con Build Process
- **npm script**: `npm run generate-types`
- **Pre-commit hook**: Regenerar tipos antes de commit
- **CI/CD**: Validar que tipos estén actualizados

## 5. Casos de Uso y Beneficios

### 5.1 Desarrollo Frontend
- **Autocompletado**: IntelliSense completo en IDE
- **Type Safety**: Detección de errores en tiempo de compilación
- **Refactoring**: Cambios seguros en toda la aplicación
- **Documentación**: Tipos como documentación viva

### 5.2 Testing
- **Mock Data**: Generar datos de prueba tipados
- **API Testing**: Validar contratos de API
- **Integration Tests**: Verificar compatibilidad frontend-backend

### 5.3 Mantenimiento
- **Sincronización**: Detectar cambios en API automáticamente
- **Versionado**: Tracking de cambios en tipos
- **Migration**: Facilitar actualizaciones de API

## 6. Implementación Técnica

### 6.1 Mapeo de Tipos SQLAlchemy → TypeScript

```python
SQLALCHEMY_TO_TYPESCRIPT = {
    'Integer': 'number',
    'String': 'string',
    'Text': 'string',
    'Boolean': 'boolean',
    'DateTime': 'string',  # ISO format
    'Date': 'string',      # ISO format
    'Float': 'number',
    'Numeric': 'number',
    'UUID': 'string',
    'JSON': 'any',
    'ARRAY': 'Array',
}
```

### 6.2 Análisis de Decoradores Flask-RESTful

```python
def extract_endpoint_info(resource_class):
    """Extraer información de endpoints de una clase Resource"""
    endpoints = []
    
    for method_name in ['get', 'post', 'put', 'delete']:
        if hasattr(resource_class, method_name):
            method = getattr(resource_class, method_name)
            
            # Analizar decoradores
            auth_required = has_jwt_required_decorator(method)
            roles_required = extract_roles_from_decorator(method)
            
            endpoints.append({
                'method': method_name.upper(),
                'auth_required': auth_required,
                'roles_required': roles_required,
                'function': method
            })
    
    return endpoints
```

### 6.3 Generación de Documentación

```typescript
// Ejemplo de documentación generada
/**
 * API Endpoint: GET /usuarios
 * 
 * @description Lista usuarios con paginación y filtros (solo admins)
 * @auth_required true
 * @roles_required ['admin']
 * 
 * @param page Número de página (default: 1)
 * @param per_page Elementos por página (max: 50)
 * @param username Filtro por nombre de usuario
 * @param rol Filtro por rol
 * 
 * @returns UsuariosListResponse
 */
export const getUsuarios = (params: GetUsuariosParams): Promise<UsuariosListResponse> => {
  // Implementation would be provided by API client
};
```

## 7. Configuración y Uso

### 7.1 Instalación

```bash
# Instalar dependencias
pip install ast-tools jinja2 typing-extensions

# Ejecutar generador
python generate_types.py --source ./src --output ./types
```

### 7.2 Configuración

```json
// tsconfig.json
{
  "compilerOptions": {
    "strict": true,
    "noImplicitAny": true,
    "exactOptionalPropertyTypes": true
  },
  "include": ["types/**/*"]
}
```

### 7.3 Integración en Proyecto

```typescript
// En tu aplicación frontend
import { Usuario, CreateUsuarioRequest, API_ENDPOINTS } from './types';

const createUser = async (userData: CreateUsuarioRequest): Promise<Usuario> => {
  const response = await fetch(API_ENDPOINTS.USUARIOS, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(userData)
  });
  
  return response.json();
};
```

Este generador proporcionará una fuente de verdad completa y automática para el desarrollo frontend, manteniendo la sincronización entre el backend Flask y el frontend TypeScript.