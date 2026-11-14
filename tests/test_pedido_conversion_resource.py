import json
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from models import (Users, Producto, PresentacionProducto, Lote, Almacen, 
                   Inventario, Movimiento, Cliente, Pedido, PedidoDetalle, 
                   Venta, VentaDetalle)
from extensions import db
from werkzeug.security import generate_password_hash

def get_auth_token(client, username, password):
    """Obtiene un token JWT para un usuario."""
    response = client.post('/auth', json={'username': username, 'password': password})
    return response.get_json()['access_token']

@pytest.fixture(scope='function')
def setup_pedido_conversion_data(test_client):
    """
    Configura datos de prueba para conversión de pedidos.
    Crea usuarios, almacenes, productos, presentaciones, inventario, clientes y pedidos.
    """
    # Limpiar datos existentes
    db.session.query(VentaDetalle).delete()
    db.session.query(Venta).delete()
    db.session.query(Movimiento).delete()
    db.session.query(PedidoDetalle).delete()
    db.session.query(Pedido).delete()
    db.session.query(Inventario).delete()
    db.session.query(PresentacionProducto).delete()
    db.session.query(Producto).delete()
    db.session.query(Lote).delete()
    db.session.query(Cliente).delete()
    db.session.query(Almacen).delete()
    db.session.query(Users).delete()
    db.session.commit()

    # Crear usuarios
    admin_user = Users(
        username='admin_pedido', 
        password=generate_password_hash('adminpass'), 
        rol='admin'
    )
    vendedor_user = Users(
        username='vendedor_pedido', 
        password=generate_password_hash('vendedorpass'), 
        rol='usuario'
    )
    db.session.add_all([admin_user, vendedor_user])
    db.session.commit()

    # Crear almacenes
    almacen1 = Almacen(nombre='Almacen Principal', direccion='Calle 123', ciudad='Lima')
    almacen2 = Almacen(nombre='Almacen Secundario', direccion='Av. 456', ciudad='Callao')
    db.session.add_all([almacen1, almacen2])
    db.session.commit()

    # Asignar almacenes a usuarios
    admin_user.almacen_id = almacen1.id
    vendedor_user.almacen_id = almacen1.id
    db.session.commit()

    # Crear productos
    producto1 = Producto(
        nombre='Carbón Premium', 
        descripcion='Carbón de alta calidad',
        precio_compra=Decimal('50.00'),
        activo=True
    )
    producto2 = Producto(
        nombre='Carbón Estándar', 
        descripcion='Carbón uso comercial',
        precio_compra=Decimal('30.00'),
        activo=True
    )
    db.session.add_all([producto1, producto2])
    db.session.commit()

    # Crear presentaciones
    presentacion1 = PresentacionProducto(
        producto_id=producto1.id,
        nombre='Bolsa 5kg Premium',
        capacidad_kg=Decimal('5.0'),
        tipo='procesado',
        precio_venta=Decimal('75.00'),
        activo=True
    )
    presentacion2 = PresentacionProducto(
        producto_id=producto1.id,
        nombre='Bolsa 10kg Premium',
        capacidad_kg=Decimal('10.0'),
        tipo='procesado',
        precio_venta=Decimal('140.00'),
        activo=True
    )
    presentacion3 = PresentacionProducto(
        producto_id=producto2.id,
        nombre='Bolsa 5kg Estándar',
        capacidad_kg=Decimal('5.0'),
        tipo='procesado',
        precio_venta=Decimal('45.00'),
        activo=True
    )
    db.session.add_all([presentacion1, presentacion2, presentacion3])
    db.session.commit()

    # Crear lotes
    lote1 = Lote(
        producto_id=producto1.id,
        descripcion='Lote Premium 001',
        peso_humedo_kg=Decimal('1000.0'),
        peso_seco_kg=Decimal('900.0'),
        cantidad_disponible_kg=Decimal('900.0')
    )
    lote2 = Lote(
        producto_id=producto2.id,
        descripcion='Lote Estándar 001',
        peso_humedo_kg=Decimal('800.0'),
        peso_seco_kg=Decimal('720.0'),
        cantidad_disponible_kg=Decimal('720.0')
    )
    db.session.add_all([lote1, lote2])
    db.session.commit()

    # Crear inventario con stock suficiente
    inventario1 = Inventario(
        presentacion_id=presentacion1.id,
        almacen_id=almacen1.id,
        lote_id=lote1.id,
        cantidad=100,  # 100 bolsas de 5kg
        stock_minimo=10
    )
    inventario2 = Inventario(
        presentacion_id=presentacion2.id,
        almacen_id=almacen1.id,
        lote_id=lote1.id,
        cantidad=50,   # 50 bolsas de 10kg
        stock_minimo=5
    )
    inventario3 = Inventario(
        presentacion_id=presentacion3.id,
        almacen_id=almacen1.id,
        lote_id=lote2.id,
        cantidad=5,    # Stock bajo para probar error
        stock_minimo=10
    )
    db.session.add_all([inventario1, inventario2, inventario3])
    db.session.commit()

    # Crear clientes
    cliente1 = Cliente(
        nombre='Restaurante El Buen Sabor',
        telefono='987654321',
        direccion='Jr. Los Olivos 123',
        ciudad='Lima'
    )
    cliente2 = Cliente(
        nombre='Pollería Doña María',
        telefono='123456789',
        direccion='Av. Principal 456',
        ciudad='Callao'
    )
    db.session.add_all([cliente1, cliente2])
    db.session.commit()

    # Crear pedidos de prueba
    
    # Pedido 1: Pedido normal que se puede convertir
    pedido1 = Pedido(
        cliente_id=cliente1.id,
        almacen_id=almacen1.id,
        vendedor_id=vendedor_user.id,
        fecha_creacion=datetime.now(timezone.utc),
        fecha_entrega=datetime.now(timezone.utc),
        estado='confirmado',
        notas='Pedido para evento especial'
    )
    
    # Pedido 2: Pedido ya entregado (no se puede convertir)
    pedido2 = Pedido(
        cliente_id=cliente2.id,
        almacen_id=almacen1.id,
        vendedor_id=vendedor_user.id,
        fecha_creacion=datetime.now(timezone.utc),
        fecha_entrega=datetime.now(timezone.utc),
        estado='entregado',
        notas='Pedido ya entregado'
    )
    
    # Pedido 3: Pedido cancelado (no se puede convertir)
    pedido3 = Pedido(
        cliente_id=cliente1.id,
        almacen_id=almacen1.id,
        vendedor_id=vendedor_user.id,
        fecha_creacion=datetime.now(timezone.utc),
        fecha_entrega=datetime.now(timezone.utc),
        estado='cancelado',
        notas='Pedido cancelado por cliente'
    )
    
    # Pedido 4: Pedido con stock insuficiente
    pedido4 = Pedido(
        cliente_id=cliente2.id,
        almacen_id=almacen1.id,
        vendedor_id=vendedor_user.id,
        fecha_creacion=datetime.now(timezone.utc),
        fecha_entrega=datetime.now(timezone.utc),
        estado='confirmado',
        notas='Pedido con stock insuficiente'
    )
    
    db.session.add_all([pedido1, pedido2, pedido3, pedido4])
    db.session.commit()

    # Crear detalles de pedidos
    
    # Detalles para pedido1 (convertible)
    detalle1_1 = PedidoDetalle(
        pedido_id=pedido1.id,
        presentacion_id=presentacion1.id,
        cantidad=10,  # 10 bolsas de 5kg
        precio_estimado=Decimal('75.00')
    )
    detalle1_2 = PedidoDetalle(
        pedido_id=pedido1.id,
        presentacion_id=presentacion2.id,
        cantidad=5,   # 5 bolsas de 10kg
        precio_estimado=Decimal('140.00')
    )
    
    # Detalles para pedido2 (ya entregado)
    detalle2_1 = PedidoDetalle(
        pedido_id=pedido2.id,
        presentacion_id=presentacion1.id,
        cantidad=2,
        precio_estimado=Decimal('75.00')
    )
    
    # Detalles para pedido3 (cancelado)
    detalle3_1 = PedidoDetalle(
        pedido_id=pedido3.id,
        presentacion_id=presentacion1.id,
        cantidad=3,
        precio_estimado=Decimal('75.00')
    )
    
    # Detalles para pedido4 (stock insuficiente)
    detalle4_1 = PedidoDetalle(
        pedido_id=pedido4.id,
        presentacion_id=presentacion3.id,
        cantidad=10,  # Solicita 10 pero solo hay 5 en stock
        precio_estimado=Decimal('45.00')
    )
    
    db.session.add_all([detalle1_1, detalle1_2, detalle2_1, detalle3_1, detalle4_1])
    db.session.commit()

    return {
        'admin_user': admin_user,
        'vendedor_user': vendedor_user,
        'almacen1': almacen1,
        'almacen2': almacen2,
        'cliente1': cliente1,
        'cliente2': cliente2,
        'pedido1': pedido1,  # Convertible
        'pedido2': pedido2,  # Ya entregado
        'pedido3': pedido3,  # Cancelado
        'pedido4': pedido4,  # Stock insuficiente
        'presentacion1': presentacion1,
        'presentacion2': presentacion2,
        'presentacion3': presentacion3
    }

