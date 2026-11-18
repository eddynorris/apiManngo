"""Contrato independiente de recordatorios: SQLite, reloj de Lima y Telegram doble.

Las importaciones de la implementación se hacen dentro de los tests para que
su ausencia no impida recoger los casos ni verificar el fixture por separado.
No se prescribe el orden entre clientes del mismo bloque ni el texto de cabecera.
"""

import logging
import re
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from html import escape
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event

from extensions import db
from models import Almacen, Cliente, Pedido, Users
from services.telegram_service import telegram_service

HOY = date(2026, 11, 14)
LIMA = ZoneInfo('America/Lima')
BLOQUES = ('hoy', 'manana', 'retrasados')


@pytest.fixture
def escenario(test_client, monkeypatch):
    """Cada caso crea sus filas y restaura los usuarios, incluso tras un commit del bot."""
    assert db.engine.dialect.name == 'sqlite', 'Este contrato solo se ejecuta con SQLite'
    monkeypatch.setenv('RECORDATORIOS_ACTIVOS', 'true')
    monkeypatch.delenv('RECORDATORIOS_DIAS_ANTES', raising=False)
    monkeypatch.delenv('RECORDATORIOS_MAX_CLIENTES', raising=False)
    sufijo = uuid4().hex[:10]
    admin = Users.query.filter_by(username='admin').one()
    vendedor = Users.query.filter_by(username='vendedor').one()
    campos = ('telegram_chat_id', 'telegram_context', 'telegram_history', 'almacen_id', 'rol')
    originales = {u.id: {c: deepcopy(getattr(u, c)) for c in campos}
                  for u in (admin, vendedor)}
    almacenes = [Almacen(nombre=f'Recordatorios {letra} {sufijo}') for letra in 'ABC']
    clientes_ids, pedidos_ids, usuarios_ids = [], [], []
    db.session.add_all(almacenes)
    db.session.flush()
    almacen_ids = [a.id for a in almacenes]
    admin.telegram_chat_id = 871001
    vendedor.telegram_chat_id = 871002
    vendedor.almacen_id = almacenes[0].id
    db.session.commit()
    send = Mock(return_value={'ok': True, 'result': {'message_id': 901}})
    edit = Mock(return_value={'ok': True})
    answer = Mock(return_value={'ok': True})
    monkeypatch.setattr(telegram_service, 'send_message', send)
    monkeypatch.setattr(telegram_service, 'edit_message', edit)
    monkeypatch.setattr(telegram_service, 'answer_callback_query', answer)

    def cliente(desfase=0, **valores):
        datos = dict(nombre=f'Cliente recordatorio {sufijo} {len(clientes_ids)}',
                     proxima_compra_manual=HOY + timedelta(days=desfase),
                     telefono='987 654 321', almacen_preferido_id=almacen_ids[0])
        datos.update(valores)
        fila = Cliente(**datos)
        db.session.add(fila)
        db.session.flush()
        clientes_ids.append(fila.id)
        db.session.commit()
        return fila

    def pedido(cliente, estado):
        fila = Pedido(cliente_id=cliente.id, almacen_id=almacen_ids[0],
                      vendedor_id=admin.id, estado=estado,
                      fecha_entrega=datetime(2026, 11, 15, 17, tzinfo=UTC))
        db.session.add(fila)
        db.session.flush()
        pedidos_ids.append(fila.id)
        db.session.commit()
        return fila

    def usuario(rol='usuario', almacen_id=None, chat_id=None):
        fila = Users(username=f'recordatorio-{sufijo}-{len(usuarios_ids)}',
                     password=admin.password, rol=rol, almacen_id=almacen_id,
                     telegram_chat_id=chat_id)
        db.session.add(fila)
        db.session.flush()
        usuarios_ids.append(fila.id)
        db.session.commit()
        return fila

    try:
        yield SimpleNamespace(
            app=test_client.application, a=almacenes[0], b=almacenes[1], c=almacenes[2],
            admin=admin, vendedor=vendedor, cliente=cliente, pedido=pedido, usuario=usuario,
            send=send, edit=edit, answer=answer,
        )
    finally:
        db.session.rollback()
        for user_id, valores in originales.items():
            fila = db.session.get(Users, user_id)
            for campo, valor in valores.items():
                setattr(fila, campo, valor)
        Pedido.query.filter(Pedido.id.in_(pedidos_ids)).delete(synchronize_session=False)
        Cliente.query.filter(Cliente.id.in_(clientes_ids)).delete(synchronize_session=False)
        Users.query.filter(Users.id.in_(usuarios_ids)).delete(synchronize_session=False)
        Almacen.query.filter(Almacen.id.in_(almacen_ids)).delete(synchronize_session=False)
        db.session.commit()


