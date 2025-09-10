# Guía de Rastreo de Depósitos Bancarios

## Descripción

Este sistema permite rastrear con precisión los depósitos directos a la cuenta corporativa, diferenciándolos claramente de los pagos recibidos en cuentas personales de gerentes (Yape, Plin, efectivo).

## Nuevos Campos en el Modelo Pago

### Campos Agregados:
- **`monto_depositado`**: Monto realmente depositado en la cuenta corporativa
- **`depositado`**: Boolean que indica si se realizó el depósito
- **`fecha_deposito`**: Fecha en que se realizó el depósito bancario

### Propiedad Calculada:
- **`monto_en_gerencia`**: Calcula automáticamente cuánto dinero quedó en manos de gerentes

## Casos de Uso

### 1. Pago Completamente Depositado
```json
{
  "venta_id": 123,
  "monto": "1000.00",
  "metodo_pago": "transferencia",
  "monto_depositado": "1000.00",
  "depositado": true,
  "fecha_deposito": "2024-01-15T10:30:00Z"
}
```
**Resultado**: `monto_en_gerencia = 0`

### 2. Pago Parcialmente Depositado
```json
{
  "venta_id": 124,
  "monto": "1500.00",
  "metodo_pago": "yape_plin",
  "monto_depositado": "1200.00",
  "depositado": true,
  "fecha_deposito": "2024-01-15T14:00:00Z"
}
```
**Resultado**: `monto_en_gerencia = 300.00`

### 3. Pago No Depositado (Efectivo/Yape en Gerencia)
```json
{
  "venta_id": 125,
  "monto": "800.00",
  "metodo_pago": "efectivo",
  "depositado": false
}
```
**Resultado**: `monto_en_gerencia = 800.00`

## Endpoints Disponibles

### 1. Crear Pago con Información de Depósito

**POST** `/api/pagos`

```json
{
  "venta_id": 123,
  "monto": "1000.00",
  "metodo_pago": "transferencia",
  "referencia": "TXN123456",
  "fecha": "2024-01-15T09:00:00Z",
  "monto_depositado": "1000.00",
  "depositado": true,
  "fecha_deposito": "2024-01-15T10:30:00Z"
}
```

### 2. Registrar Depósito Bancario (Múltiples Pagos)

**POST** `/api/depositos-bancarios`

```json
{
  "pago_ids": [1, 2, 3],
  "monto_depositado": "2500.00",
  "fecha_deposito": "2024-01-15T16:00:00Z"
}
```

### 3. Obtener Resumen de Depósitos

**GET** `/api/depositos-bancarios?fecha_desde=2024-01-01&fecha_hasta=2024-01-31&almacen_id=1`

**Respuesta:**
```json
{
  "resumen": {
    "total_pagos": "10000.00",
    "total_depositado": "8500.00",
    "total_en_gerencia": "1500.00",
    "cantidad_pagos_depositados": 15,
    "cantidad_pagos_pendientes": 5
  },
  "pagos_depositados": [...],
  "pagos_pendientes_deposito": [...]
}
```

## Flujo de Trabajo Recomendado

### Escenario 1: Pago Directo a Cuenta Corporativa
1. Cliente realiza transferencia directa a cuenta de la empresa
2. Registrar pago con `depositado: true` y `monto_depositado` igual al `monto`
3. El sistema automáticamente calcula `monto_en_gerencia = 0`

### Escenario 2: Pago a Gerente (Yape/Plin/Efectivo)
1. Cliente paga a gerente por Yape, Plin o efectivo
2. Registrar pago con `depositado: false`
3. El sistema automáticamente calcula `monto_en_gerencia = monto`
4. Cuando el gerente deposite, usar el endpoint de depósitos bancarios

### Escenario 3: Depósito Posterior de Múltiples Pagos
1. Gerente acumula varios pagos en efectivo/Yape
2. Realiza un solo depósito bancario por el total
3. Usar endpoint `/api/depositos-bancarios` para registrar el depósito
4. El sistema distribuye proporcionalmente el monto entre los pagos

## Reportes y Análisis

### Consultas Útiles

```sql
-- Total en gerencia por método de pago
SELECT 
    metodo_pago,
    SUM(monto - COALESCE(monto_depositado, 0)) as total_en_gerencia
FROM pagos 
WHERE depositado = false OR monto_depositado < monto
GROUP BY metodo_pago;

-- Pagos pendientes de depósito
SELECT * FROM pagos 
WHERE depositado = false 
ORDER BY fecha DESC;

-- Resumen mensual de depósitos
SELECT 
    DATE_FORMAT(fecha_deposito, '%Y-%m') as mes,
    COUNT(*) as cantidad_depositos,
    SUM(monto_depositado) as total_depositado
FROM pagos 
WHERE depositado = true 
GROUP BY DATE_FORMAT(fecha_deposito, '%Y-%m');
```

## Migración de Datos Existentes

### Ejecutar Migración
```bash
python migrations/add_deposito_fields_to_pagos.py
```

### Actualizar Pagos Existentes
Después de la migración, todos los pagos existentes tendrán `depositado = false`. Para actualizar pagos que ya fueron depositados:

```sql
-- Marcar transferencias como depositadas (asumiendo que fueron directas)
UPDATE pagos 
SET depositado = true, 
    monto_depositado = monto, 
    fecha_deposito = fecha 
WHERE metodo_pago = 'transferencia';

-- Marcar depósitos como depositados
UPDATE pagos 
SET depositado = true, 
    monto_depositado = monto, 
    fecha_deposito = fecha 
WHERE metodo_pago = 'deposito';
```

## Validaciones Implementadas

1. **Constraint de monto**: `monto_depositado >= 0 OR monto_depositado IS NULL`
2. **Constraint de consistencia**: Si `depositado = true`, entonces `monto_depositado` y `fecha_deposito` deben tener valores
3. **Validación de negocio**: El monto depositado no puede exceder el monto del pago
4. **Validación en batch**: El monto total depositado no puede exceder la suma de los pagos seleccionados

## Beneficios del Sistema

1. **Trazabilidad Completa**: Cada peso está rastreado desde el cliente hasta la cuenta corporativa
2. **Eliminación de Discrepancias**: No más diferencias entre ventas registradas y dinero en cuenta
3. **Control de Gerencia**: Visibilidad clara de cuánto dinero está en manos de gerentes
4. **Reportes Precisos**: Informes exactos de flujo de efectivo y depósitos
5. **Auditoría**: Historial completo de todos los movimientos de dinero

## Consideraciones de Seguridad

- Los campos de depósito solo pueden ser modificados por usuarios autenticados
- Se mantiene un log de auditoría con `created_at` y `updated_at`
- Las validaciones previenen inconsistencias en los datos
- Los endpoints requieren autenticación JWT