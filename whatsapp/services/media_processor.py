import logging
import os
import json

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder

from whatsapp.models import PendingWhatsAppMedia
from whatsapp.services.whatsapp import WhatsAppService

logger = logging.getLogger(__name__)

ALLOWED_MEDIA_MIME_TYPES = {
    # Explicit allowlist: reject executable/unknown uploads before persistence.
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain", "text/csv",
    "audio/ogg", "audio/mpeg", "audio/mp4", "video/mp4",
}


PAYMENT_WORDS = {"payment", "paid", "receipt", "easypaisa", "jazzcash", "raast", "bank", "transfer", "slip"}
MAINTENANCE_WORDS = {"leak", "repair", "maintenance", "broken", "plumbing", "electric", "ac", "cleaning", "door", "window"}
PROPERTY_WORDS = {"property", "building", "outside", "front", "parking"}
UNIT_WORDS = {"unit", "flat", "room", "kitchen", "bedroom", "bathroom"}
LEASE_WORDS = {"lease", "agreement", "cnic", "document"}


def detect_media_purpose(text, message_type):
    words = set((text or "").lower().replace("/", " ").split())
    if words & PAYMENT_WORDS:
        return PendingWhatsAppMedia.PURPOSE_PAYMENT, 80
    if words & MAINTENANCE_WORDS:
        return PendingWhatsAppMedia.PURPOSE_MAINTENANCE, 75
    if words & LEASE_WORDS or message_type == "document":
        return PendingWhatsAppMedia.PURPOSE_LEASE, 60
    if words & UNIT_WORDS:
        return PendingWhatsAppMedia.PURPOSE_UNIT, 60
    if words & PROPERTY_WORDS:
        return PendingWhatsAppMedia.PURPOSE_PROPERTY, 60
    return PendingWhatsAppMedia.PURPOSE_OTHER, 20


def create_pending_media(message_log, conversation, lease=None):
    simulator_media_id = (message_log.api_response or {}).get("simulator_pending_media_id")
    if simulator_media_id:
        existing = PendingWhatsAppMedia.objects.filter(
            pk=simulator_media_id,
            conversation=conversation,
            status=PendingWhatsAppMedia.STATUS_PENDING,
        ).first()
        if existing:
            if not existing.original_whatsapp_message_id:
                existing.original_whatsapp_message = message_log
                existing.save(update_fields=["original_whatsapp_message", "updated_at"])
            return existing
    payload = message_log.payload or {}
    message_type = payload.get("type") or message_log.message_type
    media_payload = payload.get(message_type) or {}
    media_id = media_payload.get("id", "")
    mime_type = (media_payload.get("mime_type") or "").split(";", 1)[0].lower()
    if mime_type and mime_type not in ALLOWED_MEDIA_MIME_TYPES:
        raise ValueError("This WhatsApp media type is not allowed.")
    caption = media_payload.get("caption", "") or media_payload.get("filename", "")
    purpose, confidence = detect_media_purpose(caption, message_type)

    content = None
    filename = media_payload.get("filename") or f"whatsapp-{media_id or message_log.pk}.{_extension(message_type)}"
    if media_id:
        content = WhatsAppService().download_media_bytes(media_id)
    max_bytes = int(getattr(settings, "WHATSAPP_MAX_INBOUND_MEDIA_BYTES", 16 * 1024 * 1024))
    if content and len(content) > max_bytes:
        raise ValueError("This WhatsApp media file is too large.")

    pending = PendingWhatsAppMedia(
        conversation=conversation,
        original_whatsapp_message=message_log,
        phone=message_log.phone_number,
        original_filename=os.path.basename(filename),
        media_type=message_type,
        whatsapp_media_id=media_id,
        purpose=purpose,
        lease=lease,
        tenant=getattr(lease, "tenant", None),
        property=getattr(getattr(lease, "unit", None), "property", None),
        unit=getattr(lease, "unit", None),
        ai_confidence=confidence,
        ai_notes="Media intent detected from caption/message type.",
    )
    if content:
        pending.file.save(filename, ContentFile(content), save=False)
    else:
        pending.file.name = f"whatsapp/pending/unavailable/{filename}"
        pending.ai_notes += " File download was unavailable; check WhatsApp media token/config."
    pending.save()
    return pending


def run_basic_ocr(pending_media):
    return {
        "engine": "basic",
        "text": "",
        "confidence": 0,
        "notes": "OCR engine is not configured. Original file is stored for admin review.",
    }


def run_payment_ocr(pending_media, ai_config):
    message_log = getattr(pending_media, "original_whatsapp_message", None)
    message_id = getattr(message_log, "wa_message_id", "") or ""
    cached_result = ((getattr(message_log, "api_response", None) or {}).get("receipt_ocr_result"))
    if cached_result:
        logger.info("Reused receipt OCR result for duplicate message_id=%s", message_id)
        from whatsapp.services.openai_ocr import _normalize
        return _normalize(cached_result)

    if ai_config.ocr_provider == "openai" and ai_config.openai_api_key_configured:
        from whatsapp.services.openai_ocr import extract_receipt_with_openai

        try:
            result = extract_receipt_with_openai(
                pending_media.file,
                ai_config.model,
                message_id=message_id,
                receipt_expected=pending_media.purpose == PendingWhatsAppMedia.PURPOSE_PAYMENT,
            )
            if message_log:
                api_response = dict(message_log.api_response or {})
                api_response["receipt_ocr_result"] = json.loads(
                    json.dumps(result, cls=DjangoJSONEncoder)
                )
                message_log.api_response = api_response
                message_log.save(update_fields=["api_response", "updated_at"])
            return result
        except Exception as exc:
            logger.exception("Payment receipt OCR failed for pending media %s", pending_media.pk)
            return {
                "engine": "unavailable",
                "text": "",
                "confidence": 0,
                "notes": "Payment OCR was unavailable; staff must verify the receipt manually.",
            }
    result = run_basic_ocr(pending_media)
    if ai_config.ocr_provider == "openai":
        result["notes"] = "OpenAI OCR is selected, but OPENAI_API_KEY is not configured."
    return result


def _extension(message_type):
    if message_type == "image":
        return "jpg"
    if message_type == "video":
        return "mp4"
    if message_type == "audio":
        return "ogg"
    if message_type == "document":
        return "pdf"
    return "bin"