@contextmanager
def sin_escrituras():
    """Detecta incluso escrituras que luego se reviertan o no cambien valores."""
    escrituras = []

    def observar(conn, cursor, statement, parameters, context, executemany):
        verbo = statement.lstrip().split(None, 1)[0].upper()
        if context.isinsert or context.isupdate or context.isdelete or verbo in {
            'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE', 'DROP', 'ALTER',
        }:
            escrituras.append(verbo)

    motor = db.engine
    event.listen(motor, 'before_cursor_execute', observar)
    try:
        yield
    finally:
        event.remove(motor, 'before_cursor_execute', observar)
    assert not escrituras, f'Una operación de lectura emitió escrituras: {escrituras}'


def elementos(resultado):
    return [fila for bloque in BLOQUES for fila in getattr(resultado, bloque)]


def ids(resultado):
    return {fila.cliente_id for fila in elementos(resultado)}


def botones(markup):
    return [boton for fila in (markup or {}).get('inline_keyboard', []) for boton in fila]


def envios(escenario, chat_id=None):
    mensajes = []
    for llamada in escenario.send.call_args_list:
        args, kwargs = llamada.args, llamada.kwargs
        chat = args[0] if args else kwargs['chat_id']
        texto = args[1] if len(args) > 1 else kwargs['text']
        markup = args[2] if len(args) > 2 else kwargs.get('reply_markup')
        if chat_id is None or chat == chat_id:
            mensajes.append((chat, texto, markup))
    return mensajes


def tarjetas(escenario, chat_id):
    return [(texto, markup) for _, texto, markup in envios(escenario, chat_id)
            if any(b.get('callback_data', '').startswith('contactado:')
                   for b in botones(markup))]


def ids_tarjetas(escenario, chat_id):
    return [int(b['callback_data'].split(':')[1])
            for _, markup in tarjetas(escenario, chat_id)
            for b in botones(markup) if b.get('callback_data', '').startswith('contactado:')]


def ejecutar(escenario, *opciones, fecha=HOY):
    args = ['recordatorios']
    if fecha is not None:
        args.extend(['--fecha', fecha.isoformat()])
    resultado = escenario.app.test_cli_runner().invoke(args=[*args, *opciones])
    assert resultado.exit_code == 0, resultado.output + repr(resultado.exception)
    return resultado


def resumen(resultado):
    lineas = [linea for linea in resultado.output.splitlines()
              if linea.startswith('recordatorios: ')]
    assert lineas, resultado.output
    conteos = {campo: int(valor) for campo, valor in
               re.findall(r'\b(manana|hoy|retrasados|inactivos|mensajes|errores)=(\d+)',
                          lineas[-1])}
    assert set(conteos) == {'manana', 'hoy', 'retrasados', 'inactivos', 'mensajes', 'errores'}
    return conteos


def callback(escenario, usuario, dato):
    from telegram.router import TelegramRouter

    chat = usuario.telegram_chat_id
    TelegramRouter.handle_callback_query({
        'id': 'cb1', 'from': {'id': chat}, 'data': dato,
        'message': {'message_id': 55, 'chat': {'id': chat, 'type': 'private'}},
    })


