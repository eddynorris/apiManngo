from datetime import datetime
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from extensions import db
from models import Almacen, Inventario, Lote, Movimiento, PresentacionProducto, Producto, Users


def get_auth_token(client, username, password):
    """Obtiene un token JWT para un usuario."""
    response = client.post('/auth', json={'username': username, 'password': password})
    return response.get_json()['access_token']

@pytest.fixture(scope='function')
def setup_inventario_data(test_client):
    """
    Configura datos de prueba para inventario antes de cada test.
    Crea un admin y un usuario normal, además de productos, presentaciones, lotes,
    y un almacén.
    """
    # Limpiar datos existentes para asegurar un estado limpio
    db.session.query(Movimiento).delete()
    db.session.query(Inventario).delete()
    db.session.query(PresentacionProducto).delete()
    db.session.query(Producto).delete()
    db.session.query(Lote).delete()
    db.session.query(Almacen).delete()
    db.session.query(Users).delete()
    db.session.commit()

    # Crear usuarios
    admin_user = Users(username='admin_inv', password=generate_password_hash('adminpass'), rol='admin')
    user_almacen1 = Users(username='user_alm1_inv', password=generate_password_hash('userpass'), rol='usuario')
    user_almacen2 = Users(username='user_alm2_inv', password=generate_password_hash('userpass'), rol='usuario')
    db.session.add_all([admin_user, user_almacen1, user_almacen2])
    db.session.commit()

    # Crear almacenes
    almacen1 = Almacen(nombre='Almacen Test 1', direccion='Dir 1', ciudad='Ciudad 1')
    almacen2 = Almacen(nombre='Almacen Test 2', direccion='Dir 2', ciudad='Ciudad 2')
    db.session.add_all([almacen1, almacen2])
    db.session.commit()

    admin_user.almacen_id = almacen1.id # Asignar a admin un almacén para pruebas de permisos
    user_almacen1.almacen_id = almacen1.id
    user_almacen2.almacen_id = almacen2.id
    db.session.commit()

    # Crear productos y presentaciones
    producto1 = Producto(nombre='Prod Inventario 1', descripcion='Desc 1', precio_compra=Decimal('10.00'))
    producto2 = Producto(nombre='Prod Inventario 2', descripcion='Desc 2', precio_compra=Decimal('15.00'))
    db.session.add_all([producto1, producto2])
    db.session.commit()

    presentacion1_prod1 = PresentacionProducto(producto_id=producto1.id, nombre='Saco 20kg A', capacidad_kg=Decimal('20.00'), tipo='procesado', precio_venta=Decimal('25.00'))
    presentacion2_prod1 = PresentacionProducto(producto_id=producto1.id, nombre='Bolsa 5kg B', capacidad_kg=Decimal('5.00'), tipo='detalle', precio_venta=Decimal('7.00'))
    presentacion1_prod2 = PresentacionProducto(producto_id=producto2.id, nombre='Saco 25kg C', capacidad_kg=Decimal('25.00'), tipo='procesado', precio_venta=Decimal('35.00'))
    db.session.add_all([presentacion1_prod1, presentacion2_prod1, presentacion1_prod2])
    db.session.commit()

    # Crear lotes
    lote1 = Lote(producto_id=producto1.id, descripcion='Lote Inv A', peso_humedo_kg=Decimal('5000.00'), cantidad_disponible_kg=Decimal('5000.00'), fecha_ingreso=datetime(2023, 1, 1))
    lote2 = Lote(producto_id=producto2.id, descripcion='Lote Inv B', peso_humedo_kg=Decimal('5000.00'), cantidad_disponible_kg=Decimal('5000.00'), fecha_ingreso=datetime(2023, 1, 15))
    db.session.add_all([lote1, lote2])
    db.session.commit()

    return {
        'admin_user': admin_user,
        'user_almacen1': user_almacen1,
        'user_almacen2': user_almacen2,
        'almacen1': almacen1,
        'almacen2': almacen2,
        'producto1': producto1,
        'producto2': producto2,
        'presentacion1_prod1': presentacion1_prod1,
        'presentacion2_prod1': presentacion2_prod1,
        'presentacion1_prod2': presentacion1_prod2,
        'lote1': lote1,
        'lote2': lote2,
    }

