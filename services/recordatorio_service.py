"""Clientes por contactar: lista diaria por Telegram con enlace de WhatsApp (fase A de F5).

Cada mañana (cron ``flask recordatorios``) cada usuario con Telegram vinculado recibe los
clientes a los que se les acaba el carbón: hoy, en los próximos ``RECORDATORIOS_DIAS_ANTES``
días y los retrasados de hasta ``DIAS_RETRASO_MAX`` días. Cada cliente lleva un botón que abre
WhatsApp (``wa.me``) con el mensaje ya escrito, desde el número de la empresa, y un botón
«Contactado» que lo saca de la lista hasta su próximo ciclo (``clientes.ultimo_contacto``).

No envía nada por WhatsApp: el mensaje lo manda una persona. La selección no escribe en la BD,
así que repetir el comando da la misma lista. El plan está en
``.claude/tasks/recordatorios-whatsapp-f5.md``.
"""
import html
import logging
import os
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

import click
from flask.cli import with_appcontext
from sqlalchemy import and_, func, or_, select
from werkzeug.exceptions import HTTPException

from extensions import db
from models import Cliente, Pago, Pedido, Users, Venta
from services.authorization import autorizar_almacen, es_admin
from services.proyeccion import fecha_estimada
from services.telegram_service import telegram_service
from utils.date_utils import get_peru_now, to_peru_time

logger = logging.getLogger(__name__)

DIAS_RETRASO_MAX = 30
ESTADOS_PEDIDO_ABIERTO = ('programado', 'confirmado')
LIMITE_TELEGRAM = 4096
PREFIJO_CALLBACK = 'contactado:'
# Texto aprobado por Eddy (18/11/2026), con el tuteo unificado.
MENSAJE_WHATSAPP = (
    'Hola {nombre}, ¿vas a necesitar carbón estos días? '
    'Si necesitas, te lo llevamos. Tenemos stock.'
)


@dataclass(frozen=True)
class ClientePorContactar:
    cliente_id: int
    nombre: str
    fecha_objetivo: date
    destino: str | None
    dias_retraso: int
    ciudad: str | None = None
    frecuencia_compra_dias: int | None = None
    saldo_pendiente: Decimal = Decimal('0')


@dataclass
class Seleccion:
    hoy: list = field(default_factory=list)
    manana: list = field(default_factory=list)
    retrasados: list = field(default_factory=list)
    inactivos: int = 0

    @property
    def vacia(self) -> bool:
        return not (self.hoy or self.manana or self.retrasados)

    def por_orden(self) -> list:
        return [*self.hoy, *self.manana, *self.retrasados]


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def _int_env(nombre: str, defecto: int, minimo: int = 0) -> int:
    valor = os.environ.get(nombre)
    if valor is None or not valor.strip():
        return defecto
    try:
        numero = int(valor)
    except ValueError:
        logger.warning("%s no es un entero; se usa %s", nombre, defecto)
        return defecto
    return numero if numero >= minimo else defecto


def dias_antes() -> int:
    return _int_env('RECORDATORIOS_DIAS_ANTES', 1, minimo=0)


def max_clientes() -> int:
    return _int_env('RECORDATORIOS_MAX_CLIENTES', 25, minimo=1)


def activos() -> bool:
    return os.environ.get('RECORDATORIOS_ACTIVOS', '').strip().lower() == 'true'


# ---------------------------------------------------------------------------
# Teléfono y enlace
# ---------------------------------------------------------------------------

def normalizar_telefono_pe(texto) -> str | None:
    """Celular peruano en formato ``51`` + 9 dígitos que empiezan por 9; si no, ``None``."""
    if not texto or not isinstance(texto, str):
        return None
    digitos = re.sub(r'[^0-9]', '', texto)  # solo ASCII: \D aceptaría dígitos Unicode
    if len(digitos) == 11 and digitos.startswith('51'):
        digitos = digitos[2:]
    if len(digitos) == 9 and digitos.startswith('9'):
        return f'51{digitos}'
    return None


def enmascarar(destino: str | None) -> str:
    if not destino:
        return 'sin teléfono'
    return f'{destino[:5]}***{destino[-3:]}'


def enlace_whatsapp(destino: str, nombre: str) -> str:
    texto = MENSAJE_WHATSAPP.format(nombre=nombre or '')
    return f'https://wa.me/{destino}?text={quote(texto, safe="")}'


# ---------------------------------------------------------------------------
# Selección (sin efectos)
# ---------------------------------------------------------------------------

