import json
from datetime import datetime, timedelta
from decimal import Decimal
import pytest

from models import Users, Producto, PresentacionProducto, Lote, Almacen, Inventario, Movimiento
from extensions import db
from werkzeug.security import generate_password_hash

def get_auth_token(client, username, password):
    """Obtiene un token JWT para un usuario."""
    response = client.post('/auth', json={'username': username, 'password': password})
    return response.get_json()['access_token']

@pytest.fixture(scope='function')
def setup_batch_update_data(test_client):
    """
    Configura datos de prueba para los tests de actualización múltiple de inventario.
    Crea usuarios, productos, presentaciones, lotes, almacenes e inventarios iniciales.
    """
    # Limpiar datos existentes
    db.session.query(Movimiento).delete()
    db.session.query(Inventario).delete()
    db.session.query(PresentacionProducto).delete()
    db.session.query(Producto).delete()
    db.session.query(Lote).delete()
    db.session.query(Almacen).delete()
    db.session.query(Users).delete()
    db.session.commit()

    # Crear usuarios
    admin_user = Users(username='admin_batch', password=generate_password_hash('adminpass'), rol='admin')
    user_almacen1 = Users(username='user_alm1_batch', password=generate_password_hash('userpass'), rol='usuario')
    user_almacen2 = Users(username='user_alm2_batch', password=generate_password_hash('userpass'), rol='usuario')
    db.session.add_all([admin_user, user_almacen1, user_almacen2])
    db.session.commit()

    # Crear almacenes
    almacen1 = Almacen(nombre='Almacen Batch 1', direccion='Dir 1', ciudad='Ciudad 1')
    almacen2 = Almacen(nombre='Almacen Batch 2', direccion='Dir 2', ciudad='Ciudad 2')
    db.session.add_all([almacen1, almacen2])
    db.session.commit()

    # Asignar usuarios a almacenes
    admin_user.almacen_id = almacen1.id
    user_almacen1.almacen_id = almacen1.id
    user_almacen2.almacen_id = almacen2.id
    db.session.commit()

    # Crear productos
    producto1 = Producto(nombre='Producto Batch 1', descripcion='Desc 1', precio_compra=Decimal('10.00'))
    producto2 = Producto(nombre='Producto Batch 2', descripcion='Desc 2', precio_compra=Decimal('15.00'))
    db.session.add_all([producto1, producto2])
    db.session.commit()

    # Crear presentaciones
    presentacion1 = PresentacionProducto(
        producto_id=producto1.id, 
        nombre='Saco 20kg', 
        capacidad_kg=Decimal('20.00'), 
        tipo='procesado', 
        precio_venta=Decimal('25.00')
    )
    presentacion2 = PresentacionProducto(
        producto_id=producto1.id, 
        nombre='Bolsa 5kg', 
        capacidad_kg=Decimal('5.00'), 
        tipo='detalle', 
        precio_venta=Decimal('7.00')
    )
    presentacion3 = PresentacionProducto(
        producto_id=producto2.id, 
        nombre='Saco 25kg', 
        capacidad_kg=Decimal('25.00'), 
        tipo='procesado', 
        precio_venta=Decimal('35.00')
    )
    db.session.add_all([presentacion1, presentacion2, presentacion3])
    db.session.commit()

    # Crear lotes
    lote1 = Lote(
        producto_id=producto1.id, 
        descripcion='Lote Batch 1', 
        peso_humedo_kg=Decimal('10000.00'), 
        cantidad_disponible_kg=Decimal('10000.00'),
        fecha_ingreso=datetime(2023, 1, 1)
    )
    lote2 = Lote(
        producto_id=producto2.id, 
        descripcion='Lote Batch 2', 
        peso_humedo_kg=Decimal('8000.00'), 
        cantidad_disponible_kg=Decimal('8000.00'),
        fecha_ingreso=datetime(2023, 1, 15)
    )
    db.session.add_all([lote1, lote2])
    db.session.commit()

    # Crear inventarios iniciales para testing
    inventario1 = Inventario(
        presentacion_id=presentacion1.id,
        almacen_id=almacen1.id,
        lote_id=lote1.id,
        cantidad=50,
        stock_minimo=10
    )
    inventario2 = Inventario(
        presentacion_id=presentacion2.id,
        almacen_id=almacen1.id,
        lote_id=lote1.id,
        cantidad=100,
        stock_minimo=20
    )
    inventario3 = Inventario(
        presentacion_id=presentacion3.id,
        almacen_id=almacen2.id,
        lote_id=lote2.id,
        cantidad=30,
        stock_minimo=5
    )
    db.session.add_all([inventario1, inventario2, inventario3])
    db.session.commit()

    return {
        'admin_user': admin_user,
        'user_almacen1': user_almacen1,
        'user_almacen2': user_almacen2,
        'almacen1': almacen1,
        'almacen2': almacen2,
        'producto1': producto1,
        'producto2': producto2,
        'presentacion1': presentacion1,
        'presentacion2': presentacion2,
        'presentacion3': presentacion3,
        'lote1': lote1,
        'lote2': lote2,
        'inventario1': inventario1,
        'inventario2': inventario2,
        'inventario3': inventario3,
    }

