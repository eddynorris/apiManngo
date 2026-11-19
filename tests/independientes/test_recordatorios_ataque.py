"""Auditoría adversarial: datos propios en SQLite/PostgreSQL y Telegram sin red.

Los xfail estrictos expresan el comportamiento esperado, no el bug observado.
Los casos verdes cubren rechazos y recuperación no ejercitados por el contrato.
"""

import logging
import re
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import event

from extensions import db
from models import Almacen, Cliente, Pago, Users, Venta
from services import recordatorio_service as recordatorios
from services.telegram_service import telegram_service
from telegram.router import TelegramRouter

HOY = date(2026, 11, 18)


@pytest.fixture
def ataque(test_client, monkeypatch):
    """Crea y elimina solo sus filas; restaura los chats previos tras cada caso."""
    monkeypatch.setenv('RECORDATORIOS_ACTIVOS', 'true')
    monkeypatch.delenv('RECORDATORIOS_DIAS_ANTES', raising=False)
    monkeypatch.delenv('RECORDATORIOS_MAX_CLIENTES', raising=False)
    anteriores = {u.id: u.telegram_chat_id for u in Users.query.all()}
    for usuario in Users.query.all():
        usuario.telegram_chat_id = None
    sufijo = uuid4().hex[:10]
    almacenes = [Almacen(nombre=f'Ataque {letra} {sufijo}') for letra in 'AB']
    db.session.add_all(almacenes)
    db.session.flush()
    almacen_ids = [a.id for a in almacenes]
    # El admin tiene un ID posterior a los vendedores: no depender del orden de inserción.
    usuarios = [
        Users(username=f'ataque-a-{sufijo}', password='doble', rol='usuario',
              almacen_id=almacen_ids[0], telegram_chat_id=981001),
        Users(username=f'ataque-b-{sufijo}', password='doble', rol='gerente',
              almacen_id=almacen_ids[1], telegram_chat_id=981002),
        Users(username=f'ataque-admin-{sufijo}', password='doble', rol='admin',
              telegram_chat_id=981003),
    ]
    db.session.add_all(usuarios)
    db.session.flush()
    usuario_ids = [u.id for u in usuarios]
    db.session.commit()
    cliente_ids, venta_ids, pago_ids = [], [], []
    send = Mock(return_value={'ok': True, 'result': {'message_id': 901}})
    edit = Mock(return_value={'ok': True})
    answer = Mock(return_value={'ok': True})
    monkeypatch.setattr(telegram_service, 'send_message', send)
    monkeypatch.setattr(telegram_service, 'edit_message', edit)
    monkeypatch.setattr(telegram_service, 'answer_callback_query', answer)

    def cliente(**valores):
        datos = dict(nombre=f'Ataque cliente {sufijo} {len(cliente_ids)}',
                     telefono='987654321', proxima_compra_manual=HOY,
                     almacen_preferido_id=almacen_ids[0])
        datos.update(valores)
        fila = Cliente(**datos)
        db.session.add(fila)
        db.session.flush()
        cliente_ids.append(fila.id)
        db.session.commit()
        return fila

    def deuda(fila):
        venta = Venta(cliente_id=fila.id, almacen_id=fila.almacen_preferido_id,
                      vendedor_id=usuario_ids[0], total=Decimal('100'),
                      estado_pago='parcial', tipo_pago='credito')
        db.session.add(venta)
        db.session.flush()
        venta_ids.append(venta.id)
        pago = Pago(venta_id=venta.id, usuario_id=usuario_ids[0],
                    monto=Decimal('30'), metodo_pago='efectivo')
        db.session.add(pago)
        db.session.flush()
        pago_ids.append(pago.id)
        db.session.commit()

    try:
        yield SimpleNamespace(app=test_client.application, a=almacen_ids[0], b=almacen_ids[1],
                              vendedor=usuarios[0], gerente=usuarios[1], admin=usuarios[2],
                              cliente=cliente, deuda=deuda, send=send, edit=edit, answer=answer)
    finally:
        db.session.rollback()
        db.session.remove()
        Pago.query.filter(Pago.id.in_(pago_ids)).delete(synchronize_session=False)
        Venta.query.filter(Venta.id.in_(venta_ids)).delete(synchronize_session=False)
        Cliente.query.filter(Cliente.id.in_(cliente_ids)).delete(synchronize_session=False)
        Users.query.filter(Users.id.in_(usuario_ids)).delete(synchronize_session=False)
        for usuario_id, chat in anteriores.items():
            db.session.get(Users, usuario_id).telegram_chat_id = chat
        db.session.flush()
        Almacen.query.filter(Almacen.id.in_(almacen_ids)).delete(synchronize_session=False)
        db.session.commit()