def _saldos(cliente_ids) -> dict:
    """Saldo pendiente por cliente en dos consultas agregadas (evita N+1 de ``Cliente.saldo_pendiente``)."""
    if not cliente_ids:
        return {}
    ventas = dict(
        db.session.query(Venta.cliente_id, func.coalesce(func.sum(Venta.total), 0))
        .filter(Venta.cliente_id.in_(cliente_ids), Venta.estado_pago != 'pagado')
        .group_by(Venta.cliente_id).all()
    )
    pagos = dict(
        db.session.query(Venta.cliente_id, func.coalesce(func.sum(Pago.monto), 0))
        .join(Pago, Pago.venta_id == Venta.id)
        .filter(Venta.cliente_id.in_(cliente_ids), Venta.estado_pago != 'pagado')
        .group_by(Venta.cliente_id).all()
    )
    return {cid: Decimal(ventas.get(cid, 0)) - Decimal(pagos.get(cid, 0)) for cid in cliente_ids}


def seleccionar(hoy: date, almacen_id=None) -> Seleccion:
    """Clientes por contactar en el día de Lima ``hoy``; con ``almacen_id``, solo los de ese almacén."""
    anticipacion = dias_antes()
    pedidos_abiertos = select(Pedido.cliente_id).where(Pedido.estado.in_(ESTADOS_PEDIDO_ABIERTO))
    query = Cliente.query.filter(
        or_(
            Cliente.proxima_compra_manual.isnot(None),
            and_(
                Cliente.frecuencia_compra_dias > 0,
                Cliente.ultima_fecha_compra.isnot(None),
            ),
        ),
        Cliente.id.notin_(pedidos_abiertos),
    )
    if almacen_id is not None:
        query = query.filter(Cliente.almacen_preferido_id == almacen_id)

    seleccion = Seleccion()
    for cliente in query.order_by(Cliente.id.asc()).all():
        try:
            objetivo = fecha_estimada(cliente)
        except Exception as exc:  # un dato corrupto no debe tumbar la lista del resto
            logger.warning("Cliente %s sin proyección válida: %s", cliente.id, type(exc).__name__)
            continue
        if objetivo is None:
            continue
        retraso = (hoy - objetivo).days
        if retraso < -anticipacion:
            continue  # todavía no le toca
        if cliente.ultimo_contacto is not None:
            dia_contacto = to_peru_time(cliente.ultimo_contacto).date()
            if dia_contacto >= objetivo - timedelta(days=anticipacion):
                continue  # ya contactado en este ciclo
        if retraso > DIAS_RETRASO_MAX:
            seleccion.inactivos += 1
            continue

        elemento = ClientePorContactar(
            cliente_id=cliente.id,
            nombre=cliente.nombre,
            fecha_objetivo=objetivo,
            destino=normalizar_telefono_pe(cliente.telefono),
            dias_retraso=retraso,
            ciudad=cliente.ciudad,
            frecuencia_compra_dias=cliente.frecuencia_compra_dias,
        )
        if retraso > 0:
            seleccion.retrasados.append(elemento)
        elif retraso == 0:
            seleccion.hoy.append(elemento)
        else:
            seleccion.manana.append(elemento)

    saldos = _saldos([e.cliente_id for e in seleccion.por_orden()])
    for bloque in (seleccion.hoy, seleccion.manana, seleccion.retrasados):
        bloque[:] = [replace(e, saldo_pendiente=saldos.get(e.cliente_id, Decimal('0'))) for e in bloque]
    seleccion.manana.sort(key=lambda e: (e.fecha_objetivo, e.cliente_id))
    seleccion.retrasados.sort(key=lambda e: (e.dias_retraso, e.cliente_id))
    return seleccion


# ---------------------------------------------------------------------------
# Mensajes de Telegram
# ---------------------------------------------------------------------------

def _e(texto) -> str:
    return html.escape(str(texto or ''), quote=False)


def _recortar(texto: str) -> str:
    if len(texto) <= LIMITE_TELEGRAM:
        return texto
    return texto[: LIMITE_TELEGRAM - 1] + '…'


def _etiqueta(elemento: ClientePorContactar, hoy: date) -> str:
    if elemento.dias_retraso > 0:
        dias = elemento.dias_retraso
        return f"⏰ Retrasado {dias} día{'s' if dias != 1 else ''}"
    if elemento.dias_retraso == 0:
        return '🔴 Se le acaba hoy'
    if elemento.fecha_objetivo == hoy + timedelta(days=1):
        return '🟡 Se le acaba mañana'
    return f"🟡 Se le acaba el {elemento.fecha_objetivo.strftime('%d/%m')}"