def test_fixture_crea_datos_propios_y_telegram_es_doble(escenario):
    """Se puede comprobar el montaje aunque todavía falten los servicios nuevos."""
    e = escenario
    cliente = e.cliente()
    pedido = e.pedido(cliente, 'cancelado')
    usuario = e.usuario('gerente', e.b.id, 871003)
    assert db.session.get(Cliente, cliente.id).almacen_preferido_id == e.a.id
    assert db.session.get(Pedido, pedido.id).cliente_id == cliente.id
    assert db.session.get(Users, usuario.id).almacen_id == e.b.id
    assert telegram_service.send_message is e.send
    e.send.assert_not_called()


@pytest.mark.parametrize(('manual', 'frecuencia', 'compra', 'esperado'), [
    (date(2026, 12, 2), 7, datetime(2026, 11, 11, 4, 30, tzinfo=UTC), date(2026, 12, 2)),
    (date(2026, 12, 2), None, None, date(2026, 12, 2)),
    (date(2026, 12, 2), 0, None, date(2026, 12, 2)),
    (date(2026, 12, 2), -3, None, date(2026, 12, 2)),
    (None, None, datetime(2026, 11, 11, tzinfo=UTC), None),
    (None, 0, datetime(2026, 11, 11, tzinfo=UTC), None),
    (None, -1, datetime(2026, 11, 11, tzinfo=UTC), None),
    (None, 7, None, None),
    (None, 7, datetime(2026, 11, 11, 4, 30, tzinfo=UTC), date(2026, 11, 17)),
    (None, 7, datetime(2026, 11, 11, 4, 30), date(2026, 11, 17)),
    (None, 7, datetime(2026, 11, 10, 23, 30, tzinfo=LIMA), date(2026, 11, 17)),
    (None, 1, datetime(2026, 11, 11, 5, tzinfo=UTC), date(2026, 11, 12)),
    (None, 1, datetime(2026, 1, 1, 4, 59, tzinfo=UTC), date(2026, 1, 1)),
])
def test_fecha_estimada_prioridad_nulos_y_dia_de_lima(manual, frecuencia, compra, esperado):
    from services.proyeccion import fecha_estimada

    cliente = Cliente(proxima_compra_manual=manual, frecuencia_compra_dias=frecuencia,
                      ultima_fecha_compra=compra)
    resultado = fecha_estimada(cliente)
    assert resultado == esperado
    assert resultado is None or type(resultado) is date


@pytest.mark.parametrize('telefono', [
    '987 654 321', '987654321', '+51 987-654-321', '51987654321', '(+51) 987 654 321',
])
def test_normalizar_movil_peruano(telefono):
    from services.recordatorio_service import normalizar_telefono_pe

    assert normalizar_telefono_pe(telefono) == '51987654321'


@pytest.mark.parametrize('telefono', [
    '014567890', '01 4567890', '98765', '887654321', '123456789', '', '   ', None,
])
def test_normalizar_rechaza_fijos_cortos_y_vacios(telefono):
    from services.recordatorio_service import normalizar_telefono_pe

    assert normalizar_telefono_pe(telefono) is None


@pytest.mark.parametrize('nombre', ['José & Lucía', '<b>Pollería & Co', 'Ana 👩🏽‍🍳'])
def test_enlace_whatsapp_texto_exacto_y_codificacion(nombre):
    from services.recordatorio_service import enlace_whatsapp

    enlace = enlace_whatsapp('51987654321', nombre)
    partes = urlsplit(enlace)
    esperado = (f'Hola {nombre}, ¿vas a necesitar carbón estos días? '
                'Si necesitas, te lo llevamos. Tenemos stock.')
    assert enlace.startswith('https://wa.me/51987654321?text=')
    assert (partes.scheme, partes.netloc, partes.path) == ('https', 'wa.me', '/51987654321')
    assert ' ' not in enlace
    assert parse_qs(partes.query) == {'text': [esperado]}
    assert unquote(partes.query.removeprefix('text=')) == esperado


