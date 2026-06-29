import logging
import time
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from properties.models import Unit
from whatsapp.models import (
    PendingWhatsAppMedia,
    PendingWhatsAppPayment,
    WhatsAppAIInteractionLog,
    WhatsAppConversation,
)
from whatsapp.services.ai_config import get_whatsapp_ai_config
from whatsapp.services.maintenance_ai import create_pending_maintenance, detect_maintenance_issue
from whatsapp.services.media_processor import create_pending_media, run_payment_ocr
from whatsapp.services.payment_matching import extract_payment_text_fields, match_payment_to_active_lease
from whatsapp.services.tenant_context import (
    build_lease_context,
    find_active_leases_for_phone,
    lease_option_lines,
)
from whatsapp.services.whatsapp import WhatsAppService

logger = logging.getLogger(__name__)


CENTRAL_ASSISTANT_PROMPT = """
You are the TMS WhatsApp tenant assistant. Only use active lease context unless a tenant explicitly asks for history.
Never auto-post payments. Stage payments, maintenance, and media for admin review.
Keep replies short, specific, and safe for WhatsApp.
"""


class WhatsAppAIAssistant:
    def __init__(self, service=None):
        self.service = service or WhatsAppService()
        self.ai_config = get_whatsapp_ai_config()

    def handle_inbound_message(self, message_log):
        started = time.monotonic()
        conversation = self._conversation_for(message_log)
        intent = "unknown"
        response = ""
        metadata = {}
        error_text = ""
        try:
            response, intent, metadata = self._handle(message_log, conversation)
            if response:
                self.service.send_text(
                    message_log.phone_number,
                    response,
                    tenant=metadata.get("tenant"),
                    lease=metadata.get("lease"),
                )
        except Exception as exc:
            logger.exception("WhatsApp AI assistant failed for message %s", message_log.pk)
            error_text = str(exc)
            response = "Thanks. We received your message and our office team will review it shortly."
            self.service.send_text(message_log.phone_number, response)
        finally:
            WhatsAppAIInteractionLog.objects.create(
                conversation=conversation,
                message_log=message_log,
                phone_number=message_log.phone_number,
                intent=intent,
                ai_prompt=CENTRAL_ASSISTANT_PROMPT.strip(),
                ai_response=response,
                metadata=_json_safe(metadata),
                latency_ms=int((time.monotonic() - started) * 1000),
                error_text=error_text,
            )

    def _handle(self, message_log, conversation):
        payload = message_log.payload or {}
        message_type = payload.get("type") or message_log.message_type
        text = _payload_text(payload)
        selected_lease = self._selected_active_lease(conversation)

        if self._consume_lease_selection(text, conversation):
            selected_lease = conversation.selected_lease
            ctx = build_lease_context(selected_lease)
            return (
                f"Thanks. I found your active lease for {ctx.property.property_name} - Unit {ctx.unit.unit_number}. How can I help?",
                "lease_selected",
                {"lease": selected_lease, "tenant": selected_lease.tenant},
            )

        if message_type in {"image", "document", "video"}:
            media = create_pending_media(message_log, conversation, selected_lease)
            if media.purpose == PendingWhatsAppMedia.PURPOSE_PAYMENT:
                return self._stage_payment(message_log, conversation, selected_lease, media, text)
            if media.purpose == PendingWhatsAppMedia.PURPOSE_MAINTENANCE:
                pending = create_pending_maintenance(message_log, conversation, selected_lease, media=media)
                conversation.pending_state = "pending_maintenance"
                conversation.context["pending_maintenance_id"] = pending.pk
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return (
                    "We received your maintenance media. Please share the issue type and urgency if not already included.",
                    "maintenance_media",
                    {"lease": selected_lease, "pending_maintenance_id": pending.pk},
                )
            return (
                _media_confirmation_text(media),
                "media_pending",
                {"lease": selected_lease, "pending_media_id": media.pk},
            )

        if _looks_like_yes(text) and conversation.context.get("pending_payment_id"):
            pending = PendingWhatsAppPayment.objects.filter(
                pk=conversation.context.get("pending_payment_id"),
                status=PendingWhatsAppPayment.STATUS_PENDING,
            ).first()
            if pending:
                pending.confirmed_by_tenant = True
                pending.status = PendingWhatsAppPayment.STATUS_CONFIRMED
                pending.save(update_fields=["confirmed_by_tenant", "status", "updated_at"])
                return (
                    "Thanks. Your payment is confirmed and is waiting for admin approval. We will notify you after posting.",
                    "payment_confirmed",
                    {"lease": pending.lease, "tenant": pending.tenant, "pending_payment_id": pending.pk},
                )

        if _looks_like_other(text):
            conversation.pending_state = "manual_identification"
            conversation.save(update_fields=["pending_state", "updated_at"])
            return (
                "Please send Property, Unit, Tenant Name, and Contact Number so our team can match this correctly.",
                "manual_identification",
                {},
            )

        intent = detect_intent(text)
        lease = selected_lease or self._resolve_or_request_lease(message_log.phone_number, conversation)
        if isinstance(lease, str):
            return lease, "lease_lookup", {}

        if intent == "payment":
            return self._stage_payment(message_log, conversation, lease, None, text)
        if intent == "maintenance":
            pending = create_pending_maintenance(message_log, conversation, lease)
            return (
                "Please send a clear photo or short video of the issue. Your maintenance request is staged for admin review.",
                "maintenance_request",
                {"lease": lease, "tenant": getattr(lease, "tenant", None), "pending_maintenance_id": pending.pk},
            )
        if intent == "availability":
            return self._available_units_reply(), "availability", {}
        if intent in {"balance", "lease", "payments"} and lease:
            return self._lease_reply(intent, lease), intent, {"lease": lease, "tenant": lease.tenant}

        return (
            "Thanks. I can help with rent balance, lease details, payment screenshots, maintenance, and available units. Please tell me what you need.",
            "general",
            {"lease": lease, "tenant": getattr(lease, "tenant", None)},
        )

    def _conversation_for(self, message_log):
        conversation, _ = WhatsAppConversation.objects.get_or_create(
            phone_number=message_log.phone_number,
            defaults={"last_message_at": timezone.now()},
        )
        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=["last_message_at", "updated_at"])
        return conversation

    def _resolve_or_request_lease(self, phone_number, conversation):
        selected = self._selected_active_lease(conversation)
        if selected:
            return selected

        leases = list(find_active_leases_for_phone(phone_number))
        if len(leases) == 1:
            conversation.selected_lease = leases[0]
            conversation.selected_property = leases[0].unit.property
            conversation.selected_unit = leases[0].unit
            conversation.save(update_fields=["selected_lease", "selected_property", "selected_unit", "updated_at"])
            return leases[0]
        if len(leases) > 1:
            conversation.context["lease_options"] = [lease.pk for lease in leases]
            conversation.pending_state = "lease_selection"
            conversation.save(update_fields=["context", "pending_state", "updated_at"])
            return lease_option_lines(leases)
        conversation.pending_state = "manual_identification"
        conversation.save(update_fields=["pending_state", "updated_at"])
        return "Please send Property, Unit, Contact Number, and Tenant Name so we can find your active lease."

    def _selected_active_lease(self, conversation):
        lease = conversation.selected_lease
        if lease and lease.status == "active":
            return lease
        return None

    def _consume_lease_selection(self, text, conversation):
        if conversation.pending_state != "lease_selection":
            return False
        try:
            selected_index = int((text or "").strip()) - 1
        except ValueError:
            return False
        option_ids = conversation.context.get("lease_options") or []
        if selected_index < 0 or selected_index >= len(option_ids):
            conversation.pending_state = "manual_identification"
            conversation.save(update_fields=["pending_state", "updated_at"])
            return False
        lease = find_active_leases_for_phone(conversation.phone_number).filter(pk=option_ids[selected_index]).first()
        if not lease:
            return False
        conversation.selected_lease = lease
        conversation.selected_property = lease.unit.property
        conversation.selected_unit = lease.unit
        conversation.pending_state = ""
        conversation.context.pop("lease_options", None)
        conversation.save(update_fields=["selected_lease", "selected_property", "selected_unit", "pending_state", "context", "updated_at"])
        return True

    def _stage_payment(self, message_log, conversation, lease, media, text):
        ocr_json = run_payment_ocr(media, self.ai_config) if media else extract_payment_text_fields(text)
        if not ocr_json.get("amount"):
            extracted = extract_payment_text_fields((ocr_json.get("text") or "") + "\n" + (text or ""))
            ocr_json.update(extracted)
        match = match_payment_to_active_lease(message_log.phone_number, ocr_json)
        matched_lease = lease or match.get("lease")
        pending = PendingWhatsAppPayment.objects.create(
            tenant=getattr(matched_lease, "tenant", None),
            lease=matched_lease,
            property=getattr(getattr(matched_lease, "unit", None), "property", None),
            unit=getattr(matched_lease, "unit", None),
            phone=message_log.phone_number,
            screenshot=getattr(media, "file", None),
            ocr_json=_json_safe(ocr_json),
            amount=ocr_json.get("amount"),
            date=ocr_json.get("date"),
            reference=ocr_json.get("reference", ""),
            bank_information=ocr_json.get("bank_information") or {"channel": _payment_channel(text or ocr_json.get("raw_text", ""))},
            ai_confidence=match.get("confidence", 0),
            ai_notes=match.get("notes", ""),
            original_whatsapp_message=message_log,
            conversation=conversation,
        )
        conversation.pending_state = "pending_payment_confirmation"
        conversation.context["pending_payment_id"] = pending.pk
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return _payment_confirmation_text(pending), "payment_pending", {"lease": matched_lease, "tenant": getattr(matched_lease, "tenant", None), "pending_payment_id": pending.pk}

    def _lease_reply(self, intent, lease):
        ctx = build_lease_context(lease)
        if intent == "payments":
            if not ctx.recent_payments:
                return "No recent payments are recorded for your active lease."
            lines = ["Recent payments:"]
            for payment in ctx.recent_payments:
                lines.append(f"{payment.payment_date}: Rs. {payment.amount} ({payment.reference_number or 'no reference'})")
            return "\n".join(lines)
        if intent == "lease":
            return (
                f"Active lease:\n"
                f"Property: {ctx.property.property_name}\n"
                f"Unit: {ctx.unit.unit_number}\n"
                f"Rent: Rs. {ctx.lease.monthly_rent}\n"
                f"Deposit: Rs. {ctx.lease.security_deposit or Decimal('0.00')}\n"
                f"Lease Dates: {ctx.lease.start_date} to {ctx.lease.end_date}"
            )
        return (
            f"Your outstanding balance for {ctx.property.property_name} - Unit {ctx.unit.unit_number} is Rs. {ctx.balance}."
        )

    def _available_units_reply(self):
        units = list(
            Unit.objects.select_related("property", "interest_type")
            .filter(status="vacant")
            .exclude(leases__status="active")
            .order_by("property__property_name", "unit_number")[:10]
        )
        if not units:
            return (
                "We currently don't have any available units matching your request.\n\n"
                "Would you like us to notify you when one becomes available?"
            )
        lines = ["Available units:"]
        for unit in units:
            lines.append(
                f"{unit.property.property_name}\n"
                f"Unit: {unit.unit_number}\n"
                f"Type: {getattr(unit.interest_type, 'name', '') or unit.property.property_type}\n"
                f"Bedrooms: {unit.bedrooms or '-'}\n"
                f"Rent: Rs. {unit.monthly_rent}\n"
                f"Security Deposit: {unit.security_requires or '-'}"
            )
        return "\n\n".join(lines)


