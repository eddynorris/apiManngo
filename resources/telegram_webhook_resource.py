import hmac
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from flask import current_app, request
from flask_jwt_extended import jwt_required
from flask_restful import Resource

from common import handle_db_errors, obtener_usuario_actual
from extensions import db
from models import Users
from services import telegram_inbox

logger = logging.getLogger(__name__)

# Procesa en segundo plano los updates ya guardados. Si el proceso muere, el
# update queda en la BD y lo recupera `flask telegram-pendientes` (cron).
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='telegram-inbox')


def _procesar_en_segundo_plano(app, update_id):
    with app.app_context():
        try:
            telegram_inbox.procesar(update_id)
        except Exception as e:
            # Sin traza ni mensaje: podrían contener el texto del usuario
            logger.error("Fallo inesperado procesando el update de Telegram %s: %s", update_id, type(e).__name__)
        finally:
            db.session.remove()


def programar_procesamiento(app, update_id):
    """Punto único de despacho (los tests lo sustituyen por una llamada síncrona)."""
    _executor.submit(_procesar_en_segundo_plano, app, update_id)


def _iguales(recibido, esperado):
    return hmac.compare_digest(str(recibido).encode('utf-8'), esperado.encode('utf-8'))


class TelegramWebhookResource(Resource):
    def post(self, webhook_token):
        """Recibe un update, lo guarda de forma duradera y responde 200 (A08).

        - El token de la ruta debe coincidir con TELEGRAM_WEBHOOK_SECRET (si falta, se rechaza).
        - Si TELEGRAM_BOT_SECRET_TOKEN está definido, la cabecera
          X-Telegram-Bot-Api-Secret-Token debe coincidir.
        - Un update_id repetido responde 200 sin reprocesar.
        - Si la BD falla al guardar, se responde 500 y Telegram reintenta.
        """
        esperado = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
        if not esperado or not _iguales(webhook_token, esperado):
            logger.warning("Webhook de Telegram rechazado: token de ruta inválido")
            return {"error": "Unauthorized webhook token"}, 403

        secreto = os.environ.get("TELEGRAM_BOT_SECRET_TOKEN")
        if secreto:
            cabecera = request.headers.get('X-Telegram-Bot-Api-Secret-Token') or ''
            if not _iguales(cabecera, secreto):
                logger.warning("Webhook de Telegram rechazado: cabecera secreta inválida")
                return {"error": "Unauthorized secret token"}, 403

        try:
            update_id = telegram_inbox.registrar_update(request.get_json(silent=True))
        except telegram_inbox.UpdateInvalido:
            # 200 para que Telegram no reintente indefinidamente un cuerpo inutilizable
            logger.warning("Update de Telegram ignorado: formato inválido")
            return {"status": "ignored"}, 200

        if update_id is None:
            return {"status": "ok", "message": "Duplicate update ignored"}, 200

        programar_procesamiento(current_app._get_current_object(), update_id)
        return {"status": "ok"}, 200


class TelegramLinkResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        """
        Genera un código temporal de vinculación para el usuario autenticado.
        """
        user = obtener_usuario_actual()

        for _ in range(20):
            code = f"{random.randint(100000, 999999)}"
            existente = Users.query.filter(
                Users.telegram_linking_code == code,
                Users.id != user.id
            ).first()
            if existente is None:
                break
        else:
            return {"error": "No se pudo generar un código de vinculación único"}, 503

        user.telegram_linking_code = code
        user.telegram_linking_expires = datetime.now(UTC) + timedelta(minutes=10)
        db.session.commit()

        bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "ManngoBot")
        link = f"https://t.me/{bot_username}?start={code}"

        return {
            "codigo": code,
            "enlace": link,
            "expira_en_segundos": 600
        }, 200