def test_seleccionar_limites_atributos_y_orden_retrasados(escenario):
    from services.recordatorio_service import seleccionar

    e = escenario
    clientes = {dias: e.cliente(dias) for dias in (-31, -30, -4, -1, 0, 1, 2)}
    sin_fecha = e.cliente(proxima_compra_manual=None)
    with sin_escrituras():
        resultado = seleccionar(HOY)
    assert isinstance(resultado.hoy, list)
    assert isinstance(resultado.manana, list)
    assert isinstance(resultado.retrasados, list)
    assert [f.cliente_id for f in resultado.hoy] == [clientes[0].id]
    assert [f.cliente_id for f in resultado.manana] == [clientes[1].id]
    assert [f.cliente_id for f in resultado.retrasados] == [clientes[d].id for d in (-1, -4, -30)]
    assert resultado.inactivos == 1
    assert type(resultado.inactivos) is int
    assert sin_fecha.id not in ids(resultado)
    for fila in elementos(resultado):
        assert type(fila.fecha_objetivo) is date
        assert type(fila.dias_retraso) is int
        assert fila.dias_retraso == (HOY - fila.fecha_objetivo).days
        cliente = db.session.get(Cliente, fila.cliente_id)
        assert fila.nombre == cliente.nombre
        assert fila.destino == '51987654321'


def test_seleccionar_dias_antes_se_lee_en_cada_llamada(escenario, monkeypatch):
    from services.recordatorio_service import seleccionar

    e = escenario
    clientes = {d: e.cliente(d) for d in (1, 2, 3, 4)}
    assert ids(seleccionar(HOY)) == {clientes[1].id}
    monkeypatch.setenv('RECORDATORIOS_DIAS_ANTES', '3')
    assert ids(seleccionar(HOY)) == {clientes[d].id for d in (1, 2, 3)}
    monkeypatch.setenv('RECORDATORIOS_DIAS_ANTES', '1')
    assert ids(seleccionar(HOY)) == {clientes[1].id}


@pytest.mark.parametrize('estado', ['programado', 'confirmado', 'entregado', 'cancelado'])
@pytest.mark.parametrize('desfase', [1, 0, -1, -31])
def test_pedidos_abiertos_excluyen_de_bloques_e_inactivos(escenario, estado, desfase):
    from services.recordatorio_service import seleccionar

    cliente = escenario.cliente(desfase)
    escenario.pedido(cliente, estado)
    resultado = seleccionar(HOY)
    excluido = estado in {'programado', 'confirmado'}
    assert ids(resultado) == ({cliente.id} if not excluido and desfase >= -30 else set())
    assert resultado.inactivos == int(not excluido and desfase < -30)


def test_pedido_abierto_prevalece_y_pedidos_cerrados_no_duplican(escenario):
    from services.recordatorio_service import seleccionar

    abierto, cerrado = escenario.cliente(), escenario.cliente()
    for estado in ('entregado', 'cancelado', 'confirmado'):
        escenario.pedido(abierto, estado)
    for estado in ('entregado', 'cancelado', 'entregado'):
        escenario.pedido(cerrado, estado)
    assert [f.cliente_id for f in seleccionar(HOY).hoy] == [cerrado.id]


@pytest.mark.parametrize('anticipacion', [1, 3])
@pytest.mark.parametrize('desfase', [1, 0, -1, -31])
@pytest.mark.parametrize('momento', ['antes', 'umbral', 'despues'])
def test_ultimo_contacto_por_ciclo_y_dia_de_lima(
    escenario, monkeypatch, anticipacion, desfase, momento,
):
    from services.recordatorio_service import seleccionar

    monkeypatch.setenv('RECORDATORIOS_DIAS_ANTES', str(anticipacion))
    objetivo = HOY + timedelta(days=desfase)
    umbral = objetivo - timedelta(days=anticipacion)
    # 04:59 UTC aún es el día anterior en Lima; 05:00 UTC es el umbral exacto.
    contacto = datetime.combine(umbral, datetime.min.time(), tzinfo=UTC) + timedelta(hours=5)
    contacto += {'antes': -timedelta(minutes=1), 'umbral': timedelta(0),
                 'despues': timedelta(days=1)}[momento]
    cliente = escenario.cliente(desfase, ultimo_contacto=contacto)
    resultado = seleccionar(HOY)
    incluido = momento == 'antes'
    assert ids(resultado) == ({cliente.id} if incluido and desfase >= -30 else set())
    assert resultado.inactivos == int(incluido and desfase < -30)


