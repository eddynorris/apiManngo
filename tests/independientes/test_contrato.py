"""Pruebas independientes del contrato solicitado; únicamente SQLite y dobles."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from flask_jwt_extended import create_access_token

from extensions import db
from models import (
    Inventario,
    Movimiento,
    Pago,
    PresentacionProducto,
    TelegramUpdate,
    Users,
    Venta,
    VentaDetalle,
)
from resources import telegram_webhook_resource as webhook
from services import telegram_inbox as inbox
from services.telegram_service import telegram_service
from telegram.router import TelegramRouter


def mensaje(uid, chat=81):
    return {'update_id': uid, 'message': {'chat': {'id': chat}, 'text': f'mensaje-{uid}'}}


@pytest.fixture(autouse=True)
def entorno(test_client, monkeypatch):
    TelegramUpdate.query.delete()
    db.session.commit()
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', 'contrato')
    monkeypatch.delenv('TELEGRAM_BOT_SECRET_TOKEN', raising=False)
    dobles = {nombre: Mock() for nombre in ('message', 'callback', 'programar', 'aviso')}
    monkeypatch.setattr(TelegramRouter, 'handle_message', dobles['message'])
    monkeypatch.setattr(TelegramRouter, 'handle_callback_query', dobles['callback'])
    monkeypatch.setattr(webhook, 'programar_procesamiento', dobles['programar'])
    monkeypatch.setattr(telegram_service, 'send_message', dobles['aviso'])
    monkeypatch.setattr(telegram_service, 'edit_message', Mock())
    return dobles


def fila(uid):
    db.session.expire_all()
    return db.session.get(TelegramUpdate, uid)


@pytest.mark.parametrize('caso', ['ausente', 'ruta', 'cabecera_ausente', 'cabecera_incorrecta'])
def test_secretos(test_client, monkeypatch, entorno, caso):
    ruta = 'contrato'
    if caso == 'ausente':
        monkeypatch.delenv('TELEGRAM_WEBHOOK_SECRET')
    if caso == 'ruta':
        ruta = 'equivocado'
    if caso.startswith('cabecera'):
        monkeypatch.setenv('TELEGRAM_BOT_SECRET_TOKEN', 'privado')
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'otro'} if caso.endswith('incorrecta') else {}
    assert test_client.post(f'/telegram/webhook/{ruta}', json=mensaje(1), headers=headers).status_code == 403
    assert TelegramUpdate.query.count() == 0
    entorno['programar'].assert_not_called()


@pytest.mark.parametrize('con_cabecera', [False, True])
def test_persistencia_y_duplicado(test_client, monkeypatch, entorno, con_cabecera):
    headers = {}
    if con_cabecera:
        monkeypatch.setenv('TELEGRAM_BOT_SECRET_TOKEN', 'privado')
        headers['X-Telegram-Bot-Api-Secret-Token'] = 'privado'

    def comprobar_persistido(app, uid):
        # Nueva conexión: prueba que se confirmó antes de programar/responder.
        with db.engine.connect() as connection:
            row = connection.execute(db.select(TelegramUpdate.__table__).where(
                TelegramUpdate.update_id == uid)).mappings().one()
            assert row['estado'] == 'recibido'
            assert row['payload'] == mensaje(1)
    entorno['programar'].side_effect = comprobar_persistido
    assert test_client.post('/telegram/webhook/contrato', json=mensaje(1), headers=headers).status_code == 200
    assert test_client.post('/telegram/webhook/contrato', json=mensaje(1, 99), headers=headers).status_code == 200
    assert TelegramUpdate.query.count() == 1
    assert fila(1).payload == mensaje(1)
    assert entorno['programar'].call_count == 1
    entorno['message'].assert_not_called()


@pytest.mark.parametrize('body', [[], [1], 'texto', None, 7, True, {}, {'update_id': True},
                                {'update_id': False}, {'update_id': 0}, {'update_id': -1},
                                {'update_id': '2'}, {'update_id': 1.5}])
def test_cuerpos_invalidos(test_client, entorno, body):
    import json
    response = test_client.post('/telegram/webhook/contrato', data=json.dumps(body),
                                content_type='application/json')
    assert response.status_code == 200
    assert TelegramUpdate.query.count() == 0
    entorno['programar'].assert_not_called()


@pytest.mark.parametrize('tipo', ['message', 'callback'])
def test_router_una_vez(entorno, tipo):
    payload = mensaje(2) if tipo == 'message' else {
        'update_id': 2, 'callback_query': {'id': 'cb', 'data': 'cancel',
                                        'message': {'chat': {'id': 81}}}}
    inbox.registrar_update(payload)
    inbox.procesar(2)
    inbox.procesar(2)
    assert fila(2).estado == 'procesado'
    entorno[tipo].assert_called_once_with(payload['message' if tipo == 'message' else 'callback_query'])
    entorno['callback' if tipo == 'message' else 'message'].assert_not_called()


def test_error_privado(entorno, caplog):
    secreto = 'TEXTO_PERSONAL_IRREPETIBLE_7821'
    payload = mensaje(3)
    payload['message']['text'] = secreto
    entorno['message'].side_effect = ValueError(secreto)
    inbox.registrar_update(payload)
    inbox.procesar(3)
    inbox.procesar(3)
    assert fila(3).estado == 'fallido'
    assert 'ValueError' in fila(3).ultimo_error
    assert secreto not in fila(3).ultimo_error
    assert secreto not in caplog.text
    entorno['message'].assert_called_once()


@pytest.mark.parametrize('bloqueo', ['recibido', 'procesando'])
def test_orden_y_chats_independientes(entorno, bloqueo):
    for uid, chat in [(10, 81), (11, 81), (12, 82)]:
        inbox.registrar_update(mensaje(uid, chat))
    fila(10).estado = bloqueo
    db.session.commit()
    inbox.procesar(11)
    assert fila(11).estado == 'recibido'
    entorno['message'].assert_not_called()
    inbox.procesar(12)
    assert fila(12).estado == 'procesado'
    if bloqueo == 'procesando':
        fila(10).estado = 'recibido'
        db.session.commit()
    inbox.procesar(10)
    assert [call.args[0]['text'] for call in entorno['message'].call_args_list] == [
        'mensaje-12', 'mensaje-10', 'mensaje-11']
    assert fila(11).estado == 'procesado'


def test_recuperacion_y_purga(entorno):
    ahora = datetime.now(UTC)
    casos = [(20, 'procesando', 11, 91), (21, 'recibido', 2, 92),
             (22, 'recibido', 16, 93), (23, 'recibido', 0, 94),
             (24, 'procesando', 11, -95)]
    for uid, estado, minutos, chat in casos:
        inbox.registrar_update(mensaje(uid, chat))
        row = fila(uid)
        row.estado = estado
        row.recibido_en = row.actualizado_en = ahora - timedelta(minutes=minutos)
        db.session.commit()
    for uid, estado in [(30, 'procesado'), (31, 'fallido'), (32, 'abandonado')]:
        inbox.registrar_update(mensaje(uid, uid))
        row = fila(uid)
        row.estado = estado
        row.recibido_en = ahora - timedelta(days=31)
        db.session.commit()
    resultado = inbox.procesar_pendientes(ahora=ahora)
    assert resultado == {'abandonados': 3, 'procesados': 1, 'errores': 0, 'purgados': 3}
    assert [call.args[0]['text'] for call in entorno['message'].call_args_list] == ['mensaje-21']
    assert sorted(call.args[0] for call in entorno['aviso'].call_args_list) == [91, 93]
    for uid in (20, 22, 24):
        assert fila(uid).estado == 'abandonado'
    assert fila(23).estado == 'recibido'
    assert all(fila(uid) is None for uid in (30, 31, 32))


def test_cli(test_client):
    result = test_client.application.test_cli_runner().invoke(args=['telegram-pendientes'])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize('ruta,esperado', [
    ('/telegram/webhook/privado', '/telegram/webhook/[oculto]'),
    ('/telegram/webhook/privado?a=1', '/telegram/webhook/[oculto]?a=1'),
    ('POST /telegram/webhook/privado HTTP/1.1', 'POST /telegram/webhook/[oculto] HTTP/1.1'),
    ('/reportes?a=1', '/reportes?a=1'),
])
def test_redaccion(ruta, esperado):
    from utils.gunicorn_logging import redactar_ruta
    assert redactar_ruta(ruta) == esperado


def admin_headers():
    return {'Authorization': 'Bearer ' + create_access_token(identity=str(
        Users.query.filter_by(rol='admin').first().id))}


@pytest.mark.parametrize('metodo', ['get', 'put', 'delete'])
def test_usuario_inexistente(test_client, metodo):
    response = getattr(test_client, metodo)('/usuarios/999999999', headers=admin_headers(),
                                             **({'json': {'username': 'valido'}} if metodo == 'put' else {}))
    assert response.status_code == 404, response.get_data(as_text=True)


def test_usuario_invalido_sin_cambios(test_client):
    user = Users.query.filter_by(rol='admin').first()
    antes = (user.username, user.rol, user.password, Users.query.count())
    response = test_client.put(f'/usuarios/{user.id}', headers=admin_headers(),
                               json={'username': ['invalido'], 'rol': 'superpoder'})
    db.session.refresh(user)
    assert (user.username, user.rol, user.password, Users.query.count()) == antes
    assert response.status_code == 400, response.get_data(as_text=True)


def test_venta_sin_stock_atomica(entorno):
    from services.stock_service import StockInsuficienteError
    from telegram.handlers.venta import VentaHandler
    inv = Inventario.query.first()
    venta = Venta.query.first()
    antes = (Venta.query.count(), Movimiento.query.count(),
             [(i.id, i.cantidad) for i in Inventario.query.order_by(Inventario.id)])
    context = {'almacen_id': inv.almacen_id, 'almacen_nombre': 'Prueba',
               'cliente_id': venta.cliente_id, 'total': 1500,
               'items': [{'presentacion_id': inv.presentacion_id, 'cantidad': 100,
                          'precio_unitario': 15}], 'pagos': []}
    with pytest.raises(StockInsuficienteError):
        VentaHandler.execute_venta(81, Users.query.filter_by(rol='admin').first(), context, 1)
    assert (Venta.query.count(), Movimiento.query.count(),
            [(i.id, i.cantidad) for i in Inventario.query.order_by(Inventario.id)]) == antes


@pytest.fixture()
def operaciones_lima(test_client):
    from models import Almacen, Cliente, Producto
    existente = Almacen.query.filter_by(nombre='Independiente Lima').first()
    if existente is not None:
        return existente.id
    almacen = Almacen(nombre='Independiente Lima', es_planta=True)
    producto = Producto(nombre='Independiente briqueta', precio_compra=Decimal('2'))
    cliente = Cliente(nombre='Independiente cliente')
    db.session.add_all([almacen, producto, cliente])
    db.session.flush()
    pres = PresentacionProducto(nombre='Independiente caja', producto_id=producto.id,
                                capacidad_kg=Decimal('2'), tipo='briqueta', precio_venta=10)
    db.session.add(pres)
    db.session.flush()
    admin = Users.query.filter_by(rol='admin').first()
    admin.almacen_id = almacen.id
    # Dos operaciones en días contiguos de Lima, ambas en el MISMO día UTC.
    for hora, cantidad, deposito in [(4, 3, 7), (5, 8, 19)]:
        instante = datetime(2024, 8, 18, hora, 30, tzinfo=UTC)
        venta = Venta(cliente_id=cliente.id, almacen_id=almacen.id, vendedor_id=admin.id,
                      fecha=instante, total=cantidad * 10, tipo_pago='contado', estado_pago='pagado')
        db.session.add(venta)
        db.session.flush()
        db.session.add_all([
            VentaDetalle(venta_id=venta.id, presentacion_id=pres.id,
                         cantidad=cantidad, precio_unitario=10),
            Pago(venta_id=venta.id, usuario_id=admin.id, fecha=instante,
                 monto=cantidad * 10, metodo_pago='efectivo', depositado=True,
                 monto_depositado=deposito, fecha_deposito=instante),
            Movimiento(tipo='entrada', tipo_operacion='ensamblaje',
                       presentacion_id=pres.id, cantidad=cantidad, fecha=instante,
                       usuario_id=admin.id, motivo='Prueba independiente Lima'),
        ])
    db.session.commit()
    return almacen.id


def buscar(data, key):
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = buscar(value, key)
            if found is not None:
                return found
    if isinstance(data, list):
        for value in data:
            found = buscar(value, key)
            if found is not None:
                return found
    return None


@pytest.mark.parametrize('dia,cantidad,deposito', [('2024-08-17', 3, 7), ('2024-08-18', 8, 19)])
@pytest.mark.parametrize('reporte', ['ventas-presentacion', 'resumen-financiero',
                                   'produccion-briquetas', 'produccion-entradas'])
def test_reportes_medianoche(test_client, operaciones_lima, reporte, dia, cantidad, deposito):
    response = test_client.get(f'/reportes/{reporte}', headers=admin_headers(), query_string={
        'fecha_inicio': dia, 'fecha_fin': dia, 'almacen_id': operaciones_lima, 'periodo': 'dia'})
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    if reporte == 'ventas-presentacion':
        assert Decimal(str(buscar(data, 'total_vendido'))) == cantidad * 10
    elif reporte == 'resumen-financiero':
        assert Decimal(str(buscar(data, 'total_ventas'))) == cantidad * 10
        assert Decimal(str(buscar(data, 'depositado_total'))) == deposito
    else:
        assert [row['fecha'] for row in data['resumen_temporal']] == [dia]
        if reporte == 'produccion-briquetas':
            assert data['resumen_temporal'][0]['unidades_producidas'] == cantidad