def ejecutar(e):
    resultado = e.app.test_cli_runner().invoke(args=['recordatorios', '--fecha', HOY.isoformat()])
    assert resultado.exit_code == 0, repr(resultado.exception) + resultado.output
    return resultado


def resumen(resultado):
    linea = next(fila for fila in resultado.output.splitlines()
                 if fila.startswith('recordatorios: '))
    return {clave: int(valor) for clave, valor in re.findall(r'(\w+)=(\d+)', linea)}


def callback(chat, dato, *, remitente=None, tipo='private'):
    TelegramRouter.handle_callback_query({
        'id': 'cb-ataque', 'from': {'id': chat if remitente is None else remitente},
        'message': {'message_id': 55, 'chat': {'id': chat, 'type': tipo}}, 'data': dato,
    })


def ids_enviados(send, chat):
    return [int(boton['callback_data'].split(':')[1])
            for llamada in send.call_args_list if llamada.args[0] == chat
            for fila in (llamada.kwargs.get('reply_markup') or {}).get('inline_keyboard', [])
            for boton in fila if boton.get('callback_data', '').startswith('contactado:')]


@contextmanager
def consultas_select():
    consultas = []

    def observar(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            consultas.append(statement)

    motor = db.engine
    event.listen(motor, 'before_cursor_execute', observar)
    try:
        yield consultas
    finally:
        event.remove(motor, 'before_cursor_execute', observar)


def test_frecuencia_enorme_no_aborta_lista_de_clientes_sanos(ataque):
    e = ataque
    sano = e.cliente()
    e.cliente(proxima_compra_manual=None, frecuencia_compra_dias=2_147_483_647,
              ultima_fecha_compra=datetime(2026, 11, 10, 17, tzinfo=UTC))
    # Un entero admitido por el modelo no debe impedir contactar a todos los demás.
    resultado = ejecutar(e)
    assert sano.id in ids_enviados(e.send, e.admin.telegram_chat_id)
    assert 'recordatorios: ' in resultado.output


@pytest.mark.parametrize('fallo', ['excepcion', 'none'])
def test_resumen_cuenta_envios_anteriores_a_fallo_parcial(ataque, fallo):
    e = ataque
    e.cliente()
    e.cliente()
    chat_admin = e.admin.telegram_chat_id
    exitosos = []
    intentos_admin = []

    def enviar(chat_id, text, reply_markup=None):
        if chat_id == chat_admin:
            intentos_admin.append(text)
            if len(intentos_admin) == 3:  # Ya salieron cabecera y primera tarjeta.
                if fallo == 'excepcion':
                    raise RuntimeError('Transporte interrumpido tras dos envíos')
                return None
        exitosos.append(chat_id)
        return {'ok': True, 'result': {'message_id': 902}}

    e.send.side_effect = enviar
    conteos = resumen(ejecutar(e))
    assert exitosos[:2] == [chat_admin, chat_admin]  # Admin antes de los demás.
    assert len(ids_enviados(e.send, e.vendedor.telegram_chat_id)) == 2
    assert conteos['errores'] == 1
    assert conteos['mensajes'] == len(exitosos), (
        f"El resumen declara {conteos['mensajes']} mensajes; Telegram aceptó {len(exitosos)}"
    )


def test_whatsapp_conserva_nombre_con_espacios_en_texto_exacto(ataque):
    cliente = ataque.cliente(nombre='  Pollería José 👩🏽‍🍳  ')
    enlace = recordatorios.enlace_whatsapp('51987654321', cliente.nombre)
    texto = unquote(urlsplit(enlace).query.removeprefix('text='))
    esperado = (f'Hola {cliente.nombre}, ¿vas a necesitar carbón estos días? '
                'Si necesitas, te lo llevamos. Tenemos stock.')
    assert texto == esperado


def test_telefono_con_digitos_unicode_no_crea_destino_invalido(ataque):
    cliente = ataque.cliente(telefono='9８７６５４３２１')
    fila, = recordatorios.seleccionar(HOY, almacen_id=ataque.a).hoy
    assert fila.cliente_id == cliente.id
    # Rechazarlo o convertirlo a ASCII sirve; un wa.me con dígitos mixtos no sirve.
    assert fila.destino is None or re.fullmatch(r'519[0-9]{8}', fila.destino), fila.destino


def test_seleccionar_no_multiplica_consultas_por_cliente_y_por_pago(ataque):
    e = ataque
    e.deuda(e.cliente())
    for _ in range(8):
        e.deuda(e.cliente(almacen_preferido_id=e.b))
    db.session.expire_all()  # Medir lecturas reales, sin relaciones precargadas por el montaje.
    with consultas_select() as pocas:
        pequeno = recordatorios.seleccionar(HOY, almacen_id=e.a)
    db.session.expire_all()
    with consultas_select() as muchas:
        grande = recordatorios.seleccionar(HOY, almacen_id=e.b)
    assert len(pequeno.hoy) == 1 and len(grande.hoy) == 8
    assert all(f.saldo_pendiente == Decimal('70') for f in grande.hoy)
    # Comparar contra el mismo motor, sin fijar un total de SELECT propio de SQLite.
    # Permitir varios SELECT de precarga, pero no uno por venta y otro por pagos.
    assert len(muchas) <= len(pocas) + 3, (
        f'N+1 demostrado: 1 cliente={len(pocas)} SELECT; 8 clientes={len(muchas)} SELECT'
    )


def test_callback_ids_manipulados_no_modifican_y_sesion_sigue_utilizable(ataque, caplog):
    e = ataque
    cliente = e.cliente(nombre='NombrePrivadoCallbackXYZ')
    caplog.set_level(logging.DEBUG)
    # El ID enorme es una entrada hostil del callback, nunca un valor persistido.
    datos = ['contactado:' + '9' * 50, 'contactado:-1', 'contactado:0',
             f'contactado:contactado:{cliente.id}', f'contactado:+{cliente.id}',
             f'contactado:{cliente.id}:extra', 'contactado:１２３']
    for dato in datos:
        e.send.reset_mock()
        e.edit.reset_mock()
        e.answer.reset_mock()
        callback(e.admin.telegram_chat_id, dato)
        db.session.expire_all()
        assert db.session.get(Cliente, cliente.id).ultimo_contacto is None
        assert e.send.called or e.edit.called or e.answer.called
    # El overflow del primer ID no debe dejar una transacción inutilizable.
    callback(e.admin.telegram_chat_id, f'contactado:{cliente.id}')
    db.session.expire_all()
    assert db.session.get(Cliente, cliente.id).ultimo_contacto is not None
    for privado in (cliente.nombre, cliente.telefono, '51987654321'):
        assert privado not in caplog.text


def test_callback_no_vinculado_o_identidad_suplantada_no_marca_contacto(ataque):
    e = ataque
    cliente = e.cliente()
    dato = f'contactado:{cliente.id}'
    callback(989999, dato)  # Chat privado válido, sin usuario vinculado.
    assert e.send.called or e.edit.called or e.answer.called
    callback(e.admin.telegram_chat_id, dato, remitente=e.vendedor.telegram_chat_id)
    callback(e.admin.telegram_chat_id, dato, tipo='group')
    db.session.expire_all()
    assert db.session.get(Cliente, cliente.id).ultimo_contacto is None


def test_callback_sin_almacen_y_rol_inesperado_no_cruzan_almacenes(ataque):
    e = ataque
    cliente = e.cliente()
    for rol, almacen in [('usuario', None), ('auditor', e.b)]:
        e.vendedor.rol = rol
        e.vendedor.almacen_id = almacen
        db.session.commit()
        e.send.reset_mock()
        e.edit.reset_mock()
        e.answer.reset_mock()
        callback(e.vendedor.telegram_chat_id, f'contactado:{cliente.id}')
        db.session.expire_all()
        assert db.session.get(Cliente, cliente.id).ultimo_contacto is None
        assert e.send.called or e.edit.called or e.answer.called


def test_error_bd_de_un_destinatario_recupera_sesion_para_el_siguiente(ataque, monkeypatch):
    e = ataque
    e.cliente()
    otro = e.cliente(almacen_preferido_id=e.b)
    seleccionar_real = recordatorios.seleccionar
    username_duplicado = e.admin.username

    def seleccionar_con_fallo(hoy, almacen_id=None):
        if almacen_id == e.a:
            # Provoca un IntegrityError real que deja pendiente el rollback de SQLAlchemy.
            db.session.add(Users(username=username_duplicado, password='doble', rol='usuario'))
            db.session.flush()
        return seleccionar_real(hoy, almacen_id=almacen_id)

    monkeypatch.setattr(recordatorios, 'seleccionar', seleccionar_con_fallo)
    conteos = resumen(ejecutar(e))
    assert conteos['errores'] == 1
    assert ids_enviados(e.send, e.gerente.telegram_chat_id) == [otro.id]
    assert Users.query.filter_by(username=username_duplicado).count() == 1


def test_fecha_cli_imposible_rechazada_antes_de_enviar_o_marcar(ataque):
    cliente = ataque.cliente()
    for fecha in ['2026-02-29', '2026-11-31', '0000-01-01', '99999-01-01', 'ayer']:
        resultado = ataque.app.test_cli_runner().invoke(args=['recordatorios', '--fecha', fecha])
        assert resultado.exit_code != 0
        ataque.send.assert_not_called()
        db.session.expire_all()
        assert db.session.get(Cliente, cliente.id).ultimo_contacto is None
