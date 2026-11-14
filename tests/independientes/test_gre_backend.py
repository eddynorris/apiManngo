"""Contrato GRE backend: SQLite, certificado sintético y transportes locales."""

import base64
from copy import deepcopy
from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from flask_jwt_extended import create_access_token
from lxml import etree
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from extensions import db

EMISOR = {
    'SUNAT_RUC': '20999999999',
    'SUNAT_RAZON_SOCIAL': 'GRE INDEPENDIENTE SINTETICA SAC',
    'SUNAT_EMISOR_DIRECCION': 'AV PRUEBA 123',
    'SUNAT_EMISOR_UBIGEO': '150101',
    'SUNAT_SERIE_GRE': 'T991',
}


@pytest.fixture(autouse=True)
def aislamiento(test_client, monkeypatch):
    """El fixture compartido es de módulo: retirar filas propias incluso tras commits."""
    tablas = list(db.metadata.sorted_tables)
    originales = {
        t.name: [dict(r) for r in db.session.execute(db.select(t)).mappings()]
        for t in tablas
    }
    for key, value in EMISOR.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('SUNAT_ENVIO_HABILITADO', 'false')
    monkeypatch.delenv('SUNAT_CERT_P12_B64', raising=False)
    monkeypatch.delenv('SUNAT_CERT_PASSWORD', raising=False)
    yield
    db.session.rollback()
    db.session.remove()
    for tabla in reversed(tablas):
        filas = originales[tabla.name]
        conservar = [and_(*(c == r[c.name] for c in tabla.primary_key)) for r in filas]
        sentencia = tabla.delete()
        if conservar:
            sentencia = sentencia.where(~or_(*conservar))
        db.session.execute(sentencia)
    db.session.commit()


@pytest.fixture(scope='module')
def certificado():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'GRE LOCAL SINTETICA')])
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime(2020, 1, 1, tzinfo=UTC))
            .not_valid_after(datetime(2035, 1, 1, tzinfo=UTC)).sign(key, hashes.SHA256()))
    return base64.b64encode(pkcs12.serialize_key_and_certificates(
        b'gre-test', key, cert, None,
        serialization.BestAvailableEncryption(b'clave-sintetica'))).decode()


def escenario(m):
    """Se llama desde el test, nunca durante colección ni desde un fixture."""
    cliente = m.Cliente(nombre='GRE independiente destinatario', tipo_documento='6',
                        ruc='20999999998', direccion='CALLE LOCAL 456', ubigeo='150102')
    producto = m.Producto(nombre='GRE independiente carbon', precio_compra=1)
    vehiculo = m.Vehiculo(placa='GRE991', predeterminado=True, activo=True)
    conductor = m.Conductor(numero_documento='00000991', nombres='PRUEBA',
                            apellidos='LOCAL', licencia='Q00000991',
                            predeterminado=True, activo=True)
    db.session.add_all([cliente, producto, vehiculo, conductor])
    db.session.flush()
    origen = m.Almacen(nombre='GRE independiente origen', direccion='AV LOCAL 123',
                       ubigeo='150101')
    destino = m.Almacen(nombre='GRE independiente destino', direccion='CALLE LOCAL 456',
                        ubigeo='150102', cliente_guia_id=cliente.id)
    p20 = m.PresentacionProducto(nombre='GRE independiente saco', producto_id=producto.id,
                                 capacidad_kg=20, descripcion_guia='SACOS DE CARBÓN VEGETAL '
                                 'DE 20 KILOS', tipo='procesado', precio_venta=10)
    p5 = m.PresentacionProducto(nombre='GRE independiente bolsa', producto_id=producto.id,
                                capacidad_kg=5, tipo='procesado', precio_venta=5)
    db.session.add_all([origen, destino, p20, p5])
    db.session.commit()
    return SimpleNamespace(cliente=cliente, origen=origen, destino=destino, p20=p20, p5=p5,
                           vehiculo=vehiculo, conductor=conductor,
                           actor=m.Users.query.filter_by(username='admin').one())