class TestInventarioResource:

    def test_post_single_inventario_success(self, test_client, setup_inventario_data):
        """
        Prueba la creación exitosa de un único registro de inventario.
        """
        admin_token = get_auth_token(test_client, 'admin_inv', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        data = {
            "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
            "almacen_id": setup_inventario_data['almacen1'].id,
            "cantidad": 100,
            "stock_minimo": 10,
            "lote_id": setup_inventario_data['lote1'].id
        }

        initial_lote_cantidad = setup_inventario_data['lote1'].cantidad_disponible_kg
        expected_kg_deducted = Decimal('100') * Decimal('20.00') # cantidad * capacidad_kg

        response = test_client.post('/inventarios', json=data, headers=headers)
        if response.status_code != 201:
            print(f"DEBUG: Status Code: {response.status_code}")
            print(f"DEBUG: Response JSON: {response.get_json()}")
        assert response.status_code == 201
        response_data = response.get_json()

        assert isinstance(response_data, list) # Asegurar que es una lista
        assert len(response_data) == 1 # Asegurar que es una lista de un elemento

        assert response_data[0]['presentacion_id'] == data['presentacion_id']
        assert response_data[0]['almacen_id'] == data['almacen_id']
        assert Decimal(response_data[0]['cantidad']) == Decimal(data['cantidad'])
        assert response_data[0]['stock_minimo'] == data['stock_minimo']
        assert response_data[0]['lote_id'] == data['lote_id']

        # Verificar que se creó el inventario en la DB
        inventario_db = Inventario.query.filter_by(
            presentacion_id=data['presentacion_id'],
            almacen_id=data['almacen_id']
        ).first()
        assert inventario_db is not None
        assert inventario_db.cantidad == data['cantidad']

        # Verificar que el movimiento fue registrado
        movimiento_db = Movimiento.query.filter_by(
            presentacion_id=data['presentacion_id'],
            lote_id=data['lote_id'],
            cantidad=data['cantidad'],
            tipo='entrada'
        ).first()
        assert movimiento_db is not None

        # Verificar que la cantidad disponible del lote se actualizó correctamente
        lote_after = Lote.query.get(setup_inventario_data['lote1'].id)
        assert lote_after.cantidad_disponible_kg == (initial_lote_cantidad - expected_kg_deducted)

    def test_post_multiple_inventarios_success(self, test_client, setup_inventario_data):
        """
        Prueba la creación exitosa de múltiples registros de inventario en una sola solicitud.
        """
        admin_token = get_auth_token(test_client, 'admin_inv', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        inventarios_data = [
            {
                "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
                "almacen_id": setup_inventario_data['almacen1'].id,
                "cantidad": 50,
                "lote_id": setup_inventario_data['lote1'].id
            },
            {
                "presentacion_id": setup_inventario_data['presentacion2_prod1'].id,
                "almacen_id": setup_inventario_data['almacen1'].id,
                "cantidad": 20,
                "stock_minimo": 5,
                "lote_id": setup_inventario_data['lote1'].id
            },
            {
                "presentacion_id": setup_inventario_data['presentacion1_prod2'].id,
                "almacen_id": setup_inventario_data['almacen2'].id,
                "cantidad": 30,
                "lote_id": setup_inventario_data['lote2'].id
            }
        ]

        initial_lote1_cantidad = Lote.query.get(setup_inventario_data['lote1'].id).cantidad_disponible_kg
        initial_lote2_cantidad = Lote.query.get(setup_inventario_data['lote2'].id).cantidad_disponible_kg

        response = test_client.post('/inventarios', json=inventarios_data, headers=headers)
        assert response.status_code == 201
        response_data = response.get_json()
        assert len(response_data) == 3

        # Verificar cada inventario
        for item in inventarios_data:
            inventario_db = Inventario.query.filter_by(
                presentacion_id=item['presentacion_id'],
                almacen_id=item['almacen_id']
            ).first()
            assert inventario_db is not None
            assert inventario_db.cantidad == item['cantidad']

            movimiento_db = Movimiento.query.filter_by(
                presentacion_id=item['presentacion_id'],
                cantidad=item['cantidad'],
                tipo='entrada'
            ).first()
            assert movimiento_db is not None

        # Verificar que la cantidad disponible de los lotes se actualizó correctamente
        lote1_after = Lote.query.get(setup_inventario_data['lote1'].id)
        lote2_after = Lote.query.get(setup_inventario_data['lote2'].id)

        # Cálculo esperado para lote1
        expected_deduction_lote1_p1 = Decimal('50') * setup_inventario_data['presentacion1_prod1'].capacidad_kg # 50 * 20kg = 1000kg
        expected_deduction_lote1_p2 = Decimal('20') * setup_inventario_data['presentacion2_prod1'].capacidad_kg # 20 * 5kg = 100kg
        assert lote1_after.cantidad_disponible_kg == (initial_lote1_cantidad - expected_deduction_lote1_p1 - expected_deduction_lote1_p2)

        # Cálculo esperado para lote2
        expected_deduction_lote2_p1 = Decimal('30') * setup_inventario_data['presentacion1_prod2'].capacidad_kg # 30 * 25kg = 750kg
        assert lote2_after.cantidad_disponible_kg == (initial_lote2_cantidad - expected_deduction_lote2_p1)


    def test_post_inventario_missing_required_field(self, test_client, setup_inventario_data):
        """
        Prueba la creación de inventario con un campo requerido faltante.
        """
        admin_token = get_auth_token(test_client, 'admin_inv', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        data = {
            "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
            "almacen_id": setup_inventario_data['almacen1'].id,
            # "cantidad": 100, # Faltante
            "stock_minimo": 10
        }

        response = test_client.post('/inventarios', json=data, headers=headers)
        assert response.status_code == 400
        assert "Campo requerido 'cantidad' faltante en un item" in response.get_json()['error']

        # Verificar que no se creó ningún inventario
        assert Inventario.query.count() == 0

    def test_post_inventario_invalid_numeric_value(self, test_client, setup_inventario_data):
        """
        Prueba la creación de inventario con un valor numérico inválido.
        """
        admin_token = get_auth_token(test_client, 'admin_inv', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        data = {
            "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
            "almacen_id": setup_inventario_data['almacen1'].id,
            "cantidad": -50, # Valor negativo
            "stock_minimo": 10
        }

        response = test_client.post('/inventarios', json=data, headers=headers)
        assert response.status_code == 400
        assert "La cantidad no puede ser negativa en un item" in response.get_json()['error']
        assert Inventario.query.count() == 0

        data_invalid_type = {
            "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
            "almacen_id": setup_inventario_data['almacen1'].id,
            "cantidad": "cien", # Tipo inválido
            "stock_minimo": 10
        }
        response = test_client.post('/inventarios', json=data_invalid_type, headers=headers)
        assert response.status_code == 400
        assert "Valores numéricos inválidos en un item" in response.get_json()['error']
        assert Inventario.query.count() == 0

    def test_post_inventario_invalid_relation_id(self, test_client, setup_inventario_data):
        """
        Prueba la creación de inventario con un ID de relación (presentación, almacén, lote) inválido.
        """
        admin_token = get_auth_token(test_client, 'admin_inv', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        # Presentacion ID no existente
        data = {
            "presentacion_id": 9999,
            "almacen_id": setup_inventario_data['almacen1'].id,
            "cantidad": 10,
            "lote_id": setup_inventario_data['lote1'].id
        }
        response = test_client.post('/inventarios', json=data, headers=headers)
        assert response.status_code == 400
        assert "Relación inválida (ID no encontrado)" in response.get_json()['error']
        assert Inventario.query.count() == 0

        # Almacen ID no existente
        data = {
            "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
            "almacen_id": 9999,
            "cantidad": 10,
            "lote_id": setup_inventario_data['lote1'].id
        }
        response = test_client.post('/inventarios', json=data, headers=headers)
        assert response.status_code == 400
        assert "Relación inválida (ID no encontrado)" in response.get_json()['error']
        assert Inventario.query.count() == 0

        # Lote ID no existente (si se provee)
        data = {
            "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
            "almacen_id": setup_inventario_data['almacen1'].id,
            "cantidad": 10,
            "lote_id": 9999
        }
        response = test_client.post('/inventarios', json=data, headers=headers)
        assert response.status_code == 400
        assert "Relación inválida (ID no encontrado)" in response.get_json()['error']
        assert Inventario.query.count() == 0

    def test_post_inventario_uniqueness_conflict(self, test_client, setup_inventario_data):
        """
        Prueba la creación de inventario con un conflicto de unicidad.
        """
        admin_token = get_auth_token(test_client, 'admin_inv', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        # Crear el primer inventario
        initial_data = {
            "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
            "almacen_id": setup_inventario_data['almacen1'].id,
            "cantidad": 10,
            "stock_minimo": 5
        }
        test_client.post('/inventarios', json=initial_data, headers=headers)

        # Intentar crear el mismo inventario de nuevo
        response = test_client.post('/inventarios', json=initial_data, headers=headers)
        assert response.status_code == 409
        assert "Ya existe un registro de inventario para esta presentación en este almacén" in response.get_json()['error']
        assert Inventario.query.count() == 1 # Solo el primero debe existir

    def test_post_inventario_insufficient_lote_stock(self, test_client, setup_inventario_data):
        """
        Prueba que no se pueda crear inventario si el lote no tiene stock suficiente.
        """
        admin_token = get_auth_token(test_client, 'admin_inv', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        lote = Lote.query.get(setup_inventario_data['lote1'].id)
        # Poner una cantidad disponible muy baja para la prueba
        lote.cantidad_disponible_kg = Decimal('10.00')
        db.session.commit()

        # Intentar crear inventario que requiere más kg de los disponibles en el lote
        # Presentación 1_prod1 tiene capacidad_kg = 20.00
        # Cantidad 1 * 20kg = 20kg, lo cual es más que 10kg disponibles
        data = {
            "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
            "almacen_id": setup_inventario_data['almacen1'].id,
            "cantidad": 1,
            "lote_id": setup_inventario_data['lote1'].id
        }
        response = test_client.post('/inventarios', json=data, headers=headers)
        assert response.status_code == 400
        assert "Stock insuficiente en el lote" in response.get_json()['error']
        assert Inventario.query.count() == 0 # No debe crearse

        # Verificar que la cantidad del lote no cambió
        lote_after = Lote.query.get(setup_inventario_data['lote1'].id)
        assert lote_after.cantidad_disponible_kg == Decimal('10.00')


    def test_post_inventario_permission_denied_for_other_almacen(self, test_client, setup_inventario_data):
        """
        Prueba que un usuario normal no pueda crear inventario para un almacén al que no pertenece.
        """
        user_almacen1_token = get_auth_token(test_client, 'user_alm1_inv', 'userpass')
        headers = {'Authorization': f'Bearer {user_almacen1_token}'}

        # Intentar crear inventario en almacen2 como user_almacen1 (pertenece a almacen1)
        data = {
            "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
            "almacen_id": setup_inventario_data['almacen2'].id, # Almacén incorrecto
            "cantidad": 10
        }
        response = test_client.post('/inventarios', json=data, headers=headers)
        assert response.status_code == 403
        assert "No tiene permiso para modificar este almacén" in response.get_json()['message']
        assert Inventario.query.count() == 0

    def test_post_multiple_inventarios_rollback_on_failure(self, test_client, setup_inventario_data):
        """
        Prueba que si un elemento en una solicitud masiva falla, se haga rollback de toda la transacción.
        """
        admin_token = get_auth_token(test_client, 'admin_inv', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        # Datos para 3 inventarios, el segundo tiene un presentacion_id inválido
        inventarios_data = [
            {
                "presentacion_id": setup_inventario_data['presentacion1_prod1'].id,
                "almacen_id": setup_inventario_data['almacen1'].id,
                "cantidad": 10,
                "lote_id": setup_inventario_data['lote1'].id
            },
            {
                "presentacion_id": 9999, # Este causará un error
                "almacen_id": setup_inventario_data['almacen1'].id,
                "cantidad": 5
            },
            {
                "presentacion_id": setup_inventario_data['presentacion2_prod1'].id,
                "almacen_id": setup_inventario_data['almacen1'].id,
                "cantidad": 20,
                "lote_id": setup_inventario_data['lote1'].id
            }
        ]

        initial_lote1_cantidad = Lote.query.get(setup_inventario_data['lote1'].id).cantidad_disponible_kg

        response = test_client.post('/inventarios', json=inventarios_data, headers=headers)
        assert response.status_code == 400
        assert "Relación inválida (ID no encontrado)" in response.get_json()['error']

        # Verificar que NO se creó ningún inventario
        assert Inventario.query.count() == 0
        assert Movimiento.query.count() == 0 # Ni movimientos

        # Verificar que la cantidad del lote no se modificó
        lote_after = Lote.query.get(setup_inventario_data['lote1'].id)
        assert lote_after.cantidad_disponible_kg == initial_lote1_cantidad
