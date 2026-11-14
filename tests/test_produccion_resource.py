import json
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from app import app, db
from models import (
    Almacen,
    ComponenteReceta,
    Inventario,
    Lote,
    PresentacionProducto,
    Producto,
    Receta,
    Users,
)


@pytest.fixture(scope='module')
def test_client():
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "JWT_SECRET_KEY": "test-secret-key"
    })
    with app.test_client() as testing_client:
        with app.app_context():
            db.create_all()
            yield testing_client
            db.session.remove()
            db.drop_all()

@pytest.fixture(scope='function')
def init_database(test_client):
    db.create_all()
    user = Users(id=1, username='testuser', password='password', rol='gerente', almacen_id=1)
    almacen = Almacen(id=1, nombre='Almacen Principal')
    db.session.add_all([user, almacen])
    p_carbon = Producto(id=1, nombre='Carbon Vegetal', precio_compra=100)
    p_briquetas = Producto(id=2, nombre='Briqueta', precio_compra=50)
    p_insumos = Producto(id=3, nombre='Insumos Varios', precio_compra=10)
    db.session.add_all([p_carbon, p_briquetas, p_insumos])
    pr_carbon_bruto = PresentacionProducto(id=1, producto_id=1, nombre='Carbon Bruto a Granel', capacidad_kg=1, tipo='bruto', precio_venta=0)
    pr_briqueta_granel = PresentacionProducto(id=2, producto_id=2, nombre='Briqueta a Granel', capacidad_kg=1, tipo='briqueta', precio_venta=0)
    pr_saco = PresentacionProducto(id=3, producto_id=3, nombre='Saco de 20kg', capacidad_kg=0.1, tipo='insumo', precio_venta=2)
    pr_carbon_ensacado = PresentacionProducto(id=4, producto_id=1, nombre='Carbon Ensacado 20kg', capacidad_kg=20, tipo='procesado', precio_venta=50)
    db.session.add_all([pr_carbon_bruto, pr_briqueta_granel, pr_saco, pr_carbon_ensacado])
    lote_carbon = Lote(id=1, producto_id=1, cantidad_disponible_kg=1000, descripcion='Lote Carbon A')
    lote_briquetas = Lote(id=2, producto_id=2, cantidad_disponible_kg=200, descripcion='Lote Briquetas B')
    db.session.add_all([lote_carbon, lote_briquetas])
    inv_carbon = Inventario(presentacion_id=1, almacen_id=1, lote_id=1, cantidad=1000)
    inv_briquetas = Inventario(presentacion_id=2, almacen_id=1, lote_id=2, cantidad=200)
    inv_sacos = Inventario(presentacion_id=3, almacen_id=1, lote_id=None, cantidad=500)
    db.session.add_all([inv_carbon, inv_briquetas, inv_sacos])
    receta = Receta(id=1, presentacion_id=4, nombre='Receta Carbon Ensacado 20kg')
    comp_carbon = ComponenteReceta(receta_id=1, componente_presentacion_id=1, cantidad_necesaria=18, tipo_consumo='materia_prima')
    comp_briqueta = ComponenteReceta(receta_id=1, componente_presentacion_id=2, cantidad_necesaria=2, tipo_consumo='materia_prima')
    comp_saco = ComponenteReceta(receta_id=1, componente_presentacion_id=3, cantidad_necesaria=1, tipo_consumo='insumo')
    db.session.add_all([receta, comp_carbon, comp_briqueta, comp_saco])
    db.session.commit()
    access_token = create_access_token(identity=str(user.id), additional_claims={"rol": user.rol, "almacen_id": user.almacen_id})
    yield access_token
    db.session.remove()
    db.drop_all()

def test_produccion_exitosa_creando_lote_nuevo(test_client, init_database):
    """Prueba una producción exitosa que crea un nuevo lote de destino."""
    token = init_database
    headers = {'Authorization': f'Bearer {token}'}
    payload = {
        "almacen_id": 1,
        "presentacion_id": 4,
        "cantidad_a_producir": 10,
        "lotes_seleccionados": [
            {"componente_presentacion_id": 1, "lote_id": 1},
            {"componente_presentacion_id": 2, "lote_id": 2}
        ]
    }
    response = test_client.post('/produccion', data=json.dumps(payload), headers=headers, content_type='application/json')
    assert response.status_code == 201
    lote_carbon = Lote.query.get(1)
    assert lote_carbon.cantidad_disponible_kg == pytest.approx(Decimal('820'))
    inv_final = Inventario.query.filter_by(presentacion_id=4, almacen_id=1).first()
    assert inv_final is not None
    assert inv_final.cantidad == 10