def borrador(gs, e, **kwargs):
    return gs.borrador_traslado(e.actor, e.origen.id, e.destino.id,
                               [{'presentacion_id': e.p20.id, 'cantidad': 160},
                                {'presentacion_id': e.p5.id, 'cantidad': 60}], **kwargs)


def habilitar(monkeypatch, certificado):
    monkeypatch.setenv('SUNAT_ENVIO_HABILITADO', 'true')
    monkeypatch.setenv('SUNAT_CERT_P12_B64', certificado)
    monkeypatch.setenv('SUNAT_CERT_PASSWORD', 'clave-sintetica')


def transporte(gs, monkeypatch, efecto=None):
    from services.sunat_service import GreEnvioResult

    doble = Mock(spec=['emitir_guia_remision', 'consultar_estado_ticket'])
    def enviar(prepared):
        if efecto:
            raise efecto
        return GreEnvioResult('enviado', f'{prepared.data.serie}-{prepared.data.numero:08d}',
                              prepared.package.filename, 'TICKET-LOCAL', 200)
    doble.emitir_guia_remision.side_effect = enviar
    monkeypatch.setattr(gs, 'sunat_service', doble)
    return doble


def test_modelos_y_unicidad(test_client):
    import models as m

    e = escenario(m)
    def nueva(clave):
        return m.GuiaRemision(serie='T991', numero=1, estado='enviada', motivo='01',
                              origen_tipo='traslado', almacen_origen_id=e.origen.id,
                              almacen_destino_id=e.destino.id, destinatario_cliente_id=e.cliente.id,
                              vehiculo_id=e.vehiculo.id, conductor_id=e.conductor.id,
                              fecha_inicio_traslado=date.today(), datos={},
                              idempotency_key=clave, creado_por_id=e.actor.id)
    db.session.add(nueva('gre-modelo-1'))
    db.session.commit()
    db.session.add(nueva('gre-modelo-2'))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    assert m.GuiaRemision.query.count() == 1
    assert e.conductor.tipo_documento == '1'
    assert m.SerieGre(serie='T992').serie == 'T992'
    assert 'guia_remision_id' in m.Movimiento.__table__.columns


@pytest.mark.parametrize('motivo', ['01', '04'])
def test_borrador_traslado(test_client, motivo):
    import models as m
    from services import guia_service as gs
    from utils.date_utils import get_peru_now

    e = escenario(m)
    if motivo == '04':
        e.origen.codigo_establecimiento_sunat = '0001'
        e.destino.codigo_establecimiento_sunat = '0002'
        db.session.commit()
    antes = {t.name: db.session.scalar(db.select(db.func.count()).select_from(t))
             for t in db.metadata.sorted_tables}
    hoy = get_peru_now().date().isoformat()
    b = borrador(gs, e)
    assert b['motivo'] == motivo
    assert b['origen_tipo'] == 'traslado'
    assert b['almacen_origen_id'] == e.origen.id
    assert b['almacen_destino_id'] == e.destino.id
    assert b['destinatario_cliente_id'] == (e.cliente.id if motivo == '01' else None)
    assert (b['vehiculo_id'], b['conductor_id']) == (e.vehiculo.id, e.conductor.id)
    assert b['fecha_inicio_traslado'] in {hoy, get_peru_now().date().isoformat()}
    assert b['bienes'][0] == {'presentacion_id': e.p20.id, 'cantidad': 160,
                             'descripcion': '160 SACOS DE CARBÓN VEGETAL DE 20 KILOS',
                             'unidad': 'KGM', 'peso_kg': 3200}
    assert b['bienes'][1]['descripcion'] == '60 ' + e.p5.nombre.upper()
    assert b['bienes'][1]['unidad'] == 'KGM'
    assert b['bienes'][1]['peso_kg'] == 300
    assert b['peso_total_kg'] == 3500
    assert gs.faltantes(b) == []
    db.session.flush()
    assert antes == {t.name: db.session.scalar(db.select(db.func.count()).select_from(t))
                     for t in db.metadata.sorted_tables}


