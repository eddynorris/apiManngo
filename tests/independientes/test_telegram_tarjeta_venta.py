"""Tests independientes del contrato de la tarjeta de venta, con SQLite y dobles."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from extensions import db
from models import Almacen, Cliente, Inventario, Lote, PresentacionProducto, Users, Venta
from services.telegram_service import telegram_service
from telegram.state_machine import ConversationState, StateMachine


@pytest.fixture
def escenario(test_client, monkeypatch):
    """Datos propios por caso; restaura usuarios y elimina solo las filas creadas."""
    sufijo = uuid4().hex[:10]
    admin = Users.query.filter_by(username='admin').one()
    vendedor = Users.query.filter_by(username='vendedor').one()
    originales = [(u.id, deepcopy(u.telegram_context), u.telegram_chat_id) for u in (admin, vendedor)]
    a = Almacen(nombre=f'Tarjeta A {sufijo}', ciudad='Abancay')
    b = Almacen(nombre=f'Tarjeta B {sufijo}', ciudad='Andahuaylas')
    db.session.add_all([a, b])
    db.session.flush()
    c = Cliente(nombre=f'Juan Tarjeta {sufijo} Abancay', almacen_preferido_id=a.id)
    otro = Cliente(nombre=f'Juan Tarjeta {sufijo} Andahuaylas', almacen_preferido_id=b.id)
    base = PresentacionProducto.query.filter_by(nombre='Test Pres').one()
    p = PresentacionProducto(nombre=f'Carbón Tarjeta {sufijo}', producto_id=base.producto_id,
                             capacidad_kg=1, tipo='procesado', precio_venta=10)
    db.session.add_all([c, otro, p])
    db.session.flush()
    lotes = [Lote(producto_id=base.producto_id, cantidad_disponible_kg=100,
                  peso_humedo_kg=100, peso_seco_kg=100) for _ in range(2)]
    db.session.add_all(lotes)
    db.session.flush()
    inventarios = [Inventario(presentacion_id=p.id, almacen_id=al.id,
                             lote_id=lotes[idx % 2].id, cantidad=qty)
                   for idx, (al, qty) in enumerate([(a, 2), (a, 3), (b, 19)])]
    db.session.add_all(inventarios)
    admin.telegram_chat_id = 700001
    vendedor.telegram_chat_id = 700002
    db.session.commit()
    borrador = dict(action='venta', cliente_id=c.id, cliente_nombre=c.nombre,
                    almacen_id=a.id, almacen_nombre=a.nombre, total=20.0, pagos=[],
                    gasto_asociado=None, estado='completado', fecha=None, items=[dict(
                        presentacion_id=p.id, producto_id=p.id, producto_nombre=p.nombre,
                        cantidad=2, precio_unitario=10.0, subtotal=20.0, stock_actual=999)])
    ids_mensajes = count(901)
    send = Mock(side_effect=lambda chat_id, text, reply_markup=None: {
        'ok': True, 'result': {'message_id': next(ids_mensajes)}})
    edit = Mock(return_value={'ok': True})
    monkeypatch.setattr(telegram_service, 'send_message', send)
    monkeypatch.setattr(telegram_service, 'edit_message', edit)
    ids_clientes = [c.id, otro.id]
    pres_id, almacen_ids, lote_ids = p.id, [a.id, b.id], [lote.id for lote in lotes]
    try:
        yield SimpleNamespace(a=a, b=b, c=c, otro=otro, p=p, admin=admin,
                              vendedor=vendedor, data=borrador, send=send, edit=edit,
                              busqueda=f'juan tarjeta {sufijo}')
    finally:
        db.session.rollback()
        for user_id, contexto, chat_id in originales:
            u = db.session.get(Users, user_id)
            u.telegram_context = contexto
            u.telegram_chat_id = chat_id
        Venta.query.filter(Venta.cliente_id.in_(ids_clientes)).delete(synchronize_session=False)
        Inventario.query.filter_by(presentacion_id=pres_id).delete(synchronize_session=False)
        Cliente.query.filter(Cliente.id.in_(ids_clientes)).delete(synchronize_session=False)
        Lote.query.filter(Lote.id.in_(lote_ids)).delete(synchronize_session=False)
        PresentacionProducto.query.filter_by(id=pres_id).delete(synchronize_session=False)
        Almacen.query.filter(Almacen.id.in_(almacen_ids)).delete(synchronize_session=False)
        db.session.commit()


def botones(markup):
    return [b for fila in (markup or {}).get('inline_keyboard', []) for b in fila]


def mensajes(mock):
    return [(c.args[1] if len(c.args) > 1 else c.kwargs['text'],
             c.args[2] if len(c.args) > 2 else c.kwargs.get('reply_markup'))
            for c in mock.call_args_list]


def tarjetas(e):
    return [(txt, markup) for txt, markup in mensajes(e.send)
            if 'confirm:venta' in {b['callback_data'] for b in botones(markup)}]


def confirmar_nueva(e, user):
    cards = tarjetas(e)
    assert len(cards) == 1, 'Debe enviar exactamente una tarjeta nueva'
    txt, _ = cards[0]
    for contenido in ('Confirmar', 'Total', e.p.nombre):
        assert contenido in txt
    contexto = StateMachine.get_context(user)
    assert contexto.state == ConversationState.AWAITING_CONFIRMATION
    indice_tarjeta = next(i for i, (_, markup) in enumerate(mensajes(e.send))
                          if 'confirm:venta' in {b['callback_data'] for b in botones(markup)})
    assert contexto.message_id == 901 + indice_tarjeta
    assert contexto.data['cliente_nombre'] in txt
    assert contexto.data['almacen_nombre'] in txt
    assert e.edit.called, 'Debe retirar los botones del mensaje anterior'
    llamada = e.edit.call_args
    assert llamada.args[:2] == (user.telegram_chat_id, 41)
    markup = llamada.args[3] if len(llamada.args) > 3 else llamada.kwargs.get('reply_markup')
    assert markup is None
    return contexto.data


def guardar(e, user, edit_action=None):
    if edit_action:
        StateMachine.transition_to(user, ConversationState.AWAITING_EXTRA_DATA,
                                   action=edit_action,
                                   data={'original_context': deepcopy(e.data), 'message_id': 41})
    else:
        StateMachine.set_awaiting_confirmation(user, 'venta', deepcopy(e.data), 41)


def editar(e, accion, valor):
    from telegram.router import TelegramRouter

    guardar(e, e.admin, accion)
    TelegramRouter._handle_edit_response(e.admin.telegram_chat_id, e.admin, dict(
        action='edit_response', edit_action=accion, value=valor,
        original_context=deepcopy(e.data), message_id=41))


def callback(e, user, data):
    from telegram.router import TelegramRouter

    TelegramRouter.handle_callback_query(dict(id='callback-tarjeta', data=data,
        **{'from': {'id': user.telegram_chat_id}},
        message={'message_id': 41, 'chat': {'id': user.telegram_chat_id, 'type': 'private'}}))


def venta(e, almacen, fecha, cliente=None):
    v = Venta(cliente_id=(cliente or e.c).id, almacen_id=almacen.id, vendedor_id=e.admin.id,
              fecha=fecha, total=10, tipo_pago='credito')
    db.session.add(v)
    db.session.commit()
    return v


def test_zona_cliente_preferido_ultima_venta_y_none(escenario):
    from telegram.resolvers import zona_cliente

    e = escenario
    ahora = datetime.now(UTC)
    venta(e, e.b, ahora)
    assert zona_cliente(e.c).id == e.a.id
    e.c.almacen_preferido_id = None
    db.session.commit()
    venta(e, e.a, ahora - timedelta(days=1))  # ID mayor, fecha menor.
    assert zona_cliente(e.c).id == e.b.id
    venta(e, e.a, ahora)  # Empate de fecha: gana ID mayor.
    assert zona_cliente(e.c).id == e.a.id
    Venta.query.filter_by(cliente_id=e.c.id).delete()
    db.session.commit()
    assert zona_cliente(e.c) is None


@pytest.mark.parametrize('exacto', [False, True])
def test_buscar_cliente_prioriza_preferido_y_no_bloquea_otras_zonas(escenario, exacto):
    from telegram.resolvers import buscar_cliente_db

    e = escenario
    if exacto:
        e.otro.nombre = e.c.nombre
        db.session.commit()
    nombre = e.c.nombre.upper() if exacto else e.busqueda
    assert buscar_cliente_db(nombre, almacen_id=e.b.id).id == e.otro.id
    assert buscar_cliente_db(nombre, almacen_id=e.a.id).id == e.c.id
    assert buscar_cliente_db(e.c.nombre, almacen_id=e.b.id).id == (e.otro.id if exacto else e.c.id)


def test_buscar_cliente_prioriza_ventas_si_no_hay_preferido(escenario):
    from telegram.resolvers import buscar_cliente_db

    e = escenario
    e.c.almacen_preferido_id = e.otro.almacen_preferido_id = None
    venta(e, e.b, datetime.now(UTC), cliente=e.otro)
    assert buscar_cliente_db(e.busqueda, almacen_id=e.b.id).id == e.otro.id


@pytest.mark.parametrize(('entrada', 'esperado'), [
    ('20 flete', (Decimal('20'), 'flete')),
    ('15.50', (Decimal('15.50'), 'Gasto de venta')),
    ('S/ 12,5 movilidad', (Decimal('12.5'), 'movilidad')),
    ('flete', None), ('', None), ('0', None), ('-5', None),
])
def test_parsear_gasto(entrada, esperado):
    from telegram.resolvers import parsear_gasto

    assert parsear_gasto(entrada) == esperado


def test_almacenes_permitidos_admin_usuario_y_sin_asignacion(escenario):
    from telegram.resolvers import almacenes_permitidos

    e = escenario
    assert [a.id for a in almacenes_permitidos(e.admin)] == [
        a.id for a in Almacen.query.order_by(Almacen.nombre).all()]
    assert [a.id for a in almacenes_permitidos(e.vendedor)] == [e.vendedor.almacen_id]
    assert almacenes_permitidos(SimpleNamespace(rol='gerente', almacen_id=None)) == []


@pytest.mark.parametrize('caso', ['base', 'stock', 'zona', 'sin_permiso', 'planta',
                                  'misma_zona', 'sin_zona', 'gasto_avisos'])
def test_construir_tarjeta_venta(escenario, caso):
    from telegram.handlers.venta import construir_tarjeta_venta

    e = escenario
    user = e.admin
    if caso in ('zona', 'sin_permiso', 'planta'):
        e.c.almacen_preferido_id = e.b.id
    if caso == 'sin_permiso':
        user = e.vendedor
    if caso == 'planta':
        e.a.es_planta = True
    if caso == 'sin_zona':
        e.c.almacen_preferido_id = None
    if caso == 'base':
        e.data['items'][0]['cantidad'] = 4  # Requiere sumar ambos lotes de A.
    if caso == 'stock':
        e.data['items'][0]['cantidad'] = 6  # A suma 5; B tiene 19; cache dice 999.
    if caso == 'gasto_avisos':
        e.data['gasto_asociado'] = {'monto': 20, 'descripcion': 'Flete independiente'}
    db.session.commit()
    if caso == 'gasto_avisos':
        e.data['avisos'] = ['Aviso independiente']
    txt, markup = construir_tarjeta_venta(e.data, user)
    btns = botones(markup)
    codigos = {b['callback_data'] for b in btns}
    assert {'edit:cliente', 'edit:precio', 'edit:gasto', 'edit:almacen',
            'confirm:venta', 'cancel'} <= codigos
    for contenido in (e.c.nombre, e.a.nombre, e.p.nombre, 'Total'):
        assert contenido in txt
    if caso == 'base':
        assert 'Stock insuficiente' not in txt
    if caso == 'stock':
        assert 'Stock insuficiente' in txt
    if caso in ('zona', 'sin_permiso'):
        assert f'{e.c.nombre} es cliente de {e.b.nombre}' in txt
        if caso == 'zona':
            assert any(f'Vender desde {e.b.nombre}' in b['text'] and b['callback_data'] == f'almacen:{e.b.id}'
                       for b in btns)
        else:
            assert not any(b['callback_data'].startswith('almacen:') for b in btns)
    if caso == 'planta':
        assert any(linea.startswith('ℹ️') and e.b.nombre in linea for linea in txt.splitlines())
        assert not any(b['callback_data'].startswith('almacen:') for b in btns)
    if caso in ('misma_zona', 'sin_zona'):
        assert 'es cliente de' not in txt
    if caso == 'gasto_avisos':
        assert all(s in txt for s in ('20', 'Flete independiente', 'Aviso independiente'))


@pytest.mark.parametrize(('accion', 'valor'), [('edit_gasto', '20 flete'),
                                              ('edit_cliente', 'existente'),
                                              ('edit_precio', '40')])
def test_edicion_valida_envia_tarjeta_y_actualiza_contexto(escenario, accion, valor):
    e = escenario
    editar(e, accion, e.otro.nombre if valor == 'existente' else valor)
    data = confirmar_nueva(e, e.admin)
    if accion == 'edit_gasto':
        assert Decimal(str(data['gasto_asociado']['monto'])) == 20
        assert data['gasto_asociado']['descripcion'] == 'flete'
    elif accion == 'edit_cliente':
        assert (data['cliente_id'], data['cliente_nombre']) == (e.otro.id, e.otro.nombre)
    else:
        assert data['total'] == 40


@pytest.mark.parametrize(('accion', 'valor'), [('edit_gasto', 'abc'),
                                              ('edit_cliente', 'Cliente Inexistente ZZZQW')])
def test_edicion_invalida_conserva_borrador_y_permite_reintentar(escenario, accion, valor):
    e = escenario
    editar(e, accion, valor)
    assert e.send.called
    assert not tarjetas(e)
    ctx = StateMachine.get_context(e.admin)
    assert ctx.state == ConversationState.AWAITING_EXTRA_DATA
    assert ctx.action == accion
    assert ctx.data['original_context'] == e.data
    if accion == 'edit_cliente':
        assert any('No encontré' in txt for txt, _ in mensajes(e.send))


def test_callback_edit_almacen_solo_ofrece_permitidos(escenario):
    from telegram.resolvers import almacenes_permitidos

    e = escenario
    guardar(e, e.vendedor)
    callback(e, e.vendedor, 'edit:almacen')
    codigos = [b['callback_data'] for _, markup in mensajes(e.send) for b in botones(markup)]
    assert sorted(codigos) == sorted(f'almacen:{a.id}'
                                    for a in almacenes_permitidos(e.vendedor))
    assert codigos == [f'almacen:{e.vendedor.almacen_id}']


@pytest.mark.parametrize('estado_edicion', [False, True])
def test_callback_almacen_con_permiso_recalcula_stock(escenario, estado_edicion):
    e = escenario
    guardar(e, e.admin, 'edit_almacen' if estado_edicion else None)
    callback(e, e.admin, f'almacen:{e.b.id}')
    data = confirmar_nueva(e, e.admin)
    assert (data['almacen_id'], data['almacen_nombre']) == (e.b.id, e.b.nombre)
    assert data['items'][0]['stock_actual'] == 19


def test_callback_almacen_sin_permiso_no_cambia_borrador(escenario):
    e = escenario
    guardar(e, e.vendedor)
    antes = deepcopy(e.vendedor.telegram_context)
    norte = Almacen.query.filter_by(nombre='Almacén Norte').one()
    callback(e, e.vendedor, f'almacen:{norte.id}')
    assert e.vendedor.telegram_context == antes
    assert not tarjetas(e)
    assert any('No tienes permiso' in txt for txt, _ in mensajes(e.send))


@pytest.mark.parametrize('nuevo', [True, False])
def test_prepare_venta_zona_ciudad_y_tarjeta(escenario, nuevo):
    from telegram.handlers.venta import VentaHandler
    from telegram.resolvers import buscar_presentacion, resolver_almacen

    e = escenario
    telefono = '9' + str(int(uuid4().hex[:8], 16) % 100000000).zfill(8)
    assert Cliente.query.filter_by(telefono=telefono).first() is None
    args = {'cliente_nombre': e.c.nombre, 'items': [
        {'producto_nombre': e.p.nombre, 'cantidad': 2}], 'condicion_pago': 'credito'}
    if nuevo:
        args['cliente_telefono'] = telefono
    else:
        e.c.almacen_preferido_id = e.b.id
        db.session.commit()
    try:
        VentaHandler.prepare_venta(e.admin.telegram_chat_id, e.admin, args, e.a.nombre,
                                  resolver_almacen, buscar_presentacion)
        assert len(tarjetas(e)) == 1
        if nuevo:
            cli = Cliente.query.filter_by(telefono=telefono).one()
            assert cli.almacen_preferido_id == e.a.id
            assert cli.ciudad == e.a.ciudad
        else:
            assert 'es cliente de' in tarjetas(e)[0][0]
        contexto = StateMachine.get_context(e.admin)
        assert contexto.state == ConversationState.AWAITING_CONFIRMATION
        indice_tarjeta = next(i for i, (_, markup) in enumerate(mensajes(e.send))
                              if 'confirm:venta' in {b['callback_data'] for b in botones(markup)})
        assert contexto.message_id == 901 + indice_tarjeta
        data = contexto.data
        assert data['almacen_id'] == e.a.id
        assert data['items'][0]['presentacion_id'] == e.p.id
    finally:
        db.session.rollback()
        Cliente.query.filter_by(telefono=telefono).delete(synchronize_session=False)
        db.session.commit()
