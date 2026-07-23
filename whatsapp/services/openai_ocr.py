import base64
import json
import logging
import mimetypes
import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.conf import settings
from django.utils.dateparse import parse_date
from PIL import Image, ImageOps, UnidentifiedImageError


logger = logging.getLogger(__name__)

OCR_PROMPT = """
Extract only payment-receipt fields from this image. Never infer unreadable data.
Use null for unavailable fields, YYYY-MM-DD dates, a decimal amount string without
currency or commas, and a string reference_id preserving leading zeroes. Do not
return explanations, Markdown, or full receipt text.
"""

OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["payment_receipt", "non_payment", "unknown"],
        },
        "amount": {"type": ["string", "null"]},
        "transaction_date": {"type": ["string", "null"]},
        "reference_id": {"type": ["string", "null"]},
        "recipient_name": {"type": ["string", "null"]},
        "sender_name": {"type": ["string", "null"]},
        "bank_name": {"type": ["string", "null"]},
        "confidence": {
            "type": "object",
            "properties": {
                "document_type": {"type": "number", "minimum": 0, "maximum": 1},
                "amount": {"type": "number", "minimum": 0, "maximum": 1},
                "transaction_date": {"type": "number", "minimum": 0, "maximum": 1},
                "reference_id": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["document_type", "amount", "transaction_date", "reference_id"],
            "additionalProperties": False,
        },
    },
    "required": [
        "document_type", "amount", "transaction_date", "reference_id",
        "recipient_name", "sender_name", "bank_name", "confidence",
    ],
    "additionalProperties": False,
}


def extract_receipt_with_openai(file_field, model, message_id="", receipt_expected=False):
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return _unavailable("OPENAI_API_KEY is not configured.")
    if not file_field:
        return _unavailable("No file is available for OCR.")

    mime_type = mimetypes.guess_type(file_field.name or "")[0] or ""
    if not mime_type.startswith("image/"):
        return _unavailable("OpenAI OCR is currently enabled for image receipts only.")

    try:
        encoded, normalized_mime = _normalized_image_data(file_field)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        logger.warning("Receipt OCR rejected unsupported image message_id=%s error=%s", message_id, exc.__class__.__name__)
        return _unavailable("The receipt image could not be prepared for OCR.")

    try:
        client = _openai_client()
    except ImportError:
        return _unavailable("The openai Python package is not installed.")
    initial_detail = getattr(settings, "WHATSAPP_AI_OCR_IMAGE_DETAIL", "low")
    result = _run_attempt(
        client, model, encoded, normalized_mime, initial_detail, "initial", message_id
    )
    if result.get("engine") == "unavailable":
        return result

    validation = _validation_from_result(result)
    result["validation"] = _json_safe_validation(validation)
    _log_validation(message_id, model, initial_detail, "initial", result, validation)

    appears_payment = receipt_expected or _appears_to_be_payment_receipt(result)
    needs_fallback = appears_payment and not validation["is_valid"]
    if not needs_fallback or not getattr(settings, "WHATSAPP_AI_OCR_HIGH_DETAIL_FALLBACK", True):
        return _apply_validation(result, validation)
    if initial_detail == "high":
        return _apply_validation(result, validation)

    reason_fields = validation["missing_fields"] + validation["invalid_fields"]
    logger.info(
        "Receipt OCR high-detail fallback: message_id=%s reason=%s",
        message_id,
        ",".join(reason_fields) or "invalid_structured_output",
    )
    fallback = _run_attempt(
        client, model, encoded, normalized_mime, "high", "fallback", message_id
    )
    if fallback.get("engine") == "unavailable":
        fallback["initial_result"] = _compact_result(result)
        return fallback
    fallback_validation = _validation_from_result(fallback)
    fallback["validation"] = _json_safe_validation(fallback_validation)
    fallback["fallback_used"] = True
    _log_validation(message_id, model, "high", "fallback", fallback, fallback_validation)
    return _apply_validation(fallback, fallback_validation)


