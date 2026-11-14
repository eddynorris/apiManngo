import json
from datetime import datetime
from decimal import Decimal

import pytest

from app import app as flask_app
from extensions import db
from models import (
    Almacen,
    Cliente,
    Gasto,
    Lote,
    Pago,
    PresentacionProducto,
    Producto,
    Users,
    Venta,
    VentaDetalle,
)


@pytest.fixture(scope='module')
def test_client():

    # Create a test client using the Flask application configured for testing
    with flask_app.test_client() as testing_client:
        # Establish an application context
        with flask_app.app_context():
            db.create_all()
            setup_test_data()
            yield testing_client  # this is where the testing happens!
            db.session.remove()
            db.drop_all()

def setup_test_data():
    # --- Clean up existing data ---
    db.session.query(Pago).delete()
    db.session.query(VentaDetalle).delete()
    db.session.query(Venta).delete()
    db.session.query(Gasto).delete()
    db.session.query(Lote).delete()
    db.session.query(PresentacionProducto).delete()
    db.session.query(Producto).delete()
    db.session.query(Cliente).delete()
    db.session.query(Almacen).delete()
    db.session.query(Users).delete()
    db.session.commit()

    from werkzeug.security import generate_password_hash

    # --- Create new test data ---
    almacen1 = Almacen(id=1, nombre='Almacen Principal')
    cliente1 = Cliente(id=1, nombre='Cliente de Prueba')
    user1 = Users(id=1, username='testuser', password=generate_password_hash('password123'), rol='admin', almacen_id=1)
    db.session.add_all([almacen1, cliente1, user1])
    db.session.commit()

    prod1 = Producto(id=1, nombre='Carbon Premium', precio_compra=500)
    prod2 = Producto(id=2, nombre='Bolsas', precio_compra=10)
    db.session.add_all([prod1, prod2])
    db.session.commit()

    pres1 = PresentacionProducto(id=1, producto_id=1, nombre='Saco 10kg', capacidad_kg=10, tipo='procesado', precio_venta=15)
    pres2 = PresentacionProducto(id=2, producto_id=2, nombre='Paquete 100u', capacidad_kg=1, tipo='detalle', precio_venta=5)
    db.session.add_all([pres1, pres2])
    db.session.commit()

    lote1 = Lote(id=1, producto_id=1, descripcion='Lote Carbon Enero', peso_humedo_kg=1000)
    lote2 = Lote(id=2, producto_id=2, descripcion='Lote Bolsas Feb', peso_humedo_kg=100)
    db.session.add_all([lote1, lote2])
    db.session.commit()

    # Venta 1: Contiene un producto del Lote 1
    venta1 = Venta(id=1, cliente_id=1, almacen_id=1, vendedor_id=1, fecha=datetime(2023, 1, 15), total=150, tipo_pago='credito')
    vd1 = VentaDetalle(venta_id=1, presentacion_id=1, cantidad=10, precio_unitario=15, lote_id=1) # 10 * 15 = 150
    db.session.add_all([venta1, vd1])

    # Venta 2: Contiene un producto del Lote 2
    venta2 = Venta(id=2, cliente_id=1, almacen_id=1, vendedor_id=1, fecha=datetime(2023, 2, 10), total=50, tipo_pago='credito')
    vd2 = VentaDetalle(venta_id=2, presentacion_id=2, cantidad=10, precio_unitario=5, lote_id=2) # 10 * 5 = 50
    db.session.add_all([venta2, vd2])

    # Venta 3: Contiene productos de AMBOS lotes
    venta3 = Venta(id=3, cliente_id=1, almacen_id=1, vendedor_id=1, fecha=datetime(2023, 2, 20), total=200, tipo_pago='credito')
    vd3_1 = VentaDetalle(venta_id=3, presentacion_id=1, cantidad=10, precio_unitario=15, lote_id=1) # 150 (del Lote 1)
    vd3_2 = VentaDetalle(venta_id=3, presentacion_id=2, cantidad=10, precio_unitario=5, lote_id=2)  # 50 (del Lote 2)
    db.session.add_all([venta3, vd3_1, vd3_2])

    # Pagos
    pago1 = Pago(venta_id=1, monto=100, fecha=datetime(2023, 1, 20), metodo_pago='efectivo')
    pago3 = Pago(venta_id=3, monto=50, fecha=datetime(2023, 2, 25), metodo_pago='transferencia')
    db.session.add_all([pago1, pago3])

    # Gasto
    gasto1 = Gasto(id=1, descripcion='Transporte', monto=80, fecha=datetime(2023, 2, 1), categoria='logistica', almacen_id=1)
    db.session.add(gasto1)

    db.session.commit()

