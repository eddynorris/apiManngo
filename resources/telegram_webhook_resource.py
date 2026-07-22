import os
import logging
import hmac
import uuid
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from flask import request, current_app
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions import db
from models import Users
from common import handle_db_errors
from telegram.router import TelegramRouter

logger = logging.getLogger(__name__)

# Executor de hilos para procesamiento asíncrono del webhook en segundo plano (TG-01)
executor = ThreadPoolExecutor(max_workers=5)

def _process_update_async(app, update):
    """Ejecuta el procesamiento de Telegram en un hilo de fondo dentro del contexto Flask."""
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
        """
        Registra el update_id en la base de datos para evitar procesarlo múltiples veces.
        Retorna True si es nuevo, False si ya existe (duplicado).
        """
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
        # 1. Validar token de seguridad en la URL (SEG-04)
        expected_token = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
        if not expected_token or not hmac.compare_digest(webhook_token, expected_token):
            logger.warning(f"Acceso no autorizado al webhook con token: {webhook_token}")
            return {"error": "Unauthorized webhook token"}, 403

        # 2. Validar cabecera secreta (SEG-04)
        expected_secret = os.environ.get("TELEGRAM_BOT_SECRET_TOKEN")
        if expected_secret:
            header_secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
            if not header_secret or not hmac.compare_digest(header_secret, expected_secret):
                logger.warning("Acceso no autorizado al webhook: cabecera secreta inválida.")
                return {"error": "Unauthorized secret token"}, 403

        update = request.get_json(silent=True) or {}
        if not update:
            return {"error": "No data received"}, 400

        # 3. Deduplicación de update_id (TG-01)
        update_id = update.get('update_id')
        if update_id is not None:
            if not self._marcar_update_procesado(update_id):
                logger.info(f"Update {update_id} ya procesado, omitiendo duplicado.")
                return {"status": "ok", "message": "Duplicate update ignored"}, 200

        # 4. Procesamiento asíncrono en hilo de fondo para responder 200 inmediatamente
        app = current_app._get_current_object()
        if app.testing:
            _process_update_async(app, update)
        else:
            executor.submit(_process_update_async, app, update)

        return {"status": "ok"}, 200


class TelegramLinkResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        """
        Genera un código temporal de vinculación para el usuario autenticado.
        """
        username = get_jwt_identity()
        user = Users.query.filter_by(username=username).first()
        if not user:
            return {"error": "Usuario no encontrado"}, 404
            
        code = f"{random.randint(100000, 999999)}"
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