def validate_payment_receipt(data):
    data = dict(data or {})
    missing_fields = []
    invalid_fields = []

    amount_value = data.get("amount")
    if amount_value in (None, ""):
        amount = None
        missing_fields.append("amount")
    else:
        amount = _decimal_or_none(amount_value)
        if amount is None or amount <= 0:
            amount = None
            invalid_fields.append("amount")

    date_value = data.get("transaction_date", data.get("date"))
    if date_value in (None, ""):
        transaction_date = None
        missing_fields.append("transaction_date")
    else:
        transaction_date = _date_or_none(date_value)
        if transaction_date is None:
            invalid_fields.append("transaction_date")

    reference_value = data.get("reference_id", data.get("reference"))
    if reference_value in (None, ""):
        reference = ""
        missing_fields.append("reference_id")
    else:
        reference = _reference_or_none(reference_value) or ""
        if not reference:
            invalid_fields.append("reference_id")

    normalized = dict(data)
    normalized.update(
        {
            "amount": amount,
            "date": transaction_date,
            "transaction_date": transaction_date.isoformat() if transaction_date else None,
            "reference": reference,
            "reference_id": reference or None,
        }
    )
    return {
        "is_valid": not missing_fields and not invalid_fields,
        "missing_fields": missing_fields,
        "invalid_fields": invalid_fields,
        "normalized_data": normalized,
    }


def _run_attempt(client, model, encoded, mime_type, detail, attempt, message_id):
    started = time.monotonic()
    try:
        response = client.responses.create(
            model=model,
            max_output_tokens=getattr(settings, "WHATSAPP_AI_OCR_MAX_OUTPUT_TOKENS", 300),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "payment_receipt",
                    "strict": True,
                    "schema": OCR_SCHEMA,
                }
            },
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": OCR_PROMPT.strip()},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{encoded}",
                            "detail": detail,
                        },
                    ],
                }
            ],
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "OpenAI receipt OCR failed: message_id=%s model=%s detail=%s attempt=%s input_tokens=0 cached_tokens=0 output_tokens=0 total_tokens=0 valid=false missing=amount,transaction_date,reference_id invalid=- error=%s duration_ms=%s",
            message_id, model, detail, attempt, _openai_error_kind(exc), duration_ms,
        )
        return _unavailable("Payment OCR was unavailable; staff must verify the receipt manually.")

    duration_ms = int((time.monotonic() - started) * 1000)
    raw_text = getattr(response, "output_text", "") or ""
    parsed = _parse_json(raw_text)
    parsed.update(
        {
            "engine": "openai",
            "model": model,
            "detail": detail,
            "attempt": attempt,
            "usage": _usage(response),
            "duration_ms": duration_ms,
        }
    )
    return _normalize(parsed)


def _normalized_image_data(file_field):
    file_field.open("rb")
    try:
        source = file_field.read()
    finally:
        file_field.close()
    if not source:
        raise ValueError("empty image")

    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    with Image.open(BytesIO(source)) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
        max_dimension = max(1, int(getattr(settings, "WHATSAPP_AI_OCR_MAX_IMAGE_DIMENSION", 1600)))
        if max(image.size) > max_dimension:
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=90, optimize=True)
    return base64.b64encode(output.getvalue()).decode("ascii"), "image/jpeg"


def _parse_json(raw_text):
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"malformed_json": True, "confidence": 0}
    return parsed if isinstance(parsed, dict) else {"malformed_json": True, "confidence": 0}


def _normalize(data):
    normalized = dict(data or {})
    document_type = str(normalized.get("document_type") or "unknown").strip().lower()
    normalized["document_type"] = document_type
    explicit_payment = normalized.get("is_payment_receipt")
    if isinstance(explicit_payment, str):
        explicit_payment = explicit_payment.strip().lower() in {"1", "true", "yes"}
    normalized["is_payment_receipt"] = bool(
        explicit_payment
        or document_type == "payment_receipt"
        or ("payment" in document_type and "receipt" in document_type)
        or "bank transfer receipt" in document_type
    )
    validation = validate_payment_receipt(normalized)
    normalized.update(validation["normalized_data"])
    normalized["validation"] = _json_safe_validation(validation)
    confidence = normalized.get("confidence")
    if isinstance(confidence, dict):
        values = [float(value or 0) for value in confidence.values()]
        normalized["field_confidence"] = confidence
        normalized["confidence"] = round((sum(values) / len(values)) * 100) if values else 0
    else:
        try:
            normalized["confidence"] = max(0, min(100, int(float(confidence or 0))))
        except (TypeError, ValueError):
            normalized["confidence"] = 0
    normalized["bank_information"] = {
        "bank": normalized.get("bank_name") or normalized.get("bank") or "",
        "channel": normalized.get("channel") or "",
        "receiver_account": normalized.get("receiver_account") or "",
        "receiver_name": normalized.get("recipient_name") or normalized.get("receiver_name") or "",
    }
    return normalized


