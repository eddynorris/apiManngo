from datetime import datetime
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from extensions import (
    db,  # Asegúrate de que db esté disponible, si no, se inyectará desde conftest.py
)
from models import Almacen, Inventario, Lote, Movimiento, PresentacionProducto, Producto, Users


def get_auth_token(client, username, password):
    """Obtiene un token JWT para un usuario."""
    response = client.post('/auth', json={'username': username, 'password': password})
    return response.get_json()['access_token']

@pytest.fixture(scope='function')
def setup_movimiento_data(test_client):
    """
    Configura datos de prueba para movimientos antes de cada test.
    Crea un admin y un usuario normal, además de productos, presentaciones, lotes,
    y movimientos para probar los filtros.
    """
    # Limpiar datos existentes para asegurar un estado limpio
    db.session.query(Movimiento).delete()
    db.session.query(Inventario).delete() # Se crea inventario con movimientos de entrada
    db.session.query(PresentacionProducto).delete()
    db.session.query(Producto).delete()
    db.session.query(Lote).delete()
    db.session.query(Almacen).delete()
    db.session.query(Users).delete()
    db.session.commit()

    # Crear usuarios
    admin_user = Users(username='admin_mov', password=generate_password_hash('adminpass'), rol='admin')
    user_almacen1 = Users(username='user_alm1', password=generate_password_hash('userpass'), rol='usuario')
    db.session.add_all([admin_user, user_almacen1])
    db.session.commit()

    # Crear almacenes (asumiendo que los usuarios tienen almacenes)
    almacen1 = Almacen(nombre='Almacen Principal', direccion='Calle Falsa 123', ciudad='Springfield')
    db.session.add(almacen1)
    db.session.commit() # Commit para obtener ID del almacén

    admin_user.almacen_id = almacen1.id
    user_almacen1.almacen_id = almacen1.id
    db.session.commit()

    # Crear productos y presentaciones
    producto1 = Producto(nombre='Carbón Vegetal', descripcion='Carbón de encina', precio_compra=Decimal('10.00'))
    producto2 = Producto(nombre='Carbón Premium', descripcion='Carbón de Quebracho', precio_compra=Decimal('15.00'))
    db.session.add_all([producto1, producto2])
    db.session.commit()

    presentacion1_prod1 = PresentacionProducto(producto_id=producto1.id, nombre='Saco 20kg', capacidad_kg=Decimal('20.00'), tipo='procesado', precio_venta=Decimal('25.00'))
    presentacion2_prod1 = PresentacionProducto(producto_id=producto1.id, nombre='Bolsa 5kg', capacidad_kg=Decimal('5.00'), tipo='detalle', precio_venta=Decimal('7.00'))
    presentacion1_prod2 = PresentacionProducto(producto_id=producto2.id, nombre='Saco 25kg', capacidad_kg=Decimal('25.00'), tipo='procesado', precio_venta=Decimal('35.00'))
    db.session.add_all([presentacion1_prod1, presentacion2_prod1, presentacion1_prod2])
    db.session.commit()

    # Crear lotes
    lote1 = Lote(producto_id=producto1.id, descripcion='Lote A', peso_humedo_kg=Decimal('1000.00'), cantidad_disponible_kg=Decimal('1000.00'), fecha_ingreso=datetime(2023, 1, 1))
    lote2 = Lote(producto_id=producto2.id, descripcion='Lote B', peso_humedo_kg=Decimal('500.00'), cantidad_disponible_kg=Decimal('500.00'), fecha_ingreso=datetime(2023, 1, 15))
    db.session.add_all([lote1, lote2])
    db.session.commit()

    # Crear movimientos de inventario
    movimiento1 = Movimiento(tipo='entrada', presentacion_id=presentacion1_prod1.id, lote_id=lote1.id, cantidad=10, fecha=datetime(2023, 1, 10), motivo='Carga inicial', usuario_id=admin_user.id)
    movimiento2 = Movimiento(tipo='salida', presentacion_id=presentacion1_prod1.id, lote_id=lote1.id, cantidad=2, fecha=datetime(2023, 1, 12), motivo='Venta', usuario_id=user_almacen1.id)
    movimiento3 = Movimiento(tipo='entrada', presentacion_id=presentacion2_prod1.id, lote_id=lote1.id, cantidad=20, fecha=datetime(2023, 1, 18), motivo='Recepción', usuario_id=admin_user.id)
    movimiento4 = Movimiento(tipo='entrada', presentacion_id=presentacion1_prod2.id, lote_id=lote2.id, cantidad=5, fecha=datetime(2023, 2, 1), motivo='Nueva producción', usuario_id=admin_user.id)
    movimiento5 = Movimiento(tipo='salida', presentacion_id=presentacion2_prod1.id, lote_id=lote1.id, cantidad=5, fecha=datetime(2023, 2, 5), motivo='Venta', usuario_id=user_almacen1.id)

    db.session.add_all([movimiento1, movimiento2, movimiento3, movimiento4, movimiento5])
    db.session.commit()

    return {
        'admin_user': admin_user,
        'user_almacen1': user_almacen1,
        'almacen1': almacen1,
        'producto1': producto1,
        'producto2': producto2,
        'presentacion1_prod1': presentacion1_prod1,
        'presentacion2_prod1': presentacion2_prod1,
        'presentacion1_prod2': presentacion1_prod2,
        'lote1': lote1,
        'lote2': lote2,
        'movimiento1': movimiento1,
        'movimiento2': movimiento2,
        'movimiento3': movimiento3,
        'movimiento4': movimiento4,
        'movimiento5': movimiento5,
    }