def test_borrador_venta_y_opciones(test_client):
    import models as m
    from services import guia_service as gs

    e = escenario(m)
    venta = m.Venta(cliente_id=e.cliente.id, almacen_id=e.origen.id,
                     vendedor_id=e.actor.id, total=20, tipo_pago='contado', estado_pago='pendiente')
    db.session.add(venta)
    db.session.flush()
    db.session.add(m.VentaDetalle(venta_id=venta.id, presentacion_id=e.p5.id,
                                  cantidad=Decimal('2.5'), precio_unitario=5))
    db.session.commit()
    b = gs.borrador_venta(e.actor, venta.id, fecha_inicio=date(2026, 11, 15),
                          vehiculo_id=e.vehiculo.id, conductor_id=e.conductor.id)
    assert (b['motivo'], b['origen_tipo'], b['venta_id']) == ('01', 'venta', venta.id)
    assert b['destinatario_cliente_id'] == e.cliente.id
    assert b['almacen_origen_id'] == e.origen.id
    assert b['fecha_inicio_traslado'] == '2026-11-15'
    assert len(b['bienes']) == 1
    assert b['bienes'][0]['presentacion_id'] == e.p5.id
    assert b['bienes'][0]['cantidad'] == 2.5
    assert b['bienes'][0]['peso_kg'] == b['peso_total_kg'] == 12.5
    assert b['bienes'][0]['descripcion'] == '2.5 ' + e.p5.nombre.upper()
    assert gs.faltantes(b) == []


@pytest.mark.parametrize('variable', list(EMISOR))
def test_faltantes_configuracion(test_client, monkeypatch, variable):
    import models as m
    from services import guia_service as gs

    b = borrador(gs, escenario(m))
    monkeypatch.delenv(variable)
    mensajes = gs.faltantes(b)
    assert len(mensajes) == 1
    assert variable in mensajes[0]


@pytest.mark.parametrize('objeto,campo,fragmento', [
    ('origen', 'ubigeo', 'ubigeo'), ('origen', 'direccion', 'dirección'),
    ('destino', 'ubigeo', 'ubigeo'), ('destino', 'direccion', 'dirección'),
    ('cliente', 'tipo_documento', 'documento'), ('cliente', 'ruc', 'documento'),
    ('conductor', 'licencia', 'licencia'), ('conductor', 'nombres', 'nombre'),
    ('conductor', 'apellidos', 'apellido'),
    ('p20', 'capacidad_kg', 'GRE independiente saco'),
])
def test_faltantes_datos_individuales(test_client, objeto, campo, fragmento):
    import models as m
    from services import guia_service as gs

    e = escenario(m)
    setattr(getattr(e, objeto), campo, 0 if (objeto, campo) == ('p20', 'capacidad_kg') else None)
    db.session.commit()
    b = borrador(gs, e)
    mensajes = gs.faltantes(b)
    assert mensajes
    assert any(fragmento.lower() in msg.lower() for msg in mensajes)
    if objeto in {'origen', 'destino'}:
        assert any(getattr(e, objeto).nombre in msg for msg in mensajes)
    if (objeto, campo) == ('p20', 'capacidad_kg'):
        assert getattr(getattr(e, objeto), campo) == 0
    else:
        assert getattr(getattr(e, objeto), campo) is None


@pytest.mark.parametrize('campo', ['vehiculo_id', 'conductor_id', 'destinatario_cliente_id'])
def test_faltantes_entidad_ausente(test_client, campo):
    import models as m
    from services import guia_service as gs

    b = borrador(gs, escenario(m))
    b[campo] = None
    mensajes = gs.faltantes(b)
    assert mensajes
    assert any(campo.split('_')[0] in msg.lower() for msg in mensajes)