def _apply_validation(result, validation):
    result.update(validation["normalized_data"])
    result["validation"] = _json_safe_validation(validation)
    return result


def _decimal_or_none(value):
    if value in ("", None):
        return None
    text = str(value).strip()
    text = re.sub(r"(?i)(?:\bpkr\b|\brs\.?|\brupees?\b)", "", text)
    text = text.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", text):
        return None
    try:
        amount = Decimal(text)
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _date_or_none(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip()
    iso_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    try:
        parsed = parse_date(iso_match.group(0) if iso_match else text)
    except ValueError:
        parsed = None
    if parsed:
        return parsed
    month_match = re.search(r"\b[A-Za-z]+\s+\d{1,2},\s+\d{4}\b", text)
    candidate = month_match.group(0) if month_match else text
    for date_format in ("%B %d, %Y", "%b %d, %Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(candidate, date_format).date()
        except ValueError:
            continue
    return None


def _reference_or_none(value):
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+", "", text)
    if not re.search(r"[A-Za-z0-9]", text):
        return None
    return text[:160]


def _usage(response):
    usage = getattr(response, "usage", None)
    input_details = getattr(usage, "input_tokens_details", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "cached_tokens": int(getattr(input_details, "cached_tokens", 0) or 0),
        "output_tokens": output_tokens,
        "total_tokens": int(getattr(usage, "total_tokens", 0) or input_tokens + output_tokens),
    }


def _log_validation(message_id, model, detail, attempt, result, validation):
    usage = result.get("usage") or {}
    logger.info(
        "OpenAI receipt OCR: message_id=%s model=%s detail=%s attempt=%s input_tokens=%s cached_tokens=%s output_tokens=%s total_tokens=%s valid=%s missing=%s invalid=%s duration_ms=%s",
        message_id, model, detail, attempt,
        usage.get("input_tokens", 0), usage.get("cached_tokens", 0),
        usage.get("output_tokens", 0), usage.get("total_tokens", 0),
        str(validation["is_valid"]).lower(),
        ",".join(validation["missing_fields"]) or "-",
        ",".join(validation["invalid_fields"]) or "-",
        result.get("duration_ms", 0),
    )


def _appears_to_be_payment_receipt(result):
    return bool(result.get("is_payment_receipt") or result.get("document_type") == "payment_receipt")


def _json_safe_validation(validation):
    return {
        "is_valid": validation["is_valid"],
        "missing_fields": list(validation["missing_fields"]),
        "invalid_fields": list(validation["invalid_fields"]),
    }


def _validation_from_result(result):
    recorded = result.get("validation") or {}
    if {"is_valid", "missing_fields", "invalid_fields"}.issubset(recorded):
        return {
            "is_valid": bool(recorded["is_valid"]),
            "missing_fields": list(recorded["missing_fields"]),
            "invalid_fields": list(recorded["invalid_fields"]),
            "normalized_data": dict(result),
        }
    return validate_payment_receipt(result)


def _compact_result(result):
    return {
        key: result.get(key)
        for key in ("document_type", "amount", "transaction_date", "reference_id", "detail")
    }


def _openai_error_kind(exc):
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    if "quota" in text or "insufficient_quota" in text:
        return "insufficient_quota"
    if "rate" in name or "ratelimit" in name:
        return "rate_limit"
    if "timeout" in name:
        return "timeout"
    if "connection" in name:
        return "connection_error"
    return name[:80] or "openai_error"


def _openai_client():
    from openai import OpenAI

    return OpenAI(api_key=settings.OPENAI_API_KEY)


def _unavailable(reason):
    return {
        "engine": "unavailable",
        "confidence": 0,
        "notes": reason,
        "validation": {
            "is_valid": False,
            "missing_fields": ["amount", "transaction_date", "reference_id"],
            "invalid_fields": [],
        },
    }
