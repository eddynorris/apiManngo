from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Pedido, PedidoDetalle, Cliente, PresentacionProducto, Almacen, Inventario, Movimiento, VentaDetalle, Venta, Users
from schemas import pedido_schema, pedidos_schema, venta_schema, clientes_schema, almacenes_schema, presentacion_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, mismo_almacen_o_admin, parse_iso_datetime
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from utils.file_handlers import get_presigned_url
import logging
from sqlalchemy import asc, desc

logger = logging.getLogger(__name__)

class PedidoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, pedido_id=None):
        """
        Obtiene pedido(s)
        - Con ID: Detalle completo del pedido (con URLs pre-firmadas para detalles)
        - Sin ID: Lista paginada con filtros (cliente_id, almacen_id, fecha_inicio, fecha_fin, estado)
        """
        if pedido_id:
            pedido = Pedido.query.get_or_404(pedido_id)
            
            # Serializar el pedido
            result = pedido_schema.dump(pedido)
            
            # --- GENERAR URLs PRE-FIRMADAS PARA DETALLES ---
            if 'detalles' in result and result['detalles']:
                for detalle in result['detalles']:
                    # Verificar estructura anidada
                    if 'presentacion' in detalle and detalle['presentacion'] and 'url_foto' in detalle['presentacion']:
                        s3_key = detalle['presentacion']['url_foto']
                        if s3_key:
                            # Reemplazar clave S3 con URL pre-firmada
                            detalle['presentacion']['url_foto'] = get_presigned_url(s3_key)
                        # else: url_foto ya es None o vacío, no hacer nada
            # ---------------------------------------------
            
            return result, 200
        
        # --- Lógica de Ordenación Dinámica ---
        sort_by = request.args.get('sort_by', 'fecha_creacion') # Default
        sort_order = request.args.get('sort_order', 'desc').lower() # Default

        sortable_columns = {
            'fecha_creacion': Pedido.fecha_creacion,
            'fecha_entrega': Pedido.fecha_entrega,
            'estado': Pedido.estado,
            'cliente_nombre': Cliente.nombre,
            'almacen_nombre': Almacen.nombre,
            'vendedor_username': Users.username
        }

        column_to_sort = sortable_columns.get(sort_by, Pedido.fecha_creacion)
        order_func = desc if sort_order == 'desc' else asc
        # --- Fin Lógica de Ordenación ---

        query = Pedido.query

        # --- Aplicar Joins si es necesario para ordenar ---
        if sort_by == 'cliente_nombre':
            query = query.join(Cliente, Pedido.cliente_id == Cliente.id)
        elif sort_by == 'almacen_nombre':
            query = query.join(Almacen, Pedido.almacen_id == Almacen.id)
        elif sort_by == 'vendedor_username':
             # Outerjoin porque vendedor_id puede ser NULL
            query = query.outerjoin(Users, Pedido.vendedor_id == Users.id)
        # ------------------------------------------------

        # Aplicar filtros
        if cliente_id := request.args.get('cliente_id'):
            query = query.filter_by(cliente_id=cliente_id)
        
        if almacen_id := request.args.get('almacen_id'):
            query = query.filter_by(almacen_id=almacen_id)
        
        if vendedor_id := request.args.get('vendedor_id'):
            query = query.filter_by(vendedor_id=vendedor_id)
            
        if estado := request.args.get('estado'):
            query = query.filter_by(estado=estado)
        
        if fecha_inicio := request.args.get('fecha_inicio'):
            if fecha_fin := request.args.get('fecha_fin'):
                try:
                    fecha_inicio = parse_iso_datetime(fecha_inicio, add_timezone=True)
                    fecha_fin = parse_iso_datetime(fecha_fin, add_timezone=True)
                    
                    # Filtrar por fecha de entrega
                    query = query.filter(Pedido.fecha_entrega.between(fecha_inicio, fecha_fin))
                except ValueError:
                    return {"error": "Formato de fecha inválido. Usa ISO 8601 (ej: '2025-03-05T00:00:00')"}, 400
        
        # --- APLICAR ORDENACIÓN ---
        # Quitar la ordenación fija anterior y aplicar la nueva
        query = query.order_by(order_func(column_to_sort))
        # -------------------------

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        pedidos = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return {
            "data": pedidos_schema.dump(pedidos.items),
            "pagination": {
                "total": pedidos.total,
                "page": pedidos.page,
                "per_page": pedidos.per_page,
                "pages": pedidos.pages
            }
        }, 200

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def post(self):

        data = pedido_schema.load(request.get_json())
        
        # Validaciones
        Cliente.query.get_or_404(data.cliente_id)
        Almacen.query.get_or_404(data.almacen_id)
        
        # Asignar vendedor automáticamente desde JWT
        claims = get_jwt()
        data.vendedor_id = claims.get('sub')
        
        # Validar detalles del pedido
        for detalle in data.detalles:
            presentacion = PresentacionProducto.query.get_or_404(detalle.presentacion_id)
            # El precio estimado usualmente es el de venta actual, pero podría ser diferente
            if not detalle.precio_estimado:
                detalle.precio_estimado = presentacion.precio_venta
        
        db.session.add(data)
        db.session.commit()
        
        return pedido_schema.dump(data), 201

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def put(self, pedido_id):
        """
        Actualiza un pedido existente
        """
        pedido = Pedido.query.get_or_404(pedido_id)
        
        # Validar estados - no permitir actualizar pedidos entregados
        if pedido.estado == 'entregado':
            return {"error": "No se puede modificar un pedido ya entregado"}, 400
        
        updated_pedido = pedido_schema.load(
            request.get_json(),
            instance=pedido,
            partial=True
        )
        
        db.session.commit()
        return pedido_schema.dump(updated_pedido), 200
    
    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def delete(self, pedido_id):
        """
        Elimina un pedido (o lo marca como cancelado)
        """
        pedido = Pedido.query.get_or_404(pedido_id)
        
        # Si ya está entregado, no permite eliminar
        if pedido.estado == 'entregado':
            return {"error": "No se puede eliminar un pedido ya entregado"}, 400
        
        # Opción 1: Eliminar
        db.session.delete(pedido)
        
        # Opción 2: Marcar como cancelado (alternativa)
        # pedido.estado = 'cancelado'
        
        db.session.commit()
        return "Pedido eliminado correctamente", 200

