import logging
from flask import request
from flask_restful import Resource
from flask_jwt_extended import jwt_required
from sqlalchemy import func, and_
from datetime import datetime, timedelta
from decimal import Decimal
import re
from models import db, Movimiento, PresentacionProducto, Producto, Almacen, Users, Lote
from common import handle_db_errors

logger = logging.getLogger(__name__)

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
                    from utils.date_parser import parse_telegram_date_only
                    fecha_inicio = parse_telegram_date_only(fecha_inicio_str)
                    fecha_fin = parse_telegram_date_only(fecha_fin_str)
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
                    from utils.date_parser import parse_telegram_date_only
                    fecha_inicio = parse_telegram_date_only(fecha_inicio_str)
                    fecha_fin = parse_telegram_date_only(fecha_fin_str)
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


class ReporteProduccionEntradasResource(Resource):
    """
    Reporte exhaustivo de movimientos de entrada:
    1. Producción / Ensamblaje desglosado por fechas, lotes y presentaciones.
    2. Traslados / Transferencias recibidos entre almacenes.
    3. Resumen consolidado y cronológico.
    """
    @jwt_required()
    @handle_db_errors
    def get(self):
        try:
            

            # --- 1. Filtros de Consulta ---
            fecha_inicio_str = request.args.get('fecha_inicio')
            fecha_fin_str = request.args.get('fecha_fin')
            almacen_id = request.args.get('almacen_id', type=int)
            lote_id = request.args.get('lote_id', type=int)
            presentacion_id = request.args.get('presentacion_id', type=int)
            producto_id = request.args.get('producto_id', type=int)
            tipo_operacion_filtro = request.args.get('tipo_operacion', 'todos').lower()  # 'produccion', 'transferencia', 'todos'

            # Validar y parsear fechas
            if not fecha_inicio_str or not fecha_fin_str:
                fecha_fin = datetime.now().date()
                fecha_inicio = fecha_fin - timedelta(days=30)
            else:
                try:
                    fecha_inicio = datetime.strptime(fecha_inicio_str.strip(), "%Y-%m-%d").date()
                    fecha_fin = datetime.strptime(fecha_fin_str.strip(), "%Y-%m-%d").date()
                    if fecha_inicio > fecha_fin:
                        return {'error': 'La fecha de inicio no puede ser mayor a la fecha de fin.'}, 400
                except (ValueError, TypeError):
                    return {'error': 'Formato de fecha inválido. Use YYYY-MM-DD.'}, 400

            # --- 2. Consulta Base de Movimientos de Entrada ---
            # Unimos Movimiento con PresentacionProducto, Producto, Lote, Users y Almacen
            base_query = db.session.query(
                Movimiento,
                PresentacionProducto,
                Producto,
                Lote,
                Users,
                Almacen
            ).outerjoin(
                PresentacionProducto, Movimiento.presentacion_id == PresentacionProducto.id
            ).outerjoin(
                Producto, PresentacionProducto.producto_id == Producto.id
            ).outerjoin(
                Lote, Movimiento.lote_id == Lote.id
            ).outerjoin(
                Users, Movimiento.usuario_id == Users.id
            ).outerjoin(
                Almacen, Users.almacen_id == Almacen.id
            ).filter(
                Movimiento.tipo == 'entrada',
                func.date(Movimiento.fecha).between(fecha_inicio, fecha_fin)
            )

            # Filtro por tipo de operación
            if tipo_operacion_filtro == 'produccion':
                base_query = base_query.filter(Movimiento.tipo_operacion.in_(['produccion', 'ensamblaje']))
            elif tipo_operacion_filtro == 'transferencia':
                base_query = base_query.filter(Movimiento.tipo_operacion == 'transferencia')
            else:
                base_query = base_query.filter(Movimiento.tipo_operacion.in_(['produccion', 'ensamblaje', 'transferencia']))

            # Filtros adicionales
            if lote_id:
                base_query = base_query.filter(Movimiento.lote_id == lote_id)
            if presentacion_id:
                base_query = base_query.filter(Movimiento.presentacion_id == presentacion_id)
            if producto_id:
                base_query = base_query.filter(PresentacionProducto.producto_id == producto_id)
            if almacen_id:
                base_query = base_query.filter(Users.almacen_id == almacen_id)

            movimientos_rows = base_query.order_by(Movimiento.fecha.desc(), Movimiento.id.desc()).all()

            # --- 3. Procesamiento y Agrupación de Datos ---
            produccion_por_lote_map = {}
            produccion_por_presentacion_map = {}
            traslados_list = []
            movimientos_detalle_list = []
            resumen_temporal_map = {}

            total_unidades_prod = 0
            total_kg_prod = Decimal('0.00')
            total_ops_prod = 0

            total_unidades_traslado = 0
            total_kg_traslado = Decimal('0.00')
            total_ops_traslado = 0

            for mov, pres, prod, lote, user, alm in movimientos_rows:
                cant = Decimal(str(mov.cantidad or 0))
                cap_kg = Decimal(str(pres.capacidad_kg if pres and pres.capacidad_kg else 1.0))
                kg_total_mov = cant * cap_kg
                fecha_str = mov.fecha.strftime('%Y-%m-%d') if mov.fecha else ''
                fecha_iso = mov.fecha.isoformat() if mov.fecha else None

                # Inicializar mapa temporal
                if fecha_str not in resumen_temporal_map:
                    resumen_temporal_map[fecha_str] = {
                        'fecha': fecha_str,
                        'unidades_produccion': 0,
                        'kg_produccion': 0.0,
                        'unidades_traslado': 0,
                        'kg_traslado': 0.0,
                        'total_unidades_dia': 0,
                        'total_kg_dia': 0.0
                    }

                # Objeto común de detalle
                detalle_item = {
                    'id': mov.id,
                    'fecha': fecha_iso,
                    'tipo_operacion': mov.tipo_operacion,
                    'presentacion_id': pres.id if pres else None,
                    'presentacion_nombre': pres.nombre if pres else 'Materia Prima / General',
                    'producto_id': prod.id if prod else (lote.producto_id if lote else None),
                    'producto_nombre': prod.nombre if prod else (lote.producto.nombre if lote and lote.producto else 'Sin Producto'),
                    'lote_id': lote.id if lote else mov.lote_id,
                    'codigo_lote': lote.codigo_lote if lote else (f"Lote #{mov.lote_id}" if mov.lote_id else "Sin Lote"),
                    'cantidad_unidades': float(cant),
                    'capacidad_kg': float(cap_kg) if pres else None,
                    'total_kg': float(kg_total_mov),
                    'motivo': mov.motivo,
                    'usuario_id': user.id if user else None,
                    'usuario_nombre': user.username if user else None,
                    'almacen_nombre': alm.nombre if alm else 'No asignado'
                }
                movimientos_detalle_list.append(detalle_item)

                # Clasificación según tipo_operacion
                if mov.tipo_operacion in ['produccion', 'ensamblaje']:
                    total_unidades_prod += int(cant)
                    total_kg_prod += kg_total_mov
                    total_ops_prod += 1

                    # Temporal
                    resumen_temporal_map[fecha_str]['unidades_produccion'] += int(cant)
                    resumen_temporal_map[fecha_str]['kg_produccion'] += float(kg_total_mov)
                    resumen_temporal_map[fecha_str]['total_unidades_dia'] += int(cant)
                    resumen_temporal_map[fecha_str]['total_kg_dia'] += float(kg_total_mov)

                    # Agrupación por Lote
                    lote_key = lote.id if lote else (mov.lote_id or 0)
                    if lote_key not in produccion_por_lote_map:
                        produccion_por_lote_map[lote_key] = {
                            'lote_id': lote_key if lote_key != 0 else None,
                            'codigo_lote': lote.codigo_lote if lote else (f"Lote #{lote_key}" if lote_key != 0 else "Sin Lote Asignado"),
                            'descripcion_lote': lote.descripcion if lote else None,
                            'producto_id': prod.id if prod else (lote.producto_id if lote else None),
                            'producto_nombre': prod.nombre if prod else (lote.producto.nombre if lote and lote.producto else 'General'),
                            'unidades_producidas': 0,
                            'kg_producidos': Decimal('0.00'),
                            'operaciones_count': 0,
                            'presentaciones_dict': {}
                        }

                    produccion_por_lote_map[lote_key]['unidades_producidas'] += int(cant)
                    produccion_por_lote_map[lote_key]['kg_producidos'] += kg_total_mov
                    produccion_por_lote_map[lote_key]['operaciones_count'] += 1

                    pres_key = pres.id if pres else 0
                    if pres_key not in produccion_por_lote_map[lote_key]['presentaciones_dict']:
                        produccion_por_lote_map[lote_key]['presentaciones_dict'][pres_key] = {
                            'presentacion_id': pres.id if pres else None,
                            'presentacion_nombre': pres.nombre if pres else 'General',
                            'capacidad_kg': float(cap_kg) if pres else None,
                            'unidades': 0,
                            'kg': Decimal('0.00')
                        }
                    produccion_por_lote_map[lote_key]['presentaciones_dict'][pres_key]['unidades'] += int(cant)
                    produccion_por_lote_map[lote_key]['presentaciones_dict'][pres_key]['kg'] += kg_total_mov

                    # Agrupación por Presentación
                    if pres_key not in produccion_por_presentacion_map:
                        produccion_por_presentacion_map[pres_key] = {
                            'presentacion_id': pres.id if pres else None,
                            'presentacion_nombre': pres.nombre if pres else 'General',
                            'producto_nombre': prod.nombre if prod else 'General',
                            'tipo_presentacion': pres.tipo if pres else None,
                            'capacidad_kg': float(cap_kg) if pres else None,
                            'unidades_producidas': 0,
                            'kg_producidos': Decimal('0.00'),
                            'lotes_set': set()
                        }
                    produccion_por_presentacion_map[pres_key]['unidades_producidas'] += int(cant)
                    produccion_por_presentacion_map[pres_key]['kg_producidos'] += kg_total_mov
                    if lote:
                        produccion_por_presentacion_map[pres_key]['lotes_set'].add(lote.codigo_lote or f"Lote #{lote.id}")

                elif mov.tipo_operacion == 'transferencia':
                    total_unidades_traslado += int(cant)
                    total_kg_traslado += kg_total_mov
                    total_ops_traslado += 1

                    # Temporal
                    resumen_temporal_map[fecha_str]['unidades_traslado'] += int(cant)
                    resumen_temporal_map[fecha_str]['kg_traslado'] += float(kg_total_mov)
                    resumen_temporal_map[fecha_str]['total_unidades_dia'] += int(cant)
                    resumen_temporal_map[fecha_str]['total_kg_dia'] += float(kg_total_mov)

                    # Extraer origen y operación desde motivo
                    origen_match = re.search(r"Transferencia desde (.*?) \(Op: ([^)]+)\)", mov.motivo or "")
                    almacen_origen_nombre = origen_match.group(1) if origen_match else "Almacén de Origen"
                    id_op_transferencia = origen_match.group(2) if origen_match else None

                    traslados_list.append({
                        'movimiento_id': mov.id,
                        'operacion_id': id_op_transferencia,
                        'fecha': fecha_iso,
                        'presentacion_id': pres.id if pres else None,
                        'presentacion_nombre': pres.nombre if pres else 'General',
                        'producto_nombre': prod.nombre if prod else 'General',
                        'lote_id': lote.id if lote else mov.lote_id,
                        'codigo_lote': lote.codigo_lote if lote else (f"Lote #{mov.lote_id}" if mov.lote_id else "Sin Lote"),
                        'cantidad_unidades': float(cant),
                        'capacidad_kg': float(cap_kg) if pres else None,
                        'total_kg': float(kg_total_mov),
                        'almacen_origen': almacen_origen_nombre,
                        'almacen_destino': alm.nombre if alm else 'Almacén Destino',
                        'motivo': mov.motivo,
                        'usuario_nombre': user.username if user else None
                    })

            # Formatear estructuras de retorno
            produccion_por_lote_list = []
            for item in produccion_por_lote_map.values():
                pres_list = []
                for p in item['presentaciones_dict'].values():
                    pres_list.append({
                        'presentacion_id': p['presentacion_id'],
                        'presentacion_nombre': p['presentacion_nombre'],
                        'capacidad_kg': p['capacidad_kg'],
                        'unidades': p['unidades'],
                        'kg': float(p['kg'])
                    })
                produccion_por_lote_list.append({
                    'lote_id': item['lote_id'],
                    'codigo_lote': item['codigo_lote'],
                    'descripcion_lote': item['descripcion_lote'],
                    'producto_id': item['producto_id'],
                    'producto_nombre': item['producto_nombre'],
                    'unidades_producidas': item['unidades_producidas'],
                    'kg_producidos': float(item['kg_producidos']),
                    'operaciones_count': item['operaciones_count'],
                    'presentaciones': pres_list
                })

            produccion_por_presentacion_list = []
            for item in produccion_por_presentacion_map.values():
                produccion_por_presentacion_list.append({
                    'presentacion_id': item['presentacion_id'],
                    'presentacion_nombre': item['presentacion_nombre'],
                    'producto_nombre': item['producto_nombre'],
                    'tipo_presentacion': item['tipo_presentacion'],
                    'capacidad_kg': item['capacidad_kg'],
                    'unidades_producidas': item['unidades_producidas'],
                    'kg_producidos': float(item['kg_producidos']),
                    'lotes_involucrados': list(item['lotes_set'])
                })

            # Ordenar resumen temporal por fecha
            resumen_temporal_list = sorted(resumen_temporal_map.values(), key=lambda x: x['fecha'])

            # Totales globales
            total_unidades_global = total_unidades_prod + total_unidades_traslado
            total_kg_global = total_kg_prod + total_kg_traslado

            response_data = {
                'periodo': {
                    'fecha_inicio': fecha_inicio.isoformat(),
                    'fecha_fin': fecha_fin.isoformat()
                },
                'filtros_aplicados': {
                    'almacen_id': almacen_id,
                    'lote_id': lote_id,
                    'presentacion_id': presentacion_id,
                    'producto_id': producto_id,
                    'tipo_operacion': tipo_operacion_filtro
                },
                'resumen_general': {
                    'total_unidades_ingresadas': total_unidades_global,
                    'total_kg_ingresados': float(total_kg_global),
                    'produccion': {
                        'total_unidades': total_unidades_prod,
                        'total_kg': float(total_kg_prod),
                        'total_lotes_utilizados': len(produccion_por_lote_list),
                        'total_operaciones': total_ops_prod
                    },
                    'traslados': {
                        'total_unidades': total_unidades_traslado,
                        'total_kg': float(total_kg_traslado),
                        'total_operaciones': total_ops_traslado
                    }
                },
                'produccion_por_lote': produccion_por_lote_list,
                'produccion_por_presentacion': produccion_por_presentacion_list,
                'traslados_entre_almacenes': traslados_list,
                'resumen_temporal': resumen_temporal_list,
                'movimientos_detalle': movimientos_detalle_list
            }

            return response_data, 200

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en ReporteProduccionEntradasResource: {str(e)}", exc_info=True)
            return {'error': 'Error interno al generar reporte de entradas', 'details': str(e)}, 500
