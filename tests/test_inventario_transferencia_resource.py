import json

import pytest
from flask_jwt_extended import create_access_token

from app import app, db
from models import Almacen, Inventario, Lote, Movimiento, PresentacionProducto, Producto, Users


@pytest.fixture(scope='module')
def test_client():
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "JWT_SECRET_KEY": "test-secret-key-transfer"
    })
    with app.test_client() as testing_client:
        with app.app_context():
            db.create_all()
            yield testing_client
            db.session.remove()
            db.drop_all()

@pytest.fixture(scope='function')
def init_database_for_transfer(test_client):
    db.create_all()
    # Entidades base
    user = Users(id=1, username='testuser', password='password', rol='admin') # Admin para no tener restricciones
    almacen_origen = Almacen(id=1, nombre='Planta')
    almacen_destino = Almacen(id=2, nombre='Tienda Centro')
    producto = Producto(id=1, nombre='Carbon', precio_compra=100)
    presentacion = PresentacionProducto(id=1, producto_id=1, nombre='Saco 20kg', capacidad_kg=20, tipo='procesado', precio_venta=50)
    lote = Lote(id=1, producto_id=1, cantidad_disponible_kg=1000)
    db.session.add_all([user, almacen_origen, almacen_destino, producto, presentacion, lote])

    # Inventario inicial en el almacén de origen
    inv_origen = Inventario(presentacion_id=1, almacen_id=1, lote_id=1, cantidad=50) # 50 sacos
    db.session.add(inv_origen)
    db.session.commit()

    access_token = create_access_token(identity=str(user.id), additional_claims={"rol": user.rol})
    yield access_token

    db.drop_all()

def test_transferencia_exitosa(test_client, init_database_for_transfer):
    """Prueba una transferencia de inventario exitosa."""
    token = init_database_for_transfer
    headers = {'Authorization': f'Bearer {token}'}
    payload = {
        "almacen_origen_id": 1,
        "almacen_destino_id": 2,
        "transferencias": [
            {
                "presentacion_id": 1,
                "lote_id": 1,
                "cantidad": 10
            }
        ]
    }
    response = test_client.post('/inventario/transferir', data=json.dumps(payload), headers=headers, content_type='application/json')

    assert response.status_code == 200
    assert response.get_json()['mensaje'] == "Transferencia realizada con éxito"

    # Verificar inventarios
    inv_origen = Inventario.query.filter_by(almacen_id=1, lote_id=1).first()
    assert inv_origen.cantidad == 40 # 50 - 10

    inv_destino = Inventario.query.filter_by(almacen_id=2, lote_id=1).first()
    assert inv_destino is not None
    assert inv_destino.cantidad == 10

    # Verificar movimientos
    movimientos = Movimiento.query.order_by(Movimiento.id.asc()).all()
    assert len(movimientos) == 2
    assert movimientos[0].tipo == 'salida'
    assert movimientos[0].cantidad == 10
    assert movimientos[0].tipo_operacion == 'transferencia'
    assert movimientos[1].tipo == 'entrada'
    assert movimientos[1].cantidad == 10
    assert movimientos[1].tipo_operacion == 'transferencia'

def test_transferencia_stock_insuficiente(test_client, init_database_for_transfer):
    """Prueba una transferencia que falla por stock insuficiente."""
    token = init_database_for_transfer
    headers = {'Authorization': f'Bearer {token}'}
    payload = {
        "almacen_origen_id": 1,
        "almacen_destino_id": 2,
        "transferencias": [
            {
                "presentacion_id": 1,
                "lote_id": 1,
                "cantidad": 100 # Se intentan transferir 100, pero solo hay 50
            }
        ]
    }
    response = test_client.post('/inventario/transferir', data=json.dumps(payload), headers=headers, content_type='application/json')

    assert response.status_code == 400
    assert "Stock insuficiente" in response.get_json()['error']

def test_transferencia_mismo_almacen(test_client, init_database_for_transfer):
    """Prueba una transferencia que falla por tener mismo origen y destino."""
    token = init_database_for_transfer
    headers = {'Authorization': f'Bearer {token}'}
    payload = {
        "almacen_origen_id": 1,
        "almacen_destino_id": 1, # Mismo almacén
        "transferencias": [
            {
                "presentacion_id": 1,
                "lote_id": 1,
                "cantidad": 10
            }
        ]
    }
    response = test_client.post('/inventario/transferir', data=json.dumps(payload), headers=headers, content_type='application/json')

    assert response.status_code == 400
    assert "no pueden ser el mismo" in response.get_json()['error']