def test_seleccionar_filtro_almacen_incluye_conteo_inactivos(escenario):
    from services.recordatorio_service import seleccionar

    e = escenario
    a, b = e.cliente(), e.cliente(almacen_preferido_id=e.b.id)
    sin_almacen = e.cliente(almacen_preferido_id=None)
    e.cliente(-31)
    e.cliente(-31, almacen_preferido_id=e.b.id)
    e.cliente(-32, almacen_preferido_id=e.b.id)
    assert ids(seleccionar(HOY)) == {a.id, b.id, sin_almacen.id}
    assert seleccionar(HOY).inactivos == 3
    for almacen, esperado, inactivos in ((e.a, {a.id}, 1), (e.b, {b.id}, 2), (e.c, set(), 0)):
        resultado = seleccionar(HOY, almacen_id=almacen.id)
        assert ids(resultado) == esperado
        assert resultado.inactivos == inactivos


@pytest.mark.parametrize('telefono', [None, '014567890', '98765', '+51 987-654-321'])
def test_telefono_invalido_conserva_cliente_en_su_bloque(escenario, telefono):
    from services.recordatorio_service import seleccionar

    cliente = escenario.cliente(telefono=telefono)
    fila, = seleccionar(HOY).hoy
    assert fila.cliente_id == cliente.id
    assert fila.destino == ('51987654321' if telefono == '+51 987-654-321' else None)


def test_seleccionar_usa_proyeccion_manual_y_compra_utc_persistida(escenario):
    from services.recordatorio_service import seleccionar

    e = escenario
    compra = datetime(2026, 11, 11, 4, 30, tzinfo=UTC)  # 10/11 en Lima + 4 = HOY.
    calculado = e.cliente(proxima_compra_manual=None, frecuencia_compra_dias=4,
                         ultima_fecha_compra=compra)
    manual = e.cliente(1, frecuencia_compra_dias=4, ultima_fecha_compra=compra)
    resultado = seleccionar(HOY)
    assert [f.cliente_id for f in resultado.hoy] == [calculado.id]
    assert [f.cliente_id for f in resultado.manana] == [manual.id]


def test_cli_destinatarios_aislamiento_por_almacen_y_sin_clientes(escenario):
    e = escenario
    a, b = e.cliente(), e.cliente(almacen_preferido_id=e.b.id)
    libre = e.cliente(almacen_preferido_id=None)
    gerente = e.usuario('gerente', e.b.id, 871003)
    vacio = e.usuario('usuario', e.c.id, 871004)
    sin_a = e.usuario('usuario', None, 871005)
    sin_b = e.usuario('gerente', None, 871006)
    e.usuario('gerente', e.a.id, None)
    with sin_escrituras():
        resultado = ejecutar(e)
    assert set(ids_tarjetas(e, e.admin.telegram_chat_id)) == {a.id, b.id, libre.id}
    assert ids_tarjetas(e, e.vendedor.telegram_chat_id) == [a.id]
    assert ids_tarjetas(e, gerente.telegram_chat_id) == [b.id]
    assert {chat for chat, _, _ in envios(e)} == {
        e.admin.telegram_chat_id, e.vendedor.telegram_chat_id, gerente.telegram_chat_id,
    }
    for usuario in (vacio, sin_a, sin_b):
        assert envios(e, usuario.telegram_chat_id) == []
    conteos = resumen(resultado)
    assert {k: conteos[k] for k in ('hoy', 'manana', 'retrasados', 'inactivos')} == {
        'hoy': 3, 'manana': 0, 'retrasados': 0, 'inactivos': 0,
    }
    assert conteos['mensajes'] == e.send.call_count
    assert conteos['errores'] == 0


