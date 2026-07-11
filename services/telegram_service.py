import os
import requests
import logging

logger = logging.getLogger(__name__)

class TelegramService:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            logger.warning("TELEGRAM_BOT_TOKEN no configurada en las variables de entorno.")
        self.api_url = f"https://api.telegram.org/bot{self.token}"

    def send_message(self, chat_id, text, reply_markup=None):
        """
        Envía un mensaje de texto a un chat de Telegram.
        """
        if not self.token:
            logger.error("No se puede enviar mensaje: TELEGRAM_BOT_TOKEN no configurada.")
            return None

        url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error al enviar mensaje a Telegram: {e}")
            return None

    def edit_message(self, chat_id, message_id, text, reply_markup=None):
        """
        Edita el texto de un mensaje existente.
        """
        if not self.token:
            logger.error("No se puede editar mensaje: TELEGRAM_BOT_TOKEN no configurada.")
            return None

        url = f"{self.api_url}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error al editar mensaje en Telegram: {e}")
            return None

    def answer_callback_query(self, callback_query_id, text=None):
        """
        Responde a una consulta de devolución de llamada (callback_query) de un botón.
        """
        if not self.token:
            return None

        url = f"{self.api_url}/answerCallbackQuery"
        payload = {
            "callback_query_id": callback_query_id
        }
        if text:
            payload["text"] = text

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error al responder callback query: {e}")
            return None

telegram_service = TelegramService()
