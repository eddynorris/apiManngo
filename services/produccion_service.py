# services/produccion_service.py
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from werkzeug.exceptions import BadRequest

from extensions import db
from models import Inventario, Lote, Movimiento, PresentacionProducto
from services.authorization import autorizar_almacen, normalizar_ids

logger = logging.getLogger(__name__)


class ProduccionValidationError(ValueError):
    pass


def _id_estricto(valor, alias):
    try:
        return normalizar_ids({alias: valor})[alias]
    except BadRequest as exc:
        raise ProduccionValidationError(f"{alias} inválido") from exc


def _id_opcional(valor, alias):
    if valor is None or valor == '':
        return None
    return _id_estricto(valor, alias)


def _cantidad_positiva_finita(valor):
    if valor is None or isinstance(valor, bool):
        raise ProduccionValidationError("La cantidad debe ser un número positivo")
    try:
        cantidad = Decimal(str(valor))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ProduccionValidationError("La cantidad debe ser un número positivo") from exc
    if not cantidad.is_finite() or cantidad <= 0:
        raise ProduccionValidationError("La cantidad debe ser un número positivo")
    return cantidad


class ProduccionService:
    @staticmethod
    def _normalizar_payload(data):
        """Normaliza IDs/cantidades y rechaza NaN, infinitos, negativos y duplicados inseguros."""
        if not isinstance(data, dict):
            raise ProduccionValidationError("Se requiere un cuerpo JSON de ensamblaje.")

        almacen_raw = data.get("almacen_id")
        if almacen_raw in (None, ''):
            raise ProduccionValidationError(
                "Datos de ensamblaje incompletos. Se requiere 'almacen_id'."
            )
        almacen_id = _id_estricto(almacen_raw, 'almacen_id')

        entradas_raw = data.get("entradas", [])
        salidas_raw = data.get("salidas", [])
        if not isinstance(entradas_raw, list) or not isinstance(salidas_raw, list):
            raise ProduccionValidationError("'entradas' y 'salidas' deben ser listas.")
        if not entradas_raw or not salidas_raw:
            raise ProduccionValidationError(
                "Datos de ensamblaje incompletos. Se requieren 'entradas' y 'salidas'."
            )

        entradas = []
        for item in entradas_raw:
            if not isinstance(item, dict):
                raise ProduccionValidationError("Entrada de ensamblaje inválida.")
            entradas.append({
                'presentacion_id': _id_estricto(item.get('presentacion_id'), 'presentacion_id'),
                'cantidad_unidades': _cantidad_positiva_finita(item.get('cantidad_unidades')),
                'lote_destino_id': _id_opcional(item.get('lote_destino_id'), 'lote_id'),
            })

        salidas = []
        for item in salidas_raw:
            if not isinstance(item, dict):
                raise ProduccionValidationError("Salida de ensamblaje inválida.")
            tipo_consumo = item.get('tipo_consumo')
            if tipo_consumo == 'materia_prima':
                salidas.append({
                    'tipo_consumo': 'materia_prima',
                    'lote_id': _id_estricto(item.get('lote_id'), 'lote_id'),
                    'cantidad_kg': _cantidad_positiva_finita(item.get('cantidad_kg')),
                })
            elif tipo_consumo == 'insumo':
                salidas.append({
                    'tipo_consumo': 'insumo',
                    'presentacion_id': _id_estricto(item.get('presentacion_id'), 'presentacion_id'),
                    'cantidad_unidades': _cantidad_positiva_finita(item.get('cantidad_unidades')),
                })
            else:
                raise ProduccionValidationError(
                    "Cada salida debe indicar tipo_consumo 'materia_prima' o 'insumo'."
                )

        descripcion = data.get("descripcion", "")
        if descripcion is None:
            descripcion = ""
        if not isinstance(descripcion, str):
            raise ProduccionValidationError("La descripción del ensamblaje es inválida.")

        return almacen_id, entradas, salidas, descripcion

    @staticmethod
    def ejecutar_ensamblaje(usuario_id: int, data: dict) -> dict:
        """
        Ejecuta la lógica de negocio de ensamblaje de forma atómica y segura.
        Autoriza el almacén con el actor vigente en BD antes de bloquear o mutar.
        Realiza bloqueos en orden determinista de IDs para evitar interbloqueos.
        """
        almacen_id, entradas, salidas, descripcion = (
            ProduccionService._normalizar_payload(data)
        )

        usuario = autorizar_almacen(usuario_id, almacen_id)
        usuario_id = usuario.id

        # --- Fase de Bloqueo Transaccional (PROD-01) ---
        # 1. Bloquear lotes involucrados en orden consistente de ID para evitar deadlocks
        lote_ids_to_lock = set()
        for item in salidas:
            if item.get("tipo_consumo") == "materia_prima" and item.get("lote_id"):
                lote_ids_to_lock.add(item["lote_id"])
        for item in entradas:
            if item.get("lote_destino_id"):
                lote_ids_to_lock.add(item["lote_destino_id"])

        lotes_db = {}
        if lote_ids_to_lock:
            lotes_locked = Lote.query.filter(
                Lote.id.in_(sorted(lote_ids_to_lock))
            ).with_for_update().all()
            lotes_db = {lote.id: lote for lote in lotes_locked}

        # 2. Bloquear inventarios involucrados en orden consistente de (presentacion_id, lote_id)
        inv_keys_to_lock = set()
        for item in salidas:
            if item.get("tipo_consumo") == "insumo":
                inv_keys_to_lock.add((item["presentacion_id"], None))
        for item in entradas:
            inv_keys_to_lock.add((item["presentacion_id"], item.get("lote_destino_id")))

        inventarios_db = {}
        for pres_id, lote_id in sorted(inv_keys_to_lock, key=lambda k: (k[0], k[1] or 0)):
            inv = Inventario.query.filter_by(
                almacen_id=almacen_id,
                presentacion_id=pres_id,
                lote_id=lote_id
            ).with_for_update().first()
            if inv:
                inventarios_db[(pres_id, lote_id)] = inv

        # --- Fase de Verificación sobre registros bloqueados ---
        # Agregar demandas por presentación/lote evita que líneas duplicadas
        # pasen individualmente y en conjunto dejen stock negativo.
        demanda_insumos = {}
        for item in [s for s in salidas if s.get('tipo_consumo') == 'insumo']:
            pres_id = item["presentacion_id"]
            demanda_insumos[pres_id] = (
                demanda_insumos.get(pres_id, Decimal('0')) + item["cantidad_unidades"]
            )

        for pres_id, cantidad_req in demanda_insumos.items():
            inv = inventarios_db.get((pres_id, None))
            if not inv or inv.cantidad < cantidad_req:
                raise ProduccionValidationError(
                    f"Stock de insumo insuficiente para presentación ID {pres_id}. "
                    f"Requerido: {cantidad_req}, Disponible: {inv.cantidad if inv else 0}"
                )

        demanda_materia_prima = {}
        for item in [s for s in salidas if s.get('tipo_consumo') == 'materia_prima']:
            lote_id = item["lote_id"]
            demanda_materia_prima[lote_id] = (
                demanda_materia_prima.get(lote_id, Decimal('0')) + item["cantidad_kg"]
            )

        for lote_id, cantidad_req_kg in demanda_materia_prima.items():
            lote = lotes_db.get(lote_id)
            if not lote:
                raise ProduccionValidationError(f"No se encontró el lote ID {lote_id}.")
            if lote.cantidad_disponible_kg < cantidad_req_kg:
                raise ProduccionValidationError(
                    f"Stock en KG insuficiente en Lote ID {lote_id}. "
                    f"Requerido: {cantidad_req_kg}, Disponible: {lote.cantidad_disponible_kg}"
                )

        # Verificar lotes destino (entradas)
        for item in entradas:
            presentacion_id = item["presentacion_id"]
            presentacion_final = db.session.get(PresentacionProducto, presentacion_id)
            if not presentacion_final:
                raise ProduccionValidationError(
                    f"No se encontró la presentación ID {presentacion_id}."
                )

            if lote_destino_id := item.get('lote_destino_id'):
                lote_destino = lotes_db.get(lote_destino_id)
                if not lote_destino:
                    raise ProduccionValidationError(
                        f"El lote de destino con ID {lote_destino_id} no existe."
                    )
                if lote_destino.producto_id != presentacion_final.producto_id:
                    raise ProduccionValidationError(
                        "El lote de destino es para una presentación diferente"
                    )

        # --- Fase de Ejecución (Transacción Atómica y Segura) ---
        id_ensamblaje = str(uuid.uuid4())
        fecha_operacion = datetime.now(UTC)
        motivo_base = f"Ensamblaje {id_ensamblaje}: {descripcion}"

        # Procesar salidas
        for item in salidas:
            if item.get("tipo_consumo") == "materia_prima":
                lote_id = item["lote_id"]
                cantidad_kg = item["cantidad_kg"]
                lote = lotes_db[lote_id]
                lote.cantidad_disponible_kg -= cantidad_kg
                db.session.add(Movimiento(
                    tipo='salida', presentacion_id=None, lote_id=lote_id,
                    cantidad=cantidad_kg, fecha=fecha_operacion, motivo=motivo_base,
                    usuario_id=usuario_id, tipo_operacion='ensamblaje'
                ))
            elif item.get("tipo_consumo") == "insumo":
                pres_id = item["presentacion_id"]
                cantidad_unidades = item["cantidad_unidades"]
                inv = inventarios_db[(pres_id, None)]
                inv.cantidad -= cantidad_unidades
                db.session.add(Movimiento(
                    tipo='salida', presentacion_id=pres_id, lote_id=None,
                    cantidad=cantidad_unidades, fecha=fecha_operacion, motivo=motivo_base,
                    usuario_id=usuario_id, tipo_operacion='ensamblaje'
                ))

        # Procesar entradas
        for item in entradas:
            pres_id = item["presentacion_id"]
            cantidad_unidades = item["cantidad_unidades"]
            lote_destino_id = item.get('lote_destino_id')

            inv_destino = inventarios_db.get((pres_id, lote_destino_id))
            if inv_destino:
                inv_destino.cantidad += cantidad_unidades
                inv_destino.ultima_actualizacion = fecha_operacion
            else:
                # Crear nuevo registro si no existía
                inv_destino = Inventario(
                    presentacion_id=pres_id,
                    almacen_id=almacen_id,
                    lote_id=lote_destino_id,
                    cantidad=cantidad_unidades
                )
                db.session.add(inv_destino)
                # Añadir al dict local por si hay entradas duplicadas de la misma presentación en el mismo request
                inventarios_db[(pres_id, lote_destino_id)] = inv_destino

            db.session.add(Movimiento(
                tipo='entrada',
                presentacion_id=pres_id,
                lote_id=lote_destino_id,
                cantidad=cantidad_unidades,
                fecha=fecha_operacion,
                motivo=motivo_base,
                usuario_id=usuario_id,
                tipo_operacion='ensamblaje'
            ))

        return {"mensaje": "Operación de ensamblaje registrada exitosamente", "id_ensamblaje": id_ensamblaje}