@pytest.mark.parametrize('peso', [0, -1])
def test_faltantes_peso_no_positivo(test_client, peso):
    import models as m
    from services import guia_service as gs

    e = escenario(m)
    e.p20.capacidad_kg = peso
    db.session.commit()
    assert any(e.p20.nombre in msg for msg in gs.faltantes(borrador(gs, e)))


@pytest.mark.parametrize('caso', ['faltantes', 'deshabilitado'])
def test_emitir_precondiciones_sin_reservar(test_client, monkeypatch, caso):
    import models as m
    from services import guia_service as gs

    e = escenario(m)
    b = borrador(gs, e)
    doble = transporte(gs, monkeypatch)
    error = gs.GuiaEnvioDeshabilitado
    if caso == 'faltantes':
        monkeypatch.delenv('SUNAT_RUC')
        error = gs.GuiaDatosFaltantes
    with pytest.raises(error) as exc:
        gs.emitir(b, e.actor, 'gre-precondicion')
    if caso == 'faltantes':
        assert exc.value.faltantes
        assert any('SUNAT_RUC' in msg for msg in exc.value.faltantes)
    assert m.GuiaRemision.query.count() == 0
    assert m.SerieGre.query.count() == 0
    doble.emitir_guia_remision.assert_not_called()


def test_emitir_numera_firma_commit_idempotencia_y_movimientos(test_client, monkeypatch, certificado):
    import models as m
    from services import guia_service as gs

    e = escenario(m)
    habilitar(monkeypatch, certificado)
    doble = transporte(gs, monkeypatch)
    enviar = doble.emitir_guia_remision.side_effect
    def comprobar_commit(prepared):
        with db.engine.connect() as conn:
            fila = conn.execute(db.select(m.GuiaRemision.__table__).where(
                m.GuiaRemision.numero == prepared.data.numero)).mappings().one()
            assert fila['xml_firmado'] and fila['hash_zip'] and fila['datos']
            assert 'Signature' in fila['xml_firmado']
        return enviar(prepared)
    doble.emitir_guia_remision.side_effect = comprobar_commit
    mov = m.Movimiento(tipo='salida', tipo_operacion='transferencia', presentacion_id=e.p20.id,
                        cantidad=1, usuario_id=e.actor.id)
    db.session.add(mov)
    db.session.commit()
    b = borrador(gs, e)
    g = gs.emitir(b, e.actor, 'gre-uno', chat_id=991, movimiento_ids=[mov.id])
    assert (g.serie, g.numero, g.estado, g.ticket) == ('T991', 1, 'enviada', 'TICKET-LOCAL')
    db.session.refresh(mov)
    assert mov.guia_remision_id == g.id
    assert gs.emitir(deepcopy(b), e.actor, 'gre-uno').id == g.id
    cambiado = deepcopy(b)
    cambiado['fecha_inicio_traslado'] = '2026-11-16'
    with pytest.raises(gs.GuiaConflicto):
        gs.emitir(cambiado, e.actor, 'gre-uno')
    assert doble.emitir_guia_remision.call_count == 1
    segundo = gs.emitir(b, e.actor, 'gre-dos')
    assert segundo.numero == 2
    assert m.SerieGre.query.filter_by(serie='T991').one().ultimo_numero == 2
    assert doble.emitir_guia_remision.call_count == 2


@pytest.mark.parametrize('tipo,estado', [('SunatTransmissionUncertain', 'incierta'),
                                        ('SunatAuthError', 'error')])
