import os
import sys
from decimal import Decimal

import pytest
from flask import has_app_context
from sqlalchemy.engine import make_url

# Añadir el directorio raíz del proyecto a sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.offline_test_env import (
    UnsafeTestDatabaseError,
    install_offline_guards,
    prepare_test_environment,
    validate_testing_database_url,
)

environment_status = prepare_test_environment()
VALIDATED_DATABASE_URL = os.environ.get('DATABASE_URL', '')
PG_OPT_IN = environment_status.database_kind == 'postgresql-loopback-disposable'
install_offline_guards(allow_loopback=environment_status.allow_loopback)

from werkzeug.security import generate_password_hash

from app import app, db
from models import (
    Almacen,
    Cliente,
    Inventario,
    Lote,
    Movimiento,
    Pago,
    PresentacionProducto,
    Producto,
    Users,
    Venta,
)


def _database_url_string(value):
    if hasattr(value, 'render_as_string'):
        return value.render_as_string(hide_password=False)
    return str(value)


def _validate_pg_snapshot(snapshot, configured_url, engine_url, guard_env=None):
    """Valida que el DSN capturado, la config y el engine son el mismo PG local.

    Nunca devuelve ni restaura una URL remota: la validación de guardas se
    aplica al snapshot y a las dos fuentes del engine.
    """
    if not isinstance(snapshot, str) or not snapshot.strip():
        raise RuntimeError('No hay un DSN PostgreSQL validado para restaurar')

    try:
        snapshot_kind = validate_testing_database_url(snapshot, env=guard_env)
    except UnsafeTestDatabaseError:
        raise RuntimeError(
            'Se rechaza restaurar un DSN PostgreSQL no validado'
        ) from None

    if snapshot_kind != 'postgresql-loopback-disposable':
        raise RuntimeError(
            'Se rechaza restaurar un DSN PostgreSQL no desechable'
        )

    snapshot_url = make_url(snapshot)
    for label, value in (
        ('configuración del engine', configured_url),
        ('URL del engine', engine_url),
    ):
        if value is None or not str(value).strip():
            raise RuntimeError(f'Falta la {label} para la sesión PostgreSQL')

        candidate = _database_url_string(value)
        try:
            candidate_kind = validate_testing_database_url(
                candidate, env=guard_env
            )
        except UnsafeTestDatabaseError:
            raise RuntimeError(
                f'Se rechaza la {label} no validada para PostgreSQL'
            ) from None

        if candidate_kind != 'postgresql-loopback-disposable':
            raise RuntimeError(
                f'Se rechaza la {label} no desechable para PostgreSQL'
            )
        if make_url(candidate) != snapshot_url:
            raise RuntimeError(
                f'La {label} no coincide con el DSN PostgreSQL validado'
            )

    return snapshot


def _restore_validated_pg_dsn(
    env=None,
    *,
    enabled=None,
    snapshot=None,
    configured_url=None,
    engine_url=None,
    guard_env=None,
):
    """Restaura el DSN PostgreSQL validado tras la colección de tests.

    Algunos módulos de test escriben ``DATABASE_URL`` durante la colección.
    En modo PostgreSQL opt-in eso haría que ``_require_pg_url`` comparase
    contra un DSN distinto del que usa el engine real. Solo se restaura el
    snapshot validado y solo si config/engine coinciden con él. En modo
    SQLite no se toca el entorno.
    """
    current = os.environ if env is None else env
    if enabled is None:
        enabled = PG_OPT_IN
    if not enabled:
        return False

    if snapshot is None:
        snapshot = VALIDATED_DATABASE_URL
    if configured_url is None:
        configured_url = app.config.get('SQLALCHEMY_DATABASE_URI')
    if engine_url is None:
        with app.app_context():
            engine_url = db.engine.url

    validated = _validate_pg_snapshot(
        snapshot, configured_url, engine_url, guard_env=guard_env
    )
    if current.get('DATABASE_URL') != validated:
        current['DATABASE_URL'] = validated
        return True
    return False


def pytest_collection_finish(session):
    """Recupera el DSN validado que otros módulos pudieron sobrescribir."""
    _restore_validated_pg_dsn()


@pytest.fixture(scope='module')
def test_client():
    """
    Crea un cliente de prueba para toda la sesión del módulo.
    Configura la app, crea la base de datos y la puebla con datos.
    """
    with app.test_client() as testing_client:
        with app.app_context():
            db.create_all()
            setup_test_data()
            yield testing_client  # Los tests se ejecutan aquí
            db.session.remove()
            db.drop_all()

