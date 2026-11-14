
import os
from datetime import datetime
from decimal import Decimal

import pytest

# Set env vars before importing app to avoid production config loading
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['FLASK_ENV'] = 'testing'

from flask_jwt_extended import create_access_token

from app import app
from extensions import db
from models import (
    Almacen,
    Cliente,
    Inventario,
    Lote,
    Pago,
    PresentacionProducto,
    Producto,
    Users,
)


@pytest.fixture
def test_app():
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False
    })

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

@pytest.fixture
def client(test_app):
    return test_app.test_client()

@pytest.fixture
def auth_headers(test_app):
    with test_app.app_context():
        # Crear usuario y token
        user = Users(username="testuser", password="password", rol="admin")
        db.session.add(user)
        db.session.commit()
        access_token = create_access_token(identity=str(user.id), additional_claims={"rol": "admin", "sub": str(user.id)})
        return {"Authorization": f"Bearer {access_token}"}

@pytest.fixture
def setup_data(test_app):
    with test_app.app_context():
        almacen = Almacen(nombre="Almacen Test")
        cliente = Cliente(nombre="Cliente Test")
        # Usuario vendedor
        vendedor = Users.query.first() # El creado en auth_headers o crear uno nuevo
        if not vendedor:
             vendedor = Users(username="vendedor", password="password", rol="usuario")
             db.session.add(vendedor)

        producto = Producto(nombre="Producto Test", precio_compra=10)
        presentacion = PresentacionProducto(
            producto=producto, nombre="Pres Test", capacidad_kg=1, tipo="bruto", precio_venta=100
        )
        lote = Lote(descripcion="Lote Test", cantidad_disponible_kg=100, peso_humedo_kg=100, peso_seco_kg=100, producto=producto)
        inventario = Inventario(
            presentacion=presentacion, almacen=almacen, lote=lote, cantidad=10
        )

        db.session.add_all([almacen, cliente, producto, presentacion, lote, inventario])
        db.session.commit()

        return {
            "almacen_id": almacen.id,
            "cliente_id": cliente.id,
            "presentacion_id": presentacion.id,
            "lote_id": lote.id,
            "vendedor_id": vendedor.id
        }

def test_auto_pago_creation(client, auth_headers, setup_data):
    """Prueba que se crea un pago automáticamente al crear una venta con estado 'pagado'"""

    venta_data = {
        "cliente_id": setup_data["cliente_id"],
        "almacen_id": setup_data["almacen_id"],
        "tipo_pago": "contado",
        "estado_pago": "pagado",
        "metodo_pago": "yape_plin", # Envio explícito
        "fecha": datetime.now().isoformat(),
        "detalles": [
            {
                "presentacion_id": setup_data["presentacion_id"],
                "cantidad": 1,
                "precio_unitario": 100
            }
        ]
    }

    response = client.post('/ventas', json=venta_data, headers=auth_headers)
    assert response.status_code == 201, f"Error creating venta: {response.get_json()}"

    data = response.get_json()
    venta_id = data['id']

    # Verificar que el estado de pago en la respuesta es 'pagado'
    assert data['estado_pago'] == 'pagado'

    # Verificar en la base de datos que existe el pago
    with client.application.app_context():
        pago = Pago.query.filter_by(venta_id=venta_id).first()
        assert pago is not None, "El pago no fue creado automáticamente"
        assert pago.monto == Decimal('100.00')
        assert pago.metodo_pago == 'yape_plin'

def test_auto_pago_update(client, auth_headers, setup_data):
    """Prueba que se crea un pago al actualizar una venta a 'pagado'"""

    # 1. Crear venta 'pendiente'
    venta_data = {
        "cliente_id": setup_data["cliente_id"],
        "almacen_id": setup_data["almacen_id"],
        "tipo_pago": "credito",
        "estado_pago": "pendiente",
        "fecha": datetime.now().isoformat(),
        "detalles": [
            {
                "presentacion_id": setup_data["presentacion_id"],
                "cantidad": 1,
                "precio_unitario": 100
            }
        ]
    }
    response = client.post('/ventas', json=venta_data, headers=auth_headers)
    assert response.status_code == 201
    venta_id = response.get_json()['id']

    # 2. Actualizar a 'pagado'
    update_data = {
        "cliente_id": setup_data["cliente_id"], # Required for PUT
        "almacen_id": setup_data["almacen_id"], # Required for PUT
        "tipo_pago": "credito",
        "estado_pago": "pagado",
        "metodo_pago": "transferencia",
        "fecha": datetime.now().isoformat(),
        "detalles": [
            {
                "presentacion_id": setup_data["presentacion_id"],
                "cantidad": 1,
                "precio_unitario": 100
            }
        ]
    }
    response = client.put(f'/ventas/{venta_id}', json=update_data, headers=auth_headers)
    assert response.status_code == 200, f"Error updating venta: {response.get_json()}"

    # 3. Verificar pago
    with client.application.app_context():
        pago = Pago.query.filter_by(venta_id=venta_id).first()
        assert pago is not None, "El pago no fue creado automáticamente tras update"
        assert pago.monto == Decimal('100.00')
        assert pago.metodo_pago == 'transferencia'