def test_cli_cabecera_tarjetas_orden_botones_html_y_resumen(escenario):
    from services.recordatorio_service import enlace_whatsapp

    e = escenario
    # Inserción deliberadamente distinta del orden requerido.
    lejano = e.cliente(-8)
    manana = e.cliente(1)
    hoy = e.cliente(nombre='<b>Pollería & Co')
    cercano = e.cliente(-1, telefono='014567890')
    for _ in range(7):
        e.cliente(-31)
    resultado = ejecutar(e)
    mensajes = envios(e, e.admin.telegram_chat_id)
    assert len(mensajes) == 5  # Cabecera y cuatro tarjetas, nunca una tarjeta para inactivos.
    cabecera = mensajes[0][1]
    assert not botones(mensajes[0][2])
    # Un conteo distinto de los otros bloques permite comprobarlo sin fijar redacción.
    assert re.search(r'\b7\b', cabecera)
    assert re.search(r'\b1\b', cabecera)
    assert re.search(r'\b2\b', cabecera)  # Dos retrasados.
    assert ids_tarjetas(e, e.admin.telegram_chat_id) == [hoy.id, manana.id, cercano.id, lejano.id]
    for cliente, (texto, markup) in zip(
        (hoy, manana, cercano, lejano), tarjetas(e, e.admin.telegram_chat_id), strict=True,
    ):
        assert escape(cliente.nombre) in texto
        assert '<b>Pollería' not in texto
        assert isinstance(markup, dict) and isinstance(markup['inline_keyboard'], list)
        bs = botones(markup)
        assert sum(b.get('callback_data') == f'contactado:{cliente.id}' for b in bs) == 1
        urls = [b['url'] for b in bs if 'url' in b]
        assert urls == ([] if cliente.id == cercano.id else
                        [enlace_whatsapp('51987654321', cliente.nombre)])
    assert all(len(texto) <= 4096 for _, texto, _ in envios(e))
    conteos = resumen(resultado)
    assert {k: conteos[k] for k in ('hoy', 'manana', 'retrasados', 'inactivos')} == {
        'hoy': 1, 'manana': 1, 'retrasados': 2, 'inactivos': 7,
    }
    assert conteos['mensajes'] == e.send.call_count
    assert conteos['errores'] == 0


@pytest.mark.parametrize('limite', [None, 3])
def test_cli_maximo_por_usuario_cabecera_omitidos_y_textos_cortos(escenario, monkeypatch, limite):
    e = escenario
    cantidad = 28 if limite is None else 7
    maximo = 25 if limite is None else limite
    if limite is not None:
        monkeypatch.setenv('RECORDATORIOS_MAX_CLIENTES', str(limite))
    for i in range(cantidad):
        e.cliente(nombre=f'{i:03d} ' + '&' * 240)
    ejecutar(e)
    for usuario in (e.admin, e.vendedor):
        mensajes = envios(e, usuario.telegram_chat_id)
        assert len(tarjetas(e, usuario.telegram_chat_id)) == maximo
        assert len(mensajes) == maximo + 1
        assert not botones(mensajes[0][2])
        assert re.search(rf'\b{cantidad - maximo}\b', mensajes[0][1])
    assert all(len(texto) <= 4096 for _, texto, _ in envios(e))


def test_cli_limite_respeta_prioridad_de_bloques_y_se_relee(escenario, monkeypatch):
    e = escenario
    atrasados = {e.cliente(-d).id for d in (1, 2)}
    manana = {e.cliente(1).id for _ in range(2)}
    hoy = {e.cliente().id for _ in range(2)}
    monkeypatch.setenv('RECORDATORIOS_MAX_CLIENTES', '3')
    ejecutar(e)
    enviados = ids_tarjetas(e, e.admin.telegram_chat_id)
    assert set(enviados[:2]) == hoy
    assert len(enviados) == 3 and enviados[2] in manana
    assert set(enviados).isdisjoint(atrasados)
    e.send.reset_mock()
    monkeypatch.setenv('RECORDATORIOS_MAX_CLIENTES', '6')
    ejecutar(e)
    assert set(ids_tarjetas(e, e.admin.telegram_chat_id)) == hoy | manana | atrasados


