"""Contrato independiente GRE por Telegram: SQLite y transportes sintéticos."""

import base64
import re
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from itertools import count
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture(scope='module')
def certificado():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'GRE TELEGRAM SINTETICA')])
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime(2020, 1, 1, tzinfo=UTC))
            .not_valid_after(datetime(2035, 1, 1, tzinfo=UTC)).sign(key, hashes.SHA256()))
    return base64.b64encode(pkcs12.serialize_key_and_certificates(
        b'local', key, cert, None,
        serialization.BestAvailableEncryption(b'clave-sintetica'))).decode()


@pytest.fixture
def e(test_client, monkeypatch, certificado):
    import models as m
    from extensions import db
    from services import guia_service as gs
    from services.sunat_service import GreEnvioResult
    from services.telegram_service import telegram_service

    tablas = list(db.metadata.sorted_tables)
    snapshot = {t.name: [dict(r) for r in db.session.execute(db.select(t)).mappings()]
                for t in tablas}
    for key, value in {'SUNAT_RUC': '20999999999',
                       'SUNAT_RAZON_SOCIAL': 'GRE TELEGRAM SINTETICA SAC',
                       'SUNAT_EMISOR_DIRECCION': 'AV LOCAL 123',
                       'SUNAT_EMISOR_UBIGEO': '150101', 'SUNAT_SERIE_GRE': 'T991',
                       'SUNAT_ENVIO_HABILITADO': 'true',
                       'SUNAT_CERT_P12_B64': certificado,
                       'SUNAT_CERT_PASSWORD': 'clave-sintetica'}.items():
        monkeypatch.setenv(key, value)
    admin = m.Users.query.filter_by(username='admin').one()
    vendedor = m.Users.query.filter_by(username='vendedor').one()
    admin.telegram_chat_id, vendedor.telegram_chat_id = 799001, 799002
    admin.telegram_context = vendedor.telegram_context = None
    cliente = m.Cliente(nombre='Destinatario GRE Telegram', tipo_documento='6',
                        ruc='20999999998', direccion='CALLE LOCAL 456', ubigeo='150102')
    vehiculo = m.Vehiculo(placa='GRE991', activo=True, predeterminado=True)
    conductor = m.Conductor(nombres='PRUEBA', apellidos='TELEGRAM',
                            numero_documento='00000991', licencia='Q00000991',
                            activo=True, predeterminado=True)
    db.session.add_all([cliente, vehiculo, conductor])
    db.session.flush()
    origen = m.Almacen(nombre='Planta GRE Telegram', direccion='AV LOCAL 123',
                       ubigeo='150101', es_planta=True)
    destino = m.Almacen(nombre='Destino GRE Telegram', direccion='CALLE LOCAL 456',
                        ubigeo='150102', cliente_guia_id=cliente.id)
    base = m.PresentacionProducto.query.filter_by(nombre='Test Pres').one()
    p = m.PresentacionProducto(nombre='Saco GRE Telegram', producto_id=base.producto_id,
                               capacidad_kg=20, descripcion_guia='SACOS DE CARBÓN',
                               precio_venta=10, tipo='procesado')
    db.session.add_all([origen, destino, p])
    db.session.flush()
    lote = m.Lote(producto_id=base.producto_id, cantidad_disponible_kg=1000,
                  peso_humedo_kg=1000, peso_seco_kg=1000)
    db.session.add(lote)
    db.session.flush()
    inv = m.Inventario(almacen_id=origen.id, presentacion_id=p.id,
                       lote_id=lote.id, cantidad=10)
    db.session.add(inv)
    db.session.commit()
    ids = count(901)
    envios = []

    def enviar(chat_id, text, *args, **kwargs):
        message_id = next(ids)
        envios.append((text, message_id))
        return {'ok': True, 'result': {'message_id': message_id}}

    send = Mock(side_effect=enviar)
    edit, document = Mock(return_value={'ok': True}), Mock(return_value={'ok': True})
    monkeypatch.setattr(telegram_service, 'send_message', send)
    monkeypatch.setattr(telegram_service, 'edit_message', edit)
    monkeypatch.setattr(telegram_service, 'send_document', document)
    monkeypatch.setattr(telegram_service, 'answer_callback_query', Mock())
    transporte = Mock(spec=['emitir_guia_remision', 'consultar_estado_ticket'])
    transporte.emitir_guia_remision.side_effect = lambda prepared: GreEnvioResult(
        'enviado', f'{prepared.data.serie}-{prepared.data.numero:08d}',
        prepared.package.filename, 'TICKET-LOCAL', 200)
    monkeypatch.setattr(gs, 'sunat_service', transporte)
    # También interceptar el singleton usado por el handler legado.
    from services.sunat_service import sunat_service
    monkeypatch.setattr(sunat_service, 'emitir_guia_remision',
                        transporte.emitir_guia_remision)
    monkeypatch.setattr(sunat_service, 'consultar_estado_ticket',
                        transporte.consultar_estado_ticket)
    try:
        yield SimpleNamespace(m=m, db=db, gs=gs, admin=admin, vendedor=vendedor,
                              cliente=cliente, vehiculo=vehiculo, conductor=conductor,
                              origen=origen, destino=destino, p=p, inv=inv,
                              send=send, edit=edit, document=document, transporte=transporte,
                              envios=envios)
    finally:
        db.session.rollback()
        db.session.remove()
        # Restaurar también filas modificadas (usuarios, stock y series) tras commits.
        for tabla in reversed(tablas):
            db.session.execute(tabla.delete())
        for tabla in tablas:
            if snapshot[tabla.name]:
                db.session.execute(tabla.insert(), snapshot[tabla.name])
        db.session.commit()