def test_emitir_errores_conservan_numero(test_client, monkeypatch, certificado, tipo, estado):
    import models as m
    from services import guia_service as gs
    from services import sunat_service as ss

    e = escenario(m)
    habilitar(monkeypatch, certificado)
    secreto = 'password=SECRETO-SINTETICO-991'
    doble = transporte(gs, monkeypatch, getattr(ss, tipo)(secreto))
    g = gs.emitir(borrador(gs, e), e.actor, 'gre-error')
    assert (g.estado, g.numero) == (estado, 1)
    assert m.SerieGre.query.one().ultimo_numero == 1
    if estado == 'error':
        assert g.cdr_mensaje and 'SECRETO-SINTETICO-991' not in g.cdr_mensaje
    assert gs.emitir(borrador(gs, e), e.actor, 'gre-error').id == g.id
    assert doble.emitir_guia_remision.call_count == 1


def ticket(sx, caso):
    qr = 'https://e-factura.sunat.gob.pe/descargaqr?hashqr=abc'
    xml = (f'<ApplicationResponse xmlns:cbc="{sx.CBC_NS}">'
           f'<cbc:DocumentDescription>{qr}</cbc:DocumentDescription>'
           '</ApplicationResponse>').encode()
    if caso in {'aceptada', 'sin_qr', 'rechazada'}:
        aceptada = caso != 'rechazada'
        cdr = sx.CdrResult(sx.CDR_ACEPTADO if aceptada else sx.CDR_RECHAZADO,
                           aceptada, '0' if aceptada else '2326', 'T991-00000001',
                           ('Observación local',), (), xml if caso != 'sin_qr' else
                           b'<ApplicationResponse/>')
        return sx.TicketResult(cdr.estado, aceptada, '0', cdr)
    return sx.TicketResult(sx.CDR_PENDIENTE if caso == 'pendiente' else sx.CDR_ERROR,
                           False, {'pendiente': '98', 'error99': '99',
                                   'sin_cdr': '0', 'desconocido': '777'}[caso],
                           message='Rechazo sintético' if caso == 'error99' else '')


@pytest.mark.parametrize('caso,esperado', [('aceptada', 'aceptada'), ('sin_qr', 'aceptada'),
                                         ('pendiente', 'en_proceso'),
                                         ('rechazada', 'rechazada'), ('error99', 'rechazada'),
                                         ('sin_cdr', 'enviada'), ('desconocido', 'enviada')])
def test_consultar_estados(test_client, monkeypatch, certificado, caso, esperado):
    import models as m
    from services import guia_service as gs
    from services import sunat_xml as sx

    e = escenario(m)
    habilitar(monkeypatch, certificado)
    doble = transporte(gs, monkeypatch)
    g = gs.emitir(borrador(gs, e), e.actor, 'gre-consulta')
    resultado = ticket(sx, caso)
    doble.consultar_estado_ticket.return_value = resultado
    actualizado = gs.consultar(g)
    assert actualizado.id == g.id
    assert actualizado.estado == esperado
    args = doble.consultar_estado_ticket.call_args.args
    assert args[0] == g.ticket
    assert isinstance(args[1], sx.GreRemitenteData)
    assert (args[1].serie, args[1].numero) == (g.serie, g.numero)
    if esperado == 'aceptada':
        assert g.cdr_codigo == '0'
        assert g.cdr_xml == resultado.cdr.cdr_xml.decode()
        assert list(g.cdr_observaciones) == list(resultado.cdr.observations)
        assert g.qr_url == ('https://e-factura.sunat.gob.pe/descargaqr?hashqr=abc'
                            if caso == 'aceptada' else None)
    if esperado == 'rechazada':
        assert g.cdr_codigo and g.cdr_mensaje


@pytest.mark.parametrize('estado', ['aceptada', 'rechazada', 'incierta', 'error'])
def test_consultar_no_reenvia_estados_finales(test_client, monkeypatch, estado):
    from services import guia_service as gs

    doble = transporte(gs, monkeypatch)
    g = SimpleNamespace(estado=estado)
    assert gs.consultar(g) is g
    doble.consultar_estado_ticket.assert_not_called()


