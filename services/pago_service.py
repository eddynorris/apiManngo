# services/pago_service.py
import json
import logging
from decimal import Decimal
from datetime import datetime, timezone
from werkzeug.exceptions import NotFound, Forbidden
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from common import parse_iso_datetime
from extensions import db
from models import Pago, Venta
from utils.file_handlers import save_file, delete_file
from services.money import monto_decimal_positivo
from services.authorization import (
    autorizar_pago,
    autorizar_venta_para_pago,
    es_admin,
    normalizar_ids,
    obtener_actor,
)
from services.storage_cleanup_service import track_uploaded_key, enqueue_storage_cleanup

logger = logging.getLogger(__name__)


class PagoValidationError(ValueError):
    """Error de validación específico para la lógica de pagos."""
    pass


class PagoConflictError(PagoValidationError):
    """El cambio solicitado dejaría pagos o depósitos incoherentes."""
    pass


class PagoService:
    """Contiene toda la lógica de negocio para gestionar pagos."""

    # ------------------------------------------------------------------
    # Utilidades de bloqueo y cálculo dentro de la transacción
    # ------------------------------------------------------------------
    @staticmethod
    def _lock_ventas(venta_ids):
        """Bloquea las ventas en orden determinista de ID.

        En PostgreSQL ``FOR UPDATE`` serializa la validación de saldo con
        cualquier otra solicitud que afecte a las mismas ventas. En SQLite el
        bloqueo es inocuo y las pruebas de concurrencia real se ejecutan en
        PostgreSQL.
        """
        ids = sorted({int(venta_id) for venta_id in venta_ids})
        if not ids:
            return {}
        ventas = (
            db.session.query(Venta)
            .filter(Venta.id.in_(ids))
            .order_by(Venta.id)
            .with_for_update()
            .populate_existing()
            .all()
        )
        return {venta.id: venta for venta in ventas}

    @staticmethod
    def _lock_pagos(pago_ids):
        """Bloquea los pagos en orden determinista, después de sus ventas."""
        ids = sorted({int(pago_id) for pago_id in pago_ids})
        if not ids:
            return {}
        pagos = (
            db.session.query(Pago)
            .filter(Pago.id.in_(ids))
            .order_by(Pago.id)
            .with_for_update()
            .populate_existing()
            .all()
        )
        return {pago.id: pago for pago in pagos}

    @staticmethod
    def _total_pagado(venta_id, excluir_pago_id=None):
        """Relee la suma de pagos dentro de la transacción actual."""
        query = db.session.query(
            func.coalesce(func.sum(Pago.monto), 0)
        ).filter(Pago.venta_id == venta_id)
        if excluir_pago_id is not None:
            query = query.filter(Pago.id != excluir_pago_id)
        total = query.scalar()
        return Decimal(str(total or 0))

    @staticmethod
    def _validar_monto_positivo(valor, campo="monto"):
        try:
            return monto_decimal_positivo(valor, campo)
        except ValueError as error:
            raise PagoValidationError(str(error)) from None

    @staticmethod
    def _validar_monto_venta(venta, monto, pago_existente_id=None):
        """Valida contra la suma de pagos releída en la transacción."""
        pagos_anteriores = PagoService._total_pagado(
            venta.id, excluir_pago_id=pago_existente_id
        )
        saldo_pendiente = venta.total - pagos_anteriores
        if monto > saldo_pendiente + Decimal("0.001"):
            raise PagoValidationError(
                f"El monto a pagar ({monto}) excede el saldo pendiente ({saldo_pendiente})."
            )

    @staticmethod
    def _autorizar_venta_si_aplica(actor_id, venta):
        if actor_id is None:
            return None
        return autorizar_venta_para_pago(actor_id, venta)

    @staticmethod
    def _autorizar_pago_si_aplica(actor_id, pago):
        if actor_id is None:
            return None
        return autorizar_pago(actor_id, pago)

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    @staticmethod
    def find_pago_by_id(pago_id):
        """Encuentra un pago por su ID o lanza un error 404 si no se encuentra."""
        pago = db.session.get(Pago, pago_id)
        if not pago:
            raise NotFound("Pago no encontrado.")
        return pago

    @staticmethod
    def _comprobante_referenced_by_other(pago_id, storage_key):
        if not storage_key:
            return False
        return db.session.query(Pago.id).filter(
            Pago.url_comprobante == storage_key,
            Pago.id != pago_id
        ).first() is not None

    @staticmethod
    def schedule_replaced_comprobante(pago_id, old_key, new_key=None):
        if not old_key or old_key == new_key:
            return None
        if PagoService._comprobante_referenced_by_other(pago_id, old_key):
            return None
        return enqueue_storage_cleanup(old_key, 'replaced')

    @staticmethod
    def get_pagos_query(filters, current_user_id=None, rol=None):
        """Construye una consulta de pagos optimizada con filtros y carga ansiosa."""
        query = Pago.query.options(
            joinedload(Pago.venta).joinedload(Venta.cliente),
            joinedload(Pago.venta).joinedload(Venta.almacen),
            joinedload(Pago.usuario)
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

    # ------------------------------------------------------------------
    # Mutaciones
    # ------------------------------------------------------------------
    @staticmethod
    def create_pago(nuevo_pago, file, usuario_id, actor_id=None):
        """Crea un nuevo pago validando saldo bajo bloqueo y autorización."""
        if not nuevo_pago.venta_id:
            raise PagoValidationError("El campo 'venta_id' es requerido.")

        monto = PagoService._validar_monto_positivo(nuevo_pago.monto, "monto")

        metodo_pago = str(nuevo_pago.metodo_pago or "").strip().lower()
        if not metodo_pago:
            raise PagoValidationError("El campo 'metodo_pago' es requerido.")
        nuevo_pago.metodo_pago = metodo_pago

        venta = db.session.get(Venta, nuevo_pago.venta_id)
        if not venta:
            raise NotFound("Venta no encontrada.")

        actor = PagoService._autorizar_venta_si_aplica(actor_id, venta)

        ventas_bloqueadas = PagoService._lock_ventas([venta.id])
        venta = ventas_bloqueadas.get(venta.id)
        if not venta:
            raise NotFound("Venta no encontrada.")

        if actor_id is not None:
            actor = PagoService._autorizar_venta_si_aplica(actor_id, venta)
            usuario_id = actor.id

        PagoService._validar_monto_venta(venta, monto)

        es_deposito = metodo_pago == 'deposito'
        if es_deposito and not (file and file.filename):
            raise PagoValidationError(
                "El método de pago 'deposito' requiere un comprobante (foto del recibo) obligatorio."
            )

        s3_key = None
        if file and file.filename:
            s3_key = save_file(file, "comprobantes")
            if not s3_key:
                raise PagoValidationError("Ocurrió un error interno al guardar el comprobante.")
            track_uploaded_key(s3_key)

        nuevo_pago.monto = monto
        nuevo_pago.usuario_id = usuario_id
        nuevo_pago.url_comprobante = s3_key

        if es_deposito:
            nuevo_pago.depositado = True
            nuevo_pago.monto_depositado = monto
            nuevo_pago.fecha_deposito = nuevo_pago.fecha or datetime.now(timezone.utc)

        db.session.add(nuevo_pago)
        db.session.flush()
        venta.actualizar_estado()
        return nuevo_pago

    @staticmethod
    def update_pago(pago, file, eliminar_comprobante, datos=None, actor_id=None):
        """Actualiza un pago existente validando saldo y depósito bajo bloqueo.

        ``datos`` contiene solo los campos editables ya validados por el
        schema. Los campos de servidor (usuario, depósito, URL) no se copian
        desde el cuerpo de la solicitud.
        """
        pago_id = pago.id
        pago_actual = db.session.get(Pago, pago_id)
        if not pago_actual:
            raise NotFound("Pago no encontrado.")

        # Autorizar antes de bloquear o mutar filas.
        PagoService._autorizar_pago_si_aplica(actor_id, pago_actual)

        venta_id = pago_actual.venta_id
        ventas_bloqueadas = PagoService._lock_ventas([venta_id])
        venta = ventas_bloqueadas.get(venta_id)
        if not venta:
            raise NotFound("Venta no encontrada.")
        pagos_bloqueados = PagoService._lock_pagos([pago_id])
        pago_actual = pagos_bloqueados.get(pago_id)
        if not pago_actual:
            raise NotFound("Pago no encontrado.")

        # Revalidar la autorización con el estado ya bloqueado.
        PagoService._autorizar_pago_si_aplica(actor_id, pago_actual)

        if datos is not None and 'monto' in datos:
            nuevo_monto = PagoService._validar_monto_positivo(datos['monto'], "monto")
        else:
            nuevo_monto = PagoService._validar_monto_positivo(pago_actual.monto, "monto")

        monto_depositado = pago_actual.monto_depositado
        if monto_depositado is not None and Decimal(str(monto_depositado)) > nuevo_monto:
            raise PagoConflictError(
                "El nuevo monto dejaría un depósito mayor que el pago; "
                "se requiere un flujo explícito de devolución."
            )

        PagoService._validar_monto_venta(
            venta, nuevo_monto, pago_existente_id=pago_actual.id
        )

        if eliminar_comprobante and pago_actual.url_comprobante:
            PagoService.schedule_replaced_comprobante(pago_actual.id, pago_actual.url_comprobante)
            pago_actual.url_comprobante = None
        elif file and file.filename:
            s3_key = save_file(file, "comprobantes")
            if not s3_key:
                raise PagoValidationError("Error al subir el nuevo comprobante.")
            track_uploaded_key(s3_key)
            PagoService.schedule_replaced_comprobante(
                pago_actual.id, pago_actual.url_comprobante, s3_key
            )
            pago_actual.url_comprobante = s3_key

        if datos is not None:
            pago_actual.monto = nuevo_monto
            for campo in ('metodo_pago', 'referencia', 'fecha'):
                if campo in datos:
                    setattr(pago_actual, campo, datos[campo])

        db.session.flush()
        venta.actualizar_estado()
        return pago_actual

    @staticmethod
    def delete_pago(pago_id, actor_id=None):
        """Elimina un pago tras autorizar y bloquear venta/pago."""
        pago = db.session.get(Pago, pago_id)
        if not pago:
            raise NotFound("Pago no encontrado.")

        PagoService._autorizar_pago_si_aplica(actor_id, pago)

        ventas_bloqueadas = PagoService._lock_ventas([pago.venta_id])
        venta = ventas_bloqueadas.get(pago.venta_id)
        if not venta:
            raise NotFound("Venta no encontrada.")
        pagos_bloqueados = PagoService._lock_pagos([pago_id])
        pago = pagos_bloqueados.get(pago_id)
        if not pago:
            raise NotFound("Pago no encontrado.")

        PagoService._autorizar_pago_si_aplica(actor_id, pago)

        if pago.url_comprobante:
            PagoService.schedule_replaced_comprobante(pago.id, pago.url_comprobante)

        db.session.delete(pago)
        db.session.flush()
        venta.actualizar_estado()

    @staticmethod
    def create_batch_pagos(pagos_json_str, file, fecha_str, metodo_pago, referencia,
                           usuario_id, rol=None, almacen_id=None, actor_id=None):
        """Crea múltiples pagos en lote validando todo antes de subir archivos.

        ``rol`` y ``almacen_id`` se conservan por compatibilidad de firma, pero
        nunca autorizan la operación: el actor se resuelve contra la base de
        datos vigente y los permisos se calculan desde las ventas reales.
        """
        try:
            pagos_data_list = json.loads(pagos_json_str)
            if not isinstance(pagos_data_list, list) or not pagos_data_list:
                raise PagoValidationError("El campo 'pagos' debe ser una lista no vacía.")

            fecha_pago = parse_iso_datetime(fecha_str)
            if not fecha_pago:
                raise PagoValidationError(f"Formato de fecha inválido: {fecha_str}")
        except json.JSONDecodeError as e:
            raise PagoValidationError(f"Formato JSON inválido: {str(e)}")
        except ValueError as e:
            if isinstance(e, PagoValidationError):
                raise e
            raise PagoValidationError(f"Formato de fecha inválido: {str(e)}")

        pagos_data_list = normalizar_ids(pagos_data_list)

        venta_ids = set()
        for pago_info in pagos_data_list:
            if not isinstance(pago_info, dict):
                raise PagoValidationError("Cada pago debe ser un objeto.")
            venta_id = pago_info.get('venta_id')
            if venta_id is None or pago_info.get('monto') is None:
                raise PagoValidationError(
                    f"Cada pago debe tener venta_id y monto. Falló en: {pago_info}"
                )
            venta_ids.add(venta_id)

        ventas = Venta.query.filter(Venta.id.in_(venta_ids)).all()
        ventas_map = {v.id: v for v in ventas}
        if len(ventas_map) != len(venta_ids):
            raise NotFound("Una o más ventas no encontradas. Venta no encontrada.")

        actor = None
        if actor_id is not None:
            actor = obtener_actor(actor_id)

        for pago_info in pagos_data_list:
            venta = ventas_map[pago_info['venta_id']]
            if actor is not None and not es_admin(actor):
                try:
                    autorizar_venta_para_pago(actor.id, venta)
                except Forbidden:
                    raise Forbidden(
                        f"No tiene permisos para pagos en el almacén de la venta {venta.id}."
                    ) from None

        # Validación provisional antes de bloquear y antes de subir archivos.
        saldos_provisionales = {
            venta_id: venta.total - PagoService._total_pagado(venta_id)
            for venta_id, venta in ventas_map.items()
        }
        for pago_info in pagos_data_list:
            venta_id = pago_info['venta_id']
            monto = PagoService._validar_monto_positivo(pago_info.get('monto'), 'monto')
            saldo_actual = saldos_provisionales[venta_id]
            if monto > saldo_actual + Decimal('0.001'):
                raise PagoValidationError(
                    f"Monto {monto} para venta {venta_id} excede el saldo pendiente de {saldo_actual}."
                )
            saldos_provisionales[venta_id] -= monto

        # Bloquear todas las ventas en orden y releer la suma real de pagos.
        ventas_bloqueadas = PagoService._lock_ventas(venta_ids)
        if len(ventas_bloqueadas) != len(venta_ids):
            raise NotFound("Una o más ventas no encontradas. Venta no encontrada.")

        for pago_info in pagos_data_list:
            venta = ventas_bloqueadas[pago_info['venta_id']]
            if actor is not None and not es_admin(actor):
                try:
                    autorizar_venta_para_pago(actor.id, venta)
                except Forbidden:
                    raise Forbidden(
                        f"No tiene permisos para pagos en el almacén de la venta {venta.id}."
                    ) from None

        saldos_bloqueados = {
            venta_id: venta.total - PagoService._total_pagado(venta_id)
            for venta_id, venta in ventas_bloqueadas.items()
        }
        montos_finales = []
        for pago_info in pagos_data_list:
            venta_id = pago_info['venta_id']
            monto = PagoService._validar_monto_positivo(pago_info.get('monto'), 'monto')
            saldo_actual = saldos_bloqueados[venta_id]
            if monto > saldo_actual + Decimal('0.001'):
                raise PagoValidationError(
                    f"Monto {monto} para venta {venta_id} excede el saldo pendiente de {saldo_actual}."
                )
            saldos_bloqueados[venta_id] -= monto
            montos_finales.append((venta_id, monto))

        s3_key_comprobante = None
        if file and file.filename:
            s3_key_comprobante = save_file(file, 'comprobantes')
            if not s3_key_comprobante:
                raise PagoValidationError("Error al subir el comprobante a S3.")
            track_uploaded_key(s3_key_comprobante)

        usuario_final = actor.id if actor is not None else usuario_id
        pagos_creados = []
        for venta_id, monto in montos_finales:
            nuevo_pago = Pago(
                venta_id=venta_id, usuario_id=usuario_final, monto=monto,
                fecha=fecha_pago, metodo_pago=metodo_pago, referencia=referencia,
                url_comprobante=s3_key_comprobante
            )
            db.session.add(nuevo_pago)
            pagos_creados.append(nuevo_pago)

        db.session.flush()
        for venta in ventas_bloqueadas.values():
            venta.actualizar_estado()

        return pagos_creados