def test_produccion_exitosa_a_lote_existente(test_client, init_database):
    """Prueba una producción que consolida el resultado en un lote existente."""
    token = init_database
    headers = {'Authorization': f'Bearer {token}'}
    # Crear un lote de destino preexistente para el producto final
    lote_destino = Lote(id=10, producto_id=1, cantidad_disponible_kg=500, es_produccion=True)
    inv_destino = Inventario(presentacion_id=4, almacen_id=1, lote_id=10, cantidad=25) # 500kg / 20kg/u = 25 unidades
    db.session.add_all([lote_destino, inv_destino])
    db.session.commit()

    payload = {
        "almacen_id": 1,
        "presentacion_id": 4,
        "cantidad_a_producir": 10,
        "lotes_seleccionados": [
            {"componente_presentacion_id": 1, "lote_id": 1},
            {"componente_presentacion_id": 2, "lote_id": 2}
        ],
        "lote_destino_id": 10
    }
    response = test_client.post('/produccion', data=json.dumps(payload), headers=headers, content_type='application/json')
    assert response.status_code == 201
    # Verificar que la cantidad se añadió al lote de destino
    inv_destino_actualizado = Inventario.query.filter_by(lote_id=10).first()
    # Cantidad inicial 25u + 10u = 35u
    assert inv_destino_actualizado.cantidad == 35

def test_produccion_a_lote_destino_invalido(test_client, init_database):
    """Prueba una producción que falla si el lote de destino no existe o es incorrecto."""
    token = init_database
    headers = {'Authorization': f'Bearer {token}'}
    # Crear un lote de destino preexistente para un producto diferente
    lote_incorrecto = Lote(id=11, producto_id=2, cantidad_disponible_kg=500, es_produccion=True)
    db.session.add(lote_incorrecto)
    db.session.commit()

    payload_no_existe = {
        "almacen_id": 1,
        "presentacion_id": 4,
        "cantidad_a_producir": 10,
        "lotes_seleccionados": [{"componente_presentacion_id": 1, "lote_id": 1}, {"componente_presentacion_id": 2, "lote_id": 2}],
        "lote_destino_id": 999 # ID no existe
    }
    response = test_client.post('/produccion', data=json.dumps(payload_no_existe), headers=headers, content_type='application/json')
    assert response.status_code == 400
    assert "no existe" in response.get_json()['error']

    payload_presentacion_incorrecta = {
        "almacen_id": 1,
        "presentacion_id": 4,
        "cantidad_a_producir": 10,
        "lotes_seleccionados": [{"componente_presentacion_id": 1, "lote_id": 1}, {"componente_presentacion_id": 2, "lote_id": 2}],
        "lote_destino_id": 11 # Lote de briquetas, no de carbón
    }
    response = test_client.post('/produccion', data=json.dumps(payload_presentacion_incorrecta), headers=headers, content_type='application/json')
    assert response.status_code == 400
    assert "es para una presentación diferente" in response.get_json()['error']

def test_produccion_stock_materia_prima_insuficiente(test_client, init_database):
    """Prueba una producción que falla por falta de materia prima."""
    token = init_database
    headers = {'Authorization': f'Bearer {token}'}
    payload = {
        "almacen_id": 1,
        "presentacion_id": 4,
        "cantidad_a_producir": 100, # Se necesitan 1800kg de carbón, solo hay 1000
        "lotes_seleccionados": [
            {"componente_presentacion_id": 1, "lote_id": 1},
            {"componente_presentacion_id": 2, "lote_id": 2}
        ]
    }
    response = test_client.post('/produccion', data=json.dumps(payload), headers=headers, content_type='application/json')
    assert response.status_code == 400
    assert "Stock en KG insuficiente" in response.get_json()['error']

def test_produccion_stock_insumo_insuficiente(test_client, init_database):
    token = init_database
    headers = {'Authorization': f'Bearer {token}'}
    payload = {
        "almacen_id": 1,
        "presentacion_id": 4,
        "cantidad_a_producir": 600,
        "lotes_seleccionados": [
            {"componente_presentacion_id": 1, "lote_id": 1},
            {"componente_presentacion_id": 2, "lote_id": 2}
        ]
    }
    response = test_client.post('/produccion', data=json.dumps(payload), headers=headers, content_type='application/json')
    assert response.status_code == 400
    assert "Stock de insumo insuficiente" in response.get_json()['error']

def test_produccion_sin_seleccion_lote(test_client, init_database):
    token = init_database
    headers = {'Authorization': f'Bearer {token}'}
    payload = {
        "almacen_id": 1,
        "presentacion_id": 4,
        "cantidad_a_producir": 10,
        "lotes_seleccionados": [
            {"componente_presentacion_id": 2, "lote_id": 2}
        ]
    }
    response = test_client.post('/produccion', data=json.dumps(payload), headers=headers, content_type='application/json')
    assert response.status_code == 400
    assert "No se especificó un lote para la materia prima" in response.get_json()['error']
