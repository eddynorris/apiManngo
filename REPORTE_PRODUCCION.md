# Reportes de Producción

Este documento describe los nuevos endpoints para generar reportes de producción, especialmente útiles para el seguimiento de briquetas y otros productos.

## Endpoints Disponibles

### 1. Reporte de Producción de Briquetas
**Endpoint:** `GET /reportes/produccion-briquetas`

**Descripción:** Genera un reporte específico para la producción de briquetas, mostrando cantidades producidas por período.

**Parámetros de consulta (query parameters):**
- `fecha_inicio` (opcional): Fecha de inicio en formato YYYY-MM-DD. Por defecto: hace 30 días
- `fecha_fin` (opcional): Fecha de fin en formato YYYY-MM-DD. Por defecto: hoy
- `almacen_id` (opcional): ID del almacén para filtrar
- `presentacion_id` (opcional): ID de presentación específica de briqueta
- `periodo` (opcional): Tipo de agrupación temporal ('dia', 'semana', 'mes'). Por defecto: 'dia'

**Ejemplo de uso:**
```
GET /reportes/produccion-briquetas?fecha_inicio=2024-01-01&fecha_fin=2024-01-31&periodo=dia
```

**Respuesta de ejemplo:**
```json
{
  "periodo": {
    "fecha_inicio": "2024-01-01",
    "fecha_fin": "2024-01-31",
    "tipo_agrupacion": "dia"
  },
  "resumen": {
    "total_unidades_producidas": 150,
    "total_kg_producidos": 750.0,
    "tipos_briquetas_diferentes": 3,
    "total_producciones": 12
  },
  "detalle_por_presentacion": [
    {
      "presentacion_id": 5,
      "presentacion_nombre": "Briqueta Premium 5kg",
      "producto_nombre": "Carbón Vegetal",
      "unidades_producidas": 100,
      "kg_producidos": 500.0,
      "numero_producciones": 8,
      "almacen_nombre": "Almacén Principal"
    }
  ],
  "resumen_temporal": [
    {
      "fecha": "2024-01-15",
      "unidades_producidas": 25,
      "kg_producidos": 125.0
    }
  ]
}
```

### 2. Reporte de Producción General
**Endpoint:** `GET /reportes/produccion-general`

**Descripción:** Genera un reporte de toda la producción (no solo briquetas), agrupado por tipo de presentación.

**Parámetros de consulta:**
- `fecha_inicio` (opcional): Fecha de inicio en formato YYYY-MM-DD
- `fecha_fin` (opcional): Fecha de fin en formato YYYY-MM-DD
- `almacen_id` (opcional): ID del almacén para filtrar
- `tipo_presentacion` (opcional): Filtrar por tipo específico ('briqueta', 'procesado', etc.)

**Ejemplo de uso:**
```
GET /reportes/produccion-general?fecha_inicio=2024-01-01&fecha_fin=2024-01-31&tipo_presentacion=briqueta
```

**Respuesta de ejemplo:**
```json
{
  "periodo": {
    "fecha_inicio": "2024-01-01",
    "fecha_fin": "2024-01-31"
  },
  "resumen_por_tipo": [
    {
      "tipo": "briqueta",
      "unidades_totales": 150,
      "kg_totales": 750.0,
      "producciones_totales": 12
    },
    {
      "tipo": "procesado",
      "unidades_totales": 80,
      "kg_totales": 400.0,
      "producciones_totales": 6
    }
  ],
  "detalle_completo": [
    {
      "tipo_presentacion": "briqueta",
      "presentacion_nombre": "Briqueta Premium 5kg",
      "producto_nombre": "Carbón Vegetal",
      "unidades_producidas": 100,
      "kg_producidos": 500.0,
      "numero_producciones": 8
    }
  ]
}
```

## Cómo Funciona

### Datos de Origen
Los reportes se basan en la tabla `Movimiento` con los siguientes filtros:
- `tipo = 'entrada'`: Solo movimientos de entrada al inventario
- `tipo_operacion = 'ensamblaje'`: Solo operaciones de producción
- `presentacion.tipo = 'briqueta'`: Para el reporte específico de briquetas

### Cálculos
- **Unidades producidas**: Suma de `cantidad` en los movimientos
- **Kg producidos**: Suma de `cantidad * presentacion.capacidad_kg`
- **Número de producciones**: Conteo de movimientos individuales

### Casos de Uso

1. **Reporte mensual de briquetas:**
   ```
   GET /reportes/produccion-briquetas?fecha_inicio=2024-01-01&fecha_fin=2024-01-31
   ```

2. **Reporte semanal por almacén:**
   ```
   GET /reportes/produccion-briquetas?fecha_inicio=2024-01-15&fecha_fin=2024-01-21&almacen_id=1
   ```

3. **Seguimiento diario de un tipo específico:**
   ```
   GET /reportes/produccion-briquetas?presentacion_id=5&periodo=dia
   ```

4. **Comparación de todos los tipos de productos:**
   ```
   GET /reportes/produccion-general?fecha_inicio=2024-01-01&fecha_fin=2024-01-31
   ```

## Notas Importantes

1. **Autenticación**: Todos los endpoints requieren JWT token válido
2. **Fechas por defecto**: Si no se especifican fechas, se usa el último mes
3. **Formato de fechas**: Siempre usar YYYY-MM-DD
4. **Filtro de almacén**: Se basa en el almacén del usuario que registró la producción
5. **Rendimiento**: Los reportes están optimizados con consultas agrupadas

## Integración con el Sistema Actual

Estos reportes son compatibles con el flujo actual donde:
- Las briquetas se registran como productos finales en inventario (no en lotes automáticos)
- Se usan recetas para definir la producción
- Los movimientos se registran con `tipo_operacion = 'ensamblaje'`

Esto permite un seguimiento completo de la producción de briquetas por período, facilitando la gestión y análisis del negocio.