def test_pendientes_aisla_error_y_notifica_pdf(test_client, monkeypatch, certificado):
    import models as m
    from services import guia_service as gs
    from services import sunat_xml as sx
    from services.telegram_service import telegram_service

    e = escenario(m)
    habilitar(monkeypatch, certificado)
    doble = transporte(gs, monkeypatch)
    guias = [gs.emitir(borrador(gs, e), e.actor, f'gre-pendiente-{i}', chat_id=990 + i)
             for i in range(4)]
    guias[1].estado = 'en_proceso'
    db.session.commit()
    resultados = {guias[0].numero: RuntimeError('fallo local'),
                  guias[1].numero: ticket(sx, 'aceptada'),
                  guias[2].numero: ticket(sx, 'rechazada'),
                  guias[3].numero: ticket(sx, 'pendiente')}
    def consultar(_ticket, expected):
        respuesta = resultados[expected.numero]
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta
    doble.consultar_estado_ticket.side_effect = consultar
    mensaje, documento = Mock(), Mock()
    monkeypatch.setattr(telegram_service, 'send_message', mensaje)
    monkeypatch.setattr(telegram_service, 'send_document', documento)
    assert gs.procesar_pendientes() == {
        'consultadas': 4, 'aceptadas': 1, 'rechazadas': 1, 'errores': 1}
    assert mensaje.call_count == 2
    textos = {c.args[0]: c.args[1] for c in mensaje.call_args_list}
    assert 'Aceptada' in textos[991] and 'T991-00000002' in textos[991]
    assert 'Rechazada' in textos[992] and 'T991-00000003' in textos[992]
    documento.assert_called_once()
    args = documento.call_args.args
    assert args[0] == 991 and args[1].endswith('.pdf') and args[2].startswith(b'%PDF')
    gs.procesar_pendientes()
    assert mensaje.call_count == 2
    assert documento.call_count == 1


def test_cli_guias_pendientes(test_client, monkeypatch):
    from services import guia_service as gs

    doble = Mock(return_value={'consultadas': 0, 'aceptadas': 0, 'rechazadas': 0, 'errores': 0})
    monkeypatch.setattr(gs, 'procesar_pendientes', doble)
    result = test_client.application.test_cli_runner().invoke(args=['guias-pendientes'])
    assert result.exit_code == 0, result.output
    assert any(line.startswith('guias-pendientes:') for line in result.output.splitlines())
    doble.assert_called_once()


@pytest.mark.parametrize('estado,numero', [('aceptada', 1), ('enviada', 1), ('aceptada', None)])
def test_pdf(test_client, monkeypatch, certificado, estado, numero):
    import models as m
    from services import guia_pdf
    from services import guia_service as gs
    from services import sunat_xml as sx

    e = escenario(m)
    habilitar(monkeypatch, certificado)
    doble = transporte(gs, monkeypatch)
    g = gs.emitir(borrador(gs, e), e.actor, 'gre-pdf')
    doble.consultar_estado_ticket.return_value = ticket(sx, 'aceptada')
    g = gs.consultar(g)
    g.estado, g.numero = estado, numero
    if estado != 'aceptada' or numero is None:
        with pytest.raises(gs.GuiaError):
            guia_pdf.generar_pdf(g)
    else:
        contenido = guia_pdf.generar_pdf(g)
        assert isinstance(contenido, bytes) and contenido.startswith(b'%PDF')
        assert len(contenido) > 1000


@pytest.mark.parametrize('con_token', [True, False])
def test_telegram_documento_multipart(test_client, monkeypatch, con_token):
    from services import telegram_service as ts

    if con_token:
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'TOKEN-SINTETICO')
    else:
        monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    post = Mock(return_value=Mock(json=lambda: {'ok': True}))
    monkeypatch.setattr(ts.requests, 'post', post)
    servicio = ts.TelegramService()
    resultado = servicio.send_document(991, 'gre.pdf', b'%PDF-local', 'Guía local')
    if not con_token:
        assert resultado is None
        post.assert_not_called()
    else:
        post.assert_called_once()
        args, kwargs = post.call_args
        assert args == (servicio.api_url + '/sendDocument',)
        assert kwargs['data']['chat_id'] == 991
        assert kwargs['data']['caption'] == 'Guía local'
        assert kwargs['files'] == {'document': ('gre.pdf', b'%PDF-local', 'application/pdf')}
        assert kwargs['timeout'] > 0


