"""Bandeja de entrada duradera del webhook de Telegram (A08).

Flujo:
1. El webhook valida secretos, guarda el update (``recibido``) y responde 200.
   Un ``update_id`` repetido no se vuelve a procesar.
2. ``procesar`` reclama el update de forma atómica (``procesando``), ejecuta el
   router y lo marca ``procesado`` o ``fallido``. Respeta el orden por chat: no
   reclama si el mismo chat tiene otro update ``procesando`` o uno ``recibido``
   más antiguo, y al terminar sigue con el siguiente del mismo chat.
3. ``procesar_pendientes`` (comando ``flask telegram-pendientes``, por cron):
   procesa los ``recibido`` que nunca empezaron y marca ``abandonado`` los
   ``procesando`` colgados, avisando al usuario.

Semántica: **como máximo una vez**. Un update que pudo haber creado una venta o
un pago nunca se reintenta automáticamente; el usuario recibe un aviso para
revisar antes de repetir. Límite conocido: dos updates distintos del mismo chat
reclamados en el mismo instante por procesos diferentes pueden solaparse.
"""
import logging
import uuid
from datetime import UTC, datetime, timedelta

import click
from flask.cli import with_appcontext
from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import TelegramUpdate

logger = logging.getLogger(__name__)

RECIBIDO = 'recibido'
PROCESANDO = 'procesando'
PROCESADO = 'procesado'
FALLIDO = 'fallido'
ABANDONADO = 'abandonado'

ESPERA_RECIBIDO = timedelta(minutes=1)
ESPERA_PROCESANDO = timedelta(minutes=10)

AVISO_ABANDONADO = (
    "⚠️ No pude confirmar si se completó tu último mensaje. "
    "Revisa en el sistema si la operación quedó registrada antes de repetirla."
)


class UpdateInvalido(ValueError):
    """El cuerpo recibido no es un update de Telegram utilizable."""


def _ahora():
    return datetime.now(UTC)


def _entero(valor):
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


def extraer_chat_id(update):
    """Chat del update (mensaje o callback). El router decide si es aceptable."""
    if isinstance(update.get('message'), dict):
        chat = update['message'].get('chat')
    elif isinstance(update.get('callback_query'), dict):
        chat = (update['callback_query'].get('message') or {}).get('chat')
    else:
        return None
    return _entero(chat.get('id')) if isinstance(chat, dict) else None


def registrar_update(update):
    """Guarda el update. Devuelve su ``update_id`` si es nuevo o ``None`` si es duplicado."""
    if not isinstance(update, dict):
        raise UpdateInvalido('el update no es un objeto JSON')
    update_id = _entero(update.get('update_id'))
    if update_id is None or update_id <= 0:
        raise UpdateInvalido('update_id ausente o inválido')

    ahora = _ahora()
    db.session.add(TelegramUpdate(
        update_id=update_id,
        estado=RECIBIDO,
        chat_id=extraer_chat_id(update),
        payload=update,
        intentos=0,
        recibido_en=ahora,
        actualizado_en=ahora,
    ))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None
    return update_id


def reclamar(update_id):
    """Pasa el update a ``procesando`` si sigue ``recibido`` y es el turno de su chat."""
    t = TelegramUpdate.__table__
    otro = t.alias('otro')
    fila = db.session.execute(select(t.c.chat_id).where(t.c.update_id == update_id)).first()
    if fila is None:
        return False

    condiciones = [t.c.update_id == update_id, t.c.estado == RECIBIDO]
    if fila.chat_id is not None:
        condiciones.append(~exists().where(and_(
            otro.c.chat_id == fila.chat_id, otro.c.estado == PROCESANDO,
        )))
        condiciones.append(~exists().where(and_(
            otro.c.chat_id == fila.chat_id, otro.c.estado == RECIBIDO, otro.c.update_id < update_id,
        )))
    resultado = db.session.execute(
        update(t).where(*condiciones).values(
            estado=PROCESANDO, intentos=t.c.intentos + 1, actualizado_en=_ahora(),
        )
    )
    db.session.commit()
    return resultado.rowcount == 1