def get_auth_token(client):
    """Helper to get a valid JWT token."""
    # Assuming you have a user with id=1 and password='password123' from setup_test_data
    res = client.post('/auth', json={'username': 'testuser', 'password': 'password123'})
    return json.loads(res.data)['access_token']


def test_resumen_financiero_sin_filtro(test_client):
    """Test financial summary without any filters."""
    token = get_auth_token(test_client)
    headers = {'Authorization': f'Bearer {token}'}
    response = test_client.get('/reportes/resumen-financiero', headers=headers)
    assert response.status_code == 200
    data = json.loads(response.data)

    # Total Ventas = 150 (V1) + 50 (V2) + 200 (V3) = 400
    # Total Pagado = 100 (P1) + 50 (P3) = 150
    # Total Deuda = 400 - 150 = 250
    # Total Gastos = 80
    assert Decimal(data['total_ventas']) == Decimal('400.00')
    assert Decimal(data['total_pagado']) == Decimal('150.00')
    assert Decimal(data['total_deuda']) == Decimal('250.00')
    assert Decimal(data['total_gastos']) == Decimal('80.00')
    assert data['numero_ventas'] == 3
    assert data['numero_gastos'] == 1

def test_resumen_financiero_con_filtro_lote1(test_client):
    """Test financial summary filtered by Lote 1."""
    token = get_auth_token(test_client)
    headers = {'Authorization': f'Bearer {token}'}
    response = test_client.get('/reportes/resumen-financiero?lote_id=1', headers=headers)
    assert response.status_code == 200
    data = json.loads(response.data)

    # Ventas que contienen productos del Lote 1: Venta 1 y Venta 3
    # Total Ventas (solo lineas de Lote 1) = 150 (de V1) + 150 (de V3) = 300
    # Deuda se calcula por la factura completa: Deuda V1 (150-100=50) + Deuda V3 (200-50=150) = 200
    # Pagos derivados = 300 - 200 = 100
    # Gastos se filtran por lote (gasto1 no tiene lote_id, por lo que es 0.00)
    assert Decimal(data['total_ventas']) == Decimal('300.00')
    assert Decimal(data['total_pagado']) == Decimal('100.00')
    assert Decimal(data['total_deuda']) == Decimal('200.00')
    assert Decimal(data['total_gastos']) == Decimal('0.00')
    assert data['numero_ventas'] == 2 # Venta 1 y Venta 3

def test_resumen_financiero_con_filtro_lote2(test_client):
    """Test financial summary filtered by Lote 2."""
    token = get_auth_token(test_client)
    headers = {'Authorization': f'Bearer {token}'}
    response = test_client.get('/reportes/resumen-financiero?lote_id=2', headers=headers)
    assert response.status_code == 200
    data = json.loads(response.data)

    # Ventas que contienen productos del Lote 2: Venta 2 y Venta 3
    # Total Ventas (solo lineas de Lote 2) = 50 (de V2) + 50 (de V3) = 100
    # Deuda se calcula por la factura completa: Deuda V2 (50-0=50) + Deuda V3 (200-50=150) = 200
    # Pagos derivados = 100 - 200 -> 0.00
    # Gastos se filtran por lote (gasto1 no tiene lote_id, por lo que es 0.00)
    assert Decimal(data['total_ventas']) == Decimal('100.00')
    assert Decimal(data['total_pagado']) == Decimal('0.00')
    assert Decimal(data['total_deuda']) == Decimal('200.00')
    assert Decimal(data['total_gastos']) == Decimal('0.00')
    assert data['numero_ventas'] == 2 # Venta 2 y Venta 3
