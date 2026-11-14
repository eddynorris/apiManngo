"""
Tests para:
  - InsumoAlertaResource: GET /inventario/alertas-insumos
  - CompraInsumoResource: POST /compras/insumos
"""
import json
from decimal import Decimal
import pytest

from models import (
    Users, Almacen, Producto, PresentacionProducto, Inventario,
    Movimiento, Gasto, Proveedor
)
from extensions import db
from werkzeug.security import generate_password_hash


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def get_auth_token(client, username, password):
    """Obtiene un token JWT para un usuario."""
    response = client.post('/auth', json={'username': username, 'password': password})
    assert response.status_code == 200, f"Auth fallida para {username}: {response.get_json()}"
    return response.get_json()['access_token']


# ─────────────────────────────────────────────
#  Fixture: datos de prueba
# ─────────────────────────────────────────────

@pytest.fixture(scope='function')
def setup_insumo_data(test_client):
    """
    Prepara el estado limpio de la BD con:
    - 1 admin (gerente) y 1 usuario normal con almacén asignado
    - 2 almacenes
    - 1 producto "Insumos Varios" con 2 presentaciones tipo 'insumo'
    - 1 producto "Carbón" con 1 presentación tipo 'procesado'
    - Inventario inicial para simular distintos escenarios de stock
    - 1 proveedor
    """
    db.session.remove()
    # Limpiar estado
    db.session.query(Gasto).delete()
    db.session.query(Movimiento).delete()
    db.session.query(Inventario).delete()
    db.session.query(PresentacionProducto).delete()
    db.session.query(Producto).delete()
    db.session.query(Proveedor).delete()
    db.session.query(Almacen).delete()
    db.session.query(Users).delete()
    db.session.commit()

    # Usuarios
    admin = Users(username='admin_ins', password=generate_password_hash('adminpass1'), rol='gerente')
    usuario = Users(username='user_ins', password=generate_password_hash('userpass1'), rol='usuario')
    db.session.add_all([admin, usuario])
    db.session.commit()

    # Almacenes
    alm1 = Almacen(nombre='Almacén Principal')
    alm2 = Almacen(nombre='Almacén Sur')
    db.session.add_all([alm1, alm2])
    db.session.commit()

    admin.almacen_id = alm1.id
    usuario.almacen_id = alm1.id
    db.session.commit()

    # Proveedor
    proveedor = Proveedor(nombre='Distribuidora Plásticos S.A.')
    db.session.add(proveedor)
    db.session.commit()

    # Productos
    prod_insumos = Producto(nombre='Insumos Varios', precio_compra=Decimal('5.00'))
    prod_carbon = Producto(nombre='Carbón Vegetal', precio_compra=Decimal('100.00'))
    db.session.add_all([prod_insumos, prod_carbon])
    db.session.commit()

    # Presentaciones
    bolsa_5kg = PresentacionProducto(
        producto_id=prod_insumos.id, nombre='Bolsa 5kg',
        capacidad_kg=Decimal('0.01'), tipo='insumo', precio_venta=Decimal('0.50')
    )
    caja_mecheros = PresentacionProducto(
        producto_id=prod_insumos.id, nombre='Caja de Mecheros',
        capacidad_kg=Decimal('0.01'), tipo='insumo', precio_venta=Decimal('5.00')
    )
    saco_carbon = PresentacionProducto(
        producto_id=prod_carbon.id, nombre='Saco Carbón 20kg',
        capacidad_kg=Decimal('20.00'), tipo='procesado', precio_venta=Decimal('50.00')
    )
    db.session.add_all([bolsa_5kg, caja_mecheros, saco_carbon])
    db.session.commit()

    # Inventario: bolsas con stock BAJO el mínimo y CRITICO (<=25%)
    inv_bolsas = Inventario(
        presentacion_id=bolsa_5kg.id, almacen_id=alm1.id,
        lote_id=None, cantidad=Decimal('20'), stock_minimo=100
    )
    # Inventario: mecheros con stock OK
    inv_mecheros = Inventario(
        presentacion_id=caja_mecheros.id, almacen_id=alm1.id,
        lote_id=None, cantidad=Decimal('200'), stock_minimo=50  # 200 > 50 → sin alerta
    )
    db.session.add_all([inv_bolsas, inv_mecheros])
    db.session.commit()

    return {
        'admin': admin,
        'usuario': usuario,
        'alm1': alm1,
        'alm2': alm2,
        'proveedor': proveedor,
        'prod_insumos': prod_insumos,
        'prod_carbon': prod_carbon,
        'bolsa_5kg': bolsa_5kg,
        'caja_mecheros': caja_mecheros,
        'saco_carbon': saco_carbon,
        'inv_bolsas': inv_bolsas,
        'inv_mecheros': inv_mecheros,
    }


