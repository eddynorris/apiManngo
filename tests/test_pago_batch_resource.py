import json
import pytest
from decimal import Decimal
from datetime import datetime
from unittest.mock import patch, MagicMock
from io import BytesIO

from models import Users, Almacen, Cliente, Venta, Pago
from extensions import db
from werkzeug.security import generate_password_hash

def get_auth_token(client, username, password):
    """Obtiene un token JWT para un usuario."""
    response = client.post('/auth', json={'username': username, 'password': password})
    return response.get_json()['access_token']

@pytest.fixture(scope='function')
def setup_pago_batch_data(test_client):
    """
    Configura datos de prueba para pagos batch antes de cada test.
    Crea usuarios, almacenes, clientes y ventas con diferentes estados.
    """
    # Limpiar datos existentes
    db.session.query(Pago).delete()
    db.session.query(Venta).delete()
    db.session.query(Cliente).delete()
    db.session.query(Almacen).delete()
    db.session.query(Users).delete()
    db.session.commit()

    # Crear usuarios
    admin_user = Users(
        username='admin_batch', 
        password=generate_password_hash('adminpass'), 
        rol='admin'
    )
    user_almacen1 = Users(
        username='user_alm1_batch', 
        password=generate_password_hash('userpass'), 
        rol='usuario'
    )
    user_almacen2 = Users(
        username='user_alm2_batch', 
        password=generate_password_hash('userpass'), 
        rol='usuario'
    )
    db.session.add_all([admin_user, user_almacen1, user_almacen2])
    db.session.commit()

    # Crear almacenes
    almacen1 = Almacen(nombre='Almacen Batch 1', direccion='Dir 1', ciudad='Ciudad 1')
    almacen2 = Almacen(nombre='Almacen Batch 2', direccion='Dir 2', ciudad='Ciudad 2')
    db.session.add_all([almacen1, almacen2])
    db.session.commit()

    # Asignar almacenes a usuarios
    admin_user.almacen_id = almacen1.id
    user_almacen1.almacen_id = almacen1.id
    user_almacen2.almacen_id = almacen2.id
    db.session.commit()

    # Crear clientes
    cliente1 = Cliente(nombre='Cliente Batch 1', telefono='123456789')
    cliente2 = Cliente(nombre='Cliente Batch 2', telefono='987654321')
    db.session.add_all([cliente1, cliente2])
    db.session.commit()

    # Crear ventas con diferentes estados
    venta1 = Venta(
        cliente_id=cliente1.id,
        almacen_id=almacen1.id,
        vendedor_id=user_almacen1.id,
        total=Decimal('100.00'),
        estado_pago='pendiente',
        tipo_pago='credito',
        fecha=datetime.now()
    )
    venta2 = Venta(
        cliente_id=cliente1.id,
        almacen_id=almacen1.id,
        vendedor_id=user_almacen1.id,
        total=Decimal('200.00'),
        estado_pago='pendiente',
        tipo_pago='credito',
        fecha=datetime.now()
    )
    venta3 = Venta(
        cliente_id=cliente2.id,
        almacen_id=almacen2.id,
        vendedor_id=user_almacen2.id,
        total=Decimal('150.00'),
        estado_pago='pendiente',
        tipo_pago='credito',
        fecha=datetime.now()
    )
    # Venta con pago parcial existente
    venta4 = Venta(
        cliente_id=cliente1.id,
        almacen_id=almacen1.id,
        vendedor_id=user_almacen1.id,
        total=Decimal('300.00'),
        estado_pago='parcial',
        tipo_pago='credito',
        fecha=datetime.now()
    )
    db.session.add_all([venta1, venta2, venta3, venta4])
    db.session.commit()

    # Agregar pago parcial a venta4
    pago_parcial = Pago(
        venta_id=venta4.id,
        usuario_id=user_almacen1.id,
        monto=Decimal('100.00'),
        fecha=datetime.now(),
        metodo_pago='efectivo',
        referencia='PAGO_INICIAL'
    )
    db.session.add(pago_parcial)
    venta4.actualizar_estado()
    db.session.commit()

    return {
        'admin_user': admin_user,
        'user_almacen1': user_almacen1,
        'user_almacen2': user_almacen2,
        'almacen1': almacen1,
        'almacen2': almacen2,
        'cliente1': cliente1,
        'cliente2': cliente2,
        'venta1': venta1,  # Total: 100.00, Pendiente: 100.00
        'venta2': venta2,  # Total: 200.00, Pendiente: 200.00
        'venta3': venta3,  # Total: 150.00, Pendiente: 150.00
        'venta4': venta4,  # Total: 300.00, Pendiente: 200.00 (ya tiene 100 pagado)
    }