class PedidoConversionResource(Resource):
    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def post(self, pedido_id):
        """
        Convierte un pedido en una venta real
        """
        # Obtener claims una sola vez al inicio
        claims = get_jwt()
        
        # Cargar el pedido con las relaciones necesarias
        pedido = Pedido.query.options(
            db.joinedload(Pedido.cliente),
            db.joinedload(Pedido.detalles).joinedload(PedidoDetalle.presentacion)
        ).get_or_404(pedido_id)
        
        # Validaciones previas
        if pedido.estado == 'entregado':
            return {"error": "Este pedido ya fue entregado"}, 400
            
        if pedido.estado == 'cancelado':
            return {"error": "No se puede convertir un pedido cancelado"}, 400
        
        # --- Optimización: Obtener inventarios necesarios --- 
        presentacion_ids = [d.presentacion_id for d in pedido.detalles]
        if not presentacion_ids:
            return {"error": "El pedido no tiene detalles para convertir"}, 400

        inventarios = Inventario.query.filter(
            Inventario.presentacion_id.in_(presentacion_ids),
            Inventario.almacen_id == pedido.almacen_id
        ).all()
        inventarios_dict = {i.presentacion_id: i for i in inventarios}
        # ----------------------------------------------------

        # Verificar stock antes de proceder
        inventarios_insuficientes = []
        for detalle in pedido.detalles:
            inventario = inventarios_dict.get(detalle.presentacion_id)
            
            if not inventario or inventario.cantidad < detalle.cantidad:
                inventarios_insuficientes.append({
                    "presentacion": detalle.presentacion.nombre if detalle.presentacion else f"Presentación {detalle.presentacion_id}",
                    "solicitado": detalle.cantidad,
                    "disponible": inventario.cantidad if inventario else 0
                })
        
        if inventarios_insuficientes:
            return {
                "error": "Stock insuficiente para completar el pedido",
                "detalles": inventarios_insuficientes
            }, 400
        
        # Crear nueva venta desde el pedido
        venta = Venta(
            cliente_id=pedido.cliente_id,
            almacen_id=pedido.almacen_id,
            tipo_pago=request.json.get('tipo_pago', 'contado'),
            estado_pago='pendiente'
        )
        
        # Agregar detalles y calcular total
        total = 0
        for detalle_pedido in pedido.detalles:
            # Verificar que la presentación existe y tiene precio
            if not detalle_pedido.presentacion:
                return {"error": f"Presentación {detalle_pedido.presentacion_id} no encontrada"}, 400
                
            precio_actual = detalle_pedido.presentacion.precio_venta
            
            # Usar precio actual o el estimado, según configuración
            usar_precio_actual = request.json.get('usar_precio_actual', True)
            precio_final = precio_actual if usar_precio_actual else detalle_pedido.precio_estimado
            
            detalle_venta = VentaDetalle(
                presentacion_id=detalle_pedido.presentacion_id,
                cantidad=detalle_pedido.cantidad,
                precio_unitario=precio_final
            )
            venta.detalles.append(detalle_venta)
            total += detalle_venta.cantidad * detalle_venta.precio_unitario
        
        venta.total = total
        venta.fecha = datetime.now(timezone.utc)
        venta.vendedor_id = claims.get('sub')
        
        # Añadir venta a la sesión para obtener un ID
        db.session.add(venta)
        db.session.flush()  # Esto asigna un ID sin hacer commit
        
        # Actualizar inventario y crear movimientos de salida
        for detalle in venta.detalles:
            inventario = inventarios_dict.get(detalle.presentacion_id)
            if not inventario:
                return {"error": f"No se encontró inventario para presentación {detalle.presentacion_id}"}, 400
            
            inventario.cantidad -= detalle.cantidad
            
            # Registrar movimiento
            # Usar el nombre del cliente de forma segura
            cliente_nombre = pedido.cliente.nombre if pedido.cliente else f"Cliente {pedido.cliente_id}"
            movimiento = Movimiento(
                tipo='salida',
                presentacion_id=detalle.presentacion_id,
                lote_id=inventario.lote_id,
                cantidad=detalle.cantidad,
                usuario_id=claims.get('sub'),
                fecha=datetime.now(timezone.utc),
                motivo=f"Venta ID: {venta.id} - Cliente: {cliente_nombre} (desde pedido {pedido.id})"
            )
            db.session.add(movimiento)
        
        # Actualizar cliente si es necesario (verificar que el campo existe)
        if hasattr(venta, 'consumo_diario_kg') and venta.consumo_diario_kg:
            cliente = Cliente.query.get(venta.cliente_id)
            if cliente:
                cliente.ultima_fecha_compra = datetime.now(timezone.utc)
                try:
                    cliente.frecuencia_compra_dias = (venta.total / Decimal(venta.consumo_diario_kg)).quantize(Decimal('1.00'))
                except (InvalidOperation, TypeError):
                    # Si hay error en el cálculo, no actualizar frecuencia
                    pass
        
        # Marcar pedido como entregado
        pedido.estado = 'entregado'
        
        db.session.commit()
        
        return {
            "message": "Pedido convertido a venta exitosamente",
            "venta": venta_schema.dump(venta)
        }, 201
    