class TestPedidoConversionResource:
    """Tests para el recurso de conversión de pedidos a ventas."""

    def test_conversion_exitosa_pedido_normal(self, test_client, setup_pedido_conversion_data):
        """Test: Conversión exitosa de un pedido normal a venta."""
        data = setup_pedido_conversion_data
        token = get_auth_token(test_client, 'vendedor_pedido', 'vendedorpass')
        headers = {'Authorization': f'Bearer {token}'}
        
        # Verificar stock inicial
        inventario_inicial_1 = Inventario.query.filter_by(
            presentacion_id=data['presentacion1'].id,
            almacen_id=data['almacen1'].id
        ).first()
        inventario_inicial_2 = Inventario.query.filter_by(
            presentacion_id=data['presentacion2'].id,
            almacen_id=data['almacen1'].id
        ).first()
        
        stock_inicial_1 = inventario_inicial_1.cantidad
        stock_inicial_2 = inventario_inicial_2.cantidad
        
        # Realizar conversión
        response = test_client.post(
            f'/pedidos/{data["pedido1"].id}/convertir',
            headers=headers,
            json={'tipo_pago': 'contado', 'usar_precio_actual': True}
        )
        
        assert response.status_code == 201
        response_data = response.get_json()
        assert response_data['message'] == 'Pedido convertido a venta exitosamente'
        assert 'venta' in response_data
        
        venta_data = response_data['venta']
        assert venta_data['cliente']['id'] == data['cliente1'].id
        assert venta_data['almacen']['id'] == data['almacen1'].id
        assert venta_data['tipo_pago'] == 'contado'
        assert venta_data['estado_pago'] == 'pendiente'
        
        # Verificar total calculado correctamente
        total_esperado = (10 * Decimal('75.00')) + (5 * Decimal('140.00'))  # 750 + 700 = 1450
        assert Decimal(venta_data['total']) == total_esperado
        
        # Verificar que el pedido cambió de estado
        pedido_actualizado = Pedido.query.get(data['pedido1'].id)
        assert pedido_actualizado.estado == 'entregado'
        
        # Verificar que se actualizó el inventario
        inventario_final_1 = Inventario.query.filter_by(
            presentacion_id=data['presentacion1'].id,
            almacen_id=data['almacen1'].id
        ).first()
        inventario_final_2 = Inventario.query.filter_by(
            presentacion_id=data['presentacion2'].id,
            almacen_id=data['almacen1'].id
        ).first()
        
        assert inventario_final_1.cantidad == stock_inicial_1 - 10
        assert inventario_final_2.cantidad == stock_inicial_2 - 5
        
        # Verificar que se crearon movimientos de salida
        movimientos = Movimiento.query.filter_by(tipo='salida').all()
        assert len(movimientos) == 2  # Uno por cada detalle
        
        for mov in movimientos:
            assert mov.usuario_id == data['vendedor_user'].id
            assert f"desde pedido {data['pedido1'].id}" in mov.motivo

    def test_conversion_con_precio_estimado(self, test_client, setup_pedido_conversion_data):
        """Test: Conversión usando precio estimado en lugar del precio actual."""
        data = setup_pedido_conversion_data
        token = get_auth_token(test_client, 'vendedor_pedido', 'vendedorpass')
        headers = {'Authorization': f'Bearer {token}'}
        
        # Realizar conversión usando precio estimado
        response = test_client.post(
            f'/pedidos/{data["pedido1"].id}/convertir',
            headers=headers,
            json={'tipo_pago': 'credito', 'usar_precio_actual': False}
        )
        
        assert response.status_code == 201
        response_data = response.get_json()
        venta_data = response_data['venta']
        
        # Verificar que se usó el precio estimado del pedido
        total_esperado = (10 * Decimal('75.00')) + (5 * Decimal('140.00'))
        assert Decimal(venta_data['total']) == total_esperado
        assert venta_data['tipo_pago'] == 'credito'

    def test_error_pedido_ya_entregado(self, test_client, setup_pedido_conversion_data):
        """Test: Error al intentar convertir pedido ya entregado."""
        data = setup_pedido_conversion_data
        token = get_auth_token(test_client, 'vendedor_pedido', 'vendedorpass')
        headers = {'Authorization': f'Bearer {token}'}
        
        response = test_client.post(
            f'/pedidos/{data["pedido2"].id}/convertir',
            headers=headers,
            json={'tipo_pago': 'contado'}
        )
        
        assert response.status_code == 400
        response_data = response.get_json()
        assert response_data['error'] == 'Este pedido ya fue entregado'

    def test_error_pedido_cancelado(self, test_client, setup_pedido_conversion_data):
        """Test: Error al intentar convertir pedido cancelado."""
        data = setup_pedido_conversion_data
        token = get_auth_token(test_client, 'vendedor_pedido', 'vendedorpass')
        headers = {'Authorization': f'Bearer {token}'}
        
        response = test_client.post(
            f'/pedidos/{data["pedido3"].id}/convertir',
            headers=headers,
            json={'tipo_pago': 'contado'}
        )
        
        assert response.status_code == 400
        response_data = response.get_json()
        assert response_data['error'] == 'No se puede convertir un pedido cancelado'

    def test_error_stock_insuficiente(self, test_client, setup_pedido_conversion_data):
        """Test: Error al intentar convertir pedido con stock insuficiente."""
        data = setup_pedido_conversion_data
        token = get_auth_token(test_client, 'vendedor_pedido', 'vendedorpass')
        headers = {'Authorization': f'Bearer {token}'}
        
        response = test_client.post(
            f'/pedidos/{data["pedido4"].id}/convertir',
            headers=headers,
            json={'tipo_pago': 'contado'}
        )
        
        assert response.status_code == 400
        response_data = response.get_json()
        assert response_data['error'] == 'Stock insuficiente para completar el pedido'
        assert 'detalles' in response_data
        assert len(response_data['detalles']) == 1
        
        detalle_error = response_data['detalles'][0]
        assert detalle_error['solicitado'] == 10
        assert detalle_error['disponible'] == 5

    def test_error_pedido_inexistente(self, test_client, setup_pedido_conversion_data):
        """Test: Error 404 al intentar convertir pedido inexistente."""
        data = setup_pedido_conversion_data
        token = get_auth_token(test_client, 'vendedor_pedido', 'vendedorpass')
        headers = {'Authorization': f'Bearer {token}'}
        
        response = test_client.post(
            '/pedidos/99999/convertir',
            headers=headers,
            json={'tipo_pago': 'contado'}
        )
        
        assert response.status_code == 404

    def test_sin_autenticacion(self, test_client, setup_pedido_conversion_data):
        """Test: Error 401 al intentar convertir sin autenticación."""
        data = setup_pedido_conversion_data
        
        response = test_client.post(
            f'/pedidos/{data["pedido1"].id}/convertir',
            json={'tipo_pago': 'contado'}
        )
        
        assert response.status_code == 401

    def test_admin_puede_convertir_cualquier_pedido(self, test_client, setup_pedido_conversion_data):
        """Test: Admin puede convertir pedidos de cualquier almacén."""
        data = setup_pedido_conversion_data
        token = get_auth_token(test_client, 'admin_pedido', 'adminpass')
        headers = {'Authorization': f'Bearer {token}'}
        
        response = test_client.post(
            f'/pedidos/{data["pedido1"].id}/convertir',
            headers=headers,
            json={'tipo_pago': 'credito'}
        )
        
        assert response.status_code == 201
        response_data = response.get_json()
        assert response_data['message'] == 'Pedido convertido a venta exitosamente'

    def test_verificar_venta_creada_completamente(self, test_client, setup_pedido_conversion_data):
        """Test: Verificar que la venta se crea completamente con todos sus detalles."""
        data = setup_pedido_conversion_data
        token = get_auth_token(test_client, 'vendedor_pedido', 'vendedorpass')
        headers = {'Authorization': f'Bearer {token}'}
        
        response = test_client.post(
            f'/pedidos/{data["pedido1"].id}/convertir',
            headers=headers,
            json={'tipo_pago': 'contado'}
        )
        
        assert response.status_code == 201
        response_data = response.get_json()
        venta_id = response_data['venta']['id']
        
        # Verificar venta en base de datos
        venta = Venta.query.get(venta_id)
        assert venta is not None
        assert venta.cliente_id == data['cliente1'].id
        assert venta.almacen_id == data['almacen1'].id
        assert venta.vendedor_id == data['vendedor_user'].id
        assert venta.tipo_pago == 'contado'
        assert venta.estado_pago == 'pendiente'
        assert len(venta.detalles) == 2
        
        # Verificar detalles de venta
        for detalle in venta.detalles:
            assert detalle.precio_unitario > 0
            assert detalle.cantidad > 0

    def test_error_pedido_sin_detalles(self, test_client, setup_pedido_conversion_data):
        """Test: Error al intentar convertir pedido sin detalles."""
        data = setup_pedido_conversion_data
        
        # Crear pedido sin detalles
        pedido_vacio = Pedido(
            cliente_id=data['cliente1'].id,
            almacen_id=data['almacen1'].id,
            vendedor_id=data['vendedor_user'].id,
            fecha_creacion=datetime.now(timezone.utc),
            fecha_entrega=datetime.now(timezone.utc),
            estado='confirmado',
            notas='Pedido sin detalles'
        )
        db.session.add(pedido_vacio)
        db.session.commit()
        
        token = get_auth_token(test_client, 'vendedor_pedido', 'vendedorpass')
        headers = {'Authorization': f'Bearer {token}'}
        
        response = test_client.post(
            f'/pedidos/{pedido_vacio.id}/convertir',
            headers=headers,
            json={'tipo_pago': 'contado'}
        )
        
        assert response.status_code == 400
        response_data = response.get_json()
        assert response_data['error'] == 'El pedido no tiene detalles para convertir'