import base64
import json
import mimetypes
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils.dateparse import parse_date


OCR_PROMPT = """
Classify this image and extract payment details when it is a bank transfer,
payment receipt, transaction-success screenshot, deposit slip, or other proof
of payment.
Return only JSON with these keys:
document_type, is_payment_receipt, amount, date, time, reference,
transaction_id, sender_name, sender_phone, receiver_name, receiver_account,
bank, channel, description, confidence.
Use null for unknown values. Confidence must be 0-100.
Set is_payment_receipt to true only when the image contains evidence of a
completed or submitted payment/transfer. Set it to false for property photos,
identity documents, lease documents, maintenance photos, and unrelated media.
Return amount as digits only, without a currency label or thousands separator.
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
    if "is_payment_receipt" in normalized:
        payment_flag = normalized.get("is_payment_receipt")
        if isinstance(payment_flag, str):
            payment_flag = payment_flag.strip().lower() in {"true", "yes", "1"}
        normalized["is_payment_receipt"] = bool(payment_flag)
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
    match = re.search(r"\d[\d,]*(?:\.\d{1,2})?", str(value))
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _date_or_none(value):
    if not value:
        return None
    text = str(value).strip()
    iso_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    parsed = parse_date(iso_match.group(0) if iso_match else text)
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


def _unavailable(reason):
    return {
        "engine": "unavailable",
        "text": "",
        "confidence": 0,
        "notes": reason,
    }
