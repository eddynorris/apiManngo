import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from app import app, db
from models import Users, Almacen, Producto, PresentacionProducto, Lote, Movimiento
from flask_jwt_extended import create_access_token

@pytest.fixture(scope='module')
def test_client():
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "JWT_SECRET_KEY": "test-secret-key-at-least-32-chars-long"
    })
    with app.test_client() as testing_client:
        with app.app_context():
            db.create_all()
            yield testing_client
            db.session.remove()
            db.drop_all()

@pytest.fixture(scope='function')
def init_entradas_data(test_client):
    db.create_all()

    # 1. Almacenes y Usuarios
    almacen_planta = Almacen(id=1, nombre='Planta Principal', es_planta=True)
    almacen_tienda = Almacen(id=2, nombre='Tienda Central', es_planta=False)
    db.session.add_all([almacen_planta, almacen_tienda])
    db.session.commit()

    user_admin = Users(id=1, username='admin_user', password='password', rol='admin', almacen_id=1)
    user_operario = Users(id=2, username='operario_planta', password='password', rol='usuario', almacen_id=1)
    db.session.add_all([user_admin, user_operario])

    # 2. Productos y Presentaciones
    p_carbon = Producto(id=1, nombre='Carbón Vegetal', precio_compra=100)
    p_briqueta = Producto(id=2, nombre='Briqueta Ecológica', precio_compra=80)
    db.session.add_all([p_carbon, p_briqueta])
    db.session.commit()

    pres_saco20 = PresentacionProducto(id=1, producto_id=1, nombre='Saco 20kg Granel', capacidad_kg=Decimal('20.00'), tipo='procesado', precio_venta=35)
    pres_bolsa5 = PresentacionProducto(id=2, producto_id=1, nombre='Bolsa 5kg Premium', capacidad_kg=Decimal('5.00'), tipo='detalle', precio_venta=12)
    pres_briqueta3 = PresentacionProducto(id=3, producto_id=2, nombre='Briqueta Caja 3kg', capacidad_kg=Decimal('3.00'), tipo='briqueta', precio_venta=15)
    db.session.add_all([pres_saco20, pres_bolsa5, pres_briqueta3])

    # 3. Lotes
    lote1 = Lote(id=1, codigo_lote='LOT-2026-001', producto_id=1, descripcion='Lote Carbón Quebracho', cantidad_disponible_kg=5000)
    lote2 = Lote(id=2, codigo_lote='LOT-2026-002', producto_id=2, descripcion='Lote Briquetas Aserrín', cantidad_disponible_kg=3000)
    db.session.add_all([lote1, lote2])
    db.session.commit()

    # 4. Movimientos de Entrada de Producción y Traslados
    ahora = datetime.now(timezone.utc)
    hace_2_dias = ahora - timedelta(days=2)
    hace_5_dias = ahora - timedelta(days=5)

    # Producción 1: 50 sacos de 20kg (Lote 1) -> 1,000 kg
    mov_prod1 = Movimiento(
        tipo='entrada',
        tipo_operacion='ensamblaje',
        presentacion_id=1,
        lote_id=1,
        cantidad=50,
        fecha=hace_5_dias,
        motivo='Fabricación de 50 sacos de 20kg',
        usuario_id=2
    )

    # Producción 2: 100 bolsas de 5kg (Lote 1) -> 500 kg
    mov_prod2 = Movimiento(
        tipo='entrada',
        tipo_operacion='produccion',
        presentacion_id=2,
        lote_id=1,
        cantidad=100,
        fecha=hace_2_dias,
        motivo='Envasado de 100 bolsas de 5kg',
        usuario_id=2
    )

    # Producción 3: 80 cajas de briqueta 3kg (Lote 2) -> 240 kg
    mov_prod3 = Movimiento(
        tipo='entrada',
        tipo_operacion='ensamblaje',
        presentacion_id=3,
        lote_id=2,
        cantidad=80,
        fecha=ahora,
        motivo='Prensado de briqueta 80 cajas 3kg',
        usuario_id=2
    )

    # Traslado de entrada: 20 sacos de 20kg desde Planta Principal a Tienda Central
    mov_traslado = Movimiento(
        tipo='entrada',
        tipo_operacion='transferencia',
        presentacion_id=1,
        lote_id=1,
        cantidad=20,
        fecha=ahora,
        motivo='Transferencia desde Planta Principal (Op: ab12cd34)',
        usuario_id=1
    )

    db.session.add_all([mov_prod1, mov_prod2, mov_prod3, mov_traslado])
    db.session.commit()

    token = create_access_token(identity=str(user_admin.id), additional_claims={"rol": user_admin.rol, "almacen_id": user_admin.almacen_id})
    yield token
    db.session.remove()
    db.drop_all()


