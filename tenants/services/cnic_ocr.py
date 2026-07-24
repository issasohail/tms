import logging
import mimetypes
import re
from datetime import date

from django.conf import settings

from whatsapp.services.openai_ocr import (
    _date_or_none,
    _normalized_image_data,
    _openai_client,
    _openai_error_kind,
    _parse_json,
    _usage,
)


logger = logging.getLogger(__name__)

CNIC_OCR_PROMPT = """
The first image is intended to be the FRONT and the second image the BACK of the
same Pakistani CNIC. Read only clearly printed information. Pakistani CNIC dates
are normally DD.MM.YYYY; return every date as YYYY-MM-DD.

Extract name, father/husband name, gender, country of stay, identity number,
date of birth, date of issue, and date of expiry from the front.

On the back, extract temporary and permanent residential addresses only when
the card actually labels or prints personal address text. Translate genuine Urdu
addresses into clear English, transliterating names and localities. Do not treat
generic instructions, government text, signatures, lost-card return instructions,
QR content, or issuing-authority text as an address. Never invent or infer missing
text. Use null for absent or uncertain values. Warn if the two sides appear to
belong to different cards or are reversed.
"""

_NULLABLE_TEXT = {"type": ["string", "null"]}
CNIC_OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["pakistani_cnic", "other", "unknown"],
        },
        "name": _NULLABLE_TEXT,
        "father_name": _NULLABLE_TEXT,
        "gender": {"type": ["string", "null"], "enum": ["M", "F", "O", None]},
        "country_of_stay": _NULLABLE_TEXT,
        "identity_number": _NULLABLE_TEXT,
        "date_of_birth": _NULLABLE_TEXT,
        "date_of_issue": _NULLABLE_TEXT,
        "date_of_expiry": _NULLABLE_TEXT,
        "temporary_address_urdu": _NULLABLE_TEXT,
        "permanent_address_urdu": _NULLABLE_TEXT,
        "temporary_address_english": _NULLABLE_TEXT,
        "permanent_address_english": _NULLABLE_TEXT,
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "document_type", "name", "father_name", "gender", "country_of_stay",
        "identity_number", "date_of_birth", "date_of_issue", "date_of_expiry",
        "temporary_address_urdu", "permanent_address_urdu",
        "temporary_address_english", "permanent_address_english", "confidence",
        "warnings",
    ],
    "additionalProperties": False,
}


def extract_cnic_identity(front_file, back_file, model):
    """Return review-only identity suggestions from both sides of a CNIC."""
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return _unavailable("CNIC OCR is unavailable because OPENAI_API_KEY is not configured.")
    if not front_file or not back_file:
        return _unavailable("Choose both CNIC Front and CNIC Back images first.")

    images = []
    for label, file_field in (("front", front_file), ("back", back_file)):
        mime_type = (
            getattr(file_field, "content_type", "")
            or mimetypes.guess_type(getattr(file_field, "name", "") or "")[0]
            or ""
        )
        if not mime_type.startswith("image/"):
            return _unavailable(f"CNIC {label} must be an image file.")
        try:
            encoded, normalized_mime = _normalized_image_data(file_field)
        except Exception:
            logger.warning("CNIC OCR image normalization failed side=%s", label)
            return _unavailable(f"CNIC {label} image could not be prepared for OCR.")
        images.append(
            {
                "type": "input_image",
                "image_url": f"data:{normalized_mime};base64,{encoded}",
                "detail": "high",
            }
        )

    try:
        response = _openai_client().responses.create(
            model=model,
            store=False,
            max_output_tokens=1200,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "pakistani_cnic_identity",
                    "strict": True,
                    "schema": CNIC_OCR_SCHEMA,
                }
            },
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": CNIC_OCR_PROMPT.strip()},
                        *images,
                    ],
                }
            ],
        )
    except ImportError:
        return _unavailable("The openai Python package is not installed.")
    except Exception as exc:
        logger.warning("CNIC OCR failed error=%s", _openai_error_kind(exc))
        return _unavailable("CNIC OCR could not read these images. Enter the details manually.")

    result = _parse_json(getattr(response, "output_text", "") or "")
    result["engine"] = "openai"
    result["model"] = model
    result["usage"] = _usage(response)
    try:
        result["confidence"] = max(
            0, min(100, round(float(result.get("confidence") or 0) * 100))
        )
    except (TypeError, ValueError):
        result["confidence"] = 0

    if result.get("document_type") != "pakistani_cnic":
        return {
            **result,
            "fields": {},
            "message": "Both selected images were not recognized as one Pakistani CNIC.",
        }

    warnings = [str(item).strip() for item in result.get("warnings", []) if str(item).strip()]
    cnic_digits = re.sub(r"\D", "", result.get("identity_number") or "")
    cnic = (
        f"{cnic_digits[:5]}-{cnic_digits[5:12]}-{cnic_digits[12]}"
        if len(cnic_digits) == 13
        else None
    )
    if result.get("identity_number") and not cnic:
        warnings.append("The identity number was unclear or did not contain 13 digits.")

    dob = _valid_dob(result.get("date_of_birth"))
    issue = _date_or_none(result.get("date_of_issue"))
    expiry = _date_or_none(result.get("date_of_expiry"))
    if issue and expiry and expiry < issue:
        warnings.append("Expiry date is earlier than issue date; both dates were withheld.")
        issue = expiry = None

    fields = {
        "full_name": _text(result.get("name")),
        "first_name": _text(result.get("name")),
        "last_name": _text(result.get("father_name")),
        "gender": result.get("gender") if result.get("gender") in {"M", "F", "O"} else None,
        "country": _text(result.get("country_of_stay")),
        "cnic": cnic,
        "date_of_birth": dob.isoformat() if dob else None,
        "cnic_issue_date": issue.isoformat() if issue else None,
        "cnic_expiry_date": expiry.isoformat() if expiry else None,
        "temporary_address": _text(result.get("temporary_address_english")),
        "permanent_address": _text(result.get("permanent_address_english")),
    }
    if not fields["temporary_address"] and not fields["permanent_address"]:
        warnings.append("No personal temporary or permanent address was found on the back.")
    result["fields"] = {key: value for key, value in fields.items() if value}
    result["warnings"] = list(dict.fromkeys(warnings))
    return result


def _valid_dob(value):
    dob = _date_or_none(value)
    if dob and dob <= date.today() and _age_on(dob, date.today()) <= 120:
        return dob
    return None


def _text(value):
    value = " ".join(str(value or "").split())
    return value or None


def _age_on(dob, today):
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _unavailable(message):
    return {
        "engine": "unavailable",
        "fields": {},
        "confidence": 0,
        "warnings": [],
        "message": message,
    }