def setup_test_data():
    """Crea los datos necesarios para todas las pruebas del dashboard."""
    # Limpiar datos existentes antes de cada ejecución
    db.session.query(Movimiento).delete()
    db.session.query(Inventario).delete()
    db.session.query(PresentacionProducto).delete()
    db.session.query(Producto).delete()
    db.session.query(Lote).delete()
    db.session.query(Almacen).delete()
    db.session.query(Users).delete()
    db.session.query(Cliente).delete()
    db.session.query(Venta).delete()
    db.session.query(Pago).delete()
    db.session.commit()

    # Almacenes
    almacen1 = Almacen(nombre='Almacén Central')
    almacen2 = Almacen(nombre='Almacén Norte')
    db.session.add_all([almacen1, almacen2])
    db.session.commit() # Commit para obtener IDs generados

    # Usuarios
    admin_user = Users(username='admin', password=generate_password_hash('adminpass'), rol='admin')
    vendedor_user = Users(username='vendedor', password=generate_password_hash('ventapass'), rol='usuario', almacen_id=almacen1.id)
    db.session.add_all([admin_user, vendedor_user])
    db.session.commit()

    # Clientes
    cliente1 = Cliente(nombre='Cliente A (Deuda en Almacén 1)')
    cliente2 = Cliente(nombre='Cliente B (Deuda en Almacén 2)')
    cliente3 = Cliente(nombre='Cliente C (Deuda Mixta)')
    cliente4 = Cliente(nombre='Cliente D (Sin Deuda)')
    db.session.add_all([cliente1, cliente2, cliente3, cliente4])
    db.session.commit()

    # Productos, Presentaciones, Lotes e Inventario
    prod = Producto(nombre='Test Prod', precio_compra=Decimal('10.00'))
    db.session.add(prod)
    db.session.commit()

    pres = PresentacionProducto(nombre='Test Pres', producto_id=prod.id, capacidad_kg=Decimal('1.0'), tipo='procesado', precio_venta=Decimal('15.00'))
    db.session.add(pres)
    db.session.commit()

    lote = Lote(producto_id=prod.id, cantidad_disponible_kg=Decimal('1000.0'), peso_humedo_kg=Decimal('1200.0'), peso_seco_kg=Decimal('1100.0'))
    db.session.add(lote)
    db.session.commit()

    inv = Inventario(presentacion_id=pres.id, almacen_id=almacen1.id, lote_id=lote.id, cantidad=5, stock_minimo=10) # Para alerta stock bajo
    db.session.add(inv)
    db.session.commit()

    # Ventas y Pagos
    v1 = Venta(cliente_id=cliente1.id, almacen_id=almacen1.id, vendedor_id=vendedor_user.id, total=Decimal('100.00'), estado_pago='pendiente', tipo_pago='credito')
    v2 = Venta(cliente_id=cliente1.id, almacen_id=almacen1.id, vendedor_id=vendedor_user.id, total=Decimal('50.00'), estado_pago='pendiente', tipo_pago='credito')
    v3 = Venta(cliente_id=cliente2.id, almacen_id=almacen2.id, vendedor_id=admin_user.id, total=Decimal('200.00'), estado_pago='pendiente', tipo_pago='credito')
    v4 = Venta(cliente_id=cliente3.id, almacen_id=almacen1.id, vendedor_id=vendedor_user.id, total=Decimal('100.00'), estado_pago='parcial', tipo_pago='credito') # Deuda 70
    db.session.add_all([v1, v2, v3, v4])
    db.session.commit()

    p1 = Pago(venta_id=v4.id, monto=Decimal('30.00'), usuario_id=vendedor_user.id, metodo_pago='transferencia')
    db.session.add(p1)
    db.session.commit()

    v5 = Venta(cliente_id=cliente3.id, almacen_id=almacen2.id, vendedor_id=admin_user.id, total=Decimal('50.00'), estado_pago='pendiente', tipo_pago='credito') # Deuda 50
    v6 = Venta(cliente_id=cliente4.id, almacen_id=almacen1.id, vendedor_id=vendedor_user.id, total=Decimal('100.00'), estado_pago='pagado', tipo_pago='contado')
    db.session.add_all([v5, v6])

    db.session.commit()

def _clean_active_db_session():
    if not has_app_context():
        return
    try:
        db.session.rollback()
    finally:
        db.session.remove()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    """Rollback/remove la sesión activa antes de que los fixtures hagan DDL.

    Varios fixtures de función ejecutan ``db.drop_all()`` en su teardown y el
    fixture autouse ``clean_db_session`` se finaliza después. En PostgreSQL
    eso dejaba una conexión idle in transaction con SELECTs abiertos que
    bloqueaba el DROP TABLE. El hook corre antes de cualquier finalizador de
    fixture; las aserciones del test ya terminaron, así que no se destruyen
    datos antes de los asserts.
    """
    _clean_active_db_session()


@pytest.fixture(autouse=True)
def clean_db_session():
    """Rollback y remove de la sesión del app_context vigente antes y después de cada test."""
    _clean_active_db_session()
    yield
    _clean_active_db_session()
