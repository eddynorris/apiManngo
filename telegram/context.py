import logging
from extensions import db

logger = logging.getLogger(__name__)

def set_user_context(user, context_data):
    """
    Sets the active operation context for a given Telegram user.
    """
    user.telegram_context = context_data
    db.session.commit()

def clear_user_context(user):
    """
    Clears the active operation context for a given Telegram user.
    """
    user.telegram_context = None
    db.session.commit()

def update_user_history(user, user_prompt, model_response, max_history=10):
    """
    Appends a new turn to user.telegram_history and keeps the last max_history entries.
    """
    if not user.telegram_history:
        user.telegram_history = []
    new_history = list(user.telegram_history)
    new_history.append({"role": "user", "parts": [user_prompt]})
    new_history.append({"role": "model", "parts": [model_response]})
    user.telegram_history = new_history[-max_history:]
    db.session.commit()
