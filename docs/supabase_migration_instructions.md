# 🚀 Instrucciones de Migración Supabase - Sistema de Depósitos

## 📋 Resumen
Este documento proporciona instrucciones paso a paso para implementar el sistema de rastreo de depósitos bancarios en tu base de datos Supabase.

## ✅ Pruebas Realizadas

### Pruebas de Lógica de Negocio
- ✅ **Cálculos de monto en gerencia**: Validados correctamente
- ✅ **Depósitos completos y parciales**: Funcionando
- ✅ **Precisión decimal**: Verificada
- ✅ **Resúmenes múltiples pagos**: Operativo
- ✅ **Validaciones de negocio**: Detectando inconsistencias

### Resultados de Pruebas
```
📊 Ejemplo de prueba exitosa:
- Total pagos: 3
- Depositados: 2  
- Pendientes: 1
- Monto total: $350.00
- Depositado: $250.00
- En gerencia: $100.00
```

## 🛠️ Pasos de Implementación

### Paso 1: Preparación
1. **Accede a tu dashboard de Supabase**
   - Ve a [supabase.com](https://supabase.com)
   - Selecciona tu proyecto
   - Ve a la sección "SQL Editor"

2. **Haz backup de tu base de datos** (recomendado)
   ```sql
   -- Crear backup de la tabla pagos
   CREATE TABLE pagos_backup AS SELECT * FROM pagos;
   ```

### Paso 2: Ejecutar Migración Principal
1. **Copia el contenido completo** del archivo `migrations/supabase_depositos_migration.sql`
2. **Pégalo en el SQL Editor** de Supabase
3. **Ejecuta el script** haciendo clic en "Run"
4. **Verifica que no hay errores** en la consola

### Paso 3: Validar la Migración
1. **Copia el contenido** del archivo `migrations/test_supabase_migration.sql`
2. **Ejecuta el script de validación** en Supabase
3. **Revisa los resultados** - deberías ver:
   - ✅ Columnas agregadas correctamente
   - ✅ Restricciones funcionando
   - ✅ Índices creados
   - ✅ Funciones operativas
   - ✅ Vistas funcionando

### Paso 4: Actualizar Datos Existentes (Opcional)
Si tienes pagos existentes, actualízalos:
```sql
-- Actualizar registros existentes con valores por defecto
UPDATE pagos SET 
    monto_depositado = 0.00,
    depositado = false
WHERE monto_depositado IS NULL OR depositado IS NULL;
```

## 📊 Nuevas Funcionalidades Disponibles

### Campos Agregados
- **`monto_depositado`**: Monto real depositado en cuenta corporativa
- **`depositado`**: Boolean indicando si se realizó el depósito
- **`fecha_deposito`**: Timestamp del depósito bancario

### Vistas Creadas
1. **`vista_resumen_depositos`**: Resumen general de todos los depósitos
2. **`vista_pagos_depositos`**: Vista detallada con información de depósitos

### Función Útil
- **`calcular_monto_en_gerencia(monto, monto_depositado)`**: Calcula dinero retenido

## 🔍 Consultas Útiles

### Resumen General
```sql
SELECT * FROM vista_resumen_depositos;
```

### Pagos Pendientes de Depósito
```sql
SELECT * FROM vista_pagos_depositos 
WHERE depositado = false OR depositado IS NULL;
```

### Dinero Retenido en Gerencia
```sql
SELECT * FROM vista_pagos_depositos 
WHERE monto_en_gerencia > 0;
```

### Depósitos por Rango de Fechas
```sql
SELECT * FROM vista_pagos_depositos 
WHERE depositado = true 
AND fecha_deposito BETWEEN '2024-01-01' AND '2024-12-31';
```

### Total en Gerencia por Usuario
```sql
SELECT 
    usuario_id,
    SUM(monto_en_gerencia) as total_en_gerencia
FROM vista_pagos_depositos 
GROUP BY usuario_id 
HAVING SUM(monto_en_gerencia) > 0;
```

## 🛡️ Validaciones Implementadas

1. **Monto depositado no negativo**
   ```sql
   CHECK (monto_depositado >= 0)
   ```

2. **Monto depositado no excede el total**
   ```sql
   CHECK (monto_depositado <= monto)
   ```

3. **Depósito marcado debe tener fecha**
   ```sql
   CHECK (NOT depositado OR fecha_deposito IS NOT NULL)
   ```

## 🔧 Uso en la API

### Crear Pago con Depósito
```json
{
  "venta_id": 1,
  "monto": 100.00,
  "monto_depositado": 75.00,
  "depositado": true,
  "fecha_deposito": "2024-01-15T10:30:00",
  "metodo_pago": "transferencia"
}
```

### Registrar Depósito Posterior
```json
POST /pagos/depositos
{
  "pago_ids": [1, 2, 3],
  "monto_depositado": 250.00,
  "fecha_deposito": "2024-01-15T14:30:00"
}
```

### Obtener Resumen de Depósitos
```json
GET /pagos/depositos
```

## 🚨 Solución de Problemas

### Error: "Column already exists"
- **Causa**: La migración ya se ejecutó parcialmente
- **Solución**: El script maneja esto automáticamente con verificaciones `IF NOT EXISTS`

### Error: "Check constraint violation"
- **Causa**: Datos existentes no cumplen las nuevas restricciones
- **Solución**: Actualiza los datos problemáticos antes de la migración

### Error: "Function does not exist"
- **Causa**: La función `calcular_monto_en_gerencia` no se creó
- **Solución**: Re-ejecuta la sección de funciones del script de migración

## 📈 Beneficios del Sistema

1. **Trazabilidad Completa**: Cada peso depositado está registrado
2. **Diferenciación Clara**: Separa dinero corporativo vs. gerencial
3. **Conciliación Precisa**: Elimina discrepancias manuales
4. **Reportes Automáticos**: Vistas pre-configuradas para análisis
5. **Integridad de Datos**: Validaciones automáticas
6. **Flexibilidad**: Soporta depósitos parciales y múltiples

## 🎯 Próximos Pasos

1. ✅ **Migración ejecutada**
2. ✅ **Validación completada**
3. 🔄 **Reiniciar aplicación** para cargar nuevos endpoints
4. 🧪 **Probar funcionalidad** con datos reales
5. 📊 **Comenzar a usar reportes** de depósitos
6. 🔄 **Entrenar usuarios** en el nuevo flujo

## 📞 Soporte

Si encuentras algún problema:
1. Revisa los logs de Supabase
2. Verifica que todas las restricciones se crearon
3. Ejecuta el script de validación nuevamente
4. Consulta la documentación en `docs/depositos_bancarios_guide.md`

---

**¡Tu sistema de depósitos está listo para eliminar las discrepancias y proporcionar trazabilidad completa! 🎉**