import re
from datetime import date, datetime

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import OperationalError, ProgrammingError


NON_DIGITS_RE = re.compile(r"\D+")


def normalize_cnic(value):
    """Return a machine-readable CNIC containing digits only."""
    if value is None:
        return ""
    return NON_DIGITS_RE.sub("", str(value).strip())


def validate_cnic(value):
    """Allow blank values, otherwise require exactly thirteen normalized digits."""
    digits = normalize_cnic(value)
    if digits and len(digits) != 13:
        raise ValidationError(
            "CNIC must contain exactly 13 digits (for example 71504-1234567-1).",
            code="invalid_cnic_length",
        )


def validate_date_of_birth(value):
    """Allow blank DOB values and reject impossible or likely mistyped dates."""
    if value in (None, ""):
        return
    dob = value.date() if isinstance(value, datetime) else value
    if not isinstance(dob, date):
        raise ValidationError(
            "Enter date of birth in MM/DD/YYYY format.",
            code="invalid_date_of_birth",
        )

    today = date.today()
    if dob > today:
        raise ValidationError(
            "Date of birth cannot be in the future.",
            code="future_date_of_birth",
        )
    age = today.year - dob.year - (
        (today.month, today.day) < (dob.month, dob.day)
    )
    if age > 120:
        raise ValidationError(
            "Date of birth cannot be more than 120 years ago.",
            code="implausible_date_of_birth",
        )


def format_cnic(value):
    if value in (None, ""):
        return ""
    original = str(value).strip()
    digits = normalize_cnic(original)
    if len(digits) == 13:
        return f"{digits[:5]}-{digits[5:12]}-{digits[12]}"
    # Malformed legacy values remain recognizable and are never presented as valid.
    return digits or original


def default_country_code():
    """Return the configured country code, with a safe startup fallback."""
    fallback = getattr(settings, "WHATSAPP_DEFAULT_COUNTRY_CODE", "+92")
    if not apps.ready:
        return fallback
    try:
        from core.models import GlobalSettings

        return GlobalSettings.get_solo().country_code or fallback
    except (LookupError, OperationalError, ProgrammingError):
        # The settings table may not exist yet while migrations are running.
        return fallback


def normalize_phone(value, country_code=None):
    """Normalize separators and replace one leading local zero with the country code."""
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    leading_plus = raw.startswith("+")
    digits = NON_DIGITS_RE.sub("", raw)
    if not digits:
        return ""
    if leading_plus:
        return "+" + digits

    country_digits = NON_DIGITS_RE.sub(
        "", str(default_country_code() if country_code is None else country_code)
    )
    if country_digits and digits.startswith("0") and not digits.startswith("00"):
        return "+" + country_digits + digits[1:]
    return digits


def format_phone(value, country_code=""):
    normalized = normalize_phone(value, country_code=country_code or None)
    if not normalized:
        return ""
    has_plus = normalized.startswith("+")
    digits = normalized[1:] if has_plus else normalized

    # Convert a local phone such as 03325126929 to the configured international
    # prefix. Already-international values and short/legacy identifiers remain
    # unchanged so this formatter never guesses their meaning.
    country_digits = NON_DIGITS_RE.sub("", str(country_code or ""))
    if (
        not has_plus
        and country_digits
        and len(digits) >= 10
        and digits.startswith("0")
        and not digits.startswith("00")
    ):
        digits = country_digits + digits[1:]
        has_plus = True
    elif (
        not has_plus
        and country_digits
        and digits.startswith(country_digits)
        and len(digits) >= len(country_digits) + 7
    ):
        has_plus = True

    if len(digits) > 10:
        prefix, final_ten = digits[:-10], digits[-10:]
        groups = [prefix, final_ten[:3], final_ten[3:6], final_ten[6:]]
    elif len(digits) == 10:
        groups = [digits[:3], digits[3:6], digits[6:]]
    elif len(digits) == 7:
        groups = [digits[:3], digits[3:]]
    else:
        groups = _conservative_right_groups(digits)

    formatted = "-".join(group for group in groups if group)
    return ("+" if has_plus else "") + formatted


def whatsapp_phone_digits(value, country_code=""):
    """Return an international digits-only number suitable for a wa.me link."""
    normalized = normalize_phone(value, country_code=country_code or None)
    if not normalized:
        return ""
    digits = NON_DIGITS_RE.sub("", normalized)
    if digits.startswith("00"):
        digits = digits[2:]
    country_digits = NON_DIGITS_RE.sub("", str(country_code or ""))
    if country_digits and digits.startswith("0") and not digits.startswith("00"):
        digits = country_digits + digits[1:]
    return digits


def _conservative_right_groups(digits):
    if len(digits) <= 4:
        return [digits]
    groups = [digits[-4:]]
    remaining = digits[:-4]
    while remaining:
        groups.insert(0, remaining[-3:])
        remaining = remaining[:-3]
    return groups


def normalized_identity_search_terms(value):
    """Return safe alternatives for exact/contains identity searches."""
    raw = str(value or "").strip()
    return tuple(dict.fromkeys(item for item in (raw, normalize_cnic(raw), normalize_phone(raw)) if item))
