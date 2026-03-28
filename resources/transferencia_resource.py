# ARCHIVO: resources/transferencia_resource.py
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import request
from flask_jwt_extended import get_jwt, jwt_required
from flask_restful import Resource
from sqlalchemy.orm import joinedload

from common import handle_db_errors
from extensions import db
from models import Almacen, Inventario, Movimiento, PresentacionProducto
# Imports agregados para el método GET
from schemas import almacenes_schema
from utils.file_handlers import get_presigned_url

logger = logging.getLogger(__name__)


# --- CAPA DE SERVICIO PARA LA LÓGICA DE NEGOCIO ---

class TransferenciaService:
    """Encapsula toda la lógica de negocio para las transferencias de inventario."""

    def __init__(self, data):
        self.data = data
        self.claims = get_jwt()
        self.usuario_id = self.claims.get('sub')
        self.id_operacion = str(uuid.uuid4())[:8]
        self.fecha_operacion = datetime.now(timezone.utc)
        self.transferencias_validadas = []

    def ejecutar_transferencia(self):
        """
        Orquesta el proceso completo de validación y ejecución de la transferencia.
        """
        self._validar_y_preparar_datos()
        
        # --- OPTIMIZACIÓN CLAVE: OBTENER TODO EL INVENTARIO EN POCAS CONSULTAS ---
        inventarios_origen = self._obtener_inventarios(self.almacen_origen_id)
        inventarios_destino = self._obtener_inventarios(self.almacen_destino_id)

        self._validar_stock(inventarios_origen)

        transferencias_realizadas = self._actualizar_inventarios_y_crear_movimientos(
            inventarios_origen, inventarios_destino
        )
        
        return {
            "mensaje": "Transferencia realizada con éxito",
            "id_operacion": self.id_operacion,
            "almacen_origen": self.almacen_origen.nombre,
            "almacen_destino": self.almacen_destino.nombre,
            "transferencias_realizadas": transferencias_realizadas,
            "total_transferencias": len(transferencias_realizadas)
        }

    def _validar_y_preparar_datos(self):
        """Valida el formato de la solicitud y prepara los datos iniciales."""
        if 'transferencias' not in self.data or not isinstance(self.data['transferencias'], list):
            raise ValueError("El campo 'transferencias' debe ser una lista.")
        
        self.almacen_origen_id = self.data.get('almacen_origen_id')
        self.almacen_destino_id = self.data.get('almacen_destino_id')

        if not all([self.almacen_origen_id, self.almacen_destino_id]):
            raise ValueError("Los campos 'almacen_origen_id' y 'almacen_destino_id' son requeridos.")
        
        if self.almacen_origen_id == self.almacen_destino_id:
            raise ValueError("El almacén de origen y destino no pueden ser el mismo.")

        self.almacen_origen = Almacen.query.get_or_404(self.almacen_origen_id)
        self.almacen_destino = Almacen.query.get_or_404(self.almacen_destino_id)

        for i, transfer in enumerate(self.data['transferencias']):
            try:
                cantidad = Decimal(transfer['cantidad'])
                if cantidad <= 0:
                    raise ValueError(f"Transferencia {i+1}: la cantidad debe ser positiva.")
                
                self.transferencias_validadas.append({
                    'presentacion_id': int(transfer['presentacion_id']),
                    'cantidad': cantidad
                })
            except (ValueError, TypeError, InvalidOperation) as e:
                raise ValueError(f"Transferencia {i+1}: formato de datos inválido. {e}")

    def _obtener_inventarios(self, almacen_id):
        """
        Obtiene todos los registros de inventario necesarios de un almacén 
        y los agrupa en una lista por presentacion_id para procesarlos en FIFO.
        """
        from models import Lote
        ids_presentaciones = {t['presentacion_id'] for t in self.transferencias_validadas}
        
        inventarios = Inventario.query.options(
            joinedload(Inventario.presentacion),
            joinedload(Inventario.lote)
        ).join(
            Lote, Inventario.lote_id == Lote.id, isouter=True
        ).filter(
            Inventario.almacen_id == almacen_id,
            Inventario.presentacion_id.in_(ids_presentaciones)
        ).order_by(
            Inventario.presentacion_id, 
            Lote.fecha_ingreso.asc(), # FIFO by lot date
            Inventario.id.asc()       # Fallback FIFO
        ).all()
        
        agrupados = {}
        for inv in inventarios:
            if inv.presentacion_id not in agrupados:
                agrupados[inv.presentacion_id] = []
            agrupados[inv.presentacion_id].append(inv)
            
        return agrupados

    def _validar_stock(self, inventarios_origen):
        """Valida que haya stock suficiente para todas las transferencias sumarizando los lotes disponibles."""
        for transfer in self.transferencias_validadas:
            invs_origen = inventarios_origen.get(transfer['presentacion_id'], [])
            
            stock_disponible = sum(inv.cantidad for inv in invs_origen)

            if stock_disponible < transfer['cantidad']:
                nombre_presentacion = f"ID {transfer['presentacion_id']}"
                if invs_origen and invs_origen[0].presentacion:
                     nombre_presentacion = invs_origen[0].presentacion.nombre
                raise ValueError(
                    f"Stock insuficiente para '{nombre_presentacion}'. "
                    f"Requerido: {transfer['cantidad']}, Disponible: {stock_disponible}"
                )

    def _actualizar_inventarios_y_crear_movimientos(self, inventarios_origen, inventarios_destino):
        """
        Modifica los registros empleando lógica FIFO: descuenta de los lotes más antiguos primero
        y transfiere esos mismos lotes al inventario de destino.
        """
        movimientos_a_crear = []
        transferencias_realizadas_info = []

        for transfer in self.transferencias_validadas:
            cantidad_restante = transfer['cantidad']
            invs_origen_disponibles = inventarios_origen.get(transfer['presentacion_id'], [])
            invs_destino_existentes = inventarios_destino.get(transfer['presentacion_id'], [])

            for inv_orig in invs_origen_disponibles:
                if cantidad_restante <= 0:
                    break
                
                if inv_orig.cantidad <= 0:
                    continue
                    
                cantidad_a_tomar = min(inv_orig.cantidad, cantidad_restante)
                
                # Descontar del origen
                inv_orig.cantidad -= cantidad_a_tomar
                cantidad_restante -= cantidad_a_tomar
                
                # Buscar o crear inventario destino CON EL MISMO LOTE
                inv_dest = next((inv for inv in invs_destino_existentes if inv.lote_id == inv_orig.lote_id), None)
                
                if inv_dest:
                    inv_dest.cantidad += cantidad_a_tomar
                    inv_dest.ultima_actualizacion = self.fecha_operacion
                else:
                    inv_dest_nuevo = Inventario(
                        presentacion_id=transfer['presentacion_id'],
                        almacen_id=self.almacen_destino_id,
                        lote_id=inv_orig.lote_id, # SE MANTIENE EL LOTE ORIGINAL
                        cantidad=cantidad_a_tomar,
                        stock_minimo=inv_orig.stock_minimo
                    )
                    db.session.add(inv_dest_nuevo)
                    invs_destino_existentes.append(inv_dest_nuevo)

                # Preparar movimientos manteniendo la trazabilidad
                motivo_salida = f"Transferencia a {self.almacen_destino.nombre} (Op: {self.id_operacion})"
                motivo_entrada = f"Transferencia desde {self.almacen_origen.nombre} (Op: {self.id_operacion})"

                movimientos_a_crear.append(Movimiento(
                    tipo='salida', motivo=motivo_salida, 
                    presentacion_id=transfer['presentacion_id'],
                    lote_id=inv_orig.lote_id,
                    cantidad=cantidad_a_tomar,
                    usuario_id=self.usuario_id, tipo_operacion='transferencia', fecha=self.fecha_operacion
                ))
                movimientos_a_crear.append(Movimiento(
                    tipo='entrada', motivo=motivo_entrada, 
                    presentacion_id=transfer['presentacion_id'],
                    lote_id=inv_orig.lote_id,
                    cantidad=cantidad_a_tomar,
                    usuario_id=self.usuario_id, tipo_operacion='transferencia', fecha=self.fecha_operacion
                ))
                
                transferencias_realizadas_info.append({
                    "presentacion_nombre": inv_orig.presentacion.nombre,
                    "cantidad": str(cantidad_a_tomar),
                    "lote_id": inv_orig.lote_id
                })

        db.session.add_all(movimientos_a_crear)
        return transferencias_realizadas_info