def contexto(e, venta=False):
    if venta:
        v = e.m.Venta(cliente_id=e.cliente.id, almacen_id=e.origen.id,
                       vendedor_id=e.admin.id, total=20, tipo_pago='credito')
        e.db.session.add(v)
        e.db.session.flush()
        e.db.session.add(e.m.VentaDetalle(venta_id=v.id, presentacion_id=e.p.id,
                                          cantidad=2, precio_unitario=10))
        e.db.session.commit()
        b = e.gs.borrador_venta(e.admin, v.id)
    else:
        b = e.gs.borrador_traslado(e.admin, e.origen.id, e.destino.id,
                                   [{'presentacion_id': e.p.id, 'cantidad': 2}])
    return {'action': 'guia_remision', 'borrador': b, 'idempotency_key': 'a' * 32}


def botones(markup):
    return [b for fila in (markup or {}).get('inline_keyboard', []) for b in fila]


def mensajes(mock, edit=False):
    idx = 2 if edit else 1
    return [(c.args[idx] if len(c.args) > idx else c.kwargs['text'],
             c.args[idx + 1] if len(c.args) > idx + 1 else c.kwargs.get('reply_markup'))
            for c in mock.call_args_list]


def textos(e):
    return '\n'.join(t for t, _ in mensajes(e.send) + mensajes(e.edit, True))


def codigos(e):
    return [b['callback_data'] for _, markup in mensajes(e.send) for b in botones(markup)]


def guardar(e, ctx):
    from telegram.state_machine import StateMachine

    StateMachine.set_awaiting_confirmation(e.admin, ctx['action'], deepcopy(ctx), 41)


def callback(e, data, message_id=41):
    from telegram.router import TelegramRouter

    chat = e.admin.telegram_chat_id
    TelegramRouter.handle_callback_query({'id': 'callback-gre', 'from': {'id': chat},
        'data': data, 'message': {'chat': {'id': chat, 'type': 'private'},
                                 'message_id': message_id}})


def tarjeta_nueva(e):
    from telegram.state_machine import ConversationState, StateMachine

    ctx = StateMachine.get_context(e.admin)
    assert ctx.state == ConversationState.AWAITING_CONFIRMATION
    assert ctx.action == 'guia_remision'
    cards = [(i, t) for i, (t, _) in enumerate(mensajes(e.send)) if 'Guía de Remisión' in t]
    assert len(cards) == 1
    assert ctx.message_id == next(mid for txt, mid in reversed(e.envios)
                                       if txt == cards[0][1])
    return ctx.data, cards[0][1]