# --- RECURSO PARA FORMULARIO DE PEDIDO (SIMPLIFICADO) ---
class PedidoFormDataResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Obtiene los datos necesarios para los formularios de creación/edición de pedidos.
        Devuelve listas completas de clientes, almacenes y presentaciones activas.
        """
        # No es necesario verificar el rol aquí para esta versión simplificada
        # claims = get_jwt()
        # is_admin = claims.get('rol') == 'admin'

        try:
            # Obtener Clientes
            clientes = Cliente.query.order_by(Cliente.nombre).all()
            clientes_data = clientes_schema.dump(clientes, many=True)
            
            # Obtener Almacenes
            almacenes = Almacen.query.order_by(Almacen.nombre).all()
            almacenes_data = almacenes_schema.dump(almacenes, many=True)

            # Obtener Presentaciones Activas
            presentaciones_activas = PresentacionProducto.query.filter_by(activo=True).order_by(PresentacionProducto.nombre).all()
            presentaciones_data = []
            for p in presentaciones_activas:
                dumped_p = presentacion_schema.dump(p)
                # URL pre-firmada
                if p.url_foto:
                    dumped_p['url_foto'] = get_presigned_url(p.url_foto)
                else:
                    dumped_p['url_foto'] = None
                presentaciones_data.append(dumped_p)
            
            # Devolver siempre las tres listas
            return {
                "clientes": clientes_data,
                "almacenes": almacenes_data,
                "presentaciones_activas": presentaciones_data # Clave consistente
            }, 200

        except Exception as e:
            logger.exception(f"Error en PedidoFormDataResource: {e}")
            return {"error": "Error al obtener datos para el formulario de pedido", "details": str(e)}, 500
# --- FIN RECURSO SIMPLIFICADO ---
    
    