def headers(m, rol):
    usuario = m.Users.query.filter_by(rol=rol).first()
    token = create_access_token(identity=str(usuario.id), additional_claims={
        'rol': usuario.rol, 'almacen_id': usuario.almacen_id})
    return {'Authorization': 'Bearer ' + token}


@pytest.mark.parametrize('ruta', ['vehiculos', 'conductores'])
def test_crud_permisos_listado_predeterminado(test_client, ruta):
    import models as m

    admin, usuario = headers(m, 'admin'), headers(m, 'usuario')
    def cuerpo(i):
        if ruta == 'vehiculos':
            return {'placa': f'gre99{i}', 'descripcion': 'GRE independiente',
                    'predeterminado': True, 'activo': True}
        return {'tipo_documento': '1', 'numero_documento': f'0000099{i}',
                'nombres': 'GRE', 'apellidos': 'INDEPENDIENTE', 'licencia': f'Q0000099{i}',
                'predeterminado': True, 'activo': True}
    assert test_client.post('/' + ruta, json=cuerpo(1), headers=usuario).status_code == 403
    for i in (1, 2):
        r = test_client.post('/' + ruta, json=cuerpo(i), headers=admin)
        assert r.status_code == 201, r.get_data(as_text=True)
    modelo = m.Vehiculo if ruta == 'vehiculos' else m.Conductor
    filas = modelo.query.order_by(modelo.id).all()
    assert len(filas) == 2
    primero, segundo = filas
    assert not primero.predeterminado and segundo.predeterminado
    r = test_client.get('/' + ruta, headers=admin)
    assert r.status_code == 200
    assert isinstance(r.json['data'], list) and isinstance(r.json['pagination'], dict)
    assert len(r.json['data']) == 2
    url = f'/{ruta}/{primero.id}'
    assert test_client.put(url, json={'predeterminado': True}, headers=usuario).status_code == 403
    assert test_client.delete(url, headers=usuario).status_code == 403
    assert test_client.put(url, json={'predeterminado': True}, headers=admin).status_code == 200
    db.session.expire_all()
    assert primero.predeterminado and not segundo.predeterminado
    assert test_client.delete(url, headers=admin).status_code in {200, 204}


@pytest.mark.parametrize('placa,valida', [('d8m-790', True), (' d8m 790 ', True),
                                        ('AB12', False), ('ABCDEFGHI', False), ('ABC!23', False)])
def test_vehiculo_normalizacion_validacion_duplicado(test_client, placa, valida):
    import models as m

    auth = headers(m, 'admin')
    r = test_client.post('/vehiculos', json={'placa': placa}, headers=auth)
    assert r.status_code == (201 if valida else 400), r.get_data(as_text=True)
    if valida:
        assert m.Vehiculo.query.one().placa == 'D8M790'
        assert test_client.post('/vehiculos', json={'placa': 'D8M790'},
                                headers=auth).status_code == 409