def test_transferencia_claims_fuera_request_flush_sin_commit(e, monkeypatch):
    from flask import has_request_context

    from resources.transferencia_resource import TransferenciaService

    assert not has_request_context()
    with monkeypatch.context() as patcher:
        commit = Mock(side_effect=AssertionError('El servicio no debe hacer commit'))
        patcher.setattr(e.db.session, 'commit', commit)
        servicio = TransferenciaService({'almacen_origen_id': e.origen.id,
            'almacen_destino_id': e.destino.id,
            'transferencias': [{'presentacion_id': e.p.id, 'cantidad': 2}]},
            claims={'sub': e.admin.id, 'rol': e.admin.rol, 'almacen_id': e.admin.almacen_id})
        servicio.ejecutar_transferencia()
        assert len(servicio.movimiento_ids) == 2
        movs = e.m.Movimiento.query.filter(e.m.Movimiento.id.in_(servicio.movimiento_ids)).all()
        assert {m.tipo for m in movs} == {'entrada', 'salida'}
        assert all(m.cantidad == 2 for m in movs)
        assert e.inv.cantidad == 8
        commit.assert_not_called()


@pytest.mark.parametrize('caso', ['traslado01', 'traslado04', 'venta', 'faltantes', 'apagado'])
def test_tarjeta_contenido_y_botones(e, monkeypatch, caso):
    from telegram.handlers.guia_sunat import construir_tarjeta_guia

    if caso == 'traslado04':
        e.origen.codigo_establecimiento_sunat = '0001'
        e.destino.codigo_establecimiento_sunat = '0002'
        e.db.session.commit()
    ctx = contexto(e, venta=caso == 'venta')
    if caso == 'faltantes':
        ctx['borrador']['vehiculo_id'] = None
    if caso == 'apagado':
        monkeypatch.setenv('SUNAT_ENVIO_HABILITADO', 'false')
    b = ctx['borrador']
    txt, markup = construir_tarjeta_guia(ctx, e.admin)
    btns = botones(markup)
    codes = {b['callback_data'] for b in btns}
    for fragmento in ('Guía de Remisión', e.origen.nombre,
                      e.cliente.nombre if caso == 'venta' else e.destino.nombre,
                      'kg', date.fromisoformat(b['fecha_inicio_traslado']).strftime('%d/%m/%Y'),
                      e.conductor.nombres):
        assert fragmento in txt
    assert ('Traslado entre establecimientos' if caso == 'traslado04' else 'Venta') in txt
    if caso != 'traslado04':
        assert e.cliente.nombre in txt
    if caso != 'faltantes':
        assert e.vehiculo.placa in txt
    assert all(item['descripcion'] in txt for item in b['bienes'])
    assert str(b['peso_total_kg']) in txt
    assert {'guia:vehiculo', 'guia:conductor', 'guia:fecha', 'cancel'} <= codes
    assert ('guia:destinatario' in codes) == (caso != 'venta' and b['motivo'] == '01')
    assert ('confirm:guia_remision' in codes) == (caso not in {'faltantes', 'apagado'})
    if caso == 'faltantes':
        assert 'Falta' in txt
        assert all(f in txt for f in e.gs.faltantes(b))
    elif caso == 'apagado':
        assert 'deshabilitado' in txt.lower()
    else:
        assert any('Emitir' in b['text'] for b in btns
                   if b['callback_data'] == 'confirm:guia_remision')
    if caso != 'venta':
        assert 'traslado de inventario' in txt.lower()


def test_enviar_tarjeta_guarda_estado_y_retira_botones(e):
    from telegram.handlers.guia_sunat import enviar_tarjeta_guia

    ctx = contexto(e)
    enviar_tarjeta_guia(e.admin.telegram_chat_id, e.admin, ctx, message_id_anterior=41)
    nuevo, _ = tarjeta_nueva(e)
    assert nuevo == ctx
    assert e.edit.call_args.args[:2] == (e.admin.telegram_chat_id, 41)
    assert mensajes(e.edit, True)[-1][1] is None


@pytest.mark.parametrize('caso', ['traslado', 'venta', 'destino', 'sin_destino', 'producto',
                                  'placa', 'conductor', 'permiso', 'venta_sin_permiso',
                                  'origen_planta', 'origen_usuario'])
