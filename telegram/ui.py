import html

def escape_html(text):
    """
    Escapes special HTML characters (&, <, >) to prevent HTML injection errors in Telegram formatting (SEG-09).
    """
    if not text:
        return ""
    return html.escape(str(text))

def build_inline_keyboard(buttons_matrix):
    """
    Constructs a dictionary representation of an inline keyboard markup for Telegram API.
    :param buttons_matrix: List of rows, where each row is a list of dicts with 'text' and 'callback_data'.
    """
    return {
        "inline_keyboard": buttons_matrix
    }