@pytest.mark.parametrize('valor', [None, 'false', '0', ''])
def test_cli_desactivado_sin_envios_y_con_resumen(escenario, monkeypatch, valor):
    escenario.cliente()
    if valor is None:
        monkeypatch.delenv('RECORDATORIOS_ACTIVOS', raising=False)
    else:
        monkeypatch.setenv('RECORDATORIOS_ACTIVOS', valor)
    with sin_escrituras():
        resultado = ejecutar(escenario)
    escenario.send.assert_not_called()
    assert 'desactivado' in resultado.output.lower()
    assert resumen(resultado)['mensajes'] == 0


def test_cli_activos_se_lee_en_cada_invocacion(escenario, monkeypatch):
    e = escenario
    e.cliente()
    for valor, habilitado in (('false', False), ('true', True), ('false', False)):
        monkeypatch.setenv('RECORDATORIOS_ACTIVOS', valor)
        e.send.reset_mock()
        ejecutar(e)
        assert e.send.called is habilitado


def test_cli_dry_run_imprime_lista_sin_envios_ni_escrituras(escenario):
    e = escenario
    clientes = [e.cliente(d) for d in (0, 1, -1)]
    with sin_escrituras():
        resultado = ejecutar(e, '--dry-run')
    e.send.assert_not_called()
    for cliente in clientes:
        assert cliente.nombre in resultado.output
    conteos = resumen(resultado)
    assert conteos['hoy'] == conteos['manana'] == conteos['retrasados'] == 1
    assert conteos['mensajes'] == 0


@pytest.mark.parametrize('modo', ['excepcion', 'none'])
def test_cli_fallo_de_usuario_no_interrumpe_a_los_demas(escenario, modo):
    e = escenario
    cliente = e.cliente()
    chats = {e.admin.telegram_chat_id, e.vendedor.telegram_chat_id}
    fallido = []

    def enviar(chat_id, text, reply_markup=None):
        # Falla el primer destinatario que se intente, sin asumir orden de consulta.
        if not fallido:
            fallido.append(chat_id)
        if chat_id == fallido[0]:
            if modo == 'excepcion':
                raise RuntimeError('Fallo simulado de transporte')
            return None
        return {'ok': True, 'result': {'message_id': 902}}

    e.send.side_effect = enviar
    resultado = ejecutar(e)
    sano, = chats - set(fallido)
    assert ids_tarjetas(e, sano) == [cliente.id]
    assert resumen(resultado)['errores'] >= 1


def test_cli_idempotente_y_logs_sin_nombre_ni_telefono(escenario, caplog):
    e = escenario
    cliente = e.cliente(nombre='NombrePrivadoRecordatorioXYZ', telefono='+51 987-654-321')
    caplog.set_level(logging.DEBUG)
    with sin_escrituras():
        primero = ejecutar(e)
        mensajes_primero = deepcopy(envios(e))
        e.send.reset_mock()
        segundo = ejecutar(e)
    assert mensajes_primero == envios(e)
    assert resumen(primero) == resumen(segundo)
    db.session.expire_all()
    assert db.session.get(Cliente, cliente.id).ultimo_contacto is None
    for privado in (cliente.nombre, cliente.telefono, '987654321', '51987654321'):
        assert privado not in caplog.text


def test_cli_error_de_transporte_no_filtra_datos_del_cliente_en_logs(escenario, caplog):
    e = escenario
    cliente = e.cliente(nombre='ClientePrivadoErrorXYZ', telefono='987 654 321')
    caplog.set_level(logging.DEBUG)
    # Un transporte puede incluir datos del envío en su excepción: deben sanearse.
    e.send.side_effect = RuntimeError(f'{cliente.nombre}: 51987654321 / {cliente.telefono}')
    resultado = ejecutar(e)
    assert resumen(resultado)['errores'] >= 1
    for privado in (cliente.nombre, cliente.telefono, '987654321', '51987654321'):
        assert privado not in caplog.text


def test_cli_sin_clientes_no_envia_ni_siquiera_cabecera(escenario):
    escenario.cliente(-31)  # Solo inactivos: los tres bloques siguen vacíos.
    resultado = ejecutar(escenario)
    escenario.send.assert_not_called()
    conteos = resumen(resultado)
    assert conteos['hoy'] == conteos['manana'] == conteos['retrasados'] == 0
    assert conteos['inactivos'] == 1
    assert conteos['mensajes'] == 0