def test_prepare_resuelve_o_rechaza(e, monkeypatch, caso):
    from telegram.handlers.guia_sunat import GuiaSunatHandler
    from telegram.resolvers import buscar_presentacion, resolver_almacen

    args = {'almacen_origen': e.origen.nombre.lower(),
            'almacen_destino': e.destino.nombre.upper(),
            'items': [{'producto_nombre': e.p.nombre, 'cantidad': 2}],
            'placa': 'gre-991', 'conductor': e.conductor.numero_documento,
            'fecha_inicio': '2030-12-20'}
    user, error = e.admin, None
    if caso.startswith('venta'):
        args = {'venta_id': contexto(e, True)['borrador']['venta_id']}
    if caso == 'destino':
        args['almacen_destino'], error = 'Destino inexistente ZZZ', 'destino'
    elif caso == 'sin_destino':
        args.pop('almacen_destino')
        error = 'destino'
    elif caso == 'producto':
        args['items'][0]['producto_nombre'] = error = 'ZZZXQW987654'
        buscar_presentacion = Mock(return_value=None)
    elif caso == 'placa':
        args['placa'], error = 'ZZZ999', 'ZZZ999'
    elif caso == 'conductor':
        args['conductor'], error = 'Conductor inexistente ZZZ', 'conductor'
    elif caso in {'permiso', 'venta_sin_permiso'}:
        user, error = e.vendedor, 'permiso'
        if caso == 'permiso':
            args['almacen_destino'] = 'Almacén Norte'
    elif caso in {'origen_planta', 'origen_usuario'}:
        args.pop('almacen_origen')
        if caso == 'origen_usuario':
            e.origen.es_planta = False
            e.admin.almacen_id = e.origen.id
            e.db.session.commit()
    GuiaSunatHandler.prepare_guia_remision(user.telegram_chat_id, user, args, '',
                                          resolver_almacen, buscar_presentacion)
    if error:
        assert error.lower() in textos(e).lower()
        assert 'confirm:guia_remision' not in codigos(e)
        assert 'Guía de Remisión' not in '\n'.join(t for t, _ in mensajes(e.send))
    else:
        ctx, _ = tarjeta_nueva(e)
        assert re.fullmatch('[0-9a-f]{32}', ctx['idempotency_key'])
        b = ctx['borrador']
        assert b['almacen_origen_id'] == e.origen.id
        assert b['destinatario_cliente_id'] == e.cliente.id
        assert b['bienes'][0]['presentacion_id'] == e.p.id
        assert b['bienes'][0]['cantidad'] == 2
        assert b['origen_tipo'] == ('venta' if caso == 'venta' else 'traslado')
        if caso != 'venta':
            assert b['fecha_inicio_traslado'] == '2030-12-20'
            assert b['vehiculo_id'] == e.vehiculo.id
            assert b['conductor_id'] == e.conductor.id
    assert e.m.GuiaRemision.query.count() == 0
    assert e.inv.cantidad == 10


@pytest.mark.parametrize('caso', ['traslado', 'venta', 'faltantes', 'apagado', 'stock',
                                  'incierta', 'error'])
def test_execute_efectos_mensajes_y_limpieza(e, monkeypatch, caso):
    from services.sunat_service import SunatAuthError, SunatTransmissionUncertain
    from telegram.handlers.guia_sunat import GuiaSunatHandler
    from telegram.state_machine import ConversationState, StateMachine

    ctx = contexto(e, venta=caso == 'venta')
    if caso == 'faltantes':
        ctx['borrador']['vehiculo_id'] = None
    elif caso == 'apagado':
        monkeypatch.setenv('SUNAT_ENVIO_HABILITADO', 'false')
    elif caso == 'stock':
        ctx['borrador']['bienes'][0]['cantidad'] = 100
    elif caso in {'incierta', 'error'}:
        e.transporte.emitir_guia_remision.side_effect = (
            SunatTransmissionUncertain('incierta sintética') if caso == 'incierta'
            else SunatAuthError('error sintético'))
    guardar(e, ctx)
    antes = e.m.Movimiento.query.count()
    GuiaSunatHandler.execute_guia_remision(e.admin.telegram_chat_id, e.admin, ctx, 41)
    if caso in {'faltantes', 'apagado', 'stock'}:
        assert e.m.GuiaRemision.query.count() == e.m.SerieGre.query.count() == 0
        assert e.m.Movimiento.query.count() == antes
        assert e.db.session.get(e.m.Inventario, e.inv.id).cantidad == 10
        e.transporte.emitir_guia_remision.assert_not_called()
        assert textos(e)
        if caso == 'stock':
            assert 'stock' in textos(e).lower()
        elif caso == 'apagado':
            assert 'deshabilitado' in textos(e).lower()
        else:
            assert 'vehículo' in textos(e).lower() or 'vehiculo' in textos(e).lower()
        assert e.edit.called
    else:
        g = e.m.GuiaRemision.query.one()
        assert g.estado == (caso if caso in {'incierta', 'error'} else 'enviada')
        if caso == 'traslado':
            movs = e.m.Movimiento.query.filter_by(guia_remision_id=g.id).all()
            assert len(movs) == 2
            assert {m.tipo for m in movs} == {'entrada', 'salida'}
            assert all(m.cantidad == 2 and m.presentacion_id == e.p.id for m in movs)
            assert e.inv.cantidad == 8
            assert e.m.Inventario.query.filter_by(almacen_id=e.destino.id,
                                                  presentacion_id=e.p.id).one().cantidad == 2
        elif caso == 'venta':
            assert e.m.Movimiento.query.count() == antes
            assert e.inv.cantidad == 10
        if caso == 'incierta':
            assert 'incierta' in textos(e).lower() and 'SOL' in textos(e)
        elif caso == 'error':
            assert 'No se pudo' in textos(e)
        else:
            assert e.gs.numero_completo(g) in textos(e) and 'ticket' in textos(e).lower()
    assert StateMachine.get_context(e.admin).state == ConversationState.IDLE


