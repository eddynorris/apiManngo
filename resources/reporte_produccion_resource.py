from flask import request
from flask_restful import Resource
from flask_jwt_extended import jwt_required
from sqlalchemy import func, and_
from datetime import datetime, timedelta
from decimal import Decimal

from models import db, Movimiento, PresentacionProducto, Producto, Almacen
from common import handle_db_errors

class ReporteProduccionBriquetasResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Genera un reporte de producción de briquetas por período.
        Filtros:
        - fecha_inicio, fecha_fin (YYYY-MM-DD) - Por defecto: último mes
        - almacen_id (opcional)
        - presentacion_id (opcional) - Para filtrar por tipo específico de briqueta
        - periodo: 'dia', 'semana', 'mes' (opcional) - Agrupación temporal
        """
        try:
            # --- Obtención y validación de filtros ---
            fecha_inicio_str = request.args.get('fecha_inicio')
            fecha_fin_str = request.args.get('fecha_fin')
            almacen_id = request.args.get('almacen_id', type=int)
            presentacion_id = request.args.get('presentacion_id', type=int)
            periodo = request.args.get('periodo', 'dia')  # 'dia', 'semana', 'mes'
            
            # Si no se especifican fechas, usar el último mes
            if not fecha_inicio_str or not fecha_fin_str:
                fecha_fin = datetime.now().date()
                fecha_inicio = fecha_fin - timedelta(days=30)
            else:
                try:
                    fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                    fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
                except ValueError:
                    return {'error': 'Formato de fecha inválido, usar YYYY-MM-DD'}, 400
            
            # Validar período
            if periodo not in ['dia', 'semana', 'mes']:
                return {'error': 'Período debe ser: dia, semana o mes'}, 400
            
            # --- Consulta base para movimientos de producción de briquetas ---
            query = db.session.query(
                PresentacionProducto.id.label('presentacion_id'),
                PresentacionProducto.nombre.label('presentacion_nombre'),
                Producto.nombre.label('producto_nombre'),
                func.sum(Movimiento.cantidad).label('unidades_producidas'),
                func.sum(Movimiento.cantidad * PresentacionProducto.capacidad_kg).label('kg_producidos'),
                func.count(Movimiento.id).label('numero_producciones'),
                Almacen.nombre.label('almacen_nombre')
            ).join(
                PresentacionProducto, Movimiento.presentacion_id == PresentacionProducto.id
            ).join(
                Producto, PresentacionProducto.producto_id == Producto.id
            ).join(
                Almacen, Movimiento.usuario_id.in_(
                    db.session.query(db.text('users.id')).select_from(db.text('users')).filter(
                        db.text('users.almacen_id') == Almacen.id
                    )
                )
            ).filter(
                and_(
                    Movimiento.tipo == 'entrada',
                    Movimiento.tipo_operacion == 'ensamblaje',
                    PresentacionProducto.tipo == 'briqueta',
                    func.date(Movimiento.fecha).between(fecha_inicio, fecha_fin)
                )
            )
            
            # Aplicar filtros opcionales
            if presentacion_id:
                query = query.filter(PresentacionProducto.id == presentacion_id)
            
            # Para filtro de almacén, necesitamos una aproximación diferente
            # ya que no hay relación directa entre Movimiento y Almacen
            if almacen_id:
                # Filtrar por usuarios que pertenecen al almacén específico
                from models import Users
                usuarios_almacen = db.session.query(Users.id).filter(Users.almacen_id == almacen_id).subquery()
                query = query.filter(Movimiento.usuario_id.in_(usuarios_almacen))
            
            # Agrupar por presentación
            query = query.group_by(
                PresentacionProducto.id,
                PresentacionProducto.nombre,
                Producto.nombre,
                Almacen.nombre
            )
            
            # Ejecutar consulta
            resultados = query.all()
            
            # --- Formatear respuesta ---
            reporte_data = []
            total_unidades = 0
            total_kg = Decimal('0.00')
            
            for r in resultados:
                unidades = int(r.unidades_producidas or 0)
                kg = Decimal(str(r.kg_producidos or 0))
                
                reporte_data.append({
                    'presentacion_id': r.presentacion_id,
                    'presentacion_nombre': r.presentacion_nombre,
                    'producto_nombre': r.producto_nombre,
                    'unidades_producidas': unidades,
                    'kg_producidos': float(kg),
                    'numero_producciones': int(r.numero_producciones or 0),
                    'almacen_nombre': r.almacen_nombre or 'No especificado'
                })
                
                total_unidades += unidades
                total_kg += kg
            
            # --- Consulta adicional para resumen por período ---
            resumen_temporal = []
            if periodo == 'dia':
                # Agrupar por día
                query_temporal = db.session.query(
                    func.date(Movimiento.fecha).label('fecha'),
                    func.sum(Movimiento.cantidad).label('unidades_dia'),
                    func.sum(Movimiento.cantidad * PresentacionProducto.capacidad_kg).label('kg_dia')
                ).join(
                    PresentacionProducto, Movimiento.presentacion_id == PresentacionProducto.id
                ).filter(
                    and_(
                        Movimiento.tipo == 'entrada',
                        Movimiento.tipo_operacion == 'ensamblaje',
                        PresentacionProducto.tipo == 'briqueta',
                        func.date(Movimiento.fecha).between(fecha_inicio, fecha_fin)
                    )
                ).group_by(func.date(Movimiento.fecha)).order_by(func.date(Movimiento.fecha))
                
                if presentacion_id:
                    query_temporal = query_temporal.filter(PresentacionProducto.id == presentacion_id)
                
                resultados_temporales = query_temporal.all()
                resumen_temporal = [{
                    'fecha': r.fecha.isoformat(),
                    'unidades_producidas': int(r.unidades_dia or 0),
                    'kg_producidos': float(r.kg_dia or 0)
                } for r in resultados_temporales]
            
            # Respuesta final
            respuesta = {
                'periodo': {
                    'fecha_inicio': fecha_inicio.isoformat(),
                    'fecha_fin': fecha_fin.isoformat(),
                    'tipo_agrupacion': periodo
                },
                'resumen': {
                    'total_unidades_producidas': total_unidades,
                    'total_kg_producidos': float(total_kg),
                    'tipos_briquetas_diferentes': len(reporte_data),
                    'total_producciones': sum(item['numero_producciones'] for item in reporte_data)
                },
                'detalle_por_presentacion': reporte_data,
                'resumen_temporal': resumen_temporal
            }
            
            return respuesta, 200
            
        except Exception as e:
            db.session.rollback()
            return {'error': 'Error interno del servidor', 'details': str(e)}, 500

class ReporteProduccionGeneralResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Genera un reporte general de toda la producción (no solo briquetas).
        Filtros:
        - fecha_inicio, fecha_fin (YYYY-MM-DD)
        - almacen_id (opcional)
        - tipo_presentacion (opcional): 'briqueta', 'procesado', etc.
        """
        try:
            # --- Obtención y validación de filtros ---
            fecha_inicio_str = request.args.get('fecha_inicio')
            fecha_fin_str = request.args.get('fecha_fin')
            almacen_id = request.args.get('almacen_id', type=int)
            tipo_presentacion = request.args.get('tipo_presentacion')
            
            # Si no se especifican fechas, usar el último mes
            if not fecha_inicio_str or not fecha_fin_str:
                fecha_fin = datetime.now().date()
                fecha_inicio = fecha_fin - timedelta(days=30)
            else:
                try:
                    fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                    fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
                except ValueError:
                    return {'error': 'Formato de fecha inválido, usar YYYY-MM-DD'}, 400
            
            # --- Consulta base para todos los movimientos de producción ---
            query = db.session.query(
                PresentacionProducto.tipo.label('tipo_presentacion'),
                PresentacionProducto.nombre.label('presentacion_nombre'),
                Producto.nombre.label('producto_nombre'),
                func.sum(Movimiento.cantidad).label('unidades_producidas'),
                func.sum(Movimiento.cantidad * PresentacionProducto.capacidad_kg).label('kg_producidos'),
                func.count(Movimiento.id).label('numero_producciones')
            ).join(
                PresentacionProducto, Movimiento.presentacion_id == PresentacionProducto.id
            ).join(
                Producto, PresentacionProducto.producto_id == Producto.id
            ).filter(
                and_(
                    Movimiento.tipo == 'entrada',
                    Movimiento.tipo_operacion == 'ensamblaje',
                    func.date(Movimiento.fecha).between(fecha_inicio, fecha_fin)
                )
            )
            
            # Aplicar filtros opcionales
            if tipo_presentacion:
                query = query.filter(PresentacionProducto.tipo == tipo_presentacion)
            
            if almacen_id:
                from models import Users
                usuarios_almacen = db.session.query(Users.id).filter(Users.almacen_id == almacen_id).subquery()
                query = query.filter(Movimiento.usuario_id.in_(usuarios_almacen))
            
            # Agrupar por tipo y presentación
            query = query.group_by(
                PresentacionProducto.tipo,
                PresentacionProducto.nombre,
                Producto.nombre
            ).order_by(PresentacionProducto.tipo, PresentacionProducto.nombre)
            
            # Ejecutar consulta
            resultados = query.all()
            
            # --- Formatear respuesta ---
            reporte_data = []
            resumen_por_tipo = {}
            
            for r in resultados:
                tipo = r.tipo_presentacion
                unidades = int(r.unidades_producidas or 0)
                kg = Decimal(str(r.kg_producidos or 0))
                
                # Agregar al detalle
                reporte_data.append({
                    'tipo_presentacion': tipo,
                    'presentacion_nombre': r.presentacion_nombre,
                    'producto_nombre': r.producto_nombre,
                    'unidades_producidas': unidades,
                    'kg_producidos': float(kg),
                    'numero_producciones': int(r.numero_producciones or 0)
                })
                
                # Agregar al resumen por tipo
                if tipo not in resumen_por_tipo:
                    resumen_por_tipo[tipo] = {
                        'unidades_totales': 0,
                        'kg_totales': Decimal('0.00'),
                        'producciones_totales': 0
                    }
                
                resumen_por_tipo[tipo]['unidades_totales'] += unidades
                resumen_por_tipo[tipo]['kg_totales'] += kg
                resumen_por_tipo[tipo]['producciones_totales'] += int(r.numero_producciones or 0)
            
            # Convertir resumen a formato de respuesta
            resumen_formateado = [{
                'tipo': tipo,
                'unidades_totales': datos['unidades_totales'],
                'kg_totales': float(datos['kg_totales']),
                'producciones_totales': datos['producciones_totales']
            } for tipo, datos in resumen_por_tipo.items()]
            
            # Respuesta final
            respuesta = {
                'periodo': {
                    'fecha_inicio': fecha_inicio.isoformat(),
                    'fecha_fin': fecha_fin.isoformat()
                },
                'resumen_por_tipo': resumen_formateado,
                'detalle_completo': reporte_data
            }
            
            return respuesta, 200
            
        except Exception as e:
            db.session.rollback()
            return {'error': 'Error interno del servidor', 'details': str(e)}, 500