def test_reporte_entradas_completo(test_client, init_entradas_data):
    token = init_entradas_data
    headers = {'Authorization': f'Bearer {token}'}

    resp = test_client.get('/reportes/produccion-entradas', headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()

    # Validar secciones principales
    assert 'periodo' in data
    assert 'resumen_general' in data
    assert 'produccion_por_lote' in data
    assert 'produccion_por_presentacion' in data
    assert 'traslados_entre_almacenes' in data
    assert 'resumen_temporal' in data
    assert 'movimientos_detalle' in data

    # Validar totales en resumen general
    resumen = data['resumen_general']
    # Producción: 50 + 100 + 80 = 230 unidades
    # KG Producción: (50*20) + (100*5) + (80*3) = 1000 + 500 + 240 = 1740 kg
    assert resumen['produccion']['total_unidades'] == 230
    assert resumen['produccion']['total_kg'] == 1740.0
    assert resumen['produccion']['total_lotes_utilizados'] == 2
    assert resumen['produccion']['total_operaciones'] == 3

    # Traslados: 20 unidades -> 400 kg
    assert resumen['traslados']['total_unidades'] == 20
    assert resumen['traslados']['total_kg'] == 400.0
    assert resumen['traslados']['total_operaciones'] == 1

    # Total general
    assert resumen['total_unidades_ingresadas'] == 250
    assert resumen['total_kg_ingresados'] == 2140.0


def test_reporte_entradas_filtro_por_lote(test_client, init_entradas_data):
    token = init_entradas_data
    headers = {'Authorization': f'Bearer {token}'}

    # Filtrar solo por Lote 1 (Carbón)
    resp = test_client.get('/reportes/produccion-entradas?lote_id=1', headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()

    # Debe contener solo lote 1 en producción
    assert len(data['produccion_por_lote']) == 1
    lote_info = data['produccion_por_lote'][0]
    assert lote_info['lote_id'] == 1
    assert lote_info['codigo_lote'] == 'LOT-2026-001'
    assert lote_info['unidades_producidas'] == 150 # 50 sacos + 100 bolsas
    assert lote_info['kg_producidos'] == 1500.0 # 1000 + 500


def test_reporte_entradas_filtro_tipo_operacion(test_client, init_entradas_data):
    token = init_entradas_data
    headers = {'Authorization': f'Bearer {token}'}

    # Filtrar solo transferencias
    resp = test_client.get('/reportes/produccion-entradas?tipo_operacion=transferencia', headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()

    assert data['resumen_general']['produccion']['total_operaciones'] == 0
    assert data['resumen_general']['traslados']['total_operaciones'] == 1
    assert len(data['traslados_entre_almacenes']) == 1
    traslado = data['traslados_entre_almacenes'][0]
    assert traslado['cantidad_unidades'] == 20.0
    assert traslado['almacen_origen'] == 'Planta Principal'
    assert traslado['operacion_id'] == 'ab12cd34'


def test_reporte_entradas_alias_movimientos_entrada(test_client, init_entradas_data):
    token = init_entradas_data
    headers = {'Authorization': f'Bearer {token}'}

    # Probar endpoint alternativo
    resp = test_client.get('/reportes/movimientos-entrada', headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'produccion_por_lote' in data
    assert 'traslados_entre_almacenes' in data
