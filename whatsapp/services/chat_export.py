"""WhatsApp conversation JSON export (for external AI analysis).

Reuses the existing conversation-page helpers in whatsapp/views.py rather
than re-deriving tenant/lease/message context -- this keeps the export in
sync with whatever the WhatsApp Logs page actually displays, and avoids a
second, competing implementation of "what does this message mean."

Two independent concerns, applied in sequence:
1. Build the export structure (build_single_chat_export / build_all_chat_export).
2. Sanitize it -- secret-key redaction always applies; phone/CNIC masking
   applies only when requested (mask_sensitive_data). See sanitize_export_data.
"""
import re
from decimal import Decimal
from datetime import date, datetime

from django.utils import timezone


SECRET_KEY_PATTERN = re.compile(
    r"(authoriz|access_token|api_key|apikey|secret|password|cookie|"
    r"set-cookie|client_secret|bearer)",
    re.IGNORECASE,
)

# Catches credential-shaped substrings embedded inside otherwise-ordinary
# free text (e.g. a system prompt that happens to include "api_key=sk-...").
# Deliberately narrow: only value patterns that look like an actual
# credential, not every word that happens to contain "secret".
INLINE_SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key|access[_-]?token|client[_-]?secret|app[_-]?secret)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"authorization\s*:\s*\S+", re.IGNORECASE),
    re.compile(r"bearer\s+[a-zA-Z0-9\-_.]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[a-zA-Z0-9]{10,}\b"),  # OpenAI-style secret keys
]


