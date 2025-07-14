from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request, jsonify
from models import Inventario, PresentacionProducto, Almacen, Lote, Movimiento
from schemas import inventario_schema, inventarios_schema, lote_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, mismo_almacen_o_admin, validate_pagination_params, create_pagination_response
from decimal import Decimal, InvalidOperation
import logging
from datetime import datetime, timezone
import werkzeug.exceptions
import sqlalchemy.orm.exc

# Configurar logging
logger = logging.getLogger(__name__)

class InventarioResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, inventario_id=None):
        """
        Obtiene inventario(s)
        - Con ID: Detalle completo del registro de inventario
        - Sin ID: Lista paginada con filtros (presentacion_id, almacen_id, etc)
        """
        try:
            # Si se solicita un inventario específico
            if inventario_id:
                inventario = Inventario.query.get_or_404(inventario_id)
                
                # Verificar permisos (solo admin o usuario del mismo almacén)
                claims = get_jwt()
                if claims.get('rol') != 'admin' and inventario.almacen_id != claims.get('almacen_id'):
                    return {"error": "No tiene permisos para ver este inventario"}, 403
                
                return inventario_schema.dump(inventario), 200
            
            # Construir query con filtros
            query = Inventario.query
            
            # Aplicar restricción por almacén para usuarios no admin
            claims = get_jwt()
            if claims.get('rol') != 'admin':
                almacen_id = claims.get('almacen_id')
                if not almacen_id:
                    return {"error": "Usuario sin almacén asignado"}, 400
                query = query.filter_by(almacen_id=almacen_id)
            
            # Aplicar filtros adicionales
            if presentacion_id := request.args.get('presentacion_id'):
                try:
                    query = query.filter_by(presentacion_id=int(presentacion_id))
                except ValueError:
                    return {"error": "ID de presentación inválido"}, 400
                    
            if almacen_id := request.args.get('almacen_id'):
                # Para admins que quieren filtrar por almacén específico
                if claims.get('rol') == 'admin':
                    try:
                        query = query.filter_by(almacen_id=int(almacen_id))
                    except ValueError:
                        return {"error": "ID de almacén inválido"}, 400
                        
            if lote_id := request.args.get('lote_id'):
                try:
                    query = query.filter_by(lote_id=int(lote_id))
                except ValueError:
                    return {"error": "ID de lote inválido"}, 400
            
            # Filtrar por stock mínimo
            if request.args.get('stock_bajo') == 'true':
                query = query.filter(Inventario.cantidad <= Inventario.stock_minimo)
            
            # Ordenar por almacén y luego por presentación
            query = query.order_by(Inventario.almacen_id, Inventario.presentacion_id)
            
            # Paginación con validación
            page, per_page = validate_pagination_params()
            inventarios = query.paginate(page=page, per_page=per_page)
            
            # Respuesta estandarizada
            return create_pagination_response(inventarios_schema.dump(inventarios.items), inventarios), 200
            
        except Exception as e:
            logger.error(f"Error al obtener inventario: {str(e)}")
            return {"error": "Error al procesar la solicitud"}, 500

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def post(self):
        """Crea uno o múltiples registros de inventario con validación completa"""
        try:
            if not request.is_json:
                return {"error": "Se esperaba contenido JSON"}, 400
                
            raw_data = request.get_json()
            if not raw_data:
                return {"error": "Datos JSON no válidos o vacíos"}, 400

            # Permitir un solo objeto o una lista de objetos
            if not isinstance(raw_data, list):
                raw_data = [raw_data] # Convertir a lista para procesamiento uniforme

            created_inventories = []
            claims = get_jwt()
            
            for item_data in raw_data:
                inventario, error_response = self._create_single_inventario(item_data, claims)
                if error_response:
                    # Si hay un error en cualquier elemento, revertir toda la transacción
                    db.session.rollback()
                    return error_response[0], error_response[1]
                created_inventories.append(inventario)

            db.session.commit()
            logger.info(f"Inventarios creados exitosamente. Cantidad: {len(created_inventories)}")
            return inventarios_schema.dump(created_inventories), 201
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en POST inventario: {str(e)}")
            return {"error": "Error al crear inventario", "details": str(e)}, 500

    def _create_single_inventario(self, item_data, claims):
        """Lógica para crear un único registro de inventario, usada internamente."""
        try:
            # Verificar campos requeridos
            required_fields = ["presentacion_id", "almacen_id", "cantidad"]
            for field in required_fields:
                if field not in item_data:
                    return None, ({"error": f"Campo requerido '{field}' faltante en un item", "item": item_data}, 400)
            
            # Validar valores numéricos
            try:
                presentacion_id = int(item_data.get('presentacion_id'))
                almacen_id = int(item_data.get('almacen_id'))
                cantidad = int(item_data.get('cantidad'))
                
                if cantidad < 0:
                    return None, ({"error": "La cantidad no puede ser negativa en un item", "item": item_data}, 400)
                    
                if 'stock_minimo' in item_data:
                    stock_minimo = int(item_data.get('stock_minimo'))
                    if stock_minimo < 0:
                        return None, ({"error": "El stock mínimo no puede ser negativo en un item", "item": item_data}, 400)
                        
            except (ValueError, TypeError):
                return None, ({"error": "Valores numéricos inválidos en un item", "item": item_data}, 400)
            
            # Validar permisos por almacén
            if claims.get('rol') != 'admin' and almacen_id != claims.get('almacen_id'):
                return None, ({"error": "No tiene permisos para este almacén en un item", "item": item_data}, 403)
            
            # Validar relaciones
            try:
                presentacion = PresentacionProducto.query.get_or_404(presentacion_id)
                almacen = Almacen.query.get_or_404(almacen_id)
                # Si se provee lote_id, validarlo
                if item_data.get('lote_id'):
                    lote = Lote.query.get_or_404(item_data['lote_id'])
            except (werkzeug.exceptions.NotFound, sqlalchemy.orm.exc.NoResultFound) as e:
                # Captura específica para 404 de get_or_404
                return None, ({"error": f"Relación inválida (ID no encontrado): {str(e.description)}", "item": item_data}, 400)
            except Exception as e:
                # Captura para otras excepciones inesperadas durante la validación de relaciones
                return None, ({"error": f"Error inesperado al validar relaciones: {str(e)}", "item": item_data}, 500)
            
            # Verificar unicidad
            if Inventario.query.filter_by(
                presentacion_id=presentacion_id,
                almacen_id=almacen_id
            ).first():
                return None, ({"error": "Ya existe un registro de inventario para esta presentación en este almacén", "item": item_data}, 409)
            
            # Cargar con el esquema después de validaciones básicas
            data = inventario_schema.load(item_data)
            
            # Procesar movimiento si hay cantidad inicial
            if data.cantidad > 0:
                movimiento = Movimiento(
                    tipo='entrada',
                    presentacion_id=data.presentacion_id,
                    lote_id=data.lote_id,
                    cantidad=data.cantidad,
                    usuario_id=claims.get('sub'),
                    motivo="Inicialización de inventario",
                    fecha=datetime.now(timezone.utc)
                )
                db.session.add(movimiento)
                
                # Validar y actualizar lote si corresponde
                if data.lote_id:
                    lote = Lote.query.get_or_404(data.lote_id)
                    
                    # Asegurar que capacidad_kg sea un Decimal para el cálculo
                    try:
                        capacidad_kg = Decimal(str(presentacion.capacidad_kg))
                        cantidad_decimal = Decimal(str(data.cantidad))
                        
                        # Calcular kg a restar del lote
                        kg_a_restar = cantidad_decimal * capacidad_kg
                        
                        # Verificar stock disponible en lote
                        if not lote.cantidad_disponible_kg or lote.cantidad_disponible_kg < kg_a_restar:
                            return None, ({"error": "Stock insuficiente en el lote", "disponible_kg": str(lote.cantidad_disponible_kg), "requerido_kg": str(kg_a_restar), "item": item_data}, 400)
                        
                        # Actualizar lote
                        lote.cantidad_disponible_kg -= kg_a_restar
                    except (InvalidOperation, TypeError) as e:
                        return None, ({"error": f"Error en cálculo de cantidades: {str(e)}", "item": item_data}, 400)
            
            # Guardar en la base de datos
            db.session.add(data)
            
            return data, None # Retorna el objeto inventario creado y sin errores
            
        except Exception as e:
            logger.error(f"Error al procesar item de inventario: {str(e)}")
            # No se hace rollback aquí, se delega al método post principal
            return None, ({"error": "Error interno al procesar item de inventario", "details": str(e), "item": item_data}, 500)

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def put(self, inventario_id):
        """Actualiza un registro de inventario existente"""
        try:
            if not inventario_id:
                return {"error": "Se requiere ID de inventario"}, 400
            
            # Validar formato de entrada
            if not request.is_json:
                return {"error": "Se esperaba contenido JSON"}, 400
                
            raw_data = request.get_json()
            if not raw_data:
                return {"error": "Datos JSON no válidos o vacíos"}, 400
            
            # Buscar el registro existente
            inventario = Inventario.query.get_or_404(inventario_id)
            
            # Verificar permisos
            claims = get_jwt()
            if claims.get('rol') != 'admin' and inventario.almacen_id != claims.get('almacen_id'):
                return {"error": "No tiene permisos para modificar este inventario"}, 403
            
            # Validar campos inmutables
            immutable_fields = ["presentacion_id", "almacen_id"]
            for field in immutable_fields:
                if field in raw_data:
                    current_value = getattr(inventario, field)
                    new_value = raw_data[field]
                    try:
                        if int(new_value) != int(current_value):
                            return {"error": f"Campo inmutable '{field}' no puede modificarse"}, 400
                    except (ValueError, TypeError):
                        return {"error": f"Valor inválido para '{field}'"}, 400

            # Validar valores numéricos
            if 'cantidad' in raw_data:
                try:
                    nueva_cantidad = int(raw_data['cantidad'])
                    if nueva_cantidad < 0:
                        return {"error": "La cantidad no puede ser negativa"}, 400
                except (ValueError, TypeError):
                    return {"error": "Valor de cantidad inválido"}, 400
            
            if 'stock_minimo' in raw_data:
                try:
                    stock_minimo = int(raw_data['stock_minimo'])
                    if stock_minimo < 0:
                        return {"error": "El stock mínimo no puede ser negativo"}, 400
                except (ValueError, TypeError):
                    return {"error": "Valor de stock mínimo inválido"}, 400

            # Capturar el lote actual y el nuevo si se está cambiando
            lote_actual_id = getattr(inventario, 'lote_id', None)
            lote_nuevo_id = raw_data.get('lote_id', lote_actual_id)
            
            if lote_nuevo_id != lote_actual_id:
                try:
                    if lote_nuevo_id:
                        Lote.query.get_or_404(int(lote_nuevo_id))
                except (ValueError, TypeError):
                    return {"error": "ID de lote inválido"}, 400

            # Si hay cambio en la cantidad, registrar movimiento y actualizar lote
            if 'cantidad' in raw_data:
                try:
                    nueva_cantidad = int(raw_data['cantidad'])
                    diferencia = nueva_cantidad - inventario.cantidad
                    
                    if diferencia != 0:
                        tipo_movimiento = 'entrada' if diferencia > 0 else 'salida'
                        cantidad_movimiento = abs(diferencia)
                        
                        # Determinar el lote a usar para el movimiento
                        lote_id_para_movimiento = lote_nuevo_id if (tipo_movimiento == 'entrada' and lote_nuevo_id != lote_actual_id) else lote_actual_id
                        
                        # Crear el movimiento con el lote correspondiente
                        movimiento = Movimiento(
                            tipo=tipo_movimiento,
                            presentacion_id=inventario.presentacion_id,
                            lote_id=lote_id_para_movimiento,
                            cantidad=cantidad_movimiento,
                            usuario_id=claims.get('sub'),
                            motivo=raw_data.get('motivo', "Ajuste manual de inventario")
                        )
                        db.session.add(movimiento)
                        
                        # Obtener la presentación para calcular kg
                        presentacion = PresentacionProducto.query.get(inventario.presentacion_id)
                        
                        # CASO 1: ENTRADA (aumento de inventario)
                        if tipo_movimiento == 'entrada' and lote_id_para_movimiento:
                            lote = Lote.query.get(lote_id_para_movimiento)
                            if lote is not None and lote.cantidad_disponible_kg is not None:
                                if presentacion and presentacion.capacidad_kg:
                                    try:
                                        # Calcular cuánto restar del lote (embolsado)
                                        kg_a_restar = Decimal(str(presentacion.capacidad_kg)) * Decimal(str(cantidad_movimiento))
                                        
                                        # Verificar si hay suficiente cantidad disponible
                                        if lote.cantidad_disponible_kg >= kg_a_restar:
                                            lote.cantidad_disponible_kg -= kg_a_restar
                                        else:
                                            return {
                                                "error": "Stock insuficiente en el lote",
                                                "disponible_kg": str(lote.cantidad_disponible_kg),
                                                "requerido_kg": str(kg_a_restar)
                                            }, 400
                                    except (InvalidOperation, TypeError) as e:
                                        return {"error": f"Error en cálculo: {str(e)}"}, 400
                
                except (ValueError, TypeError) as e:
                    return {"error": f"Error en actualización de cantidad: {str(e)}"}, 400
            
            # Cargar datos validados sobre la instancia existente
            updated_inventario = inventario_schema.load(
                raw_data,
                instance=inventario,
                partial=True
            )

            db.session.commit()
            
            logger.info(f"Inventario actualizado: ID {inventario_id}, Presentación {inventario.presentacion_id}, Almacén {inventario.almacen_id}")
            return inventario_schema.dump(updated_inventario), 200
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en PUT inventario: {str(e)}")
            return {"error": "Error al actualizar inventario"}, 500

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def delete(self, inventario_id):
        """Elimina un registro de inventario si no tiene movimientos asociados"""
        try:
            if not inventario_id:
                return {"error": "Se requiere ID de inventario"}, 400
                
            inventario = Inventario.query.get_or_404(inventario_id)
            
            # Verificar permisos
            claims = get_jwt()
            if claims.get('rol') != 'admin' and inventario.almacen_id != claims.get('almacen_id'):
                return {"error": "No tiene permisos para eliminar este inventario"}, 403
            
            # Verificar movimientos asociados
            movimientos = Movimiento.query.filter_by(presentacion_id=inventario.presentacion_id).count()
            
            if movimientos > 0:
                return {
                    "error": "No se puede eliminar un inventario con movimientos registrados",
                    "movimientos_asociados": movimientos
                }, 400
            
            # Guardar datos para el log
            presentacion_id = inventario.presentacion_id
            almacen_id = inventario.almacen_id
            
            db.session.delete(inventario)
            db.session.commit()
            
            logger.info(f"Inventario eliminado: ID {inventario_id}, Presentación {presentacion_id}, Almacén {almacen_id}")
            return {"message": "Inventario eliminado con éxito"}, 200
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en DELETE inventario: {str(e)}")
            return {"error": "Error al eliminar inventario"}, 500