# ─────────────────────────────────────────────
#  Tests: InsumoAlertaResource
# ─────────────────────────────────────────────

class TestInsumoAlertaResource:

    def test_alerta_retorna_insumos_con_stock_bajo(self, test_client, setup_insumo_data):
        """Debe retornar solo las bolsas (30 < 100), NO los mecheros (200 > 50)."""
        d = setup_insumo_data
        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        response = test_client.get('/inventario/alertas-insumos', headers=headers)
        assert response.status_code == 200

        body = response.get_json()
        assert body['total_alertas'] == 1
        alerta = body['alertas'][0]
        assert alerta['presentacion_id'] == d['bolsa_5kg'].id
        assert alerta['nombre_insumo'] == 'Bolsa 5kg'
        assert alerta['stock_actual'] == 20.0
        assert alerta['stock_minimo'] == 100.0
        assert alerta['porcentaje_stock'] == 20.0
        assert alerta['critico'] is True

    def test_alerta_vacia_cuando_todo_ok(self, test_client, setup_insumo_data):
        """Si el stock de bolsas se actualiza a suficiente, no debe aparecer ninguna alerta."""
        d = setup_insumo_data
        # Actualizar bolsas a stock suficiente
        d['inv_bolsas'].cantidad = Decimal('150')
        db.session.commit()

        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        response = test_client.get('/inventario/alertas-insumos', headers=headers)
        assert response.status_code == 200
        body = response.get_json()
        assert body['total_alertas'] == 0
        assert body['alertas'] == []

    def test_alerta_solo_muestra_insumos_no_otros_tipos(self, test_client, setup_insumo_data):
        """Las presentaciones de tipo 'procesado' (carbón) no deben aparecer aunque tengan stock 0."""
        d = setup_insumo_data
        # Crear inventario de carbón con stock bajo
        inv_carbon_bajo = Inventario(
            presentacion_id=d['saco_carbon'].id, almacen_id=d['alm1'].id,
            lote_id=None, cantidad=Decimal('0'), stock_minimo=50
        )
        db.session.add(inv_carbon_bajo)
        db.session.commit()

        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        response = test_client.get('/inventario/alertas-insumos', headers=headers)
        assert response.status_code == 200
        body = response.get_json()

        # Solo debe aparecer la bolsa (insumo con stock bajo), no el saco carbón (procesado)
        presentacion_ids_en_alerta = [a['presentacion_id'] for a in body['alertas']]
        assert d['saco_carbon'].id not in presentacion_ids_en_alerta

    def test_alerta_requiere_autenticacion(self, test_client, setup_insumo_data):
        """Sin token JWT, debe retornar 401."""
        response = test_client.get('/inventario/alertas-insumos')
        assert response.status_code == 401


# ─────────────────────────────────────────────
#  Tests: CompraInsumoResource
# ─────────────────────────────────────────────