def texto_cabecera(seleccion: Seleccion, hoy: date, fuera: int = 0) -> str:
    lineas = [
        f"📋 <b>Clientes por contactar · {hoy.strftime('%d/%m')}</b>",
        f"Hoy: {len(seleccion.hoy)} · Próximos: {len(seleccion.manana)} · "
        f"Retrasados: {len(seleccion.retrasados)}",
    ]
    if seleccion.inactivos:
        lineas.append(f"💤 {seleccion.inactivos} clientes llevan más de {DIAS_RETRASO_MAX} días sin comprar.")
    if fuera:
        lineas.append(f"➕ {fuera} clientes más no caben aquí: revísalos en el reporte de proyecciones.")
    lineas.append("Toca «Escribir» para abrir WhatsApp con el mensaje listo y «Contactado» cuando le escribas.")
    return _recortar('\n'.join(lineas))


def texto_cliente(elemento: ClientePorContactar, hoy: date) -> str:
    lineas = [_etiqueta(elemento, hoy), f"<b>{_e(elemento.nombre)}</b>"]
    if elemento.ciudad:
        lineas[-1] += f" · {_e(elemento.ciudad)}"
    detalle = f"Estimada: {elemento.fecha_objetivo.strftime('%d/%m')}"
    if elemento.frecuencia_compra_dias:
        detalle = f"Compra cada {elemento.frecuencia_compra_dias} días · {detalle}"
    lineas.append(detalle)
    if elemento.saldo_pendiente and elemento.saldo_pendiente > 0:
        lineas.append(f"Saldo pendiente: S/ {elemento.saldo_pendiente:.2f}")
    if not elemento.destino:
        lineas.append("⚠️ Sin celular válido registrado")
    return _recortar('\n'.join(lineas))


def botones_cliente(cliente_id: int, nombre: str, destino: str | None, contactado: bool = False) -> dict:
    filas = []
    if destino:
        filas.append([{"text": "📲 Escribir por WhatsApp", "url": enlace_whatsapp(destino, nombre)}])
    if not contactado:
        filas.append([{"text": "✅ Contactado", "callback_data": f"{PREFIJO_CALLBACK}{cliente_id}"}])
    return {"inline_keyboard": filas}


def _enviar_a_usuario(usuario, seleccion: Seleccion, hoy: date, resumen: dict) -> None:
    """Manda la cabecera y un mensaje por cliente; suma a ``resumen['mensajes']`` cada uno aceptado."""
    elementos = seleccion.por_orden()
    limite = max_clientes()
    visibles, fuera = elementos[:limite], max(0, len(elementos) - limite)

    if telegram_service.send_message(usuario.telegram_chat_id, texto_cabecera(seleccion, hoy, fuera)) is None:
        raise RuntimeError('Telegram no aceptó la cabecera')
    resumen['mensajes'] += 1
    for elemento in visibles:
        respuesta = telegram_service.send_message(
            usuario.telegram_chat_id,
            texto_cliente(elemento, hoy),
            reply_markup=botones_cliente(elemento.cliente_id, elemento.nombre, elemento.destino),
        )
        if respuesta is None:
            raise RuntimeError('Telegram no aceptó un mensaje de cliente')
        resumen['mensajes'] += 1


def _seleccion_para(usuario, hoy: date, seleccion_admin: Seleccion) -> Seleccion | None:
    if es_admin(usuario):
        return seleccion_admin
    if usuario.almacen_id is None:
        return None
    return seleccionar(hoy, almacen_id=usuario.almacen_id)


def enviar_listas(hoy: date) -> dict:
    """Manda la lista a cada usuario con Telegram. Un fallo con un usuario no detiene a los demás."""
    seleccion_admin = seleccionar(hoy)
    resumen = _conteos(seleccion_admin)
    resumen.update(usuarios=0, mensajes=0, errores=0)

    usuarios = Users.query.filter(Users.telegram_chat_id.isnot(None)).order_by(Users.id.asc()).all()
    usuarios.sort(key=lambda u: not es_admin(u))  # primero los administradores, luego el resto
    for usuario in usuarios:
        try:
            seleccion = _seleccion_para(usuario, hoy, seleccion_admin)
            if seleccion is None or seleccion.vacia:
                continue
            resumen['usuarios'] += 1
            _enviar_a_usuario(usuario, seleccion, hoy, resumen)
        except Exception as exc:
            db.session.rollback()
            resumen['errores'] += 1
            logger.error("Lista de clientes no entregada al usuario %s: %s", usuario.id, type(exc).__name__)
    return resumen