class TestMovimientoResource:

    def test_get_all_movimientos_as_admin(self, test_client, setup_movimiento_data):
        """
        Prueba obtener todos los movimientos como admin.
        """
        admin_token = get_auth_token(test_client, 'admin_mov', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        response = test_client.get('/movimientos', headers=headers)
        assert response.status_code == 200
        data = response.get_json()

        assert data['pagination']['total'] == 5 # Debe haber 5 movimientos en total
        assert len(data['data']) == 5

        # Verificar el campo total_kg para un movimiento específico
        movimiento1_res = next((m for m in data['data'] if m['id'] == setup_movimiento_data['movimiento1'].id), None)
        assert movimiento1_res is not None
        assert Decimal(movimiento1_res['cantidad']) == Decimal('10.00')
        assert Decimal(movimiento1_res['presentacion']['capacidad_kg']) == Decimal('20.00')
        assert Decimal(movimiento1_res['total_kg']) == Decimal('200.00') # 10 * 20kg

        movimiento3_res = next((m for m in data['data'] if m['id'] == setup_movimiento_data['movimiento3'].id), None)
        assert movimiento3_res is not None
        assert Decimal(movimiento3_res['cantidad']) == Decimal('20.00')
        assert Decimal(movimiento3_res['presentacion']['capacidad_kg']) == Decimal('5.00')
        assert Decimal(movimiento3_res['total_kg']) == Decimal('100.00') # 20 * 5kg

    def test_get_movimientos_by_type_entrada(self, test_client, setup_movimiento_data):
        """
        Prueba filtrar movimientos por tipo 'entrada'.
        """
        admin_token = get_auth_token(test_client, 'admin_mov', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        response = test_client.get('/movimientos?tipo=entrada', headers=headers)
        assert response.status_code == 200
        data = response.get_json()

        assert data['pagination']['total'] == 3
        for movimiento in data['data']:
            assert movimiento['tipo'] == 'entrada'

    def test_get_movimientos_by_type_salida(self, test_client, setup_movimiento_data):
        """
        Prueba filtrar movimientos por tipo 'salida'.
        """
        admin_token = get_auth_token(test_client, 'admin_mov', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        response = test_client.get('/movimientos?tipo=salida', headers=headers)
        assert response.status_code == 200
        data = response.get_json()

        assert data['pagination']['total'] == 2
        for movimiento in data['data']:
            assert movimiento['tipo'] == 'salida'

    def test_get_movimientos_by_fecha_range(self, test_client, setup_movimiento_data):
        """
        Prueba filtrar movimientos por rango de fechas.
        """
        admin_token = get_auth_token(test_client, 'admin_mov', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        # Rango que incluye movimiento1, movimiento2, movimiento3
        response = test_client.get('/movimientos?fecha_inicio=2023-01-01&fecha_fin=2023-01-31', headers=headers)
        assert response.status_code == 200
        data = response.get_json()

        assert data['pagination']['total'] == 3
        movimiento_ids = {m['id'] for m in data['data']}
        assert setup_movimiento_data['movimiento1'].id in movimiento_ids
        assert setup_movimiento_data['movimiento2'].id in movimiento_ids
        assert setup_movimiento_data['movimiento3'].id in movimiento_ids
        assert setup_movimiento_data['movimiento4'].id not in movimiento_ids # Es de febrero

    def test_get_movimientos_by_lote_id(self, test_client, setup_movimiento_data):
        """
        Prueba filtrar movimientos por lote_id.
        """
        admin_token = get_auth_token(test_client, 'admin_mov', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        lote1_id = setup_movimiento_data['lote1'].id
        response = test_client.get(f'/movimientos?lote_id={lote1_id}', headers=headers)
        assert response.status_code == 200
        data = response.get_json()

        assert data['pagination']['total'] == 4 # movimiento1, movimiento2, movimiento3, movimiento5
        movimiento_ids = {m['id'] for m in data['data']}
        assert setup_movimiento_data['movimiento1'].id in movimiento_ids
        assert setup_movimiento_data['movimiento2'].id in movimiento_ids
        assert setup_movimiento_data['movimiento3'].id in movimiento_ids
        assert setup_movimiento_data['movimiento5'].id in movimiento_ids
        assert setup_movimiento_data['movimiento4'].id not in movimiento_ids # Es de lote2

    def test_get_movimientos_by_presentacion_id(self, test_client, setup_movimiento_data):
        """
        Prueba filtrar movimientos por presentacion_id.
        """
        admin_token = get_auth_token(test_client, 'admin_mov', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        presentacion1_prod1_id = setup_movimiento_data['presentacion1_prod1'].id
        response = test_client.get(f'/movimientos?presentacion_id={presentacion1_prod1_id}', headers=headers)
        assert response.status_code == 200
        data = response.get_json()

        assert data['pagination']['total'] == 2 # movimiento1, movimiento2
        movimiento_ids = {m['id'] for m in data['data']}
        assert setup_movimiento_data['movimiento1'].id in movimiento_ids
        assert setup_movimiento_data['movimiento2'].id in movimiento_ids
        assert setup_movimiento_data['movimiento3'].id not in movimiento_ids # Otra presentacion

    def test_get_movimientos_combined_filters(self, test_client, setup_movimiento_data):
        """
        Prueba la combinación de varios filtros.
        Obtener entradas de presentacion1_prod1 en enero 2023.
        """
        admin_token = get_auth_token(test_client, 'admin_mov', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        presentacion1_prod1_id = setup_movimiento_data['presentacion1_prod1'].id
        response = test_client.get(
            f'/movimientos?tipo=entrada&presentacion_id={presentacion1_prod1_id}&fecha_inicio=2023-01-01&fecha_fin=2023-01-31',
            headers=headers
        )
        assert response.status_code == 200
        data = response.get_json()

        assert data['pagination']['total'] == 1 # Solo movimiento1
        movimiento_ids = {m['id'] for m in data['data']}
        assert setup_movimiento_data['movimiento1'].id in movimiento_ids
        assert Decimal(data['data'][0]['total_kg']) == Decimal('200.00')

    def test_get_movimiento_by_id(self, test_client, setup_movimiento_data):
        """
        Prueba obtener un movimiento específico por su ID.
        """
        admin_token = get_auth_token(test_client, 'admin_mov', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        movimiento_id = setup_movimiento_data['movimiento1'].id
        response = test_client.get(f'/movimientos/{movimiento_id}', headers=headers)
        assert response.status_code == 200
        data = response.get_json()

        assert data['id'] == movimiento_id
        assert Decimal(data['total_kg']) == Decimal('200.00')

    def test_get_movimientos_invalid_date_format(self, test_client, setup_movimiento_data):
        """
        Prueba que el endpoint maneje formatos de fecha inválidos.
        """
        admin_token = get_auth_token(test_client, 'admin_mov', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        response = test_client.get('/movimientos?fecha_inicio=2023/01/01', headers=headers)
        assert response.status_code == 400
        assert "Formato de fecha_inicio inválido" in response.get_json()['error']

        response = test_client.get('/movimientos?fecha_fin=invalid-date', headers=headers)
        assert response.status_code == 400
        assert "Formato de fecha_fin inválido" in response.get_json()['error']
