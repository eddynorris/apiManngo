from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Venta, VentaDetalle, Inventario, Cliente, PresentacionProducto, Almacen, Movimiento, Lote
from schemas import venta_schema, ventas_schema, clientes_schema, almacenes_schema, presentacion_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, mismo_almacen_o_admin
from utils.file_handlers import get_presigned_url
from datetime import datetime, timezone
from decimal import Decimal
import logging
from sqlalchemy import asc, desc, orm

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
                fecha_inicio = datetime.fromisoformat(filters["fecha_inicio"]).replace(tzinfo=timezone.utc)
                fecha_fin = datetime.fromisoformat(filters["fecha_fin"]).replace(tzinfo=timezone.utc)
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
        Crea una nueva venta. El lote_id se obtiene automáticamente del inventario.
        El frontend solo necesita enviar presentacion_id y cantidad.
        """
        data_from_request = request.get_json()
        detalles_data = data_from_request.get('detalles', [])
        if not detalles_data:
            return {"error": "La venta debe tener al menos un detalle"}, 400
        
        venta_data = venta_schema.load(data_from_request, partial=("detalles",))
        cliente = Cliente.query.get_or_404(venta_data.cliente_id)
        
        claims = get_jwt()
        venta_data.vendedor_id = claims.get('sub')

        total = Decimal('0')
        detalles_para_venta = []

        # Optimización para obtener todos los datos necesarios en menos consultas
        presentacion_ids = [d.get('presentacion_id') for d in detalles_data]
        inventarios = Inventario.query.filter(
            Inventario.presentacion_id.in_(presentacion_ids),
            Inventario.almacen_id == venta_data.almacen_id
        ).all()
        inventarios_dict = {i.presentacion_id: i for i in inventarios}
        
        for detalle_data in detalles_data:
            presentacion_id = detalle_data.get('presentacion_id')
            cantidad = detalle_data.get('cantidad')

            if not all([presentacion_id, cantidad]):
                return {"error": "Cada detalle debe incluir presentacion_id y cantidad"}, 400

            inventario = inventarios_dict.get(presentacion_id)
            if not inventario:
                return {"error": f"No se encontró inventario para la presentación {presentacion_id} en este almacén."}, 404
            
            if inventario.cantidad < cantidad:
                return {"error": f"Stock insuficiente para {inventario.presentacion.nombre} (Disponible: {inventario.cantidad})"}, 400

            # --- LÓGICA ÓPTIMA: Obtener lote automáticamente ---
            lote_id_obtenido = inventario.lote_id
            if not lote_id_obtenido:
                 return {"error": f"El inventario para {inventario.presentacion.nombre} no tiene un lote asignado."}, 400

            precio_unitario = detalle_data.get('precio_unitario') or inventario.presentacion.precio_venta
            
            nuevo_detalle = VentaDetalle(
                presentacion_id=presentacion_id,
                cantidad=cantidad,
                precio_unitario=Decimal(precio_unitario),
                lote_id=lote_id_obtenido # Se asigna el lote obtenido del inventario
            )
            detalles_para_venta.append(nuevo_detalle)
            total += cantidad * Decimal(precio_unitario)
            inventario.cantidad -= cantidad # Deducir stock

        nueva_venta = Venta(
            cliente_id=venta_data.cliente_id,
            almacen_id=venta_data.almacen_id,
            vendedor_id=venta_data.vendedor_id,
            total=total,
            tipo_pago=venta_data.tipo_pago,
            fecha=venta_data.fecha,
            consumo_diario_kg=venta_data.consumo_diario_kg,
            detalles=detalles_para_venta
        )

        db.session.add(nueva_venta)
        db.session.flush()

        for detalle in nueva_venta.detalles:
            movimiento = Movimiento(
                tipo='salida',
                presentacion_id=detalle.presentacion_id,
                lote_id=detalle.lote_id,
                cantidad=detalle.cantidad,
                usuario_id=claims['sub'],
                motivo=f"Venta ID: {nueva_venta.id} - Cliente: {cliente.nombre}"
            )
            db.session.add(movimiento)

        db.session.commit()
        return venta_schema.dump(nueva_venta), 201

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def put(self, venta_id):
        """
        Actualiza una venta existente, incluyendo sus detalles.
        Permite cambiar la presentación, y el lote se ajustará automáticamente.
        """
        venta = Venta.query.options(orm.joinedload(Venta.detalles)).get_or_404(venta_id)
        data = request.get_json()
        nuevos_detalles_data = data.get('detalles', [])

        # --- 1. Revertir el estado anterior ---
        # Devolver el stock al inventario usando el lote_id guardado en cada detalle
        for detalle_actual in venta.detalles:
            inventario = Inventario.query.filter_by(
                almacen_id=venta.almacen_id,
                presentacion_id=detalle_actual.presentacion_id
            ).first()
            if inventario:
                inventario.cantidad += detalle_actual.cantidad
        
        # Eliminar movimientos antiguos
        Movimiento.query.filter(Movimiento.motivo.like(f"Venta ID: {venta_id}%")).delete(synchronize_session=False)

        # --- 2. Procesar y aplicar el nuevo estado ---
        nuevo_total = Decimal('0')
        nuevos_detalles_obj = []

        for detalle_data in nuevos_detalles_data:
            presentacion_id = detalle_data.get('presentacion_id')
            cantidad = detalle_data.get('cantidad')
            precio_unitario = Decimal(detalle_data.get('precio_unitario'))

            if not all([presentacion_id, cantidad, precio_unitario]):
                 return {"error": "Cada nuevo detalle debe incluir presentacion_id, cantidad y precio_unitario"}, 400
            
            inventario = Inventario.query.filter_by(almacen_id=venta.almacen_id, presentacion_id=presentacion_id).first()
            if not inventario or inventario.cantidad < cantidad:
                return {"error": f"Stock insuficiente para actualizar. Presentación ID: {presentacion_id}"}, 400
            
            inventario.cantidad -= cantidad
            
            detalle_obj = VentaDetalle(
                presentacion_id=presentacion_id,
                cantidad=cantidad,
                precio_unitario=precio_unitario,
                lote_id=inventario.lote_id # Obtener el lote del nuevo inventario
            )
            nuevos_detalles_obj.append(detalle_obj)
            nuevo_total += cantidad * precio_unitario

        # --- 3. Actualizar la venta ---
        venta.cliente_id = data.get('cliente_id', venta.cliente_id)
        venta.tipo_pago = data.get('tipo_pago', venta.tipo_pago)
        venta.fecha = data.get('fecha', venta.fecha)
        venta.consumo_diario_kg = data.get('consumo_diario_kg', venta.consumo_diario_kg)
        venta.total = nuevo_total
        
        venta.detalles = nuevos_detalles_obj
        
        cliente_nombre = Cliente.query.get(venta.cliente_id).nombre
        current_user_id = get_jwt().get('sub')
        for detalle in venta.detalles:
            movimiento = Movimiento(
                tipo='salida',
                presentacion_id=detalle.presentacion_id,
                lote_id=detalle.lote_id,
                cantidad=detalle.cantidad,
                usuario_id=current_user_id,
                motivo=f"Venta ID: {venta.id} - Cliente: {cliente_nombre} (Actualizada)"
            )
            db.session.add(movimiento)

        db.session.commit()
        return venta_schema.dump(venta), 200

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def delete(self, venta_id):
        venta = Venta.query.get_or_404(venta_id)
        
        # Revertir movimientos e inventario
        movimientos = Movimiento.query.filter(Movimiento.motivo.like(f"Venta ID: {venta_id}%")).all()
        for movimiento in movimientos:
            inventario = Inventario.query.filter_by(
                presentacion_id=movimiento.presentacion_id,
                almacen_id=venta.almacen_id
            ).first()
            if inventario:
                inventario.cantidad += movimiento.cantidad
            db.session.delete(movimiento)
        
        db.session.delete(venta)
        db.session.commit()
        
        return {"message": "Venta eliminada con éxito"}, 200

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
            todos_almacenes = Almacen.query.order_by(Almacen.nombre).all()

            # --- Consulta Principal Optimizada ---
            # Carga el inventario y sus relaciones (Presentacion, Lote) en una sola consulta.
            inventario_disponible = db.session.query(Inventario).options(
                orm.joinedload(Inventario.presentacion),
                orm.joinedload(Inventario.lote)
            ).filter(
                Inventario.almacen_id == target_almacen_id,

                PresentacionProducto.activo == True
            ).join(
                PresentacionProducto, Inventario.presentacion_id == PresentacionProducto.id
            ).order_by(PresentacionProducto.nombre).all()

            presentaciones_data = []
            for inventario in inventario_disponible:
                presentacion = inventario.presentacion
                lote = inventario.lote
                
                # Serializar la presentación a JSON
                dumped_presentacion = presentacion_schema.dump(presentacion)
                
                # Añadir datos adicionales del inventario y lote
                dumped_presentacion['stock_disponible'] = inventario.cantidad
                dumped_presentacion['lote_id'] = lote.id if lote else None
                dumped_presentacion['lote_descripcion'] = lote.descripcion if lote else "Sin lote asignado"
                
                # Generar URL pre-firmada para la foto
                if presentacion.url_foto:
                    dumped_presentacion['url_foto'] = get_presigned_url(presentacion.url_foto)
                
                presentaciones_data.append(dumped_presentacion)

            return {
                "clientes": clientes_schema.dump(clientes),
                "almacenes": almacenes_schema.dump(todos_almacenes),
                "presentaciones_disponibles": presentaciones_data
            }, 200

        except Exception as e:
            logger.exception(f"Error en VentaFormDataResource: {e}")
            return {"error": "Error al obtener datos para el formulario de venta", "details": str(e)}, 500