class TestInventarioBatchUpdate:
    """Tests para la funcionalidad de actualización múltiple de inventarios."""

    def test_put_single_inventario_success(self, test_client, setup_batch_update_data):
        """
        Prueba la actualización individual de un inventario (comportamiento original).
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        inventario_id = setup_batch_update_data['inventario1'].id
        update_data = {
            "cantidad": 75,
            "stock_minimo": 15,
            "motivo": "Ajuste individual"
        }

        # Obtener estado inicial
        initial_inventario = Inventario.query.get(inventario_id)
        initial_cantidad = initial_inventario.cantidad
        initial_movimientos = Movimiento.query.count()

        response = test_client.put(f'/inventarios/{inventario_id}', json=update_data, headers=headers)
        
        assert response.status_code == 200
        response_data = response.get_json()
        
        # Verificar respuesta
        assert response_data['id'] == inventario_id
        assert Decimal(response_data['cantidad']) == Decimal(75)
        assert response_data['stock_minimo'] == 15

        # Verificar en BD
        updated_inventario = Inventario.query.get(inventario_id)
        assert updated_inventario.cantidad == 75
        assert updated_inventario.stock_minimo == 15

        # Verificar que se creó un movimiento
        final_movimientos = Movimiento.query.count()
        assert final_movimientos == initial_movimientos + 1

        # Verificar el movimiento creado
        nuevo_movimiento = Movimiento.query.filter_by(
            presentacion_id=updated_inventario.presentacion_id,
            tipo='entrada'  # 75 - 50 = 25 (entrada)
        ).first()
        assert nuevo_movimiento is not None
        assert nuevo_movimiento.cantidad == 25
        assert nuevo_movimiento.motivo == "Ajuste individual"

    def test_put_multiple_inventarios_success(self, test_client, setup_batch_update_data):
        """
        Prueba la actualización múltiple exitosa de varios inventarios.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        # Datos para actualizar múltiples inventarios
        updates_data = [
            {
                "id": setup_batch_update_data['inventario1'].id,
                "cantidad": 80,
                "stock_minimo": 12,
                "motivo": "Ajuste por inventario físico"
            },
            {
                "id": setup_batch_update_data['inventario2'].id,
                "cantidad": 120,
                "stock_minimo": 25,
                "motivo": "Reposición de stock"
            },
            {
                "id": setup_batch_update_data['inventario3'].id,
                "cantidad": 25,
                "stock_minimo": 8,
                "motivo": "Ajuste por merma"
            }
        ]

        # Obtener estado inicial
        initial_movimientos = Movimiento.query.count()

        response = test_client.put('/inventarios', json=updates_data, headers=headers)
        
        assert response.status_code == 200
        response_data = response.get_json()
        
        # Verificar respuesta
        assert len(response_data) == 3
        
        # Verificar cada inventario actualizado
        for i, update in enumerate(updates_data):
            inventario_id = update['id']
            response_item = next(item for item in response_data if item['id'] == inventario_id)
            
            assert Decimal(response_item['cantidad']) == Decimal(update['cantidad'])
            assert response_item['stock_minimo'] == update['stock_minimo']
            
            # Verificar en BD
            updated_inventario = Inventario.query.get(inventario_id)
            assert updated_inventario.cantidad == update['cantidad']
            assert updated_inventario.stock_minimo == update['stock_minimo']

        # Verificar que se crearon movimientos
        final_movimientos = Movimiento.query.count()
        assert final_movimientos == initial_movimientos + 3  # Un movimiento por cada actualización

    def test_put_multiple_inventarios_missing_id(self, test_client, setup_batch_update_data):
        """
        Prueba que falla cuando un item no tiene ID en actualización múltiple.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        updates_data = [
            {
                "id": setup_batch_update_data['inventario1'].id,
                "cantidad": 80,
                "stock_minimo": 12
            },
            {
                # "id": setup_batch_update_data['inventario2'].id,  # ID faltante
                "cantidad": 120,
                "stock_minimo": 25
            }
        ]

        response = test_client.put('/inventarios', json=updates_data, headers=headers)
        
        assert response.status_code == 400
        response_data = response.get_json()
        assert "Cada item debe tener un 'id' para actualización múltiple" in response_data['error']

        # Verificar que ningún inventario fue actualizado
        inventario1 = Inventario.query.get(setup_batch_update_data['inventario1'].id)
        assert inventario1.cantidad == 50  # Valor original

    def test_put_multiple_inventarios_invalid_id(self, test_client, setup_batch_update_data):
        """
        Prueba que falla cuando se usa un ID de inventario inválido.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        updates_data = [
            {
                "id": setup_batch_update_data['inventario1'].id,
                "cantidad": 80,
                "stock_minimo": 12
            },
            {
                "id": 9999,  # ID inexistente
                "cantidad": 120,
                "stock_minimo": 25
            }
        ]

        response = test_client.put('/inventarios', json=updates_data, headers=headers)
        
        assert response.status_code == 404
        # Verificar que ningún inventario fue actualizado (rollback)
        inventario1 = Inventario.query.get(setup_batch_update_data['inventario1'].id)
        assert inventario1.cantidad == 50  # Valor original

    def test_put_multiple_inventarios_permission_denied(self, test_client, setup_batch_update_data):
        """
        Prueba que falla cuando un usuario no tiene permisos para algún inventario.
        """
        # Usuario de almacén 1 intentando actualizar inventario de almacén 2
        user_token = get_auth_token(test_client, 'user_alm1_batch', 'userpass')
        headers = {'Authorization': f'Bearer {user_token}'}

        updates_data = [
            {
                "id": setup_batch_update_data['inventario1'].id,  # Almacén 1 (permitido)
                "cantidad": 80,
                "stock_minimo": 12
            },
            {
                "id": setup_batch_update_data['inventario3'].id,  # Almacén 2 (no permitido)
                "cantidad": 35,
                "stock_minimo": 8
            }
        ]

        response = test_client.put('/inventarios', json=updates_data, headers=headers)
        
        assert response.status_code == 403
        response_data = response.get_json()
        assert "No tiene permisos para modificar inventario" in response_data['error']

        # Verificar que ningún inventario fue actualizado (rollback)
        inventario1 = Inventario.query.get(setup_batch_update_data['inventario1'].id)
        inventario3 = Inventario.query.get(setup_batch_update_data['inventario3'].id)
        assert inventario1.cantidad == 50  # Valor original
        assert inventario3.cantidad == 30  # Valor original

    def test_put_multiple_inventarios_immutable_field_error(self, test_client, setup_batch_update_data):
        """
        Prueba que falla cuando se intenta modificar un campo inmutable.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        updates_data = [
            {
                "id": setup_batch_update_data['inventario1'].id,
                "cantidad": 80,
                "stock_minimo": 12
            },
            {
                "id": setup_batch_update_data['inventario2'].id,
                "presentacion_id": setup_batch_update_data['presentacion3'].id,  # Campo inmutable
                "cantidad": 120,
                "stock_minimo": 25
            }
        ]

        response = test_client.put('/inventarios', json=updates_data, headers=headers)
        
        assert response.status_code == 400
        response_data = response.get_json()
        assert "Campo inmutable 'presentacion_id' no puede modificarse" in response_data['error']

        # Verificar que ningún inventario fue actualizado (rollback)
        inventario1 = Inventario.query.get(setup_batch_update_data['inventario1'].id)
        inventario2 = Inventario.query.get(setup_batch_update_data['inventario2'].id)
        assert inventario1.cantidad == 50  # Valor original
        assert inventario2.cantidad == 100  # Valor original

    def test_put_multiple_inventarios_negative_quantity_error(self, test_client, setup_batch_update_data):
        """
        Prueba que falla cuando se intenta establecer una cantidad negativa.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        updates_data = [
            {
                "id": setup_batch_update_data['inventario1'].id,
                "cantidad": 80,
                "stock_minimo": 12
            },
            {
                "id": setup_batch_update_data['inventario2'].id,
                "cantidad": -10,  # Cantidad negativa
                "stock_minimo": 25
            }
        ]

        response = test_client.put('/inventarios', json=updates_data, headers=headers)
        
        assert response.status_code == 400
        response_data = response.get_json()
        assert "La cantidad no puede ser negativa" in response_data['error']

        # Verificar que ningún inventario fue actualizado (rollback)
        inventario1 = Inventario.query.get(setup_batch_update_data['inventario1'].id)
        inventario2 = Inventario.query.get(setup_batch_update_data['inventario2'].id)
        assert inventario1.cantidad == 50  # Valor original
        assert inventario2.cantidad == 100  # Valor original

    def test_put_multiple_inventarios_insufficient_lote_stock(self, test_client, setup_batch_update_data):
        """
        Prueba que falla cuando no hay suficiente stock en el lote para el aumento.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        # Reducir el stock disponible en el lote para forzar error
        lote1 = Lote.query.get(setup_batch_update_data['lote1'].id)
        lote1.cantidad_disponible_kg = Decimal('50.00')  # Stock muy bajo
        db.session.commit()

        updates_data = [
            {
                "id": setup_batch_update_data['inventario1'].id,
                "cantidad": 80,  # 80 * 20kg = 1600kg, pero solo hay 50kg disponibles
                "stock_minimo": 12
            }
        ]

        response = test_client.put('/inventarios', json=updates_data, headers=headers)
        
        assert response.status_code == 400
        response_data = response.get_json()
        assert "Stock insuficiente en el lote" in response_data['error']

        # Verificar que el inventario no fue actualizado
        inventario1 = Inventario.query.get(setup_batch_update_data['inventario1'].id)
        assert inventario1.cantidad == 50  # Valor original

    def test_put_multiple_inventarios_not_list_error(self, test_client, setup_batch_update_data):
        """
        Prueba que falla cuando se envía un objeto en lugar de una lista para actualización múltiple.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        # Enviar objeto en lugar de lista
        update_data = {
            "id": setup_batch_update_data['inventario1'].id,
            "cantidad": 80,
            "stock_minimo": 12
        }

        response = test_client.put('/inventarios', json=update_data, headers=headers)
        
        assert response.status_code == 400
        response_data = response.get_json()
        assert "Para actualización múltiple se esperaba una lista de objetos" in response_data['error']

    def test_put_multiple_inventarios_empty_list_error(self, test_client, setup_batch_update_data):
        """
        Prueba que falla cuando se envía una lista vacía.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        response = test_client.put('/inventarios', json=[], headers=headers)
        
        assert response.status_code == 400
        response_data = response.get_json()
        assert "Lista vacía para actualización múltiple" in response_data['error']

    def test_put_multiple_inventarios_with_lote_change(self, test_client, setup_batch_update_data):
        """
        Prueba actualización múltiple con cambio de lote.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        # Obtener estado inicial del lote2
        initial_lote2_cantidad = setup_batch_update_data['lote2'].cantidad_disponible_kg

        updates_data = [
            {
                "id": setup_batch_update_data['inventario1'].id,
                "cantidad": 60,  # Aumentar de 50 a 60
                "lote_id": setup_batch_update_data['lote2'].id,  # Cambiar de lote1 a lote2
                "stock_minimo": 15,
                "motivo": "Cambio de lote"
            }
        ]

        response = test_client.put('/inventarios', json=updates_data, headers=headers)
        
        assert response.status_code == 200
        response_data = response.get_json()
        
        # Verificar actualización
        assert Decimal(response_data[0]['cantidad']) == Decimal(60)
        assert response_data[0]['lote_id'] == setup_batch_update_data['lote2'].id

        # Verificar que se descontó del lote2
        lote2_after = Lote.query.get(setup_batch_update_data['lote2'].id)
        expected_deduction = Decimal('10') * Decimal('20.00')  # 10 unidades * 20kg = 200kg
        assert lote2_after.cantidad_disponible_kg == initial_lote2_cantidad - expected_deduction

        # Verificar que se creó el movimiento
        movimiento = Movimiento.query.filter_by(
            presentacion_id=setup_batch_update_data['presentacion1'].id,
            tipo='entrada',
            cantidad=10
        ).first()
        assert movimiento is not None
        assert movimiento.lote_id == setup_batch_update_data['lote2'].id
        assert movimiento.motivo == "Cambio de lote"