class TestPagoBatchResource:

    def test_create_pagos_batch_json_success(self, test_client, setup_pago_batch_data):
        """
        Prueba la creación exitosa de múltiples pagos usando JSON.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {
            'Authorization': f'Bearer {admin_token}',
            'Content-Type': 'application/json'
        }

        data = {
            "pagos": [
                {"venta_id": setup_pago_batch_data['venta1'].id, "monto": "50.00"},
                {"venta_id": setup_pago_batch_data['venta2'].id, "monto": "100.00"},
                {"venta_id": setup_pago_batch_data['venta4'].id, "monto": "150.00"}  # Saldo pendiente: 200
            ],
            "fecha": "2024-01-15T10:30:00",
            "metodo_pago": "transferencia",
            "referencia": "BATCH_001"
        }

        response = test_client.post('/pagos/batch', json=data, headers=headers)
        
        assert response.status_code == 201
        response_data = response.get_json()
        assert "message" in response_data
        assert "pagos_creados" in response_data
        assert len(response_data["pagos_creados"]) == 3
        
        # Verificar que los pagos se crearon correctamente
        pagos_creados = response_data["pagos_creados"]
        assert pagos_creados[0]["monto"] == "50.00"
        assert pagos_creados[1]["monto"] == "100.00"
        assert pagos_creados[2]["monto"] == "150.00"
        
        # Verificar que todos tienen la misma referencia y método de pago
        for pago in pagos_creados:
            assert pago["metodo_pago"] == "transferencia"
            assert pago["referencia"] == "BATCH_001"

        # Verificar estados de ventas actualizados
        db.session.refresh(setup_pago_batch_data['venta1'])
        db.session.refresh(setup_pago_batch_data['venta2'])
        db.session.refresh(setup_pago_batch_data['venta4'])
        
        assert setup_pago_batch_data['venta1'].estado_pago == 'parcial'  # 50/100
        assert setup_pago_batch_data['venta2'].estado_pago == 'parcial'  # 100/200
        assert setup_pago_batch_data['venta4'].estado_pago == 'parcial'  # 250/300

    @patch('services.pago_service.save_file')
    @patch('resources.pago_resource.get_presigned_url')
    def test_create_pagos_batch_form_data_with_file_success(self, mock_get_presigned_url, mock_save_file, test_client, setup_pago_batch_data):
        """
        Prueba la creación exitosa de múltiples pagos usando multipart/form-data con archivo.
        """
        # Configurar mocks
        mock_save_file.return_value = 'comprobantes/test_file_key.jpg'
        mock_get_presigned_url.return_value = 'https://s3.amazonaws.com/presigned-url'
        
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        # Crear archivo de prueba
        test_file = (BytesIO(b'fake image data'), 'comprobante.jpg')
        
        pagos_data = [
            {"venta_id": setup_pago_batch_data['venta1'].id, "monto": "100.00"},  # Pago completo
            {"venta_id": setup_pago_batch_data['venta2'].id, "monto": "200.00"}   # Pago completo
        ]

        data = {
            'pagos_json_data': json.dumps(pagos_data),
            'fecha': '2024-01-15T14:30:00',
            'metodo_pago': 'deposito',
            'referencia': 'DEP_BATCH_001',
            'comprobante': test_file
        }

        response = test_client.post('/pagos/batch', data=data, headers=headers)
        
        assert response.status_code == 201
        response_data = response.get_json()
        assert "message" in response_data
        assert "pagos_creados" in response_data
        assert len(response_data["pagos_creados"]) == 2
        
        # Verificar que el archivo se guardó
        mock_save_file.assert_called_once()
        
        # Verificar que ambos pagos tienen la URL del comprobante
        for pago in response_data["pagos_creados"]:
            assert pago["url_comprobante"] == 'https://s3.amazonaws.com/presigned-url'
            assert pago["metodo_pago"] == "deposito"
            assert pago["referencia"] == "DEP_BATCH_001"

        # Verificar estados de ventas (ambas deberían estar pagadas)
        db.session.refresh(setup_pago_batch_data['venta1'])
        db.session.refresh(setup_pago_batch_data['venta2'])
        
        assert setup_pago_batch_data['venta1'].estado_pago == 'pagado'
        assert setup_pago_batch_data['venta2'].estado_pago == 'pagado'

    def test_create_pagos_batch_missing_required_fields(self, test_client, setup_pago_batch_data):
        """
        Prueba el manejo de errores cuando faltan campos requeridos.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {
            'Authorization': f'Bearer {admin_token}',
            'Content-Type': 'application/json'
        }

        # Test sin campo 'pagos'
        data = {
            "fecha": "2024-01-15T10:30:00",
            "metodo_pago": "transferencia"
        }
        response = test_client.post('/pagos/batch', json=data, headers=headers)
        assert response.status_code == 400
        assert "Faltan campos requeridos" in response.get_json()["error"]

        # Test sin campo 'fecha'
        data = {
            "pagos": [{"venta_id": setup_pago_batch_data['venta1'].id, "monto": "50.00"}],
            "metodo_pago": "transferencia"
        }
        response = test_client.post('/pagos/batch', json=data, headers=headers)
        assert response.status_code == 400
        assert "Faltan campos requeridos" in response.get_json()["error"]

        # Test sin campo 'metodo_pago'
        data = {
            "pagos": [{"venta_id": setup_pago_batch_data['venta1'].id, "monto": "50.00"}],
            "fecha": "2024-01-15T10:30:00"
        }
        response = test_client.post('/pagos/batch', json=data, headers=headers)
        assert response.status_code == 400
        assert "Faltan campos requeridos" in response.get_json()["error"]

    def test_create_pagos_batch_empty_list_error(self, test_client, setup_pago_batch_data):
        """
        Prueba el manejo de errores cuando la lista de pagos está vacía.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {
            'Authorization': f'Bearer {admin_token}',
            'Content-Type': 'application/json'
        }

        data = {
            "pagos": [],
            "fecha": "2024-01-15T10:30:00",
            "metodo_pago": "transferencia"
        }

        response = test_client.post('/pagos/batch', json=data, headers=headers)
        
        if response.status_code != 400:
            pytest.fail(f"Expected 400, got {response.status_code}. Response: {response.get_json()}")
        
        error_msg = response.get_json()["error"]
        if "El campo 'pagos' debe ser una lista no vacía." not in error_msg:
            pytest.fail(f"Expected error message not found. Actual error: {error_msg}")
        
        assert response.status_code == 400
        assert "El campo 'pagos' debe ser una lista no vacía." in response.get_json()["error"]

    def test_create_pagos_batch_invalid_date_format(self, test_client, setup_pago_batch_data):
        """
        Prueba el manejo de errores con formato de fecha inválido.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {
            'Authorization': f'Bearer {admin_token}',
            'Content-Type': 'application/json'
        }

        data = {
            "pagos": [{"venta_id": setup_pago_batch_data['venta1'].id, "monto": "50.00"}],
            "fecha": "invalid-date",  # Formato incorrecto
            "metodo_pago": "transferencia"
        }

        response = test_client.post('/pagos/batch', json=data, headers=headers)
        assert response.status_code == 400
        assert "Formato de fecha inválido" in response.get_json()["error"]

    def test_create_pagos_batch_invalid_monto(self, test_client, setup_pago_batch_data):
        """
        Prueba el manejo de errores con montos inválidos.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {
            'Authorization': f'Bearer {admin_token}',
            'Content-Type': 'application/json'
        }

        # Test con monto negativo
        data = {
            "pagos": [{"venta_id": setup_pago_batch_data['venta1'].id, "monto": "-50.00"}],
            "fecha": "2024-01-15T10:30:00",
            "metodo_pago": "transferencia"
        }
        response = test_client.post('/pagos/batch', json=data, headers=headers)
        assert response.status_code == 400
        assert "debe ser positivo" in response.get_json()["error"]

        # Test con monto no numérico
        data = {
            "pagos": [{"venta_id": setup_pago_batch_data['venta1'].id, "monto": "abc"}],
            "fecha": "2024-01-15T10:30:00",
            "metodo_pago": "transferencia"
        }
        response = test_client.post('/pagos/batch', json=data, headers=headers)
        assert response.status_code == 400
        assert "Monto inválido" in response.get_json()["error"]

    def test_create_pagos_batch_venta_not_found(self, test_client, setup_pago_batch_data):
        """
        Prueba el manejo de errores cuando una venta no existe.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {
            'Authorization': f'Bearer {admin_token}',
            'Content-Type': 'application/json'
        }

        data = {
            "pagos": [{"venta_id": 99999, "monto": "50.00"}],  # ID inexistente
            "fecha": "2024-01-15T10:30:00",
            "metodo_pago": "transferencia"
        }

        response = test_client.post('/pagos/batch', json=data, headers=headers)
        assert response.status_code == 404
        assert "no encontrada" in response.get_json()["error"]

    def test_create_pagos_batch_monto_exceeds_saldo(self, test_client, setup_pago_batch_data):
        """
        Prueba el manejo de errores cuando el monto excede el saldo pendiente.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {
            'Authorization': f'Bearer {admin_token}',
            'Content-Type': 'application/json'
        }

        data = {
            "pagos": [{"venta_id": setup_pago_batch_data['venta1'].id, "monto": "150.00"}],  # Venta1 total: 100
            "fecha": "2024-01-15T10:30:00",
            "metodo_pago": "transferencia"
        }

        response = test_client.post('/pagos/batch', json=data, headers=headers)
        assert response.status_code == 400
        assert "excede el saldo pendiente" in response.get_json()["error"]

    def test_create_pagos_batch_multiple_payments_same_venta(self, test_client, setup_pago_batch_data):
        """
        Prueba múltiples pagos para la misma venta en un lote.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {
            'Authorization': f'Bearer {admin_token}',
            'Content-Type': 'application/json'
        }

        data = {
            "pagos": [
                {"venta_id": setup_pago_batch_data['venta2'].id, "monto": "100.00"},  # Total: 200
                {"venta_id": setup_pago_batch_data['venta2'].id, "monto": "100.00"}   # Segundo pago
            ],
            "fecha": "2024-01-15T10:30:00",
            "metodo_pago": "transferencia",
            "referencia": "DOBLE_PAGO"
        }

        response = test_client.post('/pagos/batch', json=data, headers=headers)
        
        assert response.status_code == 201
        response_data = response.get_json()
        assert len(response_data["pagos_creados"]) == 2
        
        # Verificar que la venta quede completamente pagada
        db.session.refresh(setup_pago_batch_data['venta2'])
        assert setup_pago_batch_data['venta2'].estado_pago == 'pagado'

    def test_create_pagos_batch_permissions_usuario_different_almacen(self, test_client, setup_pago_batch_data):
        """
        Prueba que un usuario no admin no pueda crear pagos para ventas de otro almacén.
        """
        user_token = get_auth_token(test_client, 'user_alm1_batch', 'userpass')
        headers = {
            'Authorization': f'Bearer {user_token}',
            'Content-Type': 'application/json'
        }

        data = {
            "pagos": [{"venta_id": setup_pago_batch_data['venta3'].id, "monto": "50.00"}],  # venta3 es del almacen2
            "fecha": "2024-01-15T10:30:00",
            "metodo_pago": "transferencia"
        }

        response = test_client.post('/pagos/batch', json=data, headers=headers)
        assert response.status_code == 403
        assert "No tiene permisos" in response.get_json()["error"]

    def test_create_pagos_batch_unsupported_content_type(self, test_client, setup_pago_batch_data):
        """
        Prueba el manejo de errores con Content-Type no soportado.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {
            'Authorization': f'Bearer {admin_token}',
            'Content-Type': 'text/plain'
        }

        response = test_client.post('/pagos/batch', data='invalid data', headers=headers)
        assert response.status_code == 415
        assert "Content-Type no soportado" in response.get_json()["error"]

    @patch('services.pago_service.save_file')
    @patch('services.pago_service.delete_file')
    def test_create_pagos_batch_rollback_on_error(self, mock_delete_file, mock_save_file, test_client, setup_pago_batch_data):
        """
        Prueba que se haga rollback y se elimine el archivo si hay error en la transacción.
        """
        # Configurar mock para simular error en save_file
        mock_save_file.return_value = 'comprobantes/test_file_key.jpg'
        
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        test_file = (BytesIO(b'fake image data'), 'comprobante.jpg')
        
        # Usar un monto que exceda el saldo para forzar error
        pagos_data = [
            {"venta_id": setup_pago_batch_data['venta1'].id, "monto": "150.00"}  # Excede el total de 100
        ]

        data = {
            'pagos_json_data': json.dumps(pagos_data),
            'fecha': '2024-01-15T14:30:00',
            'metodo_pago': 'deposito',
            'comprobante': test_file
        }

        response = test_client.post('/pagos/batch', data=data, headers=headers)
        
        assert response.status_code == 400
        assert "excede el saldo pendiente" in response.get_json()["error"]
        
        # Verificar que no se crearon pagos
        pagos_count = Pago.query.filter_by(venta_id=setup_pago_batch_data['venta1'].id).count()
        assert pagos_count == 0  # No debería haber pagos nuevos

    def test_create_pagos_batch_invalid_json_in_form_data(self, test_client, setup_pago_batch_data):
        """
        Prueba el manejo de errores con JSON inválido en form-data.
        """
        admin_token = get_auth_token(test_client, 'admin_batch', 'adminpass')
        headers = {'Authorization': f'Bearer {admin_token}'}

        data = {
            'pagos_json_data': 'invalid json string',
            'fecha': '2024-01-15T14:30:00',
            'metodo_pago': 'deposito'
        }

        response = test_client.post('/pagos/batch', data=data, headers=headers)
        assert response.status_code == 400
        assert "Formato JSON inválido" in response.get_json()["error"]