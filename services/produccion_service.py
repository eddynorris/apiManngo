# services/produccion_service.py
import uuid
import logging
from decimal import Decimal
from datetime import datetime, timezone
from extensions import db
from models import Lote, Inventario, PresentacionProducto, Movimiento

logger = logging.getLogger(__name__)

class ProduccionValidationError(ValueError):
    pass

class ProduccionService:
    @staticmethod
    def ejecutar_ensamblaje(usuario_id: int, data: dict) -> dict:
        """
        Ejecuta la lógica de negocio de ensamblaje de forma atómica y segura.
        Realiza bloqueos en orden determinista de IDs para evitar interbloqueos.
        """
        almacen_id = data.get("almacen_id")
        entradas = data.get("entradas", [])
        salidas = data.get("salidas", [])
        descripcion = data.get("descripcion", "")

        if not almacen_id or not entradas or not salidas:
            raise ProduccionValidationError("Datos de ensamblaje incompletos. Se requieren 'almacen_id', 'entradas' y 'salidas'.")

        # --- Fase de Bloqueo Transaccional (PROD-01) ---
        # 1. Bloquear lotes involucrados en orden consistente de ID para evitar deadlocks
        lote_ids_to_lock = set()
        for item in salidas:
            if item.get("tipo_consumo") == "materia_prima" and item.get("lote_id"):
                lote_ids_to_lock.add(int(item["lote_id"]))
        for item in entradas:
            if item.get("lote_destino_id"):
                lote_ids_to_lock.add(int(item["lote_destino_id"]))

        lotes_db = {}
        if lote_ids_to_lock:
            lotes_locked = Lote.query.filter(Lote.id.in_(sorted(lote_ids_to_lock))).with_for_update().all()
            lotes_db = {l.id: l for l in lotes_locked}

        # 2. Bloquear inventarios involucrados en orden consistente de (presentacion_id, lote_id)
        inv_keys_to_lock = set()
        for item in salidas:
            if item.get("tipo_consumo") == "insumo":
                inv_keys_to_lock.add((int(item["presentacion_id"]), None))
        for item in entradas:
            inv_keys_to_lock.add((int(item["presentacion_id"]), item.get("lote_destino_id")))

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
        # Verificar insumos (salidas)
        for item in [s for s in salidas if s.get('tipo_consumo') == 'insumo']:
            cantidad_req = Decimal(str(item["cantidad_unidades"]))
            pres_id = int(item["presentacion_id"])
            inv = inventarios_db.get((pres_id, None))
            if not inv or inv.cantidad < cantidad_req:
                raise ProduccionValidationError(f"Stock de insumo insuficiente para presentación ID {pres_id}. Requerido: {cantidad_req}, Disponible: {inv.cantidad if inv else 0}")

        # Verificar materia prima (salidas)
        for item in [s for s in salidas if s.get('tipo_consumo') == 'materia_prima']:
            lote_id = int(item["lote_id"])
            cantidad_req_kg = Decimal(str(item["cantidad_kg"]))
            lote = lotes_db.get(lote_id)
            if not lote:
                raise ProduccionValidationError(f"No se encontró el lote ID {lote_id}.")
            if lote.cantidad_disponible_kg < cantidad_req_kg:
                raise ProduccionValidationError(f"Stock en KG insuficiente en Lote ID {lote_id}. Requerido: {cantidad_req_kg}, Disponible: {lote.cantidad_disponible_kg}")

        # Verificar lotes destino (entradas)
        for item in entradas:
            presentacion_id = int(item["presentacion_id"])
            presentacion_final = db.session.get(PresentacionProducto, presentacion_id)
            if not presentacion_final:
                raise ProduccionValidationError(f"No se encontró la presentación ID {presentacion_id}.")

            if lote_destino_id := item.get('lote_destino_id'):
                lote_destino = lotes_db.get(lote_destino_id)
                if not lote_destino:
                    raise ProduccionValidationError(f"El lote de destino con ID {lote_destino_id} no existe.")
                if lote_destino.producto_id != presentacion_final.producto_id:
                    raise ProduccionValidationError("El lote de destino es para una presentación diferente")

        # --- Fase de Ejecución (Transacción Atómica y Segura) ---
        id_ensamblaje = str(uuid.uuid4())
        fecha_operacion = datetime.now(timezone.utc)
        motivo_base = f"Ensamblaje {id_ensamblaje}: {descripcion}"

        # Procesar salidas
        for item in salidas:
            if item.get("tipo_consumo") == "materia_prima":
                lote_id = int(item["lote_id"])
                cantidad_kg = Decimal(str(item["cantidad_kg"]))
                lote = lotes_db[lote_id]
                lote.cantidad_disponible_kg -= cantidad_kg
                db.session.add(Movimiento(
                    tipo='salida', presentacion_id=None, lote_id=lote_id,
                    cantidad=cantidad_kg, fecha=fecha_operacion, motivo=motivo_base,
                    usuario_id=usuario_id, tipo_operacion='ensamblaje'
                ))
            elif item.get("tipo_consumo") == "insumo":
                pres_id = int(item["presentacion_id"])
                cantidad_unidades = Decimal(str(item["cantidad_unidades"]))
                inv = inventarios_db[(pres_id, None)]
                inv.cantidad -= cantidad_unidades
                db.session.add(Movimiento(
                    tipo='salida', presentacion_id=pres_id, lote_id=None,
                    cantidad=cantidad_unidades, fecha=fecha_operacion, motivo=motivo_base,
                    usuario_id=usuario_id, tipo_operacion='ensamblaje'
                ))

        # Procesar entradas
        for item in entradas:
            pres_id = int(item["presentacion_id"])
            cantidad_unidades = Decimal(str(item["cantidad_unidades"]))
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
