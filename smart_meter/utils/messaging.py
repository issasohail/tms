# smart_meter/utils/messaging.py
import re
from urllib.parse import quote_plus


def build_whatsapp_url(number: str, message: str = "") -> str:
    """
    Build a WhatsApp deep link like:
      https://wa.me/923001234567?text=Hello%20world
    - Strips non-digits from the phone number.
    - URL-encodes the message.

    If you store numbers with a leading +, it's fine; we remove non-digits anyway.
    """
    digits = re.sub(r"\D+", "", number or "")
    return f"https://wa.me/{digits}?text={quote_plus(message or '')}"
