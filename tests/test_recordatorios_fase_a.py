"""Tests propios (Claude) de la lista diaria de clientes por contactar (F5 fase A)."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from extensions import db
from models import Almacen, Cliente, Pedido, Users
from services import recordatorio_service as rs
from services.proyeccion import fecha_estimada
from services.telegram_service import telegram_service
from telegram.router import TelegramRouter

HOY = date(2026, 11, 20)


def _compra_hace(dias, frecuencia):
    """Última compra a mediodía de Lima, `dias` antes de HOY."""
    dia = HOY - timedelta(days=dias)
    return datetime(dia.year, dia.month, dia.day, 17, 0, tzinfo=UTC), frecuencia


# --- fecha_estimada --------------------------------------------------------

def test_fecha_estimada_usa_dia_de_lima_no_utc():
    # 10/11 23:30 en Lima = 11/11 04:30 UTC
    c = SimpleNamespace(proxima_compra_manual=None, frecuencia_compra_dias=7,
                        ultima_fecha_compra=datetime(2026, 11, 11, 4, 30, tzinfo=UTC))
    assert fecha_estimada(c) == date(2026, 11, 17)


def test_fecha_estimada_naive_se_toma_como_utc():
    c = SimpleNamespace(proxima_compra_manual=None, frecuencia_compra_dias=7,
                        ultima_fecha_compra=datetime(2026, 11, 11, 4, 30))
    assert fecha_estimada(c) == date(2026, 11, 17)


def test_fecha_manual_manda_y_sin_datos_es_none():
    manual = SimpleNamespace(proxima_compra_manual=date(2026, 12, 1), frecuencia_compra_dias=7,
                             ultima_fecha_compra=datetime(2026, 11, 1, tzinfo=UTC))
    assert fecha_estimada(manual) == date(2026, 12, 1)
    for freq, ultima in [(None, datetime.now(UTC)), (0, datetime.now(UTC)), (7, None)]:
        c = SimpleNamespace(proxima_compra_manual=None, frecuencia_compra_dias=freq,
                            ultima_fecha_compra=ultima)
        assert fecha_estimada(c) is None


@pytest.mark.parametrize('texto,esperado', [
    ('987 654 321', '51987654321'), ('+51 987-654-321', '51987654321'),
    ('51987654321', '51987654321'), ('014567890', None), ('887654321', None),
    ('98765', None), ('', None), (None, None),
])
def test_normalizar_telefono(texto, esperado):
    assert rs.normalizar_telefono_pe(texto) == esperado


def test_enlace_whatsapp_codifica_el_texto():
    url = rs.enlace_whatsapp('51987654321', 'José Ñuñez')
    partes = urlparse(url)
    assert url.startswith('https://wa.me/51987654321?text=') and ' ' not in url
    assert parse_qs(partes.query)['text'][0] == rs.MENSAJE_WHATSAPP.format(nombre='José Ñuñez')


# --- selección, comando y botón -------------------------------------------

@pytest.fixture
def escenario(test_client, monkeypatch):
    monkeypatch.delenv('RECORDATORIOS_DIAS_ANTES', raising=False)
    monkeypatch.delenv('RECORDATORIOS_MAX_CLIENTES', raising=False)
    sufijo = uuid4().hex[:8]
    norte = Almacen(nombre=f'Norte {sufijo}')
    sur = Almacen(nombre=f'Sur {sufijo}')
    db.session.add_all([norte, sur])
    db.session.flush()

    def cliente(nombre, dias, freq, almacen, telefono='987654321'):
        ultima, frecuencia = _compra_hace(dias, freq)
        c = Cliente(nombre=f'{nombre} {sufijo}', telefono=telefono, frecuencia_compra_dias=frecuencia,
                    ultima_fecha_compra=ultima, almacen_preferido_id=almacen.id)
        db.session.add(c)
        return c

    clientes = {
        'manana': cliente('Mañana', 6, 7, norte),          # fecha = HOY+1
        'hoy': cliente('Hoy', 7, 7, sur, telefono='01 4567890'),
        'retrasado': cliente('Retrasado', 12, 7, norte),   # 5 días
        'inactivo': cliente('Inactivo', 60, 7, norte),     # 53 días
        'lejos': cliente('Lejos', 2, 7, norte),            # fecha = HOY+5
        'con_pedido': cliente('Con pedido', 7, 7, norte),
    }
    db.session.flush()
    admin = Users.query.filter_by(username='admin').one()
    vendedor = Users.query.filter_by(username='vendedor').one()
    originales = {u.id: (u.telegram_chat_id, u.almacen_id) for u in (admin, vendedor)}
    pedido = Pedido(cliente_id=clientes['con_pedido'].id, almacen_id=norte.id, vendedor_id=admin.id,
                    fecha_entrega=datetime(2026, 11, 21, tzinfo=UTC), estado='confirmado')
    db.session.add(pedido)
    admin.telegram_chat_id = 810001
    vendedor.telegram_chat_id = 810002
    vendedor.almacen_id = sur.id
    db.session.commit()

    send = Mock(return_value={'ok': True})
    edit = Mock(return_value={'ok': True})
    monkeypatch.setattr(telegram_service, 'send_message', send)
    monkeypatch.setattr(telegram_service, 'edit_message', edit)
    ids = {k: v.id for k, v in clientes.items()}
    pedido_id, norte_id, sur_id = pedido.id, norte.id, sur.id
    try:
        yield SimpleNamespace(ids=ids, norte=norte_id, sur=sur_id, admin=admin, vendedor=vendedor,
                              send=send, edit=edit, test_client=test_client)
    finally:
        db.session.rollback()
        Pedido.query.filter_by(id=pedido_id).delete()
        Cliente.query.filter(Cliente.id.in_(ids.values())).delete(synchronize_session=False)
        for uid, (chat, almacen) in originales.items():
            u = db.session.get(Users, uid)
            u.telegram_chat_id, u.almacen_id = chat, almacen
        db.session.flush()
        Almacen.query.filter(Almacen.id.in_([norte_id, sur_id])).delete(synchronize_session=False)
        db.session.commit()


def _ids(lista):
    return [e.cliente_id for e in lista]


def test_seleccion_por_bloques(escenario):
    sel = rs.seleccionar(HOY)
    ids = escenario.ids
    assert _ids(sel.manana) == [ids['manana']]
    assert _ids(sel.hoy) == [ids['hoy']]
    assert _ids(sel.retrasados) == [ids['retrasado']]
    assert sel.retrasados[0].dias_retraso == 5
    assert sel.inactivos == 1
    assert sel.hoy[0].destino is None  # fijo: sigue en la lista, sin enlace
    todos = _ids(sel.por_orden())
    assert ids['lejos'] not in todos and ids['con_pedido'] not in todos


def test_seleccion_filtra_por_almacen(escenario):
    assert _ids(rs.seleccionar(HOY, almacen_id=escenario.sur).por_orden()) == [escenario.ids['hoy']]


def test_comando_desactivado_y_dry_run_no_envian(escenario, monkeypatch):
    runner = escenario.test_client.application.test_cli_runner()
    monkeypatch.delenv('RECORDATORIOS_ACTIVOS', raising=False)
    salida = runner.invoke(args=['recordatorios', '--fecha', HOY.isoformat()]).output
    assert 'desactivado' in salida and 'recordatorios: manana=1 hoy=1 retrasados=1' in salida
    monkeypatch.setenv('RECORDATORIOS_ACTIVOS', 'true')
    salida = runner.invoke(args=['recordatorios', '--fecha', HOY.isoformat(), '--dry-run']).output
    assert 'estado=dry-run' in salida and '987654321' not in salida
    escenario.send.assert_not_called()


def test_comando_envia_a_admin_todo_y_al_vendedor_su_almacen(escenario, monkeypatch):
    monkeypatch.setenv('RECORDATORIOS_ACTIVOS', 'true')
    runner = escenario.test_client.application.test_cli_runner()
    salida = runner.invoke(args=['recordatorios', '--fecha', HOY.isoformat()]).output
    assert 'usuarios=2 mensajes=6 errores=0' in salida  # admin: cabecera + 3; vendedor: cabecera + 1
    por_chat = {}
    for llamada in escenario.send.call_args_list:
        por_chat.setdefault(llamada.args[0], []).append(llamada)
    assert len(por_chat[810001]) == 4 and len(por_chat[810002]) == 2
    # Orden: hoy (teléfono fijo: sin botón url), mañana, retrasado.
    sin_url = por_chat[810001][1].kwargs['reply_markup']['inline_keyboard']
    assert sin_url == [[{'text': '✅ Contactado', 'callback_data': f"contactado:{escenario.ids['hoy']}"}]]
    con_url = por_chat[810001][2].kwargs['reply_markup']['inline_keyboard']
    assert con_url[0][0]['url'].startswith('https://wa.me/51987654321?text=')
    assert con_url[1][0]['callback_data'] == f"contactado:{escenario.ids['manana']}"


def test_boton_contactado_saca_al_cliente_y_respeta_almacen(escenario):
    cid = escenario.ids['retrasado']  # almacén norte

    def callback(user):
        chat = user.telegram_chat_id
        TelegramRouter.handle_callback_query({
            'id': 'cb', 'from': {'id': chat}, 'data': f'contactado:{cid}',
            'message': {'message_id': 77, 'chat': {'id': chat, 'type': 'private'}},
        })

    callback(escenario.vendedor)  # es del almacén sur
    assert db.session.get(Cliente, cid).ultimo_contacto is None
    assert 'almacén' in escenario.send.call_args.args[1]

    callback(escenario.admin)
    assert db.session.get(Cliente, cid).ultimo_contacto is not None
    assert 'Contactado' in escenario.edit.call_args.args[2]
    assert cid not in _ids(rs.seleccionar(HOY).por_orden())