@pytest.mark.parametrize('con_codigos', [True, False])
def test_xml_codigo_establecimiento(test_client, con_codigos):
    from services import sunat_xml as sx

    data = sx.GreRemitenteData(
        serie='T991', numero=1, fecha_emision=date(2026, 11, 14), hora_emision=time(9),
        fecha_inicio_traslado=date(2026, 11, 15), motivo_traslado='04',
        tipo_documento='09', modalidad_traslado='02',
        emisor=sx.EmisorGre('20999999999', 'EMISOR LOCAL', '150101', 'AV LOCAL 123'),
        destinatario=sx.DestinatarioGre('6', '20999999999', 'EMISOR LOCAL',
                                       '150102', 'CALLE LOCAL 456'),
        partida_ubigeo='150101', partida_direccion='AV LOCAL 123',
        llegada_ubigeo='150102', llegada_direccion='CALLE LOCAL 456',
        transporte=sx.TransportePrivadoGre('GRE991', '1', '00000991', 'PRUEBA LOCAL', 'Q00000991'),
        bienes=(sx.BienGre('P1', 'SACO LOCAL', 'KGM', Decimal('1'), Decimal('20')),),
        partida_codigo_establecimiento='0001' if con_codigos else None,
        llegada_codigo_establecimiento='0002' if con_codigos else None)
    raiz = etree.fromstring(sx.build_despatch_xml(data))
    ns = {'cac': sx.CAC_NS, 'cbc': sx.CBC_NS}
    for direccion, codigo in [('OriginAddress', '0001'), ('DeliveryAddress', '0002')]:
        encontrados = raiz.xpath(f'//cac:{direccion}/cbc:AddressTypeCode/text()', namespaces=ns)
        assert encontrados == ([codigo] if con_codigos else [])


@pytest.mark.parametrize('campo,fragmento', [('ubigeo', 'ubigeo'), ('direccion', 'dirección')])
def test_faltantes_llegada_venta(test_client, campo, fragmento):
    import models as m
    from services import guia_service as gs

    e = escenario(m)
    venta = m.Venta(cliente_id=e.cliente.id, almacen_id=e.origen.id,
                     vendedor_id=e.actor.id, total=10, tipo_pago='contado', estado_pago='pendiente')
    db.session.add(venta)
    db.session.flush()
    db.session.add(m.VentaDetalle(venta_id=venta.id, presentacion_id=e.p20.id,
                                  cantidad=1, precio_unitario=10))
    setattr(e.cliente, campo, None)
    db.session.commit()
    mensajes = gs.faltantes(gs.borrador_venta(e.actor, venta.id))
    assert any(fragmento in msg.lower() and e.cliente.nombre in msg for msg in mensajes)
    assert getattr(e.cliente, campo) is None


def test_traslado_destinatario_explicito(test_client):
    import models as m
    from services import guia_service as gs

    e = escenario(m)
    otro = m.Cliente(nombre='GRE independiente explícito', tipo_documento='1',
                     ruc='00000992', ubigeo='150103', direccion='CALLE SINTETICA 789')
    db.session.add(otro)
    db.session.commit()
    b = borrador(gs, e, destinatario_cliente_id=otro.id, fecha_inicio=date(2026, 11, 15),
                 vehiculo_id=e.vehiculo.id, conductor_id=e.conductor.id)
    assert b['destinatario_cliente_id'] == otro.id
    assert b['fecha_inicio_traslado'] == '2026-11-15'
    assert b['vehiculo_id'] == e.vehiculo.id and b['conductor_id'] == e.conductor.id
    assert gs.faltantes(b) == []


def test_motivo04_emite_destinatario_empresa(test_client, monkeypatch, certificado):
    import models as m
    from services import guia_service as gs

    e = escenario(m)
    e.origen.codigo_establecimiento_sunat = '0001'
    e.destino.codigo_establecimiento_sunat = '0002'
    db.session.commit()
    habilitar(monkeypatch, certificado)
    doble = transporte(gs, monkeypatch)
    gs.emitir(borrador(gs, e), e.actor, 'gre-empresa')
    data = doble.emitir_guia_remision.call_args.args[0].data
    assert data.motivo_traslado == '04'
    assert data.destinatario.tipo_documento == '6'
    assert data.destinatario.numero_documento == EMISOR['SUNAT_RUC']
    assert data.destinatario.razon_social == EMISOR['SUNAT_RAZON_SOCIAL']
