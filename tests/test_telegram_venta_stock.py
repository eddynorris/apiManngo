"""A24: la venta individual por Telegram aplica la misma política de stock que la API."""
from decimal import Decimal

import pytest

import telegram.handlers.venta as venta_module
from extensions import db
from models import Almacen, Cliente, Inventario, Movimiento, PresentacionProducto, Users, Venta
from services.stock_service import StockInsuficienteError
from telegram.handlers.venta import VentaHandler


class _FakeTelegram:
    def __init__(self):
        self.edited = []

    def send_message(self, *args, **kwargs):
        pass

    def edit_message(self, chat_id, message_id, text, **kwargs):
        self.edited.append(text)


def _contexto(cantidad):
    almacen = Almacen.query.filter_by(nombre='Almacén Central').one()
    cliente = Cliente.query.filter_by(nombre='Cliente D (Sin Deuda)').one()
    pres = PresentacionProducto.query.filter_by(nombre='Test Pres').one()
    precio = Decimal('15.00')
    return {
        "almacen_id": almacen.id,
        "almacen_nombre": almacen.nombre,
        "cliente_id": cliente.id,
        "cliente_nombre": cliente.nombre,
        "estado": "completado",
        "items": [{
            "presentacion_id": pres.id,
            "producto_nombre": pres.nombre,
            "cantidad": cantidad,
            "precio_unitario": float(precio),
            "subtotal": float(precio * cantidad),
        }],
        "total": float(precio * cantidad),
        "pagos": [],
    }


def _stock_central():
    almacen = Almacen.query.filter_by(nombre='Almacén Central').one()
    pres = PresentacionProducto.query.filter_by(nombre='Test Pres').one()
    return sum(
        Decimal(str(i.cantidad))
        for i in Inventario.query.filter_by(almacen_id=almacen.id, presentacion_id=pres.id)
    )


def test_venta_telegram_sin_stock_suficiente_se_rechaza_sin_efectos(test_client, monkeypatch):
    fake = _FakeTelegram()
    monkeypatch.setattr(venta_module, 'telegram_service', fake)
    vendedor = Users.query.filter_by(username='vendedor').one()
    ventas_antes = Venta.query.count()
    movimientos_antes = Movimiento.query.count()
    stock_antes = _stock_central()  # 5 unidades en el fixture

    with pytest.raises(StockInsuficienteError):
        VentaHandler.execute_venta(555, vendedor, _contexto(stock_antes + 1), 9)
    db.session.rollback()  # el router hace rollback al capturar la excepción

    assert Venta.query.count() == ventas_antes
    assert Movimiento.query.count() == movimientos_antes
    assert _stock_central() == stock_antes
    assert fake.edited == []


def test_venta_telegram_con_stock_suficiente_se_registra(test_client, monkeypatch):
    fake = _FakeTelegram()
    monkeypatch.setattr(venta_module, 'telegram_service', fake)
    vendedor = Users.query.filter_by(username='vendedor').one()
    ventas_antes = Venta.query.count()
    stock_antes = _stock_central()

    VentaHandler.execute_venta(555, vendedor, _contexto(Decimal('2')), 9)

    assert Venta.query.count() == ventas_antes + 1
    assert _stock_central() == stock_antes - 2
    assert len(fake.edited) == 1 and 'Venta registrada' in fake.edited[0]
