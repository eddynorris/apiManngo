# Endpoints de Reporte Financiero

Este documento describe los endpoints para generar reportes financieros y de ventas consolidados.

## Endpoints Disponibles

### 1. Reporte de Ventas por Presentación

**Endpoint:** `GET /reportes/ventas-presentacion`

**Descripción:** Genera un reporte detallado de ventas agrupadas por presentación de producto.

**Filtros disponibles:**
- `fecha_inicio` (opcional): Fecha de inicio en formato YYYY-MM-DD
- `fecha_fin` (opcional): Fecha de fin en formato YYYY-MM-DD
- `almacen_id` (opcional): ID del almacén para filtrar
- `lote_id` (opcional): ID del lote para filtrar

**Respuesta de ejemplo:**
```json
[
  {
    "presentacion_id": 1,
    "presentacion_nombre": "Carbón 25kg",
    "unidades_vendidas": 200,
    "total_vendido": "5000.00"
  },
  {
    "presentacion_id": 2,
    "presentacion_nombre": "Leña 10kg",
    "unidades_vendidas": 150,
    "total_vendido": "3000.00"
  }
]
```

### 2. Resumen Financiero Completo

**Endpoint:** `GET /reportes/resumen-financiero`

**Descripción:** Retorna un resumen financiero completo con totales de ventas, gastos, ganancias y márgenes.

**Filtros disponibles:**
- `fecha_inicio` (opcional): Fecha de inicio en formato YYYY-MM-DD
- `fecha_fin` (opcional): Fecha de fin en formato YYYY-MM-DD
- `almacen_id` (opcional): ID del almacén para filtrar
- `lote_id` (opcional): ID del lote para filtrar (solo afecta ventas)

**Respuesta de ejemplo:**
```json
{
  "total_ventas": "150000.00",
  "total_gastos": "45000.00",
  "ganancia_neta": "105000.00",
  "margen_ganancia": "70.00%",
  "numero_ventas": 25,
  "numero_gastos": 8
}
```