@pytest.mark.parametrize('tipo', ['vehiculo', 'conductor'])
def test_callback_selector_activos_y_seleccion(e, tipo):
    if tipo == 'vehiculo':
        nuevo = e.m.Vehiculo(placa='GRE992', activo=True)
        inactivo = e.m.Vehiculo(placa='GRE993', activo=False)
        modelo = e.m.Vehiculo
        esperado = nuevo.placa
    else:
        nuevo = e.m.Conductor(nombres='OTRO', apellidos='SINTETICO',
                              numero_documento='00000992', licencia='Q00000992', activo=True)
        inactivo = e.m.Conductor(nombres='INACTIVO', apellidos='SINTETICO',
                                 numero_documento='00000993', licencia='Q00000993', activo=False)
        modelo, esperado = e.m.Conductor, nuevo.nombres
    e.db.session.add_all([nuevo, inactivo])
    e.db.session.commit()
    guardar(e, contexto(e))
    callback(e, f'guia:{tipo}')
    assert {c for c in codigos(e) if c.startswith(f'guia:{tipo}:')} == {
        f'guia:{tipo}:{obj.id}' for obj in modelo.query.filter_by(activo=True)}
    e.send.reset_mock()
    e.edit.reset_mock()
    callback(e, f'guia:{tipo}:{nuevo.id}')
    ctx, txt = tarjeta_nueva(e)
    assert ctx['borrador'][f'{tipo}_id'] == nuevo.id
    assert esperado in txt
    assert any(c.args[:2] == (e.admin.telegram_chat_id, 41)
               and markup is None for c, (_, markup) in
               zip(e.edit.call_args_list, mensajes(e.edit, True), strict=True))


@pytest.mark.parametrize('pasada', [False, True])
def test_callback_fecha_lima_y_rechazo_pasada(e, monkeypatch, pasada):
    import utils.date_utils as fechas
    from telegram.state_machine import StateMachine

    # UTC ya es el día siguiente; la fecha de negocio sigue siendo 14 en Lima.
    ahora = datetime(2030, 11, 14, 23, 30, tzinfo=fechas.get_peru_now().tzinfo)
    monkeypatch.setattr(fechas, 'get_peru_now', lambda: ahora)
    from telegram import router

    monkeypatch.setattr(router, 'get_peru_now', lambda: ahora, raising=False)
    ctx = contexto(e)
    guardar(e, ctx)
    callback(e, 'guia:fecha')
    hoy = ahora.date()
    assert codigos(e) == [f'guia:fecha:{hoy.isoformat()}',
                          f'guia:fecha:{(hoy + timedelta(days=1)).isoformat()}']
    e.send.reset_mock()
    fecha = hoy + timedelta(days=-1 if pasada else 1)
    callback(e, f'guia:fecha:{fecha.isoformat()}')
    if pasada:
        assert textos(e)
        assert StateMachine.get_context(e.admin).data['borrador'] == ctx['borrador']
    else:
        nuevo, _ = tarjeta_nueva(e)
        assert nuevo['borrador']['fecha_inicio_traslado'] == fecha.isoformat()