# --- RECURSO DE LA API (MÁS LIMPIO Y SIMPLE) ---

class TransferenciaInventarioResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Obtiene de forma optimizada los datos necesarios para el formulario de transferencias.
        Devuelve una lista de almacenes y una lista de presentaciones con su
        inventario disponible agrupado.
        """
        almacen_id_filtro = request.args.get('almacen_id', type=int)
        
        try:
            # 1. Obtener todos los almacenes para los selectores del frontend
            almacenes = Almacen.query.order_by(Almacen.nombre).all()

            # 2. Consulta única y optimizada para todo el inventario relevante
            query = db.session.query(Inventario).options(
                joinedload(Inventario.presentacion),
                joinedload(Inventario.lote),
                joinedload(Inventario.almacen)
            ).join(
                PresentacionProducto, Inventario.presentacion_id == PresentacionProducto.id
            ).filter(
                PresentacionProducto.activo == True,
                PresentacionProducto.tipo == 'procesado',
                Inventario.cantidad > 0
            ).order_by(PresentacionProducto.nombre, Inventario.almacen_id)

            if almacen_id_filtro:
                query = query.filter(Inventario.almacen_id == almacen_id_filtro)
            
            inventario_disponible = query.all()

            # 3. Procesar y agrupar los datos eficientemente en Python
            presentaciones_agrupadas = {}
            for inv in inventario_disponible:
                pres_id = inv.presentacion.id
                
                # Si es la primera vez que vemos esta presentación, creamos su objeto base
                if pres_id not in presentaciones_agrupadas:
                    presentaciones_agrupadas[pres_id] = {
                        "id": pres_id,
                        "nombre": inv.presentacion.nombre,
                        "url_foto": get_presigned_url(inv.presentacion.url_foto) if inv.presentacion.url_foto else None,
                        "inventarios": []
                    }

                # Construimos la información del lote manualmente para evitar schemas en bucle
                lote_info = None
                if inv.lote:
                    lote_info = {
                        "id": inv.lote.id,
                        "descripcion": inv.lote.descripcion
                    }

                # Agregamos la información de este inventario específico a su presentación
                presentaciones_agrupadas[pres_id]['inventarios'].append({
                    "almacen_id": inv.almacen_id,
                    "almacen_nombre": inv.almacen.nombre,
                    "stock_disponible": float(inv.cantidad),
                    "lote_id": inv.lote_id,
                    "lote_info": lote_info
                })
            
            return {
                "almacenes": almacenes_schema.dump(almacenes),
                "presentaciones_disponibles": list(presentaciones_agrupadas.values())
            }, 200

        except Exception as e:
            logger.error(f"Error al obtener datos para formulario de transferencia: {str(e)}", exc_info=True)
            return {"error": "Error interno al obtener datos para transferencias."}, 500

    @jwt_required()
    @handle_db_errors
    def post(self):
        """
        Transfiere inventario entre almacenes de forma múltiple y optimizada.
        Espera un payload:
        {
            "almacen_origen_id": 1,
            "almacen_destino_id": 2,
            "transferencias": [
                {"presentacion_id": 10, "lote_id": 5, "cantidad": "15.0"},
                {"presentacion_id": 12, "lote_id": 5, "cantidad": "30.0"}
            ]
        }
        """
        try:
            servicio = TransferenciaService(request.get_json())
            resultado = servicio.ejecutar_transferencia()
            db.session.commit()
            return resultado, 200
        except ValueError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error crítico en transferencia: {str(e)}", exc_info=True)
            return {"error": "Ocurrió un error interno al procesar la transferencia."}, 500

