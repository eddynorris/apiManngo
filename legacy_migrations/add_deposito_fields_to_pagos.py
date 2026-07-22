#!/usr/bin/env python3
"""
Migración para agregar campos de depósito al modelo Pago
Ejecuta este script para actualizar la base de datos existente
"""

from sqlalchemy import text
from extensions import db
from flask import Flask
import os
import sys

# Agregar el directorio raíz al path para importar la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def create_app():
    """Crear instancia de la aplicación Flask para la migración"""
    app = Flask(__name__)
    
    # Configuración básica de la base de datos
    # Ajusta estos valores según tu configuración
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///manngo.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    return app

def migrate_add_deposito_fields():
    """Agregar campos de depósito a la tabla pagos"""
    app = create_app()
    
    with app.app_context():
        try:
            print("Iniciando migración: Agregando campos de depósito a tabla pagos...")
            
            # Verificar si las columnas ya existen
            result = db.session.execute(text("PRAGMA table_info(pagos)"))
            columns = [row[1] for row in result.fetchall()]
            
            migrations_needed = []
            
            if 'monto_depositado' not in columns:
                migrations_needed.append("ALTER TABLE pagos ADD COLUMN monto_depositado NUMERIC(12, 2)")
                
            if 'depositado' not in columns:
                migrations_needed.append("ALTER TABLE pagos ADD COLUMN depositado BOOLEAN DEFAULT FALSE NOT NULL")
                
            if 'fecha_deposito' not in columns:
                migrations_needed.append("ALTER TABLE pagos ADD COLUMN fecha_deposito DATETIME")
            
            if not migrations_needed:
                print("✅ Las columnas ya existen. No se requiere migración.")
                return
            
            # Ejecutar migraciones
            for migration in migrations_needed:
                print(f"Ejecutando: {migration}")
                db.session.execute(text(migration))
            
            # Agregar constraints (SQLite tiene limitaciones, pero podemos validar en la aplicación)
            print("Agregando validaciones...")
            
            db.session.commit()
            print("✅ Migración completada exitosamente!")
            
            # Mostrar estadísticas
            result = db.session.execute(text("SELECT COUNT(*) FROM pagos"))
            total_pagos = result.fetchone()[0]
            print(f"📊 Total de pagos en la base de datos: {total_pagos}")
            
            if total_pagos > 0:
                print("\n⚠️  IMPORTANTE: Los pagos existentes tienen depositado=FALSE por defecto.")
                print("   Puedes actualizar manualmente los que ya fueron depositados usando:")
                print("   UPDATE pagos SET depositado=TRUE, monto_depositado=monto, fecha_deposito=fecha WHERE [condición];")
            
        except Exception as e:
            print(f"❌ Error durante la migración: {str(e)}")
            db.session.rollback()
            raise
        finally:
            db.session.close()

def rollback_migration():
    """Revertir la migración (eliminar columnas agregadas)"""
    app = create_app()
    
    with app.app_context():
        try:
            print("⚠️  Iniciando rollback: Eliminando campos de depósito...")
            
            # SQLite no soporta DROP COLUMN directamente
            # Necesitamos recrear la tabla sin las columnas
            print("Creando tabla temporal...")
            
            # Crear tabla temporal con estructura original
            db.session.execute(text("""
                CREATE TABLE pagos_backup AS 
                SELECT id, venta_id, usuario_id, monto, fecha, metodo_pago, 
                       referencia, url_comprobante, created_at, updated_at
                FROM pagos
            """))
            
            # Eliminar tabla original
            db.session.execute(text("DROP TABLE pagos"))
            
            # Renombrar tabla temporal
            db.session.execute(text("ALTER TABLE pagos_backup RENAME TO pagos"))
            
            # Recrear índices y constraints si es necesario
            # (Esto depende de tu esquema específico)
            
            db.session.commit()
            print("✅ Rollback completado exitosamente!")
            
        except Exception as e:
            print(f"❌ Error durante el rollback: {str(e)}")
            db.session.rollback()
            raise
        finally:
            db.session.close()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Migración de campos de depósito')
    parser.add_argument('--rollback', action='store_true', 
                       help='Revertir la migración (eliminar columnas)')
    
    args = parser.parse_args()
    
    if args.rollback:
        confirm = input("⚠️  ¿Estás seguro de que quieres revertir la migración? (sí/no): ")
        if confirm.lower() in ['sí', 'si', 'yes', 'y']:
            rollback_migration()
        else:
            print("Rollback cancelado.")
    else:
        migrate_add_deposito_fields()