def _marcar(update_id, estado, error=None):
    t = TelegramUpdate.__table__
    db.session.execute(
        update(t).where(t.c.update_id == update_id).values(
            estado=estado, ultimo_error=error, actualizado_en=_ahora(),
        )
    )
    db.session.commit()


def _despachar(payload):
    from telegram.router import TelegramRouter

    if isinstance(payload.get('callback_query'), dict):
        TelegramRouter.handle_callback_query(payload['callback_query'])
    elif isinstance(payload.get('message'), dict):
        TelegramRouter.handle_message(payload['message'])
    # Otros tipos (edited_message, channel_post, ...) se registran y se ignoran.


def _ejecutar_reclamado(update_id):
    payload = db.session.execute(
        select(TelegramUpdate.payload).where(TelegramUpdate.update_id == update_id)
    ).scalar_one_or_none() or {}
    try:
        _despachar(payload)
    except Exception as e:
        db.session.rollback()
        error_id = uuid.uuid4().hex[:8]
        logger.error("Error procesando update de Telegram %s [%s]", update_id, error_id, exc_info=True)
        _marcar(update_id, FALLIDO, f"{type(e).__name__} [{error_id}]")
        return
    _marcar(update_id, PROCESADO)


def _siguiente_del_chat(update_id):
    t = TelegramUpdate.__table__
    chat_id = db.session.execute(select(t.c.chat_id).where(t.c.update_id == update_id)).scalar()
    if chat_id is None:
        return None
    return db.session.execute(
        select(func.min(t.c.update_id)).where(t.c.chat_id == chat_id, t.c.estado == RECIBIDO)
    ).scalar()


def procesar(update_id):
    """Procesa el update y, a continuación, los siguientes de su chat. Devuelve cuántos procesó."""
    procesados = 0
    siguiente = update_id
    while siguiente is not None and reclamar(siguiente):
        _ejecutar_reclamado(siguiente)
        procesados += 1
        siguiente = _siguiente_del_chat(siguiente)
    return procesados


def _avisar(chat_id):
    from services.telegram_service import telegram_service

    try:
        telegram_service.send_message(chat_id, AVISO_ABANDONADO)
    except Exception:
        logger.error("No se pudo avisar de un update abandonado", exc_info=True)


def procesar_pendientes(ahora=None):
    """Recupera lo que quedó sin terminar. Devuelve un resumen de lo hecho."""
    ahora = ahora or _ahora()
    t = TelegramUpdate.__table__

    abandonados = 0
    colgados = db.session.execute(
        select(t.c.update_id, t.c.chat_id).where(
            t.c.estado == PROCESANDO, t.c.actualizado_en < ahora - ESPERA_PROCESANDO,
        )
    ).all()
    for fila in colgados:
        resultado = db.session.execute(
            update(t).where(t.c.update_id == fila.update_id, t.c.estado == PROCESANDO).values(
                estado=ABANDONADO,
                ultimo_error='Sin confirmación: el procesamiento se interrumpió',
                actualizado_en=ahora,
            )
        )
        db.session.commit()
        if resultado.rowcount == 1:
            abandonados += 1
            if fila.chat_id is not None and fila.chat_id > 0:
                _avisar(fila.chat_id)

    pendientes = db.session.execute(
        select(t.c.update_id).where(
            t.c.estado == RECIBIDO, t.c.recibido_en < ahora - ESPERA_RECIBIDO,
        ).order_by(t.c.update_id)
    ).scalars().all()
    procesados = sum(procesar(update_id) for update_id in pendientes)

    return {'abandonados': abandonados, 'procesados': procesados}


@click.command('telegram-pendientes')
@with_appcontext
def telegram_pendientes_command():
    """Procesa updates de Telegram pendientes y marca los colgados (para cron)."""
    resumen = procesar_pendientes()
    click.echo(
        f"telegram-pendientes: abandonados={resumen['abandonados']} procesados={resumen['procesados']}"
    )
