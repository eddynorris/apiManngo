from flask_restful import Resource, reqparse
from flask_jwt_extended import jwt_required, get_jwt
from flask import request, send_file
from models import Venta, VentaDetalle, Inventario, Cliente, PresentacionProducto, Almacen, Movimiento, Lote, Users, Gasto, Pago
from schemas import venta_schema, ventas_schema, clientes_schema, almacenes_schema, presentacion_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, mismo_almacen_o_admin, parse_iso_datetime
from utils.file_handlers import get_presigned_url
from services.pago_service import PagoService
from services.venta_service import VentaService, StockInsuficienteError
from datetime import datetime, timezone
from decimal import Decimal
import logging
from sqlalchemy import asc, desc, orm
import pandas as pd
import io

logger = logging.getLogger(__name__)

class VentaResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, venta_id=None):
        current_user_id = get_jwt().get('sub')
        user_rol = get_jwt().get('rol')
        is_admin = user_rol == 'admin'

        if venta_id:
            venta = Venta.query.get_or_404(venta_id)
            if not is_admin and str(venta.vendedor_id) != str(current_user_id):
                return {"error": "No tienes permiso para ver esta venta"}, 403
            
            result = venta_schema.dump(venta)
            
            if 'detalles' in result and result['detalles']:
                for detalle in result['detalles']:
                    if 'presentacion' in detalle and detalle['presentacion'] and 'url_foto' in detalle['presentacion']:
                        s3_key = detalle['presentacion']['url_foto']
                        if s3_key:
                            detalle['presentacion']['url_foto'] = get_presigned_url(s3_key)
            
            return result, 200
        
        filters = {
            "cliente_id": request.args.get('cliente_id'),
            "almacen_id": request.args.get('almacen_id'),
            "vendedor_id": request.args.get('vendedor_id'),
            "estado_pago": request.args.get('estado_pago'),
            "fecha_inicio": request.args.get('fecha_inicio'),
            "fecha_fin": request.args.get('fecha_fin')
        }

        get_all = request.args.get('all', 'false').lower() == 'true'
        query = Venta.query

        if not is_admin:
            query = query.filter_by(vendedor_id=current_user_id)
        elif filters["vendedor_id"]:
            query = query.filter_by(vendedor_id=filters["vendedor_id"])
        
        if filters["cliente_id"]:
            query = query.filter_by(cliente_id=filters["cliente_id"])
        if filters["almacen_id"]:
            query = query.filter_by(almacen_id=filters["almacen_id"])
        
        if filters["estado_pago"]:
            statuses = [status.strip() for status in filters["estado_pago"].split(',') if status.strip()]
            if statuses:
                query = query.filter(Venta.estado_pago.in_(statuses))

        if filters["fecha_inicio"] and filters["fecha_fin"]:
            try:
                fecha_inicio = parse_iso_datetime(filters["fecha_inicio"], add_timezone=True)
                fecha_fin = parse_iso_datetime(filters["fecha_fin"], add_timezone=True)
                query = query.filter(Venta.fecha.between(fecha_inicio, fecha_fin))
            except ValueError:
                return {"error": "Formato de fecha inválido. Usa ISO 8601"}, 400
        
        sort_by = request.args.get('sort_by', 'fecha')
        sort_order = request.args.get('sort_order', 'desc').lower()

        sortable_columns = {
            'fecha': Venta.fecha, 'total': Venta.total, 'cliente_nombre': Cliente.nombre
        }
        column_to_sort = sortable_columns.get(sort_by, Venta.fecha)
        order_func = desc if sort_order == 'desc' else asc

        if sort_by == 'cliente_nombre':
            query = query.join(Cliente, Venta.cliente_id == Cliente.id)
        
        query = query.order_by(order_func(column_to_sort))

        if get_all:
            ventas_items = query.all()
            return {"data": ventas_schema.dump(ventas_items)}, 200

        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        ventas = query.paginate(page=page, per_page=per_page)
        
        return {
            "data": ventas_schema.dump(ventas.items),
            "pagination": {
                "total": ventas.total, "page": ventas.page, "per_page": ventas.per_page, "pages": ventas.pages
            }
        }, 200

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def post(self):
        """
        Crea una nueva venta delegando la lógica al VentaService.
        """
        import json
        if request.content_type and 'multipart/form-data' in request.content_type:
            data_from_request = request.form.to_dict()
            file_comprobante = request.files.get('comprobante')
            if 'detalles' in data_from_request and isinstance(data_from_request['detalles'], str):
                try:
                    data_from_request['detalles'] = json.loads(data_from_request['detalles'])
                except json.JSONDecodeError:
                    return {"error": "Formato de detalles inválido"}, 400
        else:
            data_from_request = request.get_json()
            file_comprobante = None

        detalles_data = data_from_request.get('detalles', [])
        if not detalles_data:
            return {"error": "La venta debe tener al menos un detalle"}, 400

        try:
            venta_data_loaded = venta_schema.load(data_from_request, partial=("detalles", "tipo_pago", "consumo_diario_kg", "total"))
        except Exception as e:
            return {"error": str(e)}, 400

        claims = get_jwt()
        vendedor_id = claims.get('sub')
        estado = data_from_request.get('estado', 'completado').lower()
        monto_pago = Decimal(str(data_from_request.get('monto_pago') or 0))
        metodo_pago = (data_from_request.get('metodo_pago') or 'efectivo').lower()
        monto_gasto = Decimal(str(data_from_request.get('monto_gasto') or 0))

        try:
            nueva_venta = VentaService.crear_venta(
                vendedor_id=vendedor_id,
                cliente_id=venta_data_loaded.cliente_id,
                almacen_id=venta_data_loaded.almacen_id,
                detalles_data=detalles_data,
                estado=estado,
                fecha=venta_data_loaded.fecha,
                monto_pago=monto_pago,
                metodo_pago=metodo_pago,
                monto_gasto=monto_gasto,
                file_comprobante=file_comprobante,
                estado_pago=data_from_request.get('estado_pago')
            )
            db.session.commit()
            return venta_schema.dump(nueva_venta), 201
        except StockInsuficienteError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except ValueError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al crear venta: {e}", exc_info=True)
            return {"error": "Error interno al procesar la venta."}, 500

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def put(self, venta_id):
        """
        Actualiza una venta existente delegando en el servicio.
        """
        import json
        if request.content_type and 'multipart/form-data' in request.content_type:
            data = request.form.to_dict()
            file_comprobante = request.files.get('comprobante')
            if 'detalles' in data and isinstance(data['detalles'], str):
                try:
                    data['detalles'] = json.loads(data['detalles'])
                except json.JSONDecodeError:
                    return {"error": "Formato de detalles inválido"}, 400
        else:
            data = request.get_json()
            file_comprobante = None

        claims = get_jwt()
        vendedor_id = claims.get('sub')

        try:
            venta_actualizada = VentaService.actualizar_venta(
                venta_id=venta_id,
                vendedor_id=vendedor_id,
                data=data,
                file_comprobante=file_comprobante
            )

            db.session.commit()
            return venta_schema.dump(venta_actualizada), 200
        except StockInsuficienteError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except ValueError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al actualizar venta {venta_id}: {e}", exc_info=True)
            return {"error": "Error interno al actualizar la venta."}, 500

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def delete(self, venta_id=None):
        if venta_id is not None:
            try:
                VentaService.eliminar_venta(venta_id)
                db.session.commit()
                return {"message": "Venta eliminada con éxito"}, 200
            except Exception as e:
                db.session.rollback()
                logger.error(f"Error al eliminar venta {venta_id}: {e}", exc_info=True)
                return {"error": "Error al eliminar la venta"}, 500

        # Lógica tipo batch para eliminar varias ventas
        data = request.get_json() or {}
        venta_ids = data.get("ids", [])
        if not venta_ids or not isinstance(venta_ids, list):
            return {"error": "Debes proporcionar una lista de ids en la propiedad 'ids'"}, 400
        
        try:
            venta_ids = [int(vid) for vid in venta_ids]
        except (ValueError, TypeError):
            return {"error": "Todos los IDs en la lista 'ids' deben ser números enteros válidos"}, 400

        # Verificar permisos (si no es admin, solo eliminar de su propio almacén)
        claims = get_jwt()
        user_rol = claims.get('rol')
        user_almacen_id = claims.get('almacen_id')
        if user_rol != 'admin':
            # Obtener ventas para chequear almacén antes de eliminar
            ventas = Venta.query.filter(Venta.id.in_(venta_ids)).all()
            for v in ventas:
                if int(v.almacen_id) != int(user_almacen_id or 0):
                    return {"error": f"No tienes permisos para eliminar la venta #{v.id} de otro almacén"}, 403

        try:
            count = VentaService.eliminar_ventas_en_lote(venta_ids)
            db.session.commit()
            return {"message": f"{count} ventas eliminadas con éxito"}, 200
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al eliminar ventas en lote: {e}", exc_info=True)
            return {"error": "Error interno al eliminar ventas en lote"}, 500

class VentaFormDataResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Obtiene los datos para el formulario de ventas de forma optimizada.
        Asume que todos los usuarios (incluidos admins) tienen un almacen_id.
        """
        claims = get_jwt()
        user_almacen_id = claims.get('almacen_id')
        user_rol = claims.get('rol')

        # Permite que un admin o un usuario con el mismo almacen_id solicite datos de un almacén específico
        requested_almacen_id = request.args.get('almacen_id', type=int)

        if requested_almacen_id:
            if user_rol == 'admin' or requested_almacen_id == user_almacen_id:
                target_almacen_id = requested_almacen_id
            else:
                return {"error": "No tienes permiso para acceder a los datos de este almacén."}, 403
        else:
            # Si no se especifica un almacén, usa el del usuario logueado
            if not user_almacen_id:
                return {"error": "El token del usuario no tiene un almacén asignado y no se especificó uno."}, 403
            target_almacen_id = user_almacen_id

        try:
            # --- Consultas en Paralelo (si es posible) o secuenciales ---
            clientes = Cliente.query.order_by(Cliente.nombre).all()
            from common import obtener_saldos_pendientes_clientes
            cliente_ids = [c.id for c in clientes]
            if cliente_ids:
                saldos_map = obtener_saldos_pendientes_clientes(cliente_ids)
                for c in clientes:
                    c._saldo_pendiente_cached = saldos_map.get(c.id, 0)

            todos_almacenes = Almacen.query.order_by(Almacen.nombre).all()

            # --- Consulta Principal Optimizada ---
            # Carga el inventario y sus relaciones (Presentacion, Lote) en una sola consulta.
            # CORRECCIÓN: el join va ANTES del filter para que el filtro sobre
            # PresentacionProducto no genere un cross-join implícito.
            # También se agrega filtro cantidad > 0 para excluir lotes agotados
            # y evitar que el agrupamiento posterior reciba duplicados vacíos.
            inventario_disponible = db.session.query(Inventario).join(
                PresentacionProducto, Inventario.presentacion_id == PresentacionProducto.id
            ).options(
                orm.joinedload(Inventario.presentacion),
                orm.joinedload(Inventario.lote)
            ).filter(
                Inventario.almacen_id == target_almacen_id,
                Inventario.cantidad > 0,
                PresentacionProducto.activo == True
            ).order_by(PresentacionProducto.nombre).all()

            # Agrupar inventario por presentacion_id, sumando stock de todos los lotes
            presentaciones_agrupadas = {}
            for inventario in inventario_disponible:
                presentacion = inventario.presentacion
                pres_id = presentacion.id
                
                if pres_id not in presentaciones_agrupadas:
                    dumped_presentacion = presentacion_schema.dump(presentacion)
                    
                    # Generar URL pre-firmada para la foto
                    if presentacion.url_foto:
                        dumped_presentacion['url_foto'] = get_presigned_url(presentacion.url_foto)
                    
                    dumped_presentacion['stock_disponible'] = 0.0
                    presentaciones_agrupadas[pres_id] = dumped_presentacion
                
                # Sumar el stock de este lote al total de la presentación
                presentaciones_agrupadas[pres_id]['stock_disponible'] += float(inventario.cantidad)

            presentaciones_data = list(presentaciones_agrupadas.values())

            return {
                "clientes": clientes_schema.dump(clientes),
                "almacenes": almacenes_schema.dump(todos_almacenes),
                "presentaciones_disponibles": presentaciones_data
            }, 200

        except Exception as e:
            logger.exception(f"Error en VentaFormDataResource: {e}")
            return {"error": "Error al obtener datos para el formulario de venta", "details": str(e)}, 500

# VentaExportResource reescrita y optimizada
class VentaExportResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Exporta ventas a Excel de forma optimizada.
        """
        parser = reqparse.RequestParser()
        parser.add_argument('cliente_id', type=int, location='args')
        parser.add_argument('almacen_id', type=int, location='args')
        parser.add_argument('vendedor_id', type=int, location='args')
        parser.add_argument('estado_pago', type=str, location='args')
        parser.add_argument('fecha_inicio', type=str, location='args')
        parser.add_argument('fecha_fin', type=str, location='args')
        args = parser.parse_args()

        current_user_id = get_jwt().get('sub')
        user_rol = get_jwt().get('rol')
        is_admin = user_rol == 'admin'

        try:
            # --- MEJORA 1: Carga ansiosa (Eager Loading) de relaciones ---
            # Le decimos a SQLAlchemy que cargue todo en una sola vez.
            query = Venta.query.options(
                orm.joinedload(Venta.cliente),
                orm.joinedload(Venta.almacen),
                orm.joinedload(Venta.vendedor),
                orm.selectinload(Venta.detalles).joinedload(VentaDetalle.presentacion)
            )

            # (El resto de tu lógica de filtrado es correcta y se mantiene igual)
            if not is_admin:
                query = query.filter(Venta.vendedor_id == current_user_id)
            elif args['vendedor_id']:
                query = query.filter(Venta.vendedor_id == args['vendedor_id'])

            if args['cliente_id']:
                query = query.filter(Venta.cliente_id == args['cliente_id'])
            if args['almacen_id']:
                query = query.filter(Venta.almacen_id == args['almacen_id'])
            if args['estado_pago']:
                statuses = [status.strip() for status in args['estado_pago'].split(',') if status.strip()]
                if statuses:
                    query = query.filter(Venta.estado_pago.in_(statuses))

            if args['fecha_inicio'] and args['fecha_fin']:
                try:
                    fecha_inicio = parse_iso_datetime(args['fecha_inicio'], add_timezone=True)
                    fecha_fin = parse_iso_datetime(args['fecha_fin'], add_timezone=True)
                    query = query.filter(Venta.fecha.between(fecha_inicio, fecha_fin))
                except ValueError:
                    return {"error": "Formato de fecha inválido. Usa ISO 8601"}, 400

            ventas = query.order_by(desc(Venta.fecha)).all()

            if not ventas:
                return {"message": "No hay ventas para exportar con los filtros seleccionados"}, 404

            # --- MEJORA 2: Construir los datos directamente ---
            # Evitamos la serialización completa y los .apply() de Pandas.
            # Esto es mucho más rápido.
            data_para_excel = []
            for venta in ventas:
                # Concatenamos los nombres de los productos directamente
                productos_str = ', '.join([
                    f"{detalle.presentacion.nombre} (x{detalle.cantidad})"
                    for detalle in venta.detalles
                ])

                data_para_excel.append({
                    'ID': venta.id,
                    'Fecha': venta.fecha.strftime('%Y-%m-%d %H:%M:%S'), # Formatear fecha
                    'Total': float(venta.total), # Convertir Decimal a float para Excel
                    'Tipo de Pago': venta.tipo_pago,
                    'Estado de Pago': venta.estado_pago,
                    'Consumo Diario (kg)': float(venta.consumo_diario_kg) if venta.consumo_diario_kg else None,
                    'Cliente': venta.cliente.nombre if venta.cliente else 'N/A',
                    'Teléfono Cliente': venta.cliente.telefono if venta.cliente else 'N/A',
                    'Almacén': venta.almacen.nombre if venta.almacen else 'N/A',
                    'Vendedor': venta.vendedor.username if venta.vendedor else 'N/A',
                    'Cantidad de Items': len(venta.detalles),
                    'Productos': productos_str
                })

            df = pd.DataFrame(data_para_excel)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Ventas')
            
            output.seek(0)

            return send_file(
                output,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=f'ventas_{datetime.now().strftime("%Y%m%d")}.xlsx'
            )

        except Exception as e:
            logger.error(f"Error al exportar ventas: {str(e)}")
            return {"error": "Error interno al generar el archivo Excel"}, 500

class VentaFilterDataResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Proporciona los datos necesarios para poblar los selects de filtros de exportación de ventas.
        Devuelve listas optimizadas para clientes, almacenes, vendedores y estados de pago.
        """
        try:
            claims = get_jwt()
            user_rol = claims.get('rol')
            user_almacen_id = claims.get('almacen_id')
            is_admin = user_rol == 'admin'

            # 1. Clientes - Lista simple ordenada por nombre
            clientes_query = db.session.query(
                Cliente.id,
                Cliente.nombre
            ).order_by(Cliente.nombre)
            
            clientes = [{
                'id': cliente.id,
                'nombre': cliente.nombre
            } for cliente in clientes_query.all()]

            # 2. Almacenes - Filtrar según permisos del usuario
            if is_admin:
                almacenes_query = db.session.query(
                    Almacen.id,
                    Almacen.nombre
                ).order_by(Almacen.nombre)
            else:
                # Solo mostrar el almacén del usuario
                almacenes_query = db.session.query(
                    Almacen.id,
                    Almacen.nombre
                ).filter(Almacen.id == user_almacen_id).order_by(Almacen.nombre)
            
            almacenes = [{
                'id': almacen.id,
                'nombre': almacen.nombre
            } for almacen in almacenes_query.all()]

            # 3. Vendedores - Filtrar según permisos del usuario
            if is_admin:
                # Admin puede ver todos los vendedores
                vendedores_query = db.session.query(
                    Users.id,
                    Users.username
                ).filter(
                    Users.rol.in_(['usuario', 'gerente'])
                ).order_by(Users.username)
            else:
                # Usuario normal solo ve vendedores de su mismo almacén
                vendedores_query = db.session.query(
                    Users.id,
                    Users.username
                ).filter(
                    Users.rol.in_(['usuario', 'gerente']),
                    Users.almacen_id == user_almacen_id
                ).order_by(Users.username)
            
            vendedores = [{
                'id': vendedor.id,
                'username': vendedor.username
            } for vendedor in vendedores_query.all()]

            # 4. Estados de pago - Lista estática
            estados_pago = [
                {'value': 'pendiente', 'label': 'Pendiente'},
                {'value': 'parcial', 'label': 'Parcial'},
                {'value': 'pagado', 'label': 'Pagado'}
            ]

            return {
                'clientes': clientes,
                'almacenes': almacenes,
                'vendedores': vendedores,
                'estados_pago': estados_pago
            }, 200

        except Exception as e:
            logger.exception(f"Error en VentaFilterDataResource: {e}")
            return {"error": "Error al obtener datos para filtros de exportación", "details": str(e)}, 500
