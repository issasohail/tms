import logging
import os

from django.core.files.base import ContentFile

from whatsapp.models import PendingWhatsAppMedia
from whatsapp.services.whatsapp import WhatsAppService

logger = logging.getLogger(__name__)


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
    payload = message_log.payload or {}
    message_type = payload.get("type") or message_log.message_type
    media_payload = payload.get(message_type) or {}
    media_id = media_payload.get("id", "")
    caption = media_payload.get("caption", "") or media_payload.get("filename", "")
    purpose, confidence = detect_media_purpose(caption, message_type)

    content = None
    filename = media_payload.get("filename") or f"whatsapp-{media_id or message_log.pk}.{_extension(message_type)}"
    if media_id:
        content = WhatsAppService().download_media_bytes(media_id)

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
    if ai_config.ocr_provider == "openai" and ai_config.openai_api_key_configured:
        from whatsapp.services.openai_ocr import extract_receipt_with_openai

        return extract_receipt_with_openai(pending_media.file, ai_config.model)
    result = run_basic_ocr(pending_media)
    if ai_config.ocr_provider == "openai":
        result["notes"] = "OpenAI OCR is selected, but OPENAI_API_KEY is not configured."
    return result


def _extension(message_type):
    if message_type == "image":
        return "jpg"
    if message_type == "video":
        return "mp4"
    if message_type == "document":
        return "pdf"
    return "bin"