@pytest.mark.parametrize('existe', [True, False])
def test_callback_destinatario_edicion_y_reintento(e, existe):
    from telegram.router import TelegramRouter
    from telegram.state_machine import ConversationState, StateMachine

    ctx = contexto(e)
    ctx['borrador']['destinatario_cliente_id'] = None
    guardar(e, ctx)
    callback(e, 'guia:destinatario')
    estado = StateMachine.get_context(e.admin)
    assert estado.state == ConversationState.AWAITING_EXTRA_DATA
    assert estado.action == 'edit_guia_destinatario'
    assert e.send.called
    e.send.reset_mock()
    TelegramRouter._handle_edit_response(e.admin.telegram_chat_id, e.admin, {
        'edit_action': 'edit_guia_destinatario',
        'value': e.cliente.nombre if existe else 'Cliente inexistente ZZZ',
        'original_context': ctx, 'message_id': 41})
    if existe:
        nuevo, _ = tarjeta_nueva(e)
        assert nuevo['borrador']['destinatario_cliente_id'] == e.cliente.id
    else:
        assert 'No encontré' in textos(e)
        estado = StateMachine.get_context(e.admin)
        assert estado.state == ConversationState.AWAITING_EXTRA_DATA
        assert estado.action == 'edit_guia_destinatario'


def test_callback_venta_prepara_guia(e):
    ctx = contexto(e, True)
    guardar(e, contexto(e))
    callback(e, f"guia:venta:{ctx['borrador']['venta_id']}")
    nuevo, txt = tarjeta_nueva(e)
    assert nuevo['borrador']['venta_id'] == ctx['borrador']['venta_id']
    assert nuevo['borrador']['motivo'] == '01'
    assert nuevo['borrador']['destinatario_cliente_id'] == e.cliente.id
    assert e.cliente.nombre in txt


@pytest.mark.parametrize('vieja', [False, True])
def test_callback_confirmacion_y_tarjeta_desactualizada(e, monkeypatch, vieja):
    from telegram.handlers.guia_sunat import GuiaSunatHandler

    ctx = contexto(e)
    guardar(e, ctx)
    ejecutar = Mock()
    monkeypatch.setattr(GuiaSunatHandler, 'execute_guia_remision', ejecutar)
    callback(e, 'confirm:guia_remision', message_id=40 if vieja else 41)
    if vieja:
        ejecutar.assert_not_called()
        assert 'desactualizada' in textos(e).lower()
    else:
        ejecutar.assert_called_once()
        chat, user, recibido, mid = ejecutar.call_args.args
        assert (chat, user.id, mid) == (e.admin.telegram_chat_id, e.admin.id, 41)
        assert all(recibido[k] == v for k, v in ctx.items())


def test_transferencia_ofrece_guia_y_callback_no_mueve_stock(e):
    from telegram.handlers.transferencia import TransferenciaHandler
    from telegram.resolvers import buscar_presentacion, resolver_almacen
    from telegram.state_machine import StateMachine

    args = {'almacen_origen': e.origen.nombre, 'almacen_destino': e.destino.nombre,
            'items': [{'producto_nombre': e.p.nombre, 'cantidad': 2}]}
    antes = e.m.Movimiento.query.count()
    TransferenciaHandler.prepare_transferencia(e.admin.telegram_chat_id, e.admin, args, '',
                                               resolver_almacen, buscar_presentacion)
    assert {'confirm:transferencia', 'guia:desde_transferencia'} <= set(codigos(e))
    data = StateMachine.get_context(e.admin).data
    # Compatibilidad con el contexto plano que usa el handler de traslado original.
    if not data:
        data = deepcopy(e.admin.telegram_context)
    StateMachine.set_awaiting_confirmation(e.admin, 'transferencia', data, 41)
    e.send.reset_mock()
    callback(e, 'guia:desde_transferencia')
    nuevo, _ = tarjeta_nueva(e)
    b = nuevo['borrador']
    assert (b['almacen_origen_id'], b['almacen_destino_id']) == (e.origen.id, e.destino.id)
    assert [(i['presentacion_id'], i['cantidad']) for i in b['bienes']] == [(e.p.id, 2)]
    assert e.m.Movimiento.query.count() == antes
    assert e.inv.cantidad == 10
    assert e.m.GuiaRemision.query.count() == 0