def test_cli_sin_destinatarios_conserva_conteos_globales(escenario):
    e = escenario
    e.cliente()
    e.admin.telegram_chat_id = e.vendedor.telegram_chat_id = None
    db.session.commit()
    resultado = ejecutar(e)
    e.send.assert_not_called()
    assert resumen(resultado)['hoy'] == 1


def test_cli_fecha_omitida_coincide_con_dia_actual_de_lima(escenario):
    e = escenario
    hoy_lima = datetime.now(LIMA).date()
    for dias, cantidad in ((-1, 3), (0, 2), (1, 1)):
        for _ in range(cantidad):
            e.cliente(proxima_compra_manual=hoy_lima + timedelta(days=dias))
    resultado = ejecutar(e, fecha=None)
    conteos = resumen(resultado)
    assert datetime.now(LIMA).date() == hoy_lima, 'El caso cruzó medianoche de Lima; repetir'
    assert (conteos['hoy'], conteos['manana'], conteos['retrasados']) == (2, 1, 3)


@pytest.mark.parametrize('rol', ['admin', 'usuario', 'gerente'])
def test_callback_autorizado_persiste_contacto_edita_y_es_repetible(escenario, rol):
    from services.recordatorio_service import seleccionar

    e = escenario
    hoy_lima = datetime.now(LIMA).date()
    cliente = e.cliente(proxima_compra_manual=hoy_lima,
                        almacen_preferido_id=e.b.id if rol == 'admin' else e.a.id)
    usuario = e.admin if rol == 'admin' else e.vendedor
    usuario.rol = rol
    db.session.commit()
    assert cliente.id in ids(seleccionar(hoy_lima))
    antes = datetime.now(UTC)
    callback(e, usuario, f'contactado:{cliente.id}')
    db.session.expire_all()
    contacto = db.session.get(Cliente, cliente.id).ultimo_contacto
    assert contacto is not None
    contacto = contacto.replace(tzinfo=UTC) if contacto.tzinfo is None else contacto
    assert antes <= contacto <= datetime.now(UTC) + timedelta(seconds=1)
    assert e.edit.called
    assert any(
        (ll.args[0] if ll.args else ll.kwargs['chat_id']) == usuario.telegram_chat_id
        and (ll.args[1] if len(ll.args) > 1 else ll.kwargs['message_id']) == 55
        and 'Contactado' in (ll.args[2] if len(ll.args) > 2 else ll.kwargs['text'])
        for ll in e.edit.call_args_list
    )
    assert cliente.id not in ids(seleccionar(hoy_lima))
    callback(e, usuario, f'contactado:{cliente.id}')
    db.session.expire_all()
    assert db.session.get(Cliente, cliente.id).ultimo_contacto is not None


@pytest.mark.parametrize('rol', ['usuario', 'gerente'])
@pytest.mark.parametrize('contacto_previo', [None, datetime(2026, 1, 1, 12, tzinfo=UTC)])
def test_callback_otro_almacen_no_modifica_y_avisa(escenario, rol, contacto_previo):
    e = escenario
    cliente = e.cliente(almacen_preferido_id=e.b.id, ultimo_contacto=contacto_previo)
    e.vendedor.rol = rol
    db.session.commit()
    db.session.expire_all()
    anterior = db.session.get(Cliente, cliente.id).ultimo_contacto
    callback(e, e.vendedor, f'contactado:{cliente.id}')
    db.session.expire_all()
    assert db.session.get(Cliente, cliente.id).ultimo_contacto == anterior
    assert e.send.called or e.edit.called or e.answer.called


@pytest.mark.parametrize('dato', ['contactado:999999', 'contactado:abc'])
def test_callback_inexistente_o_malformado_avisa_sin_excepcion(escenario, dato):
    e = escenario
    e.cliente()
    callback(e, e.admin, dato)
    assert e.send.called or e.edit.called or e.answer.called
