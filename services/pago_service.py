# services/pago_service.py
import json
import logging
import re
import uuid
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from werkzeug.exceptions import NotFound, Forbidden

from extensions import db
from models import Pago, Venta, Cliente, Users
from utils.file_handlers import save_file, delete_file

logger = logging.getLogger(__name__)

class PagoValidationError(ValueError):
    """Error de validación específico para la lógica de pagos."""
    pass

class PagoService:
    """Contiene toda la lógica de negocio para gestionar pagos."""

    @staticmethod
    def find_pago_by_id(pago_id):
        """Encuentra un pago por su ID o lanza un error 404 si no se encuentra."""
        pago = db.session.get(Pago, pago_id)
        if not pago:
            raise NotFound("Pago no encontrado.")
        return pago

    @staticmethod
    def _validate_monto(venta, monto, pago_existente_id=None):
        """Valida que el monto de un pago no exceda el saldo pendiente de la venta."""
        pagos_anteriores = sum(p.monto for p in venta.pagos if p.id != pago_existente_id)
        saldo_pendiente = venta.total - pagos_anteriores
        if monto > saldo_pendiente + Decimal("0.001"):
            raise PagoValidationError(
                f"El monto a pagar ({monto}) excede el saldo pendiente ({saldo_pendiente})."
            )

    @staticmethod
    def get_pagos_query(filters, current_user_id=None, rol=None):
        """Construye una consulta de pagos optimizada con filtros y carga ansiosa (eager loading)."""
        query = Pago.query.options(
            db.joinedload(Pago.venta).joinedload(Venta.cliente),
            db.joinedload(Pago.venta).joinedload(Venta.almacen),
            db.joinedload(Pago.usuario)
        )
        if venta_id := filters.get('venta_id'):
            query = query.filter(Pago.venta_id == venta_id)
        if metodo := filters.get('metodo_pago'):
            query = query.filter(Pago.metodo_pago == metodo)
        if usuario_id := filters.get('usuario_id'):
            query = query.filter(Pago.usuario_id == usuario_id)
        if almacen_id := filters.get('almacen_id'):
            query = query.join(Venta).filter(Venta.almacen_id == almacen_id)
        if (depositado_str := filters.get('depositado')) is not None:
            is_depositado = depositado_str.lower() == 'true'
            query = query.filter(Pago.depositado == is_depositado)
        if fecha_inicio := filters.get('fecha_inicio'):
             query = query.filter(Pago.fecha >= fecha_inicio)
        if fecha_fin := filters.get('fecha_fin'):
             query = query.filter(Pago.fecha <= fecha_fin)
        
        # --- FILTRO POR ROL ---
        if rol and rol != 'admin' and current_user_id:
            query = query.filter(Pago.usuario_id == current_user_id)
            
        return query

    @staticmethod
    def create_pago(nuevo_pago, file, usuario_id):
        """Crea un nuevo pago, valida y actualiza la venta."""
        if not nuevo_pago.venta_id:
            raise PagoValidationError("El campo 'venta_id' es requerido.")
        
        venta = Venta.query.get_or_404(nuevo_pago.venta_id)
        monto = nuevo_pago.monto or Decimal("0")
        
        PagoService._validate_monto(venta, monto)

        es_deposito = (nuevo_pago.metodo_pago or '').lower() == 'deposito'
        if es_deposito and not (file and file.filename):
            raise PagoValidationError(
                "El método de pago 'deposito' requiere un comprobante (foto del recibo) obligatorio."
            )
        
        s3_key = None
        if file and file.filename:
            s3_key = save_file(file, "comprobantes")
            if not s3_key:
                raise Exception("Ocurrió un error interno al guardar el comprobante.")
        
        nuevo_pago.usuario_id = usuario_id
        nuevo_pago.url_comprobante = s3_key

        if es_deposito:
            nuevo_pago.depositado = True
            nuevo_pago.monto_depositado = monto
            nuevo_pago.fecha_deposito = nuevo_pago.fecha or datetime.now(timezone.utc)
        
        db.session.add(nuevo_pago)
        venta.actualizar_estado()
        return nuevo_pago

    @staticmethod
    def update_pago(pago, file, eliminar_comprobante):
        """Actualiza un pago existente, valida y gestiona el comprobante."""
        venta = pago.venta
        
        PagoService._validate_monto(venta, pago.monto, pago_existente_id=pago.id)
            
        if eliminar_comprobante and pago.url_comprobante:
            otros_pagos_con_mismo_comprobante = db.session.query(Pago.id).filter(
                Pago.url_comprobante == pago.url_comprobante,
                Pago.id != pago.id
            ).count()
            if otros_pagos_con_mismo_comprobante == 0:
                delete_file(pago.url_comprobante)
            pago.url_comprobante = None
        elif file and file.filename:
            if pago.url_comprobante:
                otros_pagos = db.session.query(Pago.id).filter(
                    Pago.url_comprobante == pago.url_comprobante,
                    Pago.id != pago.id
                ).count()
                if otros_pagos == 0:
                    delete_file(pago.url_comprobante)
            s3_key = save_file(file, "comprobantes")
            if not s3_key:
                raise Exception("Error al subir el nuevo comprobante.")
            pago.url_comprobante = s3_key
            
        venta.actualizar_estado()
        return pago

    @staticmethod
    def delete_pago(pago_id):
        """Elimina un pago, su comprobante (solo si no es usado por otros pagos) y actualiza la venta."""
        pago = PagoService.find_pago_by_id(pago_id)
        venta = pago.venta
        
        if pago.url_comprobante:
            otros_pagos_con_mismo_comprobante = db.session.query(Pago.id).filter(
                Pago.url_comprobante == pago.url_comprobante,
                Pago.id != pago.id
            ).count()

            if otros_pagos_con_mismo_comprobante == 0:
                delete_file(pago.url_comprobante)
        
        db.session.delete(pago)
        venta.actualizar_estado()

    @staticmethod
    def create_batch_pagos(pagos_json_str, file, fecha_str, metodo_pago, referencia, usuario_id, rol, almacen_id):
        """Crea múltiples pagos en lote. Operación transaccional."""
        s3_key_comprobante = None
        try:
            if file and file.filename:
                s3_key_comprobante = save_file(file, 'comprobantes')
                if not s3_key_comprobante:
                    raise Exception("Error al subir el comprobante a S3.")

            try:
                pagos_data_list = json.loads(pagos_json_str)
                if not isinstance(pagos_data_list, list) or not pagos_data_list:
                    raise PagoValidationError("El campo 'pagos' debe ser una lista no vacía.")
                
                # Import helper dynamically to prevent circular imports
                from services.venta_service import parse_iso_datetime
                fecha_pago = parse_iso_datetime(fecha_str)
                if not fecha_pago:
                    raise PagoValidationError(f"Formato de fecha inválido: {fecha_str}")
            except json.JSONDecodeError as e:
                raise PagoValidationError(f"Formato JSON inválido: {str(e)}")
            except ValueError as e:
                if isinstance(e, PagoValidationError):
                    raise e
                raise PagoValidationError(f"Formato de fecha inválido: {str(e)}")

            venta_ids = {p.get('venta_id') for p in pagos_data_list if p.get('venta_id') is not None}
            if not venta_ids:
                raise PagoValidationError("No se proporcionaron IDs de venta en los datos de pagos.")

            ventas = Venta.query.filter(Venta.id.in_(venta_ids)).all()
            ventas_map = {v.id: v for v in ventas}
            
            if len(ventas_map) != len(venta_ids):
                raise NotFound("Una o más ventas no encontradas. Venta no encontrada.")

            pagos_a_crear_info = []
            saldos_provisionales = {vid: v.saldo_pendiente for vid, v in ventas_map.items()}

            for pago_info in pagos_data_list:
                venta_id = pago_info.get('venta_id')
                monto_str = pago_info.get('monto')

                if venta_id is None or monto_str is None:
                    raise PagoValidationError(f"Cada pago debe tener venta_id y monto. Falló en: {pago_info}")

                venta = ventas_map.get(venta_id)
                if not venta:
                    raise NotFound(f"Venta con ID {venta_id} no encontrada.")
                
                if rol != 'admin' and venta.almacen_id != almacen_id:
                    raise Forbidden(f"No tiene permisos para pagos en el almacén de la venta {venta_id}.")
                
                try:
                    monto = Decimal(str(monto_str))
                except (ValueError, InvalidOperation) as e:
                    raise PagoValidationError(f"Monto inválido '{monto_str}' para venta_id {venta_id}: {str(e)}")
                
                if monto <= 0:
                    raise PagoValidationError(f"El monto para venta_id {venta_id} debe ser positivo.")

                saldo_actual = saldos_provisionales[venta_id]
                if monto > saldo_actual + Decimal('0.001'):
                    raise PagoValidationError(f"Monto {monto} para venta {venta_id} excede el saldo pendiente de {saldo_actual}.")
                
                saldos_provisionales[venta_id] -= monto
                pagos_a_crear_info.append({"venta_id": venta_id, "monto": monto})

            pagos_creados = []
            for pago_info in pagos_a_crear_info:
                nuevo_pago = Pago(
                    venta_id=pago_info['venta_id'], usuario_id=usuario_id, monto=pago_info['monto'],
                    fecha=fecha_pago, metodo_pago=metodo_pago, referencia=referencia,
                    url_comprobante=s3_key_comprobante
                )
                db.session.add(nuevo_pago)
                pagos_creados.append(nuevo_pago)
            
            db.session.flush()
            for venta in ventas_map.values():
                venta.actualizar_estado()

            return pagos_creados
        except Exception:
            if s3_key_comprobante:
                delete_file(s3_key_comprobante)
            raise
