import os
import logging
import uuid
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from flask_restful import Resource
from flask_jwt_extended import jwt_required

from extensions import db
from models import Users
from common import handle_db_errors, obtener_usuario_actual
from telegram.router import TelegramRouter

logger = logging.getLogger(__name__)

# LEGADO INACTIVO: el executor y `_process_update_async` no se usan desde
# `post` en esta versión. Se conservan solo como referencia histórica y no
# implican entrega operativa. No reactivar sin una cola durable, idempotencia
# de efectos de negocio y workers.
executor = ThreadPoolExecutor(max_workers=5)


def _process_update_async(app, update):
    """LEGADO NO EJECUTADO: no se invoca desde `TelegramWebhookResource.post`."""
    with app.app_context():
        try:
            if "callback_query" in update:
                TelegramRouter.handle_callback_query(update["callback_query"])
            elif "message" in update:
                TelegramRouter.handle_message(update["message"])
        except Exception as e:
            error_id = uuid.uuid4().hex[:8]
            logger.error(f"Error asíncrono procesando update de Telegram [{error_id}]: {e}", exc_info=True)

class TelegramWebhookResource(Resource):
    def _marcar_update_procesado(self, update_id: int) -> bool:
        """LEGADO NO EJECUTADO: deduplicación histórica, no se llama desde `post`."""
        try:
            from sqlalchemy import text
            stmt = text("INSERT INTO telegram_updates (update_id) VALUES (:u) ON CONFLICT (update_id) DO NOTHING RETURNING update_id")
            res = db.session.execute(stmt, {'u': update_id})
            db.session.commit()
            return res.first() is not None
        except Exception as e:
            logger.error(f"Error al deduplicar update_id {update_id}: {e}")
            db.session.rollback()
            return False

    def post(self, webhook_token):
        """Contención de release: la entrega de Telegram no está implementada.

        Devuelve 503 antes de validar secretos, deduplicar, escribir en BD,
        encolar o invocar el router. No se habilita con flags ni se responde
        200 ficticio: sin cola durable, idempotencia y workers no hay entrega
        operativa que confirmar a Telegram.
        """
        logger.warning(
            "Webhook de Telegram rechazado: entrega no disponible en esta versión"
        )
        return {
            "error": "IntegrationUnavailable",
            "message": "Telegram delivery is disabled in this release.",
        }, 503


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
        user.telegram_linking_expires = datetime.now(timezone.utc) + timedelta(minutes=10)
        db.session.commit()
        
        bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "ManngoBot")
        link = f"https://t.me/{bot_username}?start={code}"
        
        return {
            "codigo": code,
            "enlace": link,
            "expira_en_segundos": 600
        }, 200