class TestCompraInsumoResource:

    def _payload_base(self, d, **overrides):
        base = {
            "presentacion_id": d['bolsa_5kg'].id,
            "almacen_id": d['alm1'].id,
            "cantidad": 500,
            "costo_total": 250.00,
            "proveedor_id": d['proveedor'].id,
            "descripcion": "Compra de prueba",
            "fecha": "2026-03-11"
        }
        base.update(overrides)
        return base

    def test_compra_actualiza_inventario_existente(self, test_client, setup_insumo_data):
        """Una compra sobre inventario ya existente SUMA la cantidad al stock actual."""
        d = setup_insumo_data
        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        stock_inicial = float(d['inv_bolsas'].cantidad)  # 30

        response = test_client.post('/compras/insumos', json=self._payload_base(d), headers=headers)
        assert response.status_code == 201

        body = response.get_json()
        assert body['resumen']['nuevo_stock'] == stock_inicial + 500  # 530

        # Verificar en DB
        inv = Inventario.query.filter_by(
            presentacion_id=d['bolsa_5kg'].id, almacen_id=d['alm1'].id, lote_id=None
        ).first()
        assert inv is not None
        assert float(inv.cantidad) == 520.0

    def test_compra_crea_inventario_si_no_existe(self, test_client, setup_insumo_data):
        """Si no existe registro de inventario para el insumo/almacén, debe CREARLO."""
        d = setup_insumo_data
        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        # Comprar para almacén 2 donde no hay inventario de bolsas
        payload = self._payload_base(d, almacen_id=d['alm2'].id)
        response = test_client.post('/compras/insumos', json=payload, headers=headers)
        assert response.status_code == 201

        body = response.get_json()
        assert body['resumen']['nuevo_stock'] == 500.0
        assert body['resumen']['inventario'] == 'creado'

        inv = Inventario.query.filter_by(
            presentacion_id=d['bolsa_5kg'].id, almacen_id=d['alm2'].id, lote_id=None
        ).first()
        assert inv is not None
        assert float(inv.cantidad) == 500.0

    def test_compra_registra_movimiento(self, test_client, setup_insumo_data):
        """La compra debe crear un Movimiento de tipo='entrada' y tipo_operacion='compra'."""
        d = setup_insumo_data
        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        response = test_client.post('/compras/insumos', json=self._payload_base(d), headers=headers)
        assert response.status_code == 201

        mov = Movimiento.query.filter_by(
            presentacion_id=d['bolsa_5kg'].id,
            tipo='entrada',
            tipo_operacion='compra'
        ).first()
        assert mov is not None
        assert float(mov.cantidad) == 500.0

    def test_compra_registra_gasto(self, test_client, setup_insumo_data):
        """La compra debe crear un Gasto con categoria='insumos' y el monto correcto."""
        d = setup_insumo_data
        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        response = test_client.post('/compras/insumos', json=self._payload_base(d), headers=headers)
        assert response.status_code == 201

        gasto = Gasto.query.filter_by(
            almacen_id=d['alm1'].id,
            categoria='insumos'
        ).first()
        assert gasto is not None
        assert float(gasto.monto) == 250.0

    def test_compra_falla_si_presentacion_no_es_insumo(self, test_client, setup_insumo_data):
        """Si la presentación es de tipo 'procesado' o similar, debe devolver 400."""
        d = setup_insumo_data
        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        payload = self._payload_base(d, presentacion_id=d['saco_carbon'].id)
        response = test_client.post('/compras/insumos', json=payload, headers=headers)
        assert response.status_code == 400
        assert "no 'insumo'" in response.get_json()['error']

        # Verificar que no se creó nada en la DB
        assert Gasto.query.count() == 0
        # Solo los movimientos pre-existentes (ninguno en este fixture)
        assert Movimiento.query.count() == 0

    def test_compra_falla_si_faltan_campos_requeridos(self, test_client, setup_insumo_data):
        """Campos requeridos faltantes deben retornar 400 con mensaje descriptivo."""
        d = setup_insumo_data
        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        campos_requeridos = ['presentacion_id', 'almacen_id', 'cantidad', 'costo_total']
        for campo in campos_requeridos:
            payload = self._payload_base(d)
            del payload[campo]
            response = test_client.post('/compras/insumos', json=payload, headers=headers)
            assert response.status_code == 400, f"Campo '{campo}' no generó error 400"
            assert campo in response.get_json()['error']

    def test_compra_falla_si_presentacion_no_existe(self, test_client, setup_insumo_data):
        """Un presentacion_id inválido debe retornar 404."""
        d = setup_insumo_data
        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        payload = self._payload_base(d, presentacion_id=99999)
        response = test_client.post('/compras/insumos', json=payload, headers=headers)
        assert response.status_code == 404

    def test_compra_falla_si_cantidad_negativa(self, test_client, setup_insumo_data):
        """Cantidad <= 0 debe retornar 400."""
        d = setup_insumo_data
        token = get_auth_token(test_client, 'admin_ins', 'adminpass1')
        headers = {'Authorization': f'Bearer {token}'}

        payload = self._payload_base(d, cantidad=-10)
        response = test_client.post('/compras/insumos', json=payload, headers=headers)
        assert response.status_code == 400
        assert "mayor a cero" in response.get_json()['error']

    def test_compra_requiere_rol_admin_o_gerente(self, test_client, setup_insumo_data):
        """Un usuario con rol 'usuario' no debe poder registrar compras."""
        d = setup_insumo_data
        token = get_auth_token(test_client, 'user_ins', 'userpass1')
        headers = {'Authorization': f'Bearer {token}'}

        response = test_client.post('/compras/insumos', json=self._payload_base(d), headers=headers)
        assert response.status_code == 403