def _conteos(seleccion: Seleccion) -> dict:
    return {
        'manana': len(seleccion.manana),
        'hoy': len(seleccion.hoy),
        'retrasados': len(seleccion.retrasados),
        'inactivos': seleccion.inactivos,
    }


# ---------------------------------------------------------------------------
# Botón «Contactado»
# ---------------------------------------------------------------------------

def marcar_contactado(usuario, data: str, ahora: datetime | None = None):
    """Procesa ``contactado:<id>``. Devuelve ``(ok, texto_html, reply_markup)``."""
    valor = data[len(PREFIJO_CALLBACK):] if data.startswith(PREFIJO_CALLBACK) else ''
    if not (valor.isascii() and valor.isdigit()):
        return False, "⚠️ No reconozco ese cliente.", None
    cliente = db.session.get(Cliente, int(valor))
    if cliente is None:
        return False, "⚠️ Ese cliente ya no existe.", None

    if not es_admin(usuario):
        try:
            autorizar_almacen(usuario.id, cliente.almacen_preferido_id)
        except HTTPException:
            return False, "⛔ Ese cliente no es de tu almacén.", None

    momento = ahora or datetime.now(UTC)
    cliente.ultimo_contacto = momento
    db.session.commit()
    logger.info("Cliente %s marcado como contactado por el usuario %s", cliente.id, usuario.id)

    hora = to_peru_time(momento).strftime('%d/%m %H:%M')
    texto = (
        f"✅ <b>{_e(cliente.nombre)}</b>\n"
        f"Contactado por {_e(usuario.username)} el {hora}. Vuelve a la lista en su próximo ciclo."
    )
    destino = normalizar_telefono_pe(cliente.telefono)
    markup = botones_cliente(cliente.id, cliente.nombre, destino, contactado=True)
    return True, texto, (markup if markup['inline_keyboard'] else None)


# ---------------------------------------------------------------------------
# Comando para cron
# ---------------------------------------------------------------------------

def _linea(resumen: dict, estado: str) -> str:
    claves = ('manana', 'hoy', 'retrasados', 'inactivos', 'usuarios', 'mensajes', 'errores')
    return "recordatorios: " + " ".join(f"{c}={resumen.get(c, 0)}" for c in claves) + f" estado={estado}"


def _imprimir_seleccion(seleccion: Seleccion, hoy: date) -> None:
    click.echo(f"Clientes por contactar el {hoy.isoformat()} (vista de admin):")
    for elemento in seleccion.por_orden():
        click.echo(
            f"  - [{_etiqueta(elemento, hoy)}] #{elemento.cliente_id} {elemento.nombre} "
            f"({enmascarar(elemento.destino)}), estimada {elemento.fecha_objetivo.isoformat()}"
        )
    if seleccion.vacia:
        click.echo("  (nadie)")
    if seleccion.inactivos:
        click.echo(f"  + {seleccion.inactivos} inactivos (más de {DIAS_RETRASO_MAX} días de retraso)")


@click.command('recordatorios')
@click.option('--fecha', default=None, help='Día de Lima a calcular (AAAA-MM-DD). Por defecto, hoy.')
@click.option('--dry-run', is_flag=True, help='Imprime la lista sin enviar nada ni escribir en la BD.')
@with_appcontext
def recordatorios_command(fecha, dry_run):
    """Manda por Telegram la lista diaria de clientes por contactar (para cron)."""
    if fecha:
        try:
            hoy = date.fromisoformat(fecha)
        except ValueError:
            raise click.BadParameter('usa el formato AAAA-MM-DD', param_hint='--fecha') from None
    else:
        hoy = get_peru_now().date()

    if dry_run:
        seleccion = seleccionar(hoy)
        _imprimir_seleccion(seleccion, hoy)
        resumen = _conteos(seleccion)
        click.echo(_linea(resumen, 'dry-run'))
        return

    if not activos():
        resumen = _conteos(seleccionar(hoy))
        click.echo(_linea(resumen, 'desactivado'))
        click.echo("recordatorios desactivado: define RECORDATORIOS_ACTIVOS=true para enviar.")
        return

    click.echo(_linea(enviar_listas(hoy), 'enviado'))
