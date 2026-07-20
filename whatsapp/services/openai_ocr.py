import base64
import json
import mimetypes
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils.dateparse import parse_date


OCR_PROMPT = """
Extract payment receipt details from this tenant payment proof.
Return only JSON with these keys:
amount, date, time, reference, transaction_id, sender_name, sender_phone,
receiver_name, receiver_account, bank, channel, description, confidence.
Use null for unknown values. Confidence must be 0-100.
Return date in YYYY-MM-DD format. Treat the large transferred/paid value as the
payment amount; do not use a transaction fee as the payment amount.
"""


def extract_receipt_with_openai(file_field, model):
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return _unavailable("OPENAI_API_KEY is not configured.")
    if not file_field:
        return _unavailable("No file is available for OCR.")

    mime_type = mimetypes.guess_type(file_field.name or "")[0] or ""
    if not mime_type.startswith("image/"):
        return _unavailable("OpenAI OCR is currently enabled for image receipts only.")

    try:
        from openai import OpenAI
    except ImportError:
        return _unavailable("The openai Python package is not installed.")

    file_field.open("rb")
    try:
        encoded = base64.b64encode(file_field.read()).decode("ascii")
    finally:
        file_field.close()

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": OCR_PROMPT.strip()},
                    {"type": "input_image", "image_url": f"data:{mime_type};base64,{encoded}"},
                ],
            }
        ],
    )
    raw_text = getattr(response, "output_text", "") or ""
    parsed = _parse_json(raw_text)
    parsed["engine"] = "openai"
    parsed["model"] = model
    parsed["raw_text"] = raw_text
    return _normalize(parsed)


def _parse_json(raw_text):
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": raw_text, "confidence": 0}


def _normalize(data):
    normalized = dict(data or {})
    normalized["amount"] = _decimal_or_none(normalized.get("amount"))
    normalized["date"] = _date_or_none(normalized.get("date"))
    normalized["reference"] = normalized.get("reference") or normalized.get("transaction_id") or ""
    normalized["confidence"] = int(normalized.get("confidence") or 0)
    normalized["bank_information"] = {
        "bank": normalized.get("bank") or "",
        "channel": normalized.get("channel") or "",
        "receiver_account": normalized.get("receiver_account") or "",
        "receiver_name": normalized.get("receiver_name") or "",
    }
    return normalized


def _decimal_or_none(value):
    if value in ("", None):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _date_or_none(value):
    if not value:
        return None
    text = str(value).strip()
    parsed = parse_date(text)
    if parsed:
        return parsed
    for date_format in ("%B %d, %Y", "%b %d, %Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    return None


def _unavailable(reason):
    return {
        "engine": "unavailable",
        "text": "",
        "confidence": 0,
        "notes": reason,
    }
