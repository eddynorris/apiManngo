# services/stock_service.py
from dataclasses import dataclass
from decimal import Decimal
from extensions import db
from models import Inventario, Lote, Movimiento
from sqlalchemy.orm import joinedload
from sqlalchemy import orm

@dataclass
class ConsumoLote:
    inventario_id: int
    lote_id: int | None
    cantidad: Decimal

class StockInsuficienteError(ValueError):
    def __init__(self, presentacion_id: int, requerido: Decimal, disponible: Decimal):
        self.presentacion_id = presentacion_id
        self.requerido = requerido
        self.disponible = disponible
        super().__init__(f"Stock insuficiente para presentación ID {presentacion_id}. Requerido: {requerido}, Disponible: {disponible}")

class StockService:
    @staticmethod
    def stock_disponible(almacen_id: int, presentacion_id: int) -> Decimal:
        """Retorna el stock total disponible para una presentación en un almacén."""
        invs = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=presentacion_id).all()
        return sum(inv.cantidad for inv in invs)

    @staticmethod
    def bloquear_y_obtener_inventarios(almacen_id: int, presentacion_ids: list[int]) -> dict[int, list[Inventario]]:
        """
        Bloquea y obtiene todas las filas de inventario disponibles para los IDs de presentación dados.
        Retorna un diccionario mapeando presentacion_id a una lista de filas de Inventario (ordenadas por fecha de lote FIFO).
        """
        if not presentacion_ids:
            return {}
            
        from models import Lote as LoteModel
        invs_raw = (
            Inventario.query
            .with_for_update(of=Inventario)  # Bloqueo pesimista
            .options(orm.joinedload(Inventario.presentacion), orm.joinedload(Inventario.lote))
            .join(LoteModel, Inventario.lote_id == LoteModel.id, isouter=True)
            .filter(
                Inventario.presentacion_id.in_(presentacion_ids),
                Inventario.almacen_id == almacen_id,
                Inventario.cantidad > 0
            )
            .order_by(
                Inventario.presentacion_id,
                LoteModel.fecha_ingreso.asc(),  # FIFO
                Inventario.id.asc()
            )
            .all()
        )
        
        inventarios_por_presentacion = {}
        for inv in invs_raw:
            inventarios_por_presentacion.setdefault(inv.presentacion_id, []).append(inv)
            
        return inventarios_por_presentacion

    @staticmethod
    def descontar_fifo(almacen_id: int, presentacion_id: int, cantidad: Decimal,
                        permitir_negativo: bool = False, invs_disponibles: list[Inventario] = None) -> list[ConsumoLote]:
        """
        Descuenta el stock solicitado usando el criterio FIFO.
        Si se proporciona invs_disponibles, asume que ya han sido bloqueadas y obtenidas externamente.
        De lo contrario, realiza la consulta y el bloqueo correspondiente.
        """
        if cantidad <= 0:
            return []

        if invs_disponibles is None:
            invs_dict = StockService.bloquear_y_obtener_inventarios(almacen_id, [presentacion_id])
            invs_disponibles = invs_dict.get(presentacion_id, [])

        total_disponible = sum(inv.cantidad for inv in invs_disponibles)
        if total_disponible < cantidad and not permitir_negativo:
            raise StockInsuficienteError(presentacion_id, cantidad, total_disponible)

        consumos = []
        cantidad_restante = cantidad

        # Consumir FIFO
        for inv in invs_disponibles:
            if cantidad_restante <= 0:
                break
            cantidad_a_tomar = min(inv.cantidad, cantidad_restante)
            inv.cantidad -= cantidad_a_tomar
            cantidad_restante -= cantidad_a_tomar
            consumos.append(ConsumoLote(
                inventario_id=inv.id,
                lote_id=inv.lote_id,
                cantidad=cantidad_a_tomar
            ))

        # Si aún queda cantidad por descontar y se permite negativo (ej. en el bot para ventas en lote)
        if cantidad_restante > 0 and permitir_negativo:
            # Buscar o crear registro 'sin lote' para descontar en negativo
            inv_sin_lote = (
                Inventario.query
                .with_for_update()
                .filter_by(almacen_id=almacen_id, presentacion_id=presentacion_id, lote_id=None)
                .first()
            )
            if not inv_sin_lote:
                inv_sin_lote = Inventario(
                    almacen_id=almacen_id,
                    presentacion_id=presentacion_id,
                    lote_id=None,
                    cantidad=Decimal('0')
                )
                db.session.add(inv_sin_lote)
                db.session.flush()
                
            inv_sin_lote.cantidad -= cantidad_restante
            consumos.append(ConsumoLote(
                inventario_id=inv_sin_lote.id,
                lote_id=None,
                cantidad=cantidad_restante
            ))

        return consumos

    @staticmethod
    def revertir_venta(venta) -> None:
        """
        Devuelve al inventario las cantidades por lote exacto de los detalles de una venta.
        También elimina todos los movimientos asociados a esta venta en la base de datos.
        """
        presentacion_ids = [d.presentacion_id for d in venta.detalles]
        if not presentacion_ids:
            return

        # Bloquear todas las filas de inventario correspondientes en el almacén de la venta
        inventarios = (
            Inventario.query
            .with_for_update(of=Inventario)
            .filter(
                Inventario.almacen_id == venta.almacen_id,
                Inventario.presentacion_id.in_(presentacion_ids)
            )
            .all()
        )
        inventario_dict = {(i.presentacion_id, i.lote_id): i for i in inventarios}

        for detalle in venta.detalles:
            inv = inventario_dict.get((detalle.presentacion_id, detalle.lote_id))
            if inv:
                inv.cantidad += detalle.cantidad
            else:
                # Si por alguna razón no existía la fila, se recrea
                inv = Inventario(
                    presentacion_id=detalle.presentacion_id,
                    almacen_id=venta.almacen_id,
                    lote_id=detalle.lote_id,
                    cantidad=detalle.cantidad
                )
                db.session.add(inv)
                inventario_dict[(detalle.presentacion_id, detalle.lote_id)] = inv

        # Eliminar movimientos asociados a la venta
        Movimiento.query.filter_by(venta_id=venta.id).delete(synchronize_session=False)
        db.session.flush()