def detect_intent(text):
    lowered = (text or "").lower()
    if any(word in lowered for word in ("available", "vacancy", "vacant", "room", "flat available", "rent available")):
        return "availability"
    if any(word in lowered for word in ("payment", "paid", "receipt", "screenshot", "transfer", "easypaisa", "jazzcash", "raast")):
        return "payment"
    issue, _, confidence = detect_maintenance_issue(lowered)
    if issue != "Other" or confidence >= 75 or "maintenance" in lowered or "repair" in lowered:
        return "maintenance"
    if any(word in lowered for word in ("history", "payments", "paid before")):
        return "payments"
    if any(word in lowered for word in ("lease", "expiry", "expire", "renewal", "deposit")):
        return "lease"
    if any(word in lowered for word in ("balance", "outstanding", "rent due", "dues")):
        return "balance"
    return "general"


def _payload_text(payload):
    text_payload = payload.get("text") or {}
    if isinstance(text_payload, dict) and text_payload.get("body"):
        return text_payload.get("body")
    if payload.get("type") == "button":
        return (payload.get("button") or {}).get("text", "")
    if payload.get("type") == "interactive":
        interactive = payload.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return reply.get("title") or reply.get("id") or ""
    for media_type in ("image", "document", "video"):
        media = payload.get(media_type) or {}
        if media.get("caption"):
            return media.get("caption")
    return ""


def _looks_like_yes(text):
    return (text or "").strip().lower() in {"yes", "y", "confirm", "confirmed", "ok"}


def _looks_like_other(text):
    return (text or "").strip().lower() in {"other", "another", "different"}


def _payment_channel(text):
    lowered = (text or "").lower()
    for channel in ("easypaisa", "jazzcash", "raast"):
        if channel in lowered:
            return channel.title()
    if "bank" in lowered:
        return "Bank"
    if "cheque" in lowered or "check" in lowered:
        return "Cheque"
    return ""


def _payment_confirmation_text(pending):
    prop = getattr(pending.property, "property_name", "") or "Not detected"
    unit = getattr(pending.unit, "unit_number", "") or "Not detected"
    channel = (pending.bank_information or {}).get("channel") or "Not detected"
    return (
        "We received your payment screenshot.\n\n"
        "We believe this payment belongs to:\n"
        f"Property: {prop}\n"
        f"Unit: {unit}\n"
        f"Detected Amount: {pending.amount or 'Not detected'}\n"
        f"Detected Date: {pending.date or 'Not detected'}\n"
        f"Payment Channel: {channel}\n"
        f"Reference Number: {pending.reference or 'Not detected'}\n\n"
        "Reply YES to confirm.\n"
        "Reply OTHER if this belongs to another property/unit."
    )


def _media_confirmation_text(media):
    if media.purpose == PendingWhatsAppMedia.PURPOSE_OTHER:
        return (
            "We received your media. What would you like to do?\n\n"
            "1 Property Photos\n2 Unit Photos\n3 Lease Documents\n4 Maintenance\n5 Payment\n6 Other"
        )
    return "We received your media and staged it for admin review before attaching it to any record."


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items() if key not in {"tenant", "lease"}}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "pk"):
        return {"model": value.__class__.__name__, "id": value.pk}
    return value


def process_inbound_whatsapp_message(message_log):
    if not get_whatsapp_ai_config().enabled:
        return
    WhatsAppAIAssistant().handle_inbound_message(message_log)