def test_venta_registrada_ofrece_boton_guia(e):
    from telegram.handlers.venta import VentaHandler

    data = {'almacen_id': e.origen.id, 'almacen_nombre': e.origen.nombre,
            'cliente_id': e.cliente.id, 'cliente_nombre': e.cliente.nombre,
            'estado': 'completado', 'items': [{'presentacion_id': e.p.id,
            'producto_nombre': e.p.nombre, 'cantidad': 2, 'precio_unitario': 10,
            'subtotal': 20}], 'total': 20, 'pagos': []}
    VentaHandler.execute_venta(e.admin.telegram_chat_id, e.admin, data, 41)
    v = e.m.Venta.query.filter_by(cliente_id=e.cliente.id).one()
    assert any(b['callback_data'] == f'guia:venta:{v.id}'
               for _, markup in mensajes(e.edit, True) for b in botones(markup))
    assert e.inv.cantidad == 8


@pytest.mark.parametrize('caso', ['aceptada', 'enviada', 'en_proceso', 'pendiente_aceptada',
                                  'ajena', 'inexistente', 'creador', 'admin_ajena'])
@pytest.mark.parametrize('numero', ['1', '00000001'])
def test_comando_guia_autorizacion_consulta_pdf_sin_gemini(e, monkeypatch, caso, numero):
    from services import sunat_xml as sx
    from services.gemini_service import gemini_service
    from telegram.router import TelegramRouter

    monkeypatch.setattr(gemini_service, 'process_command',
                        Mock(side_effect=AssertionError('/guia no debe llamar a Gemini')))
    g = e.gs.emitir(contexto(e)['borrador'], e.admin, 'comando-local')
    g.estado = 'en_proceso' if caso == 'en_proceso' else 'aceptada'
    if caso in {'enviada', 'pendiente_aceptada'}:
        g.estado = 'enviada'
    user = e.vendedor if caso in {'ajena', 'creador'} else e.admin
    if caso in {'creador', 'admin_ajena'}:
        g.creado_por_id = e.vendedor.id
    e.db.session.commit()
    aceptada = caso == 'pendiente_aceptada'
    cdr = sx.CdrResult(sx.CDR_ACEPTADO, True, '0', 'T991-00000001', (), (),
                       b'<ApplicationResponse/>') if aceptada else None
    e.transporte.consultar_estado_ticket.return_value = sx.TicketResult(
        sx.CDR_ACEPTADO if aceptada else sx.CDR_PENDIENTE, aceptada,
        '0' if aceptada else '98', cdr)
    e.send.reset_mock()
    serie = 'T999' if caso == 'inexistente' else 'T991'
    update = {'update_id': 991, 'message': {'chat': {'id': user.telegram_chat_id,
        'type': 'private'}, 'from': {'id': user.telegram_chat_id},
        'text': f'/guia {serie}-{numero}'}}
    # handle_message recibe el contenido de update.message, como el webhook real.
    TelegramRouter.handle_message(update['message'])
    if caso in {'ajena', 'inexistente'}:
        assert 'No encontré' in textos(e)
        assert 'TICKET-LOCAL' not in textos(e)
        e.document.assert_not_called()
        e.transporte.consultar_estado_ticket.assert_not_called()
    elif caso in {'enviada', 'en_proceso', 'pendiente_aceptada'}:
        e.transporte.consultar_estado_ticket.assert_called_once()
        e.db.session.refresh(g)
        assert g.estado == ('aceptada' if aceptada else 'en_proceso')
        assert g.estado in textos(e).lower().replace(' ', '_')
    else:
        e.transporte.consultar_estado_ticket.assert_not_called()
    pdf_esperado = caso in {'aceptada', 'creador', 'admin_ajena', 'pendiente_aceptada'}
    if pdf_esperado:
        e.document.assert_called_once()
        llamada = e.document.call_args
        filename = llamada.args[1] if len(llamada.args) > 1 else llamada.kwargs['filename']
        content = llamada.args[2] if len(llamada.args) > 2 else llamada.kwargs['content']
        assert filename.endswith('.pdf')
        assert content.startswith(b'%PDF')
    else:
        e.document.assert_not_called()