def _redact_inline_secrets(text):
    if not text or not isinstance(text, str):
        return text
    for pattern in INLINE_SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _json_safe(value):
    """Convert a value into something json.dumps can handle without
    relying on Django's model serializer (which the spec explicitly says
    not to use blindly)."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def redact_secrets(value):
    """Recursively walk a JSON-safe structure and replace any value whose
    key looks like a credential, regardless of nesting depth. Also scrubs
    credential-shaped substrings embedded inside ordinary text fields."""
    if isinstance(value, dict):
        result = {}
        for key, sub_value in value.items():
            if SECRET_KEY_PATTERN.search(str(key)):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_secrets(sub_value)
        return result
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return _redact_inline_secrets(value)
    return value


_PHONE_MASK_RE = re.compile(r"(\+?\d{1,3}[-\s]?\d{2,4})[-\s]?(\d{2,4})[-\s]?(\d{4,})")
_CNIC_MASK_RE = re.compile(r"\b(\d{5})-(\d{7})-(\d{1})\b")


def _mask_phone_string(value):
    if not value or not isinstance(value, str):
        return value

    def _mask(match):
        head, _middle, tail = match.groups()
        return f"{head}-***-{tail}"

    return _PHONE_MASK_RE.sub(_mask, value)


def _mask_cnic_string(value):
    if not value or not isinstance(value, str):
        return value
    return _CNIC_MASK_RE.sub(lambda m: f"{m.group(1)}-*******-{m.group(3)}", value)


def mask_sensitive_data(export_data):
    """Mask phone numbers and CNIC numbers throughout the export.

    The same input phone always masks to the same output string (regex
    substitution is deterministic per-input), so conversation identity
    stays intact -- this does not touch financial amounts or
    property/unit names, per the spec.
    """
    def _walk(value):
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        if isinstance(value, str):
            return _mask_cnic_string(_mask_phone_string(value))
        return value

    return _walk(export_data)


def sanitize_export_data(export_data, mask=False):
    """Apply secret redaction (always) and PII masking (optional)."""
    safe = _json_safe(export_data)
    safe = redact_secrets(safe)
    if mask:
        safe = mask_sensitive_data(safe)
    return safe


def serialize_ai_interaction(ai_log):
    return {
        "intent": ai_log.intent,
        "provider": ai_log.provider,
        "model": ai_log.model,
        "confidence": ai_log.confidence,
        "language": ai_log.language,
        "input_summary": ai_log.input_summary,
        "decision": ai_log.decision_json,
        "tool_calls": ai_log.tool_calls,
        "tool_results": ai_log.tool_results_summary,
        "response": ai_log.ai_response,
        "ai_prompt": ai_log.ai_prompt,
        "fallback_used": ai_log.fallback_used,
        "handover_triggered": ai_log.handover_triggered,
        "handover_reason": ai_log.handover_reason,
        "latency_ms": ai_log.latency_ms,
        "prompt_tokens": ai_log.prompt_tokens,
        "completion_tokens": ai_log.completion_tokens,
        "estimated_cost": ai_log.estimated_cost,
        "error_text": ai_log.error_text,
        "metadata": ai_log.metadata,
        "created_at": ai_log.created_at,
    }


def serialize_pending_payment(pending_payment):
    if not pending_payment:
        return None
    return {
        "amount": pending_payment.amount,
        "date": pending_payment.date,
        "reference": pending_payment.reference,
        "bank_information": pending_payment.bank_information,
        "ai_confidence": pending_payment.ai_confidence,
        "ai_notes": pending_payment.ai_notes,
        "status": pending_payment.status,
        "ocr_json": pending_payment.ocr_json,
    }


def serialize_media(media, pending_payment=None):
    if not media and not pending_payment:
        return None
    result = {}
    if media:
        result.update({
            "type": media.media_type,
            "purpose": media.purpose,
            "filename": media.original_filename or "",
            "whatsapp_media_id": getattr(media, "whatsapp_media_id", "") or "",
            "processing_status": getattr(media, "status", "") or "",
        })
    payment_info = serialize_pending_payment(pending_payment)
    if payment_info:
        result["payment_receipt"] = payment_info
    return result or None


def serialize_message(log, *, message_text, media_obj, ai_logs, pending_payment):
    return {
        "id": log.id,
        "timestamp": log.created_at,
        "direction": log.direction,
        "message_type": log.message_type,
        "status": log.status,
        "message": message_text,
        "wa_message_id": log.wa_message_id,
        "tenant_id": log.tenant_id,
        "lease_id": log.lease_id,
        "invoice_id": log.invoice_id,
        "payment_id": log.payment_id,
        "maintenance_request_id": log.maintenance_request_id,
        "error": log.error_text,
        "ai_interactions": [serialize_ai_interaction(item) for item in ai_logs],
        "media": serialize_media(media_obj, pending_payment),
    }


def serialize_conversation_meta(phone_number, context):
    """context is the dict returned by whatsapp.views._conversation_context_for_phone."""
    from whatsapp.models import WhatsAppConversation
    from leases.models import Lease

    conversation = (
        WhatsAppConversation.objects.select_related(
            "tenant", "selected_lease__unit__property"
        )
        .filter(phone_number=phone_number)
        .first()
    )
    lease = Lease.objects.filter(pk=context.get("lease_id")).select_related("unit__property").first() if context.get("lease_id") else None

    return {
        "phone_number": phone_number,
        "tenant": {
            "id": context.get("tenant_id"),
            "name": context.get("tenant_name"),
        },
        "lease": {
            "id": context.get("lease_id"),
            "property": getattr(getattr(lease, "unit", None), "property", None) and lease.unit.property.property_name,
            "unit": getattr(getattr(lease, "unit", None), "unit_number", None),
            "status": getattr(lease, "status", None),
        } if lease else None,
        "conversation_state": {
            "selected_mode": conversation.selected_mode if conversation else "",
            "pending_state": conversation.pending_state if conversation else "",
            "ai_enabled": conversation.ai_enabled if conversation else None,
            "handover_active": conversation.handover_active if conversation else None,
            "last_ai_confidence": conversation.last_ai_confidence if conversation else None,
            "preferred_language": conversation.preferred_language if conversation else "",
            "status": conversation.status if conversation else "",
        } if conversation else None,
    }


def _messages_for_export(phone_number):
    """Same source data as whatsapp.views._conversation_messages, but
    returns the raw querysets/lookups needed for full AI-log and payment
    serialization (the view's version only keeps the single latest AI log
    per message, which isn't enough here -- the spec explicitly wants
    every AI interaction log preserved, not just the most recent)."""
    from whatsapp.models import (
        WhatsAppMessageLog,
        WhatsAppAIInteractionLog,
        PendingWhatsAppMedia,
        PendingWhatsAppPayment,
    )
    from whatsapp.views import _message_text

    logs = list(
        WhatsAppMessageLog.objects.filter(phone_number=phone_number)
        .exclude(direction=WhatsAppMessageLog.DIRECTION_STATUS)
        .order_by("created_at")
    )
    log_ids = [log.id for log in logs]

    media_by_message_id = {
        media.original_whatsapp_message_id: media
        for media in PendingWhatsAppMedia.objects.filter(original_whatsapp_message_id__in=log_ids)
    }
    pending_payment_by_message_id = {
        payment.original_whatsapp_message_id: payment
        for payment in PendingWhatsAppPayment.objects.filter(original_whatsapp_message_id__in=log_ids)
    }
    ai_logs_by_message_id = {}
    for item in (
        WhatsAppAIInteractionLog.objects.filter(message_log_id__in=log_ids).order_by("message_log_id", "created_at")
    ):
        ai_logs_by_message_id.setdefault(item.message_log_id, []).append(item)

    return [
        serialize_message(
            log,
            message_text=_message_text(log),
            media_obj=media_by_message_id.get(log.id),
            ai_logs=ai_logs_by_message_id.get(log.id, []),
            pending_payment=pending_payment_by_message_id.get(log.id),
        )
        for log in logs
    ]


def build_single_chat_export(phone_number):
    from whatsapp.views import _conversation_context_for_phone

    context = _conversation_context_for_phone(phone_number)
    return {
        "export": {
            "type": "single_conversation",
            "generated_at": timezone.now(),
            "system": "Kirayas TMS",
        },
        "conversation": serialize_conversation_meta(phone_number, context),
        "messages": _messages_for_export(phone_number),
    }


def build_all_chat_export():
    from whatsapp.views import _conversation_summary

    summary = _conversation_summary()
    # Latest activity descending -- _conversation_summary is already
    # ordered by -created_at of each phone's latest message.
    conversations = []
    total_messages = 0
    for row in summary:
        phone_number = row["phone_number"]
        messages = _messages_for_export(phone_number)
        total_messages += len(messages)
        context = {
            "tenant_id": row["tenant_id"],
            "tenant_name": row["tenant_name"],
            "lease_id": row["lease_id"],
        }
        conversations.append({
            "conversation": serialize_conversation_meta(phone_number, context),
            "messages": messages,
        })

    return {
        "export": {
            "type": "all_conversations",
            "generated_at": timezone.now(),
            "system": "Kirayas TMS",
            "conversation_count": len(conversations),
            "message_count": total_messages,
        },
        "conversations": conversations,
    }
