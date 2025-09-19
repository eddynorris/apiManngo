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

logger = logging.getLogger(__name__)

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
                    'lote_id': int(transfer['lote_id']) if transfer.get('lote_id') is not None else None,
                    'cantidad': cantidad
                })
            except (ValueError, TypeError, InvalidOperation) as e:
                raise ValueError(f"Transferencia {i+1}: formato de datos inválido. {e}")

    def _obtener_inventarios(self, almacen_id):
        """
        OPTIMIZACIÓN: Obtiene todos los registros de inventario necesarios
        para un almacén en una sola consulta y los devuelve en un diccionario
        para acceso rápido.
        """
        ids_presentaciones = {t['presentacion_id'] for t in self.transferencias_validadas}
        
        inventarios = Inventario.query.options(joinedload(Inventario.presentacion)).filter(
            Inventario.almacen_id == almacen_id,
            Inventario.presentacion_id.in_(ids_presentaciones)
        ).all()
        
        # Crear un diccionario con una clave compuesta (presentacion_id, lote_id)
        return {(inv.presentacion_id, inv.lote_id): inv for inv in inventarios}

    def _validar_stock(self, inventarios_origen):
        """Valida que haya stock suficiente para todas las transferencias."""
        for transfer in self.transferencias_validadas:
            clave_inv = (transfer['presentacion_id'], transfer['lote_id'])
            inv_origen = inventarios_origen.get(clave_inv)

            if not inv_origen or inv_origen.cantidad < transfer['cantidad']:
                stock_disponible = inv_origen.cantidad if inv_origen else 0
                nombre_presentacion = f"ID {transfer['presentacion_id']}"
                if inv_origen and inv_origen.presentacion:
                     nombre_presentacion = inv_origen.presentacion.nombre
                raise ValueError(
                    f"Stock insuficiente para '{nombre_presentacion}'. "
                    f"Requerido: {transfer['cantidad']}, Disponible: {stock_disponible}"
                )

    def _actualizar_inventarios_y_crear_movimientos(self, inventarios_origen, inventarios_destino):
        """
        Modifica los registros de inventario en la sesión de la base de datos
        y crea los movimientos correspondientes.
        """
        movimientos_a_crear = []
        transferencias_realizadas_info = []

        for transfer in self.transferencias_validadas:
            clave_inv = (transfer['presentacion_id'], transfer['lote_id'])
            inv_origen = inventarios_origen[clave_inv]
            inv_destino = inventarios_destino.get(clave_inv)

            cantidad = transfer['cantidad']

            # Actualizar inventarios
            inv_origen.cantidad -= cantidad
            if inv_destino:
                inv_destino.cantidad += cantidad
                inv_destino.ultima_actualizacion = self.fecha_operacion
            else:
                inv_destino_nuevo = Inventario(
                    presentacion_id=transfer['presentacion_id'],
                    almacen_id=self.almacen_destino_id,
                    lote_id=transfer['lote_id'],
                    cantidad=cantidad,
                    stock_minimo=inv_origen.stock_minimo
                )
                db.session.add(inv_destino_nuevo)

            # Preparar movimientos
            motivo_salida = f"Transferencia a {self.almacen_destino.nombre} (Op: {self.id_operacion})"
            motivo_entrada = f"Transferencia desde {self.almacen_origen.nombre} (Op: {self.id_operacion})"

            movimientos_a_crear.append(Movimiento(
                tipo='salida', motivo=motivo_salida, **transfer,
                usuario_id=self.usuario_id, tipo_operacion='transferencia', fecha=self.fecha_operacion
            ))
            movimientos_a_crear.append(Movimiento(
                tipo='entrada', motivo=motivo_entrada, **transfer,
                usuario_id=self.usuario_id, tipo_operacion='transferencia', fecha=self.fecha_operacion
            ))
            
            transferencias_realizadas_info.append({
                "presentacion_nombre": inv_origen.presentacion.nombre,
                "cantidad": str(cantidad),
                "lote_id": transfer['lote_id']
            })

        db.session.add_all(movimientos_a_crear)
        return transferencias_realizadas_info


# --- RECURSO DE LA API (MÁS LIMPIO Y SIMPLE) ---

class TransferenciaInventarioResource(Resource):
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