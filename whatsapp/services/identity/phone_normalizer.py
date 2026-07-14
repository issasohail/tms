from django.conf import settings

from leases.whatsapp import normalize_whatsapp_phone


def normalize_phone_number(value, country_code=None):
    """Return one canonical WhatsApp phone form for local and international numbers."""
    return normalize_whatsapp_phone(
        value or "",
        country_code=country_code
        or getattr(settings, "WHATSAPP_DEFAULT_COUNTRY_CODE", "+92"),
    )


def phone_digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def phone_matches(left, right):
    left_digits = phone_digits(normalize_phone_number(left))
    right_digits = phone_digits(normalize_phone_number(right))
    if not left_digits or not right_digits:
        return False
    return left_digits == right_digits


def searchable_suffix(value):
    digits = phone_digits(normalize_phone_number(value))
    return digits[-10:] if len(digits) >= 10 else digits
