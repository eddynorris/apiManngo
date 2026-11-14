from decimal import Decimal

from common import obtener_saldos_pendientes_clientes, paginar
from extensions import db
from models import Almacen, Cliente, Pago, Users, Venta


def test_obtener_saldos_pendientes_clientes_batch(test_client):
    """
    Verifica que obtener_saldos_pendientes_clientes calcule los saldos correctamente
    en 1 sola consulta SQL agregada.
    """
    with test_client.application.app_context():
        user = Users(username="user_perf", password="password", rol="usuario")
        almacen = Almacen(nombre="Almacen Perf")
        cliente = Cliente(nombre="Cliente Test Saldo", telefono="987654321")
        db.session.add_all([user, almacen, cliente])
        db.session.commit()

        venta1 = Venta(cliente_id=cliente.id, almacen_id=almacen.id, vendedor_id=user.id, total=Decimal("100.00"), tipo_pago="credito", estado_pago="pendiente")
        venta2 = Venta(cliente_id=cliente.id, almacen_id=almacen.id, vendedor_id=user.id, total=Decimal("200.00"), tipo_pago="credito", estado_pago="parcial")
        db.session.add_all([venta1, venta2])
        db.session.commit()

        pago = Pago(venta_id=venta2.id, usuario_id=user.id, monto=Decimal("50.00"), metodo_pago="efectivo")
        db.session.add(pago)
        db.session.commit()

        saldos_map = obtener_saldos_pendientes_clientes([cliente.id])
        assert cliente.id in saldos_map
        # Total pendiente = (100 - 0) + (200 - 50) = 250.00
        assert saldos_map[cliente.id] == Decimal("250.00")

def test_paginar_helper(test_client):
    """
    Verifica que el helper paginar estandarice la respuesta con metadatos.
    """
    with test_client.application.app_context():
        query = Cliente.query
        response = paginar(query)
        assert "data" in response
        assert "pagination" in response
        assert "total" in response["pagination"]
        assert "page" in response["pagination"]
        assert "per_page" in response["pagination"]
