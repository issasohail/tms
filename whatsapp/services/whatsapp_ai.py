import json
import logging
import time
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from core.utils.identity import format_phone

from invoices.models import Invoice
from invoices.public_links import make_public_invoice_token
from leases.public_links import create_public_ledger_link, public_ledger_url
from leases.models import Lease
from leases.models import PendingPoliceVerificationSubmission
from leases.services.police_verification import (
    build_police_whatsapp_message,
    create_pending_police_submission,
    create_police_verification_link,
    police_whatsapp_command,
)
from maintenance.models import MaintenanceRequest
from properties.models import Property, Unit
from tenants.models import Tenant, normalize_cnic
from whatsapp.models import (
    PendingWhatsAppMaintenance,
    PendingWhatsAppMedia,
    PendingWhatsAppPayment,
    WhatsAppMessageLog,
    WhatsAppExternalLinkToken,
    WhatsAppAIInteractionLog,
    WhatsAppConversation,
)
from whatsapp.services.ai_config import get_whatsapp_ai_config
from whatsapp.services.ai.orchestrator import WhatsAppAIOrchestrator
from whatsapp.services.ai.safety import safe_summary
from whatsapp.services.handover.lifecycle import create_handover
from whatsapp.services.handover.notifications import notify_new_handover
from whatsapp.services.handover.workflow import (
    detect_handover_request,
    handle_active_tenant_message,
    handle_staff_handover_media,
    handle_staff_handover_message,
)
from whatsapp.services.maintenance_ai import create_pending_maintenance, detect_maintenance_issue
from whatsapp.services.media_processor import create_pending_media, run_payment_ocr
from whatsapp.services.payment_matching import extract_payment_text_fields, match_payment_to_active_lease
from whatsapp.services.role_mode import (
    guest_menu_text,
    identify_sender,
    log_staff_action,
    mode_selection_text,
    resolve_mode,
    staff_can_access_property,
    staff_menu_text,
    staff_submenu_text,
    tenant_menu_text,
    upload_type_menu_text,
)
from whatsapp.services.tenant_context import (
    build_lease_context,
    find_active_leases_for_phone,
    lease_option_lines,
)
from whatsapp.services.whatsapp import WhatsAppService
from handyman.whatsapp import handle_handyman_media_message, handle_handyman_whatsapp_message

logger = logging.getLogger(__name__)


CENTRAL_ASSISTANT_PROMPT = """
You are the Sonaz Property Management WhatsApp Assistant.
Always identify the sender by WhatsApp phone number first.
Tenant Mode is allowed only when the tenant has a current active approved lease and today's date is between lease start date and lease end date.
If the sender has more than one valid role, ask them to choose mode using numbered options.
Always prefer numbered menus and keep replies short, clear, and action-based.
Never guess balances, invoices, leases, payments, maintenance status, meter readings, or billing status. Use only TMS data.
Never expose internal database IDs or data from properties the staff member is not allowed to access.
Tenant actions are self-service and limited to their own active lease.
Staff actions require role and property permission checks and must be logged.
Sensitive write actions must create drafts or pending approval records unless the role explicitly allows direct approval.
"""


SAFE_TEXT_INTENTS = {
    "availability",
    "payment",
    "payments",
    "latest_invoice",
    "payment_receipt",
    "maintenance",
    "maintenance_status",
    "lease",
    "lease_documents",
    "agreement",
    "balance",
    "inspection",
    "meter",
    "family",
    "police_verification",
    "move_out",
    "renewal",
    "contact",
    "suggestion",
    "general",
}


class WhatsAppAIAssistant:
    def __init__(self, service=None):
        self.service = service or WhatsAppService()
        self.ai_config = get_whatsapp_ai_config()
        self.orchestrator = WhatsAppAIOrchestrator(self.ai_config, service=self.service)

    def _clear_context_keys(self, conversation, *keys):
        for key in keys:
            conversation.context.pop(key, None)

    def handle_inbound_message(self, message_log):
        started = time.monotonic()
        conversation = self._conversation_for(message_log)
        intent = "unknown"
        response = ""
        metadata = {}
        error_text = ""
        try:
            response, intent, metadata = self._handle(message_log, conversation)
            deferred_ai_audit = (conversation.context or {}).pop("_deferred_ai_audit", None)
            if deferred_ai_audit:
                # Preserve attempted/denied tool calls even when the legacy fallback
                # ultimately owns the user-facing response.
                for key, value in deferred_ai_audit.items():
                    metadata.setdefault(key, value)
                conversation.save(update_fields=["context", "updated_at"])
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
            if self.ai_config.store_logs:
                WhatsAppAIInteractionLog.objects.create(
                conversation=conversation,
                message_log=message_log,
                phone_number=message_log.phone_number,
                intent=intent,
                model=self.ai_config.model,
                provider=self.ai_config.provider,
                input_summary=safe_summary(_payload_text(message_log.payload or {}), 500),
                decision_json=_json_safe(metadata.get("ai_decision") or {}),
                tool_calls=_json_safe(metadata.get("tool_calls") or []),
                tool_results_summary=_json_safe(metadata.get("tool_results") or []),
                confidence=int((metadata.get("ai_decision") or {}).get("confidence") or 0),
                language=str((metadata.get("ai_decision") or {}).get("language") or "")[:20],
                fallback_used=bool(metadata.get("fallback_used")),
                handover_triggered=bool(metadata.get("handover_id")),
                handover_reason=str((metadata.get("ai_decision") or {}).get("handover_reason") or "")[:160],
                ai_prompt=CENTRAL_ASSISTANT_PROMPT.strip(),
                ai_response=response,
                metadata=_json_safe(metadata),
                latency_ms=int((time.monotonic() - started) * 1000),
                prompt_tokens=int((metadata.get("usage") or {}).get("prompt_tokens") or 0),
                completion_tokens=int((metadata.get("usage") or {}).get("completion_tokens") or 0),
                error_text=error_text,
            )

    def _handle(self, message_log, conversation):
        payload = message_log.payload or {}
        message_type = payload.get("type") or message_log.message_type
        text = _payload_text(payload)
        identity = identify_sender(message_log.phone_number)

        # Active tenant handovers suppress substantive AI replies and relay updates to staff.
        staff_switch = (text or "").strip().lower() in {"staff", "staff mode", "staff inbox"}
        if conversation.handover_active and identity.has_active_tenant and not staff_switch:
            media = None
            if message_type in {"image", "document", "video", "audio"}:
                media = create_pending_media(message_log, conversation, conversation.selected_lease)
            reply = handle_active_tenant_message(message_log, conversation, text, media=media, service=self.service)
            if reply:
                return reply, "handover_tenant_update", {"tenant": identity.tenant, "lease": conversation.selected_lease}

        if message_type in {"image", "document", "video", "audio"} and identity.has_staff:
            staff_media_reply = handle_staff_handover_media(
                message_log, conversation, text, identity.staff_user, service=self.service
            )
            if staff_media_reply:
                return staff_media_reply, "handover_staff_media_reply", {"staff_user": identity.staff_user}

        if message_type in {"image", "document", "video", "audio"}:
            handyman_media_response = handle_handyman_media_message(message_log, conversation, text, message_type, identity)
            if handyman_media_response:
                return handyman_media_response
            return self._handle_media_message(message_log, conversation, text, message_type, identity)

        state_response = self._consume_global_pending_state(message_log, conversation, text, identity)
        if state_response:
            return state_response

        handyman_response = handle_handyman_whatsapp_message(message_log, conversation, text, message_type, identity)
        if handyman_response:
            return handyman_response

        was_mode_selection = (
            conversation.pending_state == "mode_selection"
            or (not conversation.selected_mode_is_valid and identity.has_staff and identity.has_active_tenant)
        )
        mode = resolve_mode(conversation, text, identity)

        if mode == "ambiguous_identity":
            return (
                "This WhatsApp number matches more than one account. For privacy, no account details were opened. Our staff will review the identity match.",
                "ambiguous_identity",
                {},
            )
        if mode == "choose_mode":
            return mode_selection_text(), "mode_selection", {"staff_user": identity.staff_user, "tenant": identity.tenant}
        if mode == WhatsAppConversation.MODE_GUEST:
            return self._handle_guest_message(message_log, conversation, text), "guest", {}
        if mode == WhatsAppConversation.MODE_STAFF:
            return self._handle_staff_message(message_log, conversation, text, message_type, identity), "staff", {
                "staff_user": identity.staff_user,
            }

        handover_request = detect_handover_request(text) if self.ai_config.handover_enabled else None
        if handover_request:
            reason, department, priority = handover_request
            handover, _created = create_handover(
                conversation,
                message_log,
                reason=reason,
                department=department,
                priority=priority,
                ai_summary=text,
            )
            notify_new_handover(handover, service=self.service)
            return (
                f"Your message has been sent to management. Reference: {handover.reference}. Staff will decide whether to reply or call you.",
                "handover",
                {
                    "tenant": identity.tenant,
                    "lease": conversation.selected_lease,
                    "handover_id": handover.pk,
                    "ai_decision": {
                        "handover": True,
                        "handover_reason": reason,
                        "confidence": 100,
                        "language": conversation.preferred_language,
                    },
                },
            )

        selected_lease = self._selected_active_lease(conversation)
        lowered = (text or "").strip().lower()
        command = police_whatsapp_command().strip().lower()

        if selected_lease and lowered in {command, "police", "police verification"}:
            link, url = create_police_verification_link(
                None,
                selected_lease,
                phone_number=message_log.phone_number,
            )
            conversation.pending_state = "police_verification_upload"
            conversation.context["police_verification_lease_id"] = selected_lease.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return (
                build_police_whatsapp_message(None, selected_lease, url),
                "police_verification_link",
                {"lease": selected_lease, "link_id": link.pk},
            )

        if was_mode_selection and lowered in {"2", "tenant", "continue as tenant"}:
            lease = selected_lease or self._resolve_or_request_lease(message_log.phone_number, conversation)
            if isinstance(lease, str):
                return lease, "lease_lookup", {}
            return self._tenant_welcome_menu(lease), "tenant_welcome", {"lease": lease, "tenant": lease.tenant}

        if self._consume_lease_selection(text, conversation):
            selected_lease = conversation.selected_lease
            ctx = build_lease_context(selected_lease)
            return (
                f"Thanks. I found your active lease for {ctx.property.property_name} - Unit {ctx.unit.unit_number}. How can I help?",
                "lease_selected",
                {"lease": selected_lease, "tenant": selected_lease.tenant},
            )

        if message_type in {"image", "document", "video"}:
            police_response = self._consume_police_verification_media(
                message_log,
                conversation,
                selected_lease,
                text,
            )
            if police_response:
                return police_response
            media = create_pending_media(message_log, conversation, selected_lease)
            if media.purpose == PendingWhatsAppMedia.PURPOSE_PAYMENT:
                return self._stage_payment(message_log, conversation, selected_lease, media, text)
            if media.purpose == PendingWhatsAppMedia.PURPOSE_MAINTENANCE:
                pending = create_pending_maintenance(message_log, conversation, selected_lease, media=media)
                conversation.pending_state = "pending_maintenance"
                conversation.context["pending_maintenance_id"] = pending.pk
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                notify_staff_pending_request("maintenance", pending)
                return (
                    "We received your maintenance media. Please share the issue type and urgency if not already included.",
                    "maintenance_media",
                    {"lease": selected_lease, "pending_maintenance_id": pending.pk},
                )
            conversation.pending_state = "tenant_upload_type"
            conversation.context["pending_media_id"] = media.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            notify_staff_pending_request("upload", media)
            return (
                _media_confirmation_text(media),
                "media_pending",
                {"lease": selected_lease, "pending_media_id": media.pk},
            )

        upload_response = self._consume_tenant_upload_type(message_log, conversation, text, selected_lease)
        if upload_response:
            return upload_response

        if _looks_like_yes(text) and conversation.context.get("pending_payment_id"):
            pending = PendingWhatsAppPayment.objects.filter(
                pk=conversation.context.get("pending_payment_id"),
                status=PendingWhatsAppPayment.STATUS_PENDING,
            ).first()
            if pending:
                pending.confirmed_by_tenant = True
                pending.status = PendingWhatsAppPayment.STATUS_CONFIRMED
                pending.save(update_fields=["confirmed_by_tenant", "status", "updated_at"])
                conversation.pending_state = ""
                self._clear_context_keys(conversation, "pending_payment_id", "pending_media_id")
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
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

        if lowered in {"hi", "hello", "start", "menu", "main menu", ""}:
            lease = selected_lease or self._resolve_or_request_lease(message_log.phone_number, conversation)
            if isinstance(lease, str):
                return lease, "lease_lookup", {}
            if conversation.pending_state:
                conversation.pending_state = ""
                conversation.save(update_fields=["pending_state", "updated_at"])
            return self._tenant_welcome_menu(lease), "tenant_welcome", {"lease": lease, "tenant": lease.tenant}

        lease = selected_lease or self._resolve_or_request_lease(message_log.phone_number, conversation)
        if isinstance(lease, str):
            return lease, "lease_lookup", {}

        orchestration = self.orchestrator.handle(text, identity, conversation, message_log, lease=lease)
        if orchestration.handled:
            orchestration.metadata.update({"tenant": lease.tenant, "lease": lease})
            return orchestration.reply, orchestration.intent, orchestration.metadata
        if orchestration.metadata:
            conversation.context["_deferred_ai_audit"] = _json_safe(orchestration.metadata)
            conversation.save(update_fields=["context", "updated_at"])

        guided_maintenance_response = self._consume_guided_maintenance(message_log, conversation, text, lease)
        if guided_maintenance_response:
            return guided_maintenance_response

        invoice_payment_response = self._consume_tenant_invoice_payment_menu(conversation, text, lease)
        if invoice_payment_response:
            return invoice_payment_response

        if any(word in lowered for word in ("ledger", "statement")):
            return self._ledger_link_reply(lease), "ledger", {"lease": lease, "tenant": getattr(lease, "tenant", None)}
        if any(word in lowered for word in ("family", "member", "members")):
            return self._family_list_reply(lease, message_log.phone_number), "family_list", {"lease": lease, "tenant": getattr(lease, "tenant", None)}
        if _looks_like_inspection_request(text):
            return self._inspection_sheet_reply(lease), "inspection", {"lease": lease, "tenant": getattr(lease, "tenant", None)}

        intent = detect_intent(text)
        if intent == "general":
            intent = self._openai_text_intent(text)

        tenant_action_response = self._handle_tenant_data_intent(message_log, conversation, intent, text, lease)
        if tenant_action_response:
            return tenant_action_response

        if lowered in {
            "2",
            "invoice",
            "invoices",
            "payment",
            "payments",
            "payment history",
            "recent payments",
            "invoice / payment",
        }:
            conversation.pending_state = "tenant_invoice_payment_menu"
            conversation.save(update_fields=["pending_state", "updated_at"])
            return (
                _tenant_invoice_payment_menu_text(),
                "tenant_invoice_payment_menu",
                {"lease": lease, "tenant": getattr(lease, "tenant", None)},
            )

        if lowered in {"7", "vacant", "vacancy", "available", "vacant units"}:
            return self._available_units_reply(), "availability", {}
        if lowered in {"8", "registration", "tenant registration"}:
            return (
                "Tenant Registration\n\n"
                "1. Ask staff for registration link\n"
                "2. Contact office\n\n"
                "Reply with a number or type your request.",
                "tenant_registration",
                {"lease": lease, "tenant": getattr(lease, "tenant", None)},
            )
        if lowered in {"9", "contact", "contact office", "office"}:
            return (
                "Please tell me what you need, and our office team will follow up.",
                "contact_office",
                {"lease": lease, "tenant": getattr(lease, "tenant", None)},
            )
        if lowered in {"10", "suggestion", "suggestions", "advice", "advise", "feedback", "idea"}:
            return (
                self._start_suggestion_capture(conversation, "WhatsApp Tenant"),
                "suggestion_prompt",
                {"lease": lease, "tenant": getattr(lease, "tenant", None)},
            )
        if lowered in {"5", "meter", "meter reading", "meter readings", "utility", "utility bills"}:
            return (
                "Please send your latest meter reading or utility bill photo. Our team will review it.",
                "meter_reading",
                {"lease": lease, "tenant": getattr(lease, "tenant", None)},
            )
        if lowered in {"6", "upload", "photo", "upload receipt", "upload photo"}:
            return (
                "Please upload the payment receipt or maintenance photo here, and I will attach it to your active lease for admin review.",
                "upload_prompt",
                {"lease": lease, "tenant": getattr(lease, "tenant", None)},
            )

        if intent == "payment":
            return self._stage_payment(message_log, conversation, lease, None, text)
        if intent == "maintenance":
            return self._start_guided_maintenance(message_log, conversation, text, lease)
        if intent == "availability":
            return self._available_units_reply(), "availability", {}
        if intent == "payments":
            conversation.pending_state = "tenant_invoice_payment_menu"
            conversation.save(update_fields=["pending_state", "updated_at"])
            return (
                _tenant_invoice_payment_menu_text(),
                "tenant_invoice_payment_menu",
                {"lease": lease, "tenant": getattr(lease, "tenant", None)},
            )
        if intent == "inspection":
            return self._inspection_sheet_reply(lease), "inspection", {"lease": lease, "tenant": getattr(lease, "tenant", None)}
        if intent in {"balance", "lease"} and lease:
            return self._lease_reply(intent, lease), intent, {"lease": lease, "tenant": lease.tenant}

        return (
            tenant_menu_text(),
            "general",
            {"lease": lease, "tenant": getattr(lease, "tenant", None)},
        )

    def _handle_media_message(self, message_log, conversation, text, message_type, identity):
        selected_lease = self._selected_active_lease(conversation)
        if not selected_lease and identity.active_leases and len(identity.active_leases) == 1:
            selected_lease = identity.active_leases[0]
            conversation.selected_lease = selected_lease
            conversation.selected_property = selected_lease.unit.property
            conversation.selected_unit = selected_lease.unit
            conversation.tenant = selected_lease.tenant
            conversation.save(update_fields=["selected_lease", "selected_property", "selected_unit", "tenant", "updated_at"])

        police_response = self._consume_police_verification_media(
            message_log,
            conversation,
            selected_lease,
            text,
        )
        if police_response:
            return police_response

        media = create_pending_media(message_log, conversation, selected_lease)
        pending_maintenance_id = conversation.context.get("pending_maintenance_id")
        if conversation.pending_state == "pending_maintenance" and pending_maintenance_id:
            pending = PendingWhatsAppMaintenance.objects.filter(
                pk=pending_maintenance_id,
                status=PendingWhatsAppMaintenance.STATUS_PENDING,
            ).first()
            if pending:
                media.purpose = PendingWhatsAppMedia.PURPOSE_MAINTENANCE
                media.lease = selected_lease or pending.lease
                media.tenant = pending.tenant
                media.property = pending.property
                media.unit = pending.unit
                media.ai_notes = f"{media.ai_notes} Attached to guided WhatsApp maintenance request #{pending.pk}.".strip()
                media.save(update_fields=["purpose", "lease", "tenant", "property", "unit", "ai_notes", "updated_at"])
                pending.media.add(media)
                return (
                    "Maintenance media attached to your request. Thank you.",
                    "maintenance_media_attached",
                    {"lease": pending.lease, "tenant": pending.tenant, "pending_maintenance_id": pending.pk},
                )
        ocr_json = run_payment_ocr(media, self.ai_config) if message_type == "image" else {"engine": "skipped", "confidence": 0}
        if media.purpose == PendingWhatsAppMedia.PURPOSE_PAYMENT or _ocr_looks_like_payment(ocr_json):
            media.purpose = PendingWhatsAppMedia.PURPOSE_PAYMENT
            media.ai_confidence = max(media.ai_confidence or 0, int(ocr_json.get("confidence") or 0), 85 if ocr_json.get("amount") else 0)
            media.ai_notes = f"{media.ai_notes} AI classified this upload as a payment receipt.".strip()
            media.save(update_fields=["purpose", "ai_confidence", "ai_notes", "updated_at"])
            if selected_lease:
                return self._stage_payment(message_log, conversation, selected_lease, media, text, ocr_json=ocr_json)
            return self._stage_unassigned_payment(message_log, conversation, media, text, ocr_json)

        if media.purpose == PendingWhatsAppMedia.PURPOSE_MAINTENANCE:
            pending = create_pending_maintenance(message_log, conversation, selected_lease, media=media)
            conversation.pending_state = "pending_maintenance"
            conversation.context["pending_maintenance_id"] = pending.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            notify_staff_pending_request("maintenance", pending)
            return (
                "I read this as maintenance media. Please share the issue type and urgency if not already included.",
                "maintenance_media",
                {"lease": selected_lease, "pending_maintenance_id": pending.pk},
            )

        state = "staff_upload_type" if identity.has_staff and not identity.has_active_tenant else "tenant_upload_type"
        conversation.pending_state = state
        conversation.context["pending_media_id"] = media.pk
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        notify_staff_pending_request("upload", media)
        return (
            _media_confirmation_text(media),
            "media_pending",
            {"lease": selected_lease, "pending_media_id": media.pk, "ocr": ocr_json},
        )

    def _consume_police_verification_media(self, message_log, conversation, selected_lease, text):
        caption = (text or "").strip().lower()
        lease = selected_lease
        if conversation.pending_state == "police_verification_upload":
            lease_id = conversation.context.get("police_verification_lease_id")
            if lease_id:
                lease = Lease.objects.select_related("tenant", "unit__property").filter(pk=lease_id).first() or lease
        elif "police verification" not in caption:
            return None

        if not lease:
            return None
        media = create_pending_media(message_log, conversation, lease)
        media.purpose = PendingWhatsAppMedia.PURPOSE_LEASE
        media.lease = lease
        media.tenant = lease.tenant
        media.property = lease.unit.property
        media.unit = lease.unit
        media.ai_notes = f"{media.ai_notes} Staged as police verification.".strip()
        media.save(update_fields=["purpose", "lease", "tenant", "property", "unit", "ai_notes", "updated_at"])
        if media.file:
            create_pending_police_submission(
                lease,
                media.file,
                PendingPoliceVerificationSubmission.SOURCE_WHATSAPP,
                phone=message_log.phone_number,
                notes=caption,
                whatsapp_media=media,
            )
        else:
            return (
                "We received the police verification message, but the file download was not available. Please resend the PDF/image.",
                "police_verification_media_missing",
                {"lease": lease, "pending_media_id": media.pk},
            )
        conversation.pending_state = ""
        self._clear_context_keys(conversation, "police_verification_lease_id")
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return (
            "Police verification file received and sent for staff approval.",
            "police_verification_media",
            {"lease": lease, "pending_media_id": media.pk},
        )

    def _consume_global_pending_state(self, message_log, conversation, text, identity):
        lowered = (text or "").strip().lower()
        if conversation.pending_state == "suggestion_capture":
            if lowered in {"cancel", "back", "menu", "main menu"}:
                conversation.pending_state = ""
                self._clear_context_keys(conversation, "suggestion_source")
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return (
                    "Suggestion cancelled.\n\n" + self._menu_for_identity(identity),
                    "suggestion_cancelled",
                    {},
                )
            ticket = self._create_suggestion_ticket(message_log, conversation, text, identity)
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "suggestion_source")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return (
                f"Thanks. Your suggestion has been saved for review.\nSuggestion #{ticket.id}",
                "suggestion_saved",
                {"suggestion_id": ticket.id},
            )
        if conversation.pending_state in {"payment_apply_lookup", "payment_apply_lease_selection"}:
            if lowered in {"cancel", "back", "menu", "main menu"}:
                conversation.pending_state = ""
                self._clear_context_keys(
                    conversation,
                    "payment_apply_lease_options",
                    "payment_apply_retry_count",
                    "pending_payment_id",
                    "pending_media_id",
                )
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return (
                    "Payment receipt kept for admin review.\n\n" + self._menu_for_identity(identity),
                    "payment_apply_cancelled",
                    {},
                )
            return self._consume_payment_apply_lookup(message_log, conversation, text, identity)
        return None

    def _start_suggestion_capture(self, conversation, source="WhatsApp"):
        conversation.pending_state = "suggestion_capture"
        conversation.context["suggestion_source"] = source
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return (
            "Please send your suggestion or advice in one message.\n\n"
            "You can type CANCEL to stop."
        )

    def _create_suggestion_ticket(self, message_log, conversation, text, identity):
        from core.suggestion_store import create_whatsapp_ticket

        user_name = "WhatsApp"
        if identity.staff_user:
            user_name = identity.staff_user.get_username()
        elif identity.tenant:
            user_name = identity.tenant.get_full_name() or str(identity.tenant)
        screen_name = conversation.context.get("suggestion_source") or "WhatsApp"
        return create_whatsapp_ticket(
            text,
            phone_number=message_log.phone_number,
            user_name=user_name,
            screen_name=screen_name,
        )

    def _conversation_for(self, message_log):
        conversation, _ = WhatsAppConversation.objects.get_or_create(
            phone_number=message_log.phone_number,
            defaults={"last_message_at": timezone.now()},
        )
        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=["last_message_at", "updated_at"])
        return conversation

    def _menu_for_identity(self, identity):
        if identity.has_staff and identity.has_active_tenant:
            return mode_selection_text()
        if identity.has_staff:
            return staff_menu_text(identity.staff_user)
        if identity.has_active_tenant:
            return tenant_menu_text()
        return guest_menu_text()

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
        today = timezone.localdate()
        if lease and lease.status == "active" and lease.start_date <= today <= lease.end_date:
            return lease
        return None

    def _handle_guest_message(self, message_log, conversation, text):
        intent = detect_intent(text)
        if intent == "general":
            intent = self._openai_text_intent(text)
        lowered = (text or "").strip().lower()
        if lowered in {"menu", "hi", "hello", "start", ""}:
            return guest_menu_text()
        if lowered in {"4", "suggestion", "suggestions", "advice", "advise", "feedback", "idea"} or intent == "suggestion":
            return self._start_suggestion_capture(conversation, "WhatsApp Guest")
        if intent in {"payments", "balance", "lease"}:
            return "Please send Property, Unit, Contact Number, and Tenant Name so we can find the correct lease/ledger."
        if lowered in {"1", "vacant", "vacancy", "available"} or intent == "availability":
            return self._available_units_reply()
        if lowered in {"2", "registration", "tenant registration"}:
            return (
                "Tenant Registration\n\n"
                "1. Ask staff for registration link\n"
                "2. Contact office\n\n"
                "Reply with a number or type your request."
            )
        if lowered in {"3", "contact", "contact office", "office"} or intent == "contact":
            return "Please tell me what you need, and our office team will follow up."
        if intent == "inspection":
            return "Inspection sheets are available only after we match your active lease. Please send Property, Unit, Contact Number, and Tenant Name."
        return guest_menu_text()

    def _handle_staff_message(self, message_log, conversation, text, message_type, identity):
        staff_user = identity.staff_user
        handover_response = handle_staff_handover_message(
            message_log, conversation, text, staff_user, service=self.service
        )
        if handover_response:
            return handover_response
        lowered = (text or "").strip().lower()
        if message_type in {"image", "document", "video"}:
            media = create_pending_media(message_log, conversation)
            conversation.pending_state = "staff_upload_type"
            conversation.context["pending_media_id"] = media.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            notify_staff_pending_request("upload", media)
            log_staff_action(
                staff_user,
                message_log.phone_number,
                "upload_menu_requested",
                "pending",
                message_type=message_type,
                pending_media_id=media.pk,
            )
            return upload_type_menu_text()
        upload_response = self._consume_staff_upload_type(message_log, conversation, text, staff_user)
        if upload_response:
            return upload_response
        if lowered in {"cancel", "back"}:
            conversation.pending_state = ""
            self._clear_context_keys(
                conversation,
                "staff_add_lease",
                "staff_search_action",
                "staff_search_options",
                "staff_upload_hint",
            )
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return staff_menu_text(staff_user)
        if lowered in {"menu", "staff", "hi", "hello", "start", ""}:
            conversation.pending_state = ""
            self._clear_context_keys(
                conversation,
                "staff_add_lease",
                "staff_search_action",
                "staff_search_options",
                "staff_upload_hint",
            )
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            log_staff_action(staff_user, message_log.phone_number, "staff_menu", "allowed")
            return staff_menu_text(staff_user)
        natural_staff_response = self._handle_staff_natural_language(message_log, conversation, text, staff_user)
        if natural_staff_response:
            return natural_staff_response
        if "ledger" in lowered or "statement" in lowered:
            return self._start_staff_search(conversation, "tenant_ledger", "Send tenant name, phone, CNIC, property, or unit for ledger.")
        if lowered in {"add tenant", "new tenant"}:
            conversation.pending_state = "staff_add_tenant"
            conversation.save(update_fields=["pending_state", "updated_at"])
            log_staff_action(staff_user, message_log.phone_number, "add_tenant_menu", "allowed")
            return _add_tenant_menu_text()
        if conversation.pending_state == "staff_tenant_management" and lowered in {"1", "add tenant", "add new tenant"}:
            conversation.pending_state = "staff_add_tenant"
            conversation.save(update_fields=["pending_state", "updated_at"])
            log_staff_action(staff_user, message_log.phone_number, "add_tenant_menu", "allowed")
            return _add_tenant_menu_text()
        if conversation.pending_state == "staff_add_tenant" and lowered in {"1", "public link", "send public tenant registration link"}:
            return self._create_registration_link_for_staff(message_log, conversation, staff_user)
        lease_flow_response = self._consume_staff_add_lease_flow(message_log, conversation, text, staff_user)
        if lease_flow_response:
            return lease_flow_response
        staff_state_response = self._consume_staff_menu_state(message_log, conversation, text, staff_user)
        if staff_state_response:
            return staff_state_response
        if lowered in {"1", "tenant", "tenant management"}:
            conversation.pending_state = "staff_tenant_management"
            conversation.save(update_fields=["pending_state", "updated_at"])
            log_staff_action(staff_user, message_log.phone_number, "tenant_management_menu", "allowed")
            return staff_submenu_text(text)
        if lowered in {"2", "lease", "lease management"}:
            conversation.pending_state = "staff_lease_management"
            conversation.save(update_fields=["pending_state", "updated_at"])
            log_staff_action(staff_user, message_log.phone_number, "lease_management_menu", "allowed")
            return staff_submenu_text(text)
        if lowered in {"3", "billing"}:
            conversation.pending_state = "staff_billing_management"
            conversation.save(update_fields=["pending_state", "updated_at"])
            log_staff_action(staff_user, message_log.phone_number, "billing_menu", "allowed")
            return staff_submenu_text(text)
        if lowered in {"4", "maintenance"}:
            return self._staff_maintenance_summary(message_log, staff_user)
        if lowered in {"5", "photos", "property photos", "unit photos"}:
            conversation.pending_state = "staff_property_media_menu"
            conversation.save(update_fields=["pending_state", "updated_at"])
            log_staff_action(staff_user, message_log.phone_number, "property_media_menu", "allowed")
            return staff_submenu_text("5")
        if lowered in {"6", "reports"}:
            return self._staff_reports_summary(message_log, staff_user)
        if lowered in {"7", "upload", "upload documents", "documents"}:
            conversation.pending_state = "staff_waiting_upload"
            conversation.save(update_fields=["pending_state", "updated_at"])
            log_staff_action(staff_user, message_log.phone_number, "upload_documents_prompt", "pending")
            return "Please send the image or document you want to upload. I will ask what it is for after receiving it."
        if lowered in {"8", "search"}:
            conversation.pending_state = "staff_search_category"
            conversation.save(update_fields=["pending_state", "updated_at"])
            return _staff_search_menu_text()
        if lowered in {"10", "suggestion", "suggestions", "advice", "advise", "feedback", "idea"}:
            log_staff_action(staff_user, message_log.phone_number, "suggestion_prompt", "allowed")
            return self._start_suggestion_capture(conversation, "WhatsApp Staff")
        agreement_response = self._handle_staff_agreement_link(message_log, text, staff_user)
        if agreement_response:
            return agreement_response
        if lowered in {"add lease", "create lease"}:
            return self._start_staff_add_lease(message_log, conversation, staff_user)
        if lowered in {"9", "switch mode"}:
            conversation.selected_mode = ""
            conversation.mode_expires_at = None
            conversation.pending_state = "mode_selection"
            conversation.save(update_fields=["selected_mode", "mode_expires_at", "pending_state", "updated_at"])
            return mode_selection_text()
        log_staff_action(staff_user, message_log.phone_number, "staff_menu_request", "allowed", text=text[:200])
        return staff_submenu_text(text)

    def _handle_staff_natural_language(self, message_log, conversation, text, staff_user):
        lowered = (text or "").strip().lower()
        query = _strip_staff_action_words(text)
        if not query:
            return None
        if any(phrase in lowered for phrase in ("pending payment", "payment verification", "verify payments")):
            return self._staff_payment_verification(message_log, staff_user)
        if any(phrase in lowered for phrase in ("pending request", "pending requests", "pending queue", "whatsapp pending")):
            return self._staff_pending_requests(message_log, staff_user)
        if any(phrase in lowered for phrase in ("open maintenance", "maintenance summary", "pending maintenance")):
            return self._staff_maintenance_summary(message_log, staff_user)
        if any(phrase in lowered for phrase in ("missing meter", "missing reading")):
            return self._staff_missing_meter_readings(message_log, staff_user)
        if any(phrase in lowered for phrase in ("vacant", "available unit", "empty unit")):
            return self._available_units_reply()
        if "invoice" in lowered and ("link" in lowered or "send" in lowered):
            invoices = self._staff_search_invoices(staff_user, query)
            if len(invoices) == 1:
                return self._staff_invoice_link_reply(message_log, staff_user, invoices[0])
            if invoices:
                conversation.pending_state = "staff_search_selection"
                conversation.context["staff_search_options"] = [{"type": "invoice", "id": item.pk} for item in invoices[:9]]
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                lines = ["Invoice Search Results"]
                for index, invoice in enumerate(invoices[:9], start=1):
                    lines.append(f"{index}. {invoice.invoice_number} - {invoice.lease.tenant.get_full_name()} - Rs. {invoice.amount} - {invoice.get_status_display()}")
                lines.append("\nReply with a number.")
                return "\n".join(lines)
        if "balance" in lowered:
            leases = self._staff_search_leases(staff_user, query)
            if len(leases) == 1:
                return self._staff_lease_action_reply(message_log, staff_user, leases[0], "lease_balance")
            if leases:
                return self._staff_search_results_for_action(conversation, leases, "lease_balance")
        if "ledger" in lowered or "statement" in lowered:
            leases = self._staff_search_leases(staff_user, query)
            if len(leases) == 1:
                return self._staff_lease_action_reply(message_log, staff_user, leases[0], "lease_ledger")
            if leases:
                return self._staff_search_results_for_action(conversation, leases, "lease_ledger")
        return None

    def _staff_search_results_for_action(self, conversation, leases, action):
        conversation.pending_state = "staff_search_selection"
        conversation.context["staff_search_action"] = action
        conversation.context["staff_search_options"] = [{"type": "lease", "id": item.pk} for item in leases[:9]]
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        lines = ["Lease Search Results"]
        for index, lease in enumerate(leases[:9], start=1):
            lines.append(f"{index}. {lease.tenant.get_full_name()} - {lease.unit.property.property_name} / {lease.unit.unit_number} - {lease.get_status_display()}")
        lines.append("\nReply with a number.")
        return "\n".join(lines)

    def _create_registration_link_for_staff(self, message_log, conversation, staff_user):
        from tenants.models import Tenant
        from tenants.views import tenant_registration_token
        from whatsapp.models import WhatsAppExternalLinkToken

        tenant = Tenant.objects.create(
            first_name="New",
            last_name="Registration",
            phone="",
            email="",
            cnic=f"NEW{timezone.now().strftime('%y%m%d%H%M%S')}",
            is_active=False,
            notes=f"Created from WhatsApp registration link by {staff_user or 'staff'}.",
        )
        path = reverse("tenants:tenant_public_registration", args=[tenant_registration_token(tenant)])
        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        link = f"{base_url.rstrip('/')}{path}"
        WhatsAppExternalLinkToken.objects.create(
            link_type=WhatsAppExternalLinkToken.LINK_TENANT_REGISTRATION,
            phone_number=message_log.phone_number,
            tenant=tenant,
            staff_user=staff_user,
            target_app_label="tenants",
            target_model="tenant",
            target_object_id=tenant.pk,
            metadata={"generated_link": link},
            expires_at=timezone.now() + timedelta(days=7),
        )
        conversation.pending_state = ""
        conversation.save(update_fields=["pending_state", "updated_at"])
        log_staff_action(
            staff_user,
            message_log.phone_number,
            "tenant_registration_link_created",
            "allowed",
            tenant=tenant,
            link=link,
        )
        return (
            "Tenant registration link created.\n\n"
            f"Link:\n{link}\n\n"
            "After submission:\n"
            "Pending Approval"
        )

    def _consume_staff_menu_state(self, message_log, conversation, text, staff_user):
        state = conversation.pending_state
        lowered = (text or "").strip().lower()
        if state == "staff_tenant_management":
            if lowered in {"2", "search tenant", "search"}:
                return self._start_staff_search(conversation, "tenant_search", "Send tenant name, phone, CNIC, property, or unit.")
            if lowered in {"3", "tenant balance", "balance"}:
                return self._start_staff_search(conversation, "tenant_balance", "Send tenant name, phone, CNIC, property, or unit for balance.")
            if lowered in {"4", "tenant ledger", "ledger"}:
                return self._start_staff_search(conversation, "tenant_ledger", "Send tenant name, phone, CNIC, property, or unit for ledger.")
            if lowered in {"5", "tenant documents", "documents"}:
                return self._start_staff_search(conversation, "tenant_documents", "Send tenant name, phone, CNIC, property, or unit for documents.")
            if lowered in {"6", "send whatsapp", "send message"}:
                return self._start_staff_search(conversation, "tenant_message", "Send tenant name, phone, CNIC, property, or unit to draft a WhatsApp message.")
            if lowered in {"7", "view tenant", "view"}:
                return self._start_staff_search(conversation, "tenant_view", "Send tenant name, phone, CNIC, property, or unit to view tenant.")
            if lowered in {"8", "back"}:
                conversation.pending_state = ""
                conversation.save(update_fields=["pending_state", "updated_at"])
                return staff_menu_text(staff_user)
            return "Please choose a Tenant Management option by number, or type BACK."

        if state == "staff_lease_management":
            if lowered in {"2", "renew lease", "renew"}:
                return self._start_staff_search(conversation, "lease_renew", "Send tenant, property, unit, or CNIC for the lease to renew.")
            if lowered in {"3", "end lease", "terminate"}:
                return self._start_staff_search(conversation, "lease_end", "Send tenant, property, unit, or CNIC for the lease to end.")
            if lowered in {"4", "view lease", "view"}:
                return self._start_staff_search(conversation, "lease_view", "Send tenant, property, unit, or CNIC to view lease.")
            if lowered in {"5", "upload lease document", "upload"}:
                conversation.pending_state = "staff_waiting_upload"
                conversation.context["staff_upload_hint"] = "lease"
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return "Please send the lease document now. I will stage it for admin review."
            if lowered in {"6", "lease ledger", "ledger"}:
                return self._start_staff_search(conversation, "lease_ledger", "Send tenant, property, unit, or CNIC for lease ledger.")
            if lowered in {"7", "lease balance", "balance"}:
                return self._start_staff_search(conversation, "lease_balance", "Send tenant, property, unit, or CNIC for lease balance.")
            if lowered in {"8", "agreement", "agreement view", "agreement edit"}:
                return (
                    "Agreement Links\n\n"
                    "Reply with:\n"
                    "agreement view TENANT_ID_NUMBER\n"
                    "agreement edit TENANT_ID_NUMBER"
                )
            if lowered in {"9", "back"}:
                conversation.pending_state = ""
                conversation.save(update_fields=["pending_state", "updated_at"])
                return staff_menu_text(staff_user)
            return "Please choose a Lease Management option by number, or type BACK."

        if state == "staff_billing_management":
            if lowered in {"1", "outstanding tenants", "outstanding"}:
                return self._staff_outstanding_tenants(message_log, staff_user)
            if lowered in {"2", "invoice link", "invoice"}:
                return self._start_staff_search(conversation, "invoice_link", "Send invoice number, tenant, property, or unit.")
            if lowered in {"3", "monthly billing status", "monthly"}:
                return self._staff_monthly_billing_status(message_log, staff_user)
            if lowered in {"4", "electric billing", "electric"}:
                return self._staff_electric_billing_status(message_log, staff_user)
            if lowered in {"5", "missing meter readings", "missing readings"}:
                return self._staff_missing_meter_readings(message_log, staff_user)
            if lowered in {"6", "payment verification", "payments"}:
                return self._staff_payment_verification(message_log, staff_user)
            if lowered in {"7", "water charges", "water"}:
                log_staff_action(staff_user, message_log.phone_number, "water_charge_requested", "pending")
                return "Water charge changes are sensitive. Please send Property, Unit, Amount, and Month. I will leave it for admin review."
            if lowered in {"8", "back"}:
                conversation.pending_state = ""
                conversation.save(update_fields=["pending_state", "updated_at"])
                return staff_menu_text(staff_user)
            return "Please choose a Billing option by number, or type BACK."

        if state == "staff_property_media_menu":
            if lowered in {"1", "property photo", "property photos", "2", "unit photo", "unit photos", "3", "lease photo", "lease photos", "4", "tenant document", "tenant documents"}:
                conversation.pending_state = "staff_waiting_upload"
                conversation.context["staff_upload_hint"] = lowered
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return "Please send the image or document now. I will stage it for admin review."
            if lowered in {"5", "view photos"}:
                return self._staff_property_media_summary(message_log, staff_user)
            if lowered in {"6", "back"}:
                conversation.pending_state = ""
                conversation.save(update_fields=["pending_state", "updated_at"])
                return staff_menu_text(staff_user)
            return "Please choose a Property Menu option by number, or type BACK."

        if state == "staff_search_category":
            category_map = {
                "1": "tenant_search",
                "tenant": "tenant_search",
                "search tenant": "tenant_search",
                "2": "lease_view",
                "lease": "lease_view",
                "3": "invoice_link",
                "invoice": "invoice_link",
                "4": "property_search",
                "property": "property_search",
                "5": "unit_search",
                "unit": "unit_search",
            }
            action = category_map.get(lowered)
            if action:
                return self._start_staff_search(conversation, action, "Send your search text.")
            if lowered in {"6", "back"}:
                conversation.pending_state = ""
                conversation.save(update_fields=["pending_state", "updated_at"])
                return staff_menu_text(staff_user)
            return _staff_search_menu_text()

        if state == "staff_search_query":
            return self._consume_staff_search_query(message_log, conversation, text, staff_user)
        if state == "staff_search_selection":
            return self._consume_staff_search_selection(message_log, conversation, text, staff_user)
        return None

    def _start_staff_search(self, conversation, action, prompt):
        conversation.pending_state = "staff_search_query"
        conversation.context["staff_search_action"] = action
        self._clear_context_keys(conversation, "staff_add_lease", "staff_search_options", "staff_upload_hint")
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return f"{prompt}\n\nReply BACK to return to the Staff Menu."

    def _consume_staff_search_query(self, message_log, conversation, text, staff_user):
        action = conversation.context.get("staff_search_action") or "tenant_search"
        if action in {"tenant_search", "tenant_balance", "tenant_ledger", "tenant_documents", "tenant_message", "tenant_view"}:
            tenants = self._staff_search_tenants(staff_user, text)
            if not tenants:
                log_staff_action(staff_user, message_log.phone_number, f"{action}_no_results", "allowed", text=text[:120])
                return "No matching tenant found in properties you can access. Send another search term or type BACK."
            if len(tenants) == 1:
                return self._staff_tenant_action_reply(message_log, staff_user, tenants[0], action)
            conversation.pending_state = "staff_search_selection"
            conversation.context["staff_search_options"] = [{"type": "tenant", "id": item.pk} for item in tenants[:9]]
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            lines = ["Tenant Search Results"]
            for index, tenant in enumerate(tenants[:9], start=1):
                lease = self._staff_latest_accessible_lease(staff_user, tenant)
                suffix = f" - {lease.unit.property.property_name} / {lease.unit.unit_number}" if lease else ""
                lines.append(f"{index}. {tenant.get_full_name()}{suffix}")
            lines.append("\nReply with a number.")
            return "\n".join(lines)

        if action in {"lease_view", "lease_renew", "lease_end", "lease_ledger", "lease_balance"}:
            leases = self._staff_search_leases(staff_user, text)
            if not leases:
                log_staff_action(staff_user, message_log.phone_number, f"{action}_no_results", "allowed", text=text[:120])
                return "No matching lease found in properties you can access. Send another search term or type BACK."
            if len(leases) == 1:
                return self._staff_lease_action_reply(message_log, staff_user, leases[0], action)
            conversation.pending_state = "staff_search_selection"
            conversation.context["staff_search_options"] = [{"type": "lease", "id": item.pk} for item in leases[:9]]
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            lines = ["Lease Search Results"]
            for index, lease in enumerate(leases[:9], start=1):
                lines.append(f"{index}. {lease.tenant.get_full_name()} - {lease.unit.property.property_name} / {lease.unit.unit_number} - {lease.get_status_display()}")
            lines.append("\nReply with a number.")
            return "\n".join(lines)

        if action == "invoice_link":
            invoices = self._staff_search_invoices(staff_user, text)
            if not invoices:
                return "No matching invoice found in properties you can access. Send another search term or type BACK."
            if len(invoices) == 1:
                return self._staff_invoice_link_reply(message_log, staff_user, invoices[0])
            conversation.pending_state = "staff_search_selection"
            conversation.context["staff_search_options"] = [{"type": "invoice", "id": item.pk} for item in invoices[:9]]
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            lines = ["Invoice Search Results"]
            for index, invoice in enumerate(invoices[:9], start=1):
                lines.append(f"{index}. {invoice.invoice_number} - {invoice.lease.tenant.get_full_name()} - Rs. {invoice.amount} - {invoice.get_status_display()}")
            lines.append("\nReply with a number.")
            return "\n".join(lines)

        if action == "property_search":
            properties = [item for item in self._staff_accessible_properties(staff_user) if text.lower() in item.property_name.lower()]
            if not properties:
                return "No matching property found in your WhatsApp access list."
            lines = ["Property Results"]
            for index, property_obj in enumerate(properties[:9], start=1):
                vacant = property_obj.units.filter(status="vacant").count()
                lines.append(f"{index}. {property_obj.property_name} - Vacant units: {vacant}")
            return "\n".join(lines)

        if action == "unit_search":
            units = self._staff_search_units(staff_user, text)
            if not units:
                return "No matching unit found in properties you can access."
            lines = ["Unit Results"]
            for index, unit in enumerate(units[:9], start=1):
                lines.append(f"{index}. {unit.property.property_name} / {unit.unit_number} - {unit.get_status_display()} - Rent Rs. {unit.monthly_rent}")
            return "\n".join(lines)

        return "Search action is not available. Type MENU to return to the Staff Menu."

    def _consume_staff_search_selection(self, message_log, conversation, text, staff_user):
        try:
            selected_index = int((text or "").strip()) - 1
        except ValueError:
            return "Please reply with one of the result numbers, or type BACK."
        options = conversation.context.get("staff_search_options") or []
        if selected_index < 0 or selected_index >= len(options):
            return "That result number is not in the list. Please choose again or type BACK."
        selected = options[selected_index]
        action = conversation.context.get("staff_search_action") or ""
        conversation.pending_state = ""
        self._clear_context_keys(conversation, "staff_search_action", "staff_search_options")
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        if selected["type"] == "tenant":
            tenant = Tenant.objects.filter(pk=selected["id"]).first()
            return self._staff_tenant_action_reply(message_log, staff_user, tenant, action)
        if selected["type"] == "lease":
            lease = Lease.objects.select_related("tenant", "unit__property").filter(pk=selected["id"]).first()
            return self._staff_lease_action_reply(message_log, staff_user, lease, action)
        if selected["type"] == "invoice":
            invoice = Invoice.objects.select_related("lease__tenant", "lease__unit__property").filter(pk=selected["id"]).first()
            return self._staff_invoice_link_reply(message_log, staff_user, invoice)
        return "Selected result is no longer available."

    def _staff_accessible_leases(self, staff_user):
        leases = Lease.objects.select_related("tenant", "unit__property")
        if staff_user.is_superuser:
            return leases
        property_ids = [item.pk for item in self._staff_accessible_properties(staff_user)]
        return leases.filter(unit__property_id__in=property_ids)

    def _staff_search_tenants(self, staff_user, text):
        leases = self._staff_search_leases(staff_user, text)
        tenant_ids = {lease.tenant_id for lease in leases}
        query = (text or "").strip()
        digits = "".join(ch for ch in query if ch.isdigit())
        cnic_digits = normalize_cnic(query)
        tenant_query = Q()
        if query:
            for token in [item for item in query.lower().replace(",", " ").split() if len(item) >= 2]:
                tenant_query |= Q(first_name__icontains=token) | Q(last_name__icontains=token)
        if cnic_digits:
            tenant_query |= Q(cnic_digits=cnic_digits) | Q(cnic__icontains=query)
        if digits:
            suffix = digits[-10:]
            tenant_query |= Q(phone__icontains=suffix) | Q(phone2__icontains=suffix) | Q(phone3__icontains=suffix)
        if tenant_query:
            scoped_lease_tenant_ids = set(self._staff_accessible_leases(staff_user).values_list("tenant_id", flat=True))
            tenant_ids.update(Tenant.objects.filter(tenant_query, pk__in=scoped_lease_tenant_ids).values_list("pk", flat=True))
        return list(Tenant.objects.filter(pk__in=tenant_ids).order_by("first_name", "last_name")[:10])

    def _staff_search_leases(self, staff_user, text):
        query_text = (text or "").strip()
        lowered = query_text.lower()
        digits = "".join(ch for ch in query_text if ch.isdigit())
        cnic_digits = normalize_cnic(query_text)
        query = Q()
        if cnic_digits:
            query |= Q(tenant__cnic_digits=cnic_digits) | Q(tenant__cnic__icontains=query_text)
        if digits:
            suffix = digits[-10:]
            query |= Q(tenant__phone__icontains=suffix) | Q(tenant__phone2__icontains=suffix) | Q(tenant__phone3__icontains=suffix)
            query |= Q(unit__unit_number__icontains=query_text)
        for token in [item for item in lowered.replace(",", " ").split() if len(item) >= 2]:
            query |= Q(tenant__first_name__icontains=token) | Q(tenant__last_name__icontains=token)
            query |= Q(unit__property__property_name__icontains=token) | Q(unit__unit_number__icontains=token)
        if not query:
            return []
        return list(self._staff_accessible_leases(staff_user).filter(query).distinct().order_by("-start_date", "-id")[:10])

    def _staff_search_invoices(self, staff_user, text):
        leases = self._staff_accessible_leases(staff_user)
        query = (text or "").strip()
        invoice_query = Q(invoice_number__icontains=query)
        for token in [item for item in query.lower().replace(",", " ").split() if len(item) >= 2]:
            invoice_query |= Q(lease__tenant__first_name__icontains=token) | Q(lease__tenant__last_name__icontains=token)
            invoice_query |= Q(lease__unit__property__property_name__icontains=token) | Q(lease__unit__unit_number__icontains=token)
        return list(Invoice.objects.select_related("lease__tenant", "lease__unit__property").filter(invoice_query, lease__in=leases).order_by("-issue_date", "-id")[:10])

    def _staff_search_units(self, staff_user, text):
        property_ids = [item.pk for item in self._staff_accessible_properties(staff_user)]
        units = Unit.objects.select_related("property")
        if not staff_user.is_superuser:
            units = units.filter(property_id__in=property_ids)
        query = (text or "").strip()
        unit_query = Q(unit_number__icontains=query)
        for token in [item for item in query.lower().replace(",", " ").split() if len(item) >= 2]:
            unit_query |= Q(property__property_name__icontains=token) | Q(unit_number__icontains=token)
        return list(units.filter(unit_query).order_by("property__property_name", "unit_number")[:10])

    def _staff_latest_accessible_lease(self, staff_user, tenant):
        return self._staff_accessible_leases(staff_user).filter(tenant=tenant).order_by("-start_date", "-id").first()

    def _staff_tenant_action_reply(self, message_log, staff_user, tenant, action):
        if not tenant:
            return "Selected tenant is no longer available."
        lease = self._staff_latest_accessible_lease(staff_user, tenant)
        if not lease:
            log_staff_action(staff_user, message_log.phone_number, f"{action}_blocked", "blocked", tenant=tenant)
            return "You do not have WhatsApp access to this tenant's property data."
        if action == "tenant_balance":
            return self._staff_lease_action_reply(message_log, staff_user, lease, "lease_balance")
        if action == "tenant_ledger":
            return self._staff_lease_action_reply(message_log, staff_user, lease, "lease_ledger")
        if action == "tenant_documents":
            log_staff_action(staff_user, message_log.phone_number, "tenant_documents_requested", "allowed", tenant=tenant, lease=lease, property=lease.unit.property)
            docs_count = getattr(tenant, "photo", None) and 1 or 0
            return (
                f"Tenant Documents\n\n"
                f"Tenant: {tenant.get_full_name()}\n"
                f"CNIC Front: {'Available' if tenant.cnic_front else 'Not uploaded'}\n"
                f"CNIC Back: {'Available' if tenant.cnic_back else 'Not uploaded'}\n"
                f"Photo: {'Available' if tenant.photo else 'Not uploaded'}"
            )
        if action == "tenant_message":
            log_staff_action(staff_user, message_log.phone_number, "tenant_message_draft_requested", "pending", tenant=tenant, lease=lease, property=lease.unit.property)
            return (
                f"Message draft target selected:\n{tenant.get_full_name()} - {lease.unit.property.property_name} / {lease.unit.unit_number}\n\n"
                "Please type the message you want to send. It will be staged for review in this chat; it is not sent automatically."
            )
        log_staff_action(staff_user, message_log.phone_number, "tenant_viewed", "allowed", tenant=tenant, lease=lease, property=lease.unit.property)
        return (
            f"Tenant Summary\n\n"
            f"Name: {tenant.get_full_name()}\n"
            f"Phone: {format_phone(tenant.phone) or '-'}\n"
            f"Property: {lease.unit.property.property_name}\n"
            f"Unit: {lease.unit.unit_number}\n"
            f"Lease: {lease.start_date} to {lease.end_date}\n"
            f"Status: {lease.get_status_display()}"
        )

    def _staff_lease_action_reply(self, message_log, staff_user, lease, action):
        if not lease:
            return "Selected lease is no longer available."
        if not staff_can_access_property(staff_user, lease.unit.property):
            log_staff_action(staff_user, message_log.phone_number, f"{action}_blocked", "blocked", lease=lease, property=lease.unit.property)
            return "You do not have WhatsApp access to that property's lease data."
        ctx = build_lease_context(lease)
        if action == "lease_balance":
            log_staff_action(staff_user, message_log.phone_number, "lease_balance_viewed", "allowed", lease=lease, property=ctx.property, tenant=ctx.tenant)
            return f"Lease Balance\n\n{ctx.property.property_name} / {ctx.unit.unit_number}\nTenant: {ctx.tenant.get_full_name()}\nOutstanding: Rs. {ctx.balance}"
        if action == "lease_ledger":
            log_staff_action(staff_user, message_log.phone_number, "lease_ledger_viewed", "allowed", lease=lease, property=ctx.property, tenant=ctx.tenant)
            base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
            link = create_public_ledger_link(lease, phone_number=message_log.phone_number, staff_user=staff_user)
            ledger_link = public_ledger_url(base_url, link)
            lines = [
                f"Lease Ledger\n\n{ctx.property.property_name} / {ctx.unit.unit_number}",
                f"Outstanding: Rs. {ctx.balance}",
                "",
                "Public ledger link valid for 24 hours:",
                ledger_link,
                "",
                "Recent payments:",
            ]
            if ctx.recent_payments:
                for payment in ctx.recent_payments[:5]:
                    lines.append(f"- {payment.payment_date}: Rs. {payment.amount} ({payment.reference_number or 'no reference'})")
            else:
                lines.append("- No recent payments")
            return "\n".join(lines)
        if action == "lease_renew":
            log_staff_action(staff_user, message_log.phone_number, "lease_renew_requested", "pending", lease=lease, property=ctx.property, tenant=ctx.tenant)
            return f"Renew Lease\n\nSelected: {ctx.tenant.get_full_name()} - {ctx.property.property_name} / {ctx.unit.unit_number}\n\nRenewal is a sensitive action. Please open TMS Lease Detail to complete it."
        if action == "lease_end":
            log_staff_action(staff_user, message_log.phone_number, "lease_end_requested", "pending", lease=lease, property=ctx.property, tenant=ctx.tenant)
            return f"End Lease\n\nSelected: {ctx.tenant.get_full_name()} - {ctx.property.property_name} / {ctx.unit.unit_number}\n\nLease termination was not applied automatically. It is staged for admin review."
        log_staff_action(staff_user, message_log.phone_number, "lease_viewed", "allowed", lease=lease, property=ctx.property, tenant=ctx.tenant)
        return (
            f"Lease Summary\n\n"
            f"Tenant: {ctx.tenant.get_full_name()}\n"
            f"Property: {ctx.property.property_name}\n"
            f"Unit: {ctx.unit.unit_number}\n"
            f"Rent: Rs. {lease.monthly_rent}\n"
            f"Deposit: Rs. {lease.security_deposit or Decimal('0.00')}\n"
            f"Dates: {lease.start_date} to {lease.end_date}\n"
            f"Status: {lease.get_status_display()}"
        )

    def _staff_invoice_link_reply(self, message_log, staff_user, invoice):
        if not invoice:
            return "Selected invoice is no longer available."
        lease = invoice.lease
        if not staff_can_access_property(staff_user, lease.unit.property):
            log_staff_action(staff_user, message_log.phone_number, "invoice_link_blocked", "blocked", lease=lease, property=lease.unit.property, tenant=lease.tenant)
            return "You do not have WhatsApp access to that invoice's property."
        token = make_public_invoice_token(invoice.pk)
        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        link = f"{base_url.rstrip('/')}{reverse('invoices:public_invoice_detail', args=[token])}"
        log_staff_action(staff_user, message_log.phone_number, "invoice_link_created", "allowed", lease=lease, property=lease.unit.property, tenant=lease.tenant, invoice_id=invoice.pk)
        return (
            f"Invoice Link\n\n"
            f"Invoice: {invoice.invoice_number}\n"
            f"Tenant: {lease.tenant.get_full_name()}\n"
            f"Amount: Rs. {invoice.amount}\n"
            f"Status: {invoice.get_status_display()}\n\n"
            f"Link:\n{link}"
        )

    def _staff_outstanding_tenants(self, message_log, staff_user):
        lines = ["Outstanding Tenants"]
        count = 0
        for lease in self._staff_accessible_leases(staff_user).filter(status="active").order_by("unit__property__property_name", "unit__unit_number")[:50]:
            ctx = build_lease_context(lease)
            if ctx.balance > 0:
                count += 1
                lines.append(f"{count}. {ctx.tenant.get_full_name()} - {ctx.property.property_name} / {ctx.unit.unit_number} - Rs. {ctx.balance}")
            if count >= 10:
                break
        log_staff_action(staff_user, message_log.phone_number, "billing_outstanding_requested", "allowed")
        if count == 0:
            return "No outstanding active tenant balances found in properties you can access."
        return "\n".join(lines)

    def _staff_monthly_billing_status(self, message_log, staff_user):
        today = timezone.localdate()
        invoices = Invoice.objects.filter(
            lease__in=self._staff_accessible_leases(staff_user),
            issue_date__year=today.year,
            issue_date__month=today.month,
        )
        total = invoices.count()
        by_status = {status: invoices.filter(status=status).count() for status, _ in Invoice.INVOICE_STATUS}
        log_staff_action(staff_user, message_log.phone_number, "monthly_billing_status_requested", "allowed")
        return (
            f"Monthly Billing Status ({today:%B %Y})\n\n"
            f"Total invoices: {total}\n"
            f"Draft: {by_status.get('draft', 0)}\n"
            f"Sent: {by_status.get('sent', 0)}\n"
            f"Paid: {by_status.get('paid', 0)}\n"
            f"Overdue: {by_status.get('overdue', 0)}\n"
            f"Cancelled: {by_status.get('cancelled', 0)}"
        )

    def _staff_electric_billing_status(self, message_log, staff_user):
        units = Unit.objects.select_related("property")
        if not staff_user.is_superuser:
            units = units.filter(property__in=self._staff_accessible_properties(staff_user))
        total = units.count()
        smart = units.filter(is_smart_meter=True).count()
        non_smart = total - smart
        log_staff_action(staff_user, message_log.phone_number, "electric_billing_status_requested", "allowed")
        return (
            "Electric Billing Status\n\n"
            f"Units in your access: {total}\n"
            f"Smart-meter units: {smart}\n"
            f"Non-smart units: {non_smart}\n\n"
            "Use Missing Meter Readings to see units needing attention."
        )

    def _staff_missing_meter_readings(self, message_log, staff_user):
        from smart_meter.models import MeterInstallation, LiveReading

        units = Unit.objects.select_related("property").filter(is_smart_meter=True)
        if not staff_user.is_superuser:
            units = units.filter(property__in=self._staff_accessible_properties(staff_user))
        active_installations = MeterInstallation.objects.filter(unit__in=units, is_active=True, end_date__isnull=True).select_related("unit__property", "meter")
        missing = []
        for installation in active_installations[:100]:
            if not LiveReading.objects.filter(meter=installation.meter).exists():
                missing.append(installation)
        log_staff_action(staff_user, message_log.phone_number, "missing_meter_readings_requested", "allowed")
        if not missing:
            return "No missing smart-meter live readings found in properties you can access."
        lines = ["Missing Meter Readings"]
        for index, installation in enumerate(missing[:10], start=1):
            lines.append(f"{index}. {installation.unit.property.property_name} / {installation.unit.unit_number} - Meter {installation.meter.meter_number}")
        return "\n".join(lines)

    def _staff_payment_verification(self, message_log, staff_user):
        payments = PendingWhatsAppPayment.objects.filter(
            status__in=[PendingWhatsAppPayment.STATUS_PENDING, PendingWhatsAppPayment.STATUS_CONFIRMED],
            approved=False,
            rejected=False,
        ).select_related("tenant", "lease", "property", "unit").order_by("-created_at")
        if not staff_user.is_superuser:
            property_ids = [item.pk for item in self._staff_accessible_properties(staff_user)]
            payments = payments.filter(Q(property_id__in=property_ids) | Q(property__isnull=True))
        log_staff_action(staff_user, message_log.phone_number, "payment_verification_listed", "allowed")
        if not payments.exists():
            return "No pending WhatsApp payment verifications found."
        lines = ["Payment Verification Queue"]
        for index, payment in enumerate(payments[:10], start=1):
            target = f"{payment.property.property_name} / {payment.unit.unit_number}" if payment.property and payment.unit else "Unmatched"
            lines.append(f"{index}. Rs. {payment.amount or 'Not detected'} - {target} - {payment.get_status_display()}")
        return "\n".join(lines)

    def _staff_pending_requests(self, message_log, staff_user):
        property_ids = None
        if not staff_user.is_superuser:
            property_ids = [item.pk for item in self._staff_accessible_properties(staff_user)]

        payments = PendingWhatsAppPayment.objects.filter(
            status__in=[PendingWhatsAppPayment.STATUS_PENDING, PendingWhatsAppPayment.STATUS_CONFIRMED],
            approved=False,
            rejected=False,
        ).select_related("tenant", "property", "unit").order_by("-created_at")
        media = PendingWhatsAppMedia.objects.filter(
            status=PendingWhatsAppMedia.STATUS_PENDING,
        ).select_related("tenant", "property", "unit", "lease").order_by("-created_at")
        maintenance = PendingWhatsAppMaintenance.objects.filter(
            status=PendingWhatsAppMaintenance.STATUS_PENDING,
        ).select_related("tenant", "property", "unit").order_by("-created_at")

        if property_ids is not None:
            payments = payments.filter(Q(property_id__in=property_ids) | Q(property__isnull=True))
            media = media.filter(Q(property_id__in=property_ids) | Q(property__isnull=True))
            maintenance = maintenance.filter(Q(property_id__in=property_ids) | Q(property__isnull=True))

        log_staff_action(staff_user, message_log.phone_number, "pending_requests_listed", "allowed")

        if not payments.exists() and not media.exists() and not maintenance.exists():
            return "No pending WhatsApp requests found in properties you can access."

        lines = ["Pending WhatsApp Requests"]
        if payments.exists():
            lines.append("\nPayments")
            for index, payment in enumerate(payments[:5], start=1):
                target = _pending_target(payment)
                lines.append(f"{index}. Rs. {payment.amount or 'Not detected'} - {target} - {payment.get_status_display()}")
        if maintenance.exists():
            lines.append("\nMaintenance")
            for index, item in enumerate(maintenance[:5], start=1):
                target = _pending_target(item)
                lines.append(f"{index}. {target} - {item.issue_type or 'Issue'} - {item.urgency or 'normal'}")
        if media.exists():
            lines.append("\nUploads")
            for index, item in enumerate(media[:5], start=1):
                target = _pending_target(item)
                lines.append(f"{index}. {item.get_purpose_display()} - {target}")
        lines.append("\nOpen TMS admin pending approval screens to approve or reject.")
        return "\n".join(lines)

    def _staff_maintenance_summary(self, message_log, staff_user):
        requests = MaintenanceRequest.objects.select_related("tenant", "lease", "unit__property").exclude(status__in=["completed", "cancelled"]).order_by("-reported_date", "-id")
        if not staff_user.is_superuser:
            requests = requests.filter(unit__property__in=self._staff_accessible_properties(staff_user))
        log_staff_action(staff_user, message_log.phone_number, "maintenance_summary_requested", "allowed")
        if not requests.exists():
            return "No open maintenance requests found in properties you can access."
        lines = ["Open Maintenance"]
        for index, item in enumerate(requests[:10], start=1):
            unit = f"{item.unit.property.property_name} / {item.unit.unit_number}" if item.unit else "No unit"
            lines.append(f"{index}. {unit} - {item.title} - {item.get_status_display()}")
        return "\n".join(lines)

    def _staff_reports_summary(self, message_log, staff_user):
        active_leases = self._staff_accessible_leases(staff_user).filter(status="active")
        properties = self._staff_accessible_properties(staff_user)
        vacant_units = Unit.objects.filter(property__in=properties, status="vacant").count() if not staff_user.is_superuser else Unit.objects.filter(status="vacant").count()
        pending_payments = PendingWhatsAppPayment.objects.filter(status__in=[PendingWhatsAppPayment.STATUS_PENDING, PendingWhatsAppPayment.STATUS_CONFIRMED], approved=False, rejected=False).count()
        log_staff_action(staff_user, message_log.phone_number, "reports_summary_requested", "allowed")
        return (
            "Reports Summary\n\n"
            f"Properties in WhatsApp access: {len(properties)}\n"
            f"Active leases: {active_leases.count()}\n"
            f"Vacant units: {vacant_units}\n"
            f"Pending WhatsApp payments: {pending_payments}\n\n"
            "For full reports, open TMS Reports."
        )

    def _staff_property_media_summary(self, message_log, staff_user):
        properties = self._staff_accessible_properties(staff_user)
        units = Unit.objects.filter(property__in=properties) if not staff_user.is_superuser else Unit.objects.all()
        log_staff_action(staff_user, message_log.phone_number, "property_media_summary_requested", "allowed")
        return (
            "Property / Unit Photos\n\n"
            f"Properties in access: {len(properties)}\n"
            f"Units in access: {units.count()}\n\n"
            "Send an image/document to stage a new upload."
        )

    def _start_staff_add_lease(self, message_log, conversation, staff_user):
        conversation.pending_state = "staff_add_lease_tenant_id"
        conversation.context["staff_add_lease"] = {}
        self._clear_context_keys(conversation, "staff_search_action", "staff_search_options", "staff_upload_hint")
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        log_staff_action(staff_user, message_log.phone_number, "add_lease_started", "allowed")
        return (
            "Create Lease\n\n"
            "Please enter Tenant ID Number.\n\n"
            "Example:\n"
            "35202-1234567-8\n\n"
            "Reply CANCEL to stop."
        )

    def _consume_staff_add_lease_flow(self, message_log, conversation, text, staff_user):
        state = conversation.pending_state
        lowered = (text or "").strip().lower()
        if state == "staff_lease_management" and lowered in {"1", "create lease", "add lease"}:
            return self._start_staff_add_lease(message_log, conversation, staff_user)
        if state == "staff_lease_management" and lowered in {"8", "agreement", "agreement view", "agreement edit"}:
            return (
                "Agreement Links\n\n"
                "Reply with:\n"
                "agreement view TENANT_ID_NUMBER\n"
                "agreement edit TENANT_ID_NUMBER"
            )
        if state == "staff_add_lease_tenant_id":
            return self._staff_add_lease_select_tenant(message_log, conversation, text, staff_user)
        if state == "staff_add_lease_property":
            return self._staff_add_lease_select_property(message_log, conversation, text, staff_user)
        if state == "staff_add_lease_unit":
            return self._staff_add_lease_select_unit(message_log, conversation, text, staff_user)
        return None

    def _staff_add_lease_select_tenant(self, message_log, conversation, text, staff_user):
        cnic_digits = normalize_cnic(text)
        tenant = None
        if cnic_digits:
            tenant = Tenant.objects.filter(cnic_digits=cnic_digits).first()
            if not tenant:
                tenant = next((item for item in Tenant.objects.all() if normalize_cnic(item.cnic) == cnic_digits), None)
        if not tenant:
            log_staff_action(staff_user, message_log.phone_number, "add_lease_tenant_not_found", "blocked", tenant_id_number=text[:80])
            return (
                "Tenant not found.\n\n"
                "1. Send public tenant registration link\n"
                "2. Enter another Tenant ID Number\n"
                "3. Back"
            )
        if not tenant.is_active:
            log_staff_action(staff_user, message_log.phone_number, "add_lease_pending_tenant_blocked", "blocked", tenant=tenant)
            return (
                "This tenant is still Pending Approval or inactive, so a lease cannot be created yet.\n\n"
                "Approve the tenant first, then try Add Lease again."
            )

        properties = self._staff_accessible_properties(staff_user)
        if not properties:
            log_staff_action(staff_user, message_log.phone_number, "add_lease_no_property_access", "blocked", tenant=tenant)
            return "No WhatsApp property access is assigned to your staff user."

        conversation.context["staff_add_lease"] = {
            "tenant_id": tenant.pk,
            "property_options": [item.pk for item in properties],
        }
        conversation.pending_state = "staff_add_lease_property"
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return self._property_options_text(tenant, properties)

    def _staff_add_lease_select_property(self, message_log, conversation, text, staff_user):
        data = conversation.context.get("staff_add_lease") or {}
        option_ids = data.get("property_options") or []
        property_obj = self._option_from_number(text, Property, option_ids)
        if not property_obj or not staff_can_access_property(staff_user, property_obj):
            log_staff_action(staff_user, message_log.phone_number, "add_lease_property_blocked", "blocked", property=property_obj)
            return "Invalid property selection or you do not have WhatsApp access to that property."

        units = list(Unit.objects.filter(property=property_obj, status="vacant").order_by("unit_number")[:30])
        if not units:
            return f"No vacant units found for {property_obj.property_name}."
        data["property_id"] = property_obj.pk
        data["unit_options"] = [unit.pk for unit in units]
        conversation.context["staff_add_lease"] = data
        conversation.pending_state = "staff_add_lease_unit"
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return self._unit_options_text(property_obj, units)

    def _staff_add_lease_select_unit(self, message_log, conversation, text, staff_user):
        data = conversation.context.get("staff_add_lease") or {}
        tenant = Tenant.objects.filter(pk=data.get("tenant_id"), is_active=True).first()
        property_obj = Property.objects.filter(pk=data.get("property_id")).first()
        unit = self._option_from_number(text, Unit, data.get("unit_options") or [])
        if not tenant or not property_obj or not unit or unit.property_id != property_obj.pk:
            return "Invalid unit selection. Please start Add Lease again."
        if not staff_can_access_property(staff_user, property_obj):
            log_staff_action(staff_user, message_log.phone_number, "add_lease_property_blocked", "blocked", property=property_obj, tenant=tenant)
            return "You do not have WhatsApp access to that property."

        link = self._create_lease_creation_link(message_log, staff_user, tenant, property_obj, unit)
        conversation.pending_state = ""
        conversation.context.pop("staff_add_lease", None)
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        log_staff_action(staff_user, message_log.phone_number, "lease_creation_link_created", "allowed", property=property_obj, tenant=tenant, link=link)
        return (
            "Lease form link created.\n\n"
            f"Tenant:\n{tenant}\n\n"
            f"Property:\n{property_obj.property_name}\n\n"
            f"Unit:\n{unit.unit_number}\n\n"
            f"Link:\n{link}\n\n"
            "After saving:\nPending Approval"
        )

    def _create_lease_creation_link(self, message_log, staff_user, tenant, property_obj, unit):
        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        token = WhatsAppExternalLinkToken.objects.create(
            link_type=WhatsAppExternalLinkToken.LINK_LEASE_CREATION,
            phone_number=message_log.phone_number,
            tenant=tenant,
            staff_user=staff_user,
            target_app_label="leases",
            target_model="lease",
            metadata={
                "tenant_id": tenant.pk,
                "property_id": property_obj.pk,
                "unit_id": unit.pk,
            },
            expires_at=timezone.now() + timedelta(days=7),
        )
        path = reverse("leases:public_lease_create", args=[token.token])
        return f"{base_url.rstrip('/')}{path}"

    def _staff_accessible_properties(self, staff_user):
        if staff_user.is_superuser:
            return list(Property.objects.order_by("property_name"))
        return [
            access.property
            for access in staff_user.whatsapp_property_access.filter(is_active=True).select_related("property").order_by("property__property_name")
        ]

    def _option_from_number(self, text, model, option_ids):
        try:
            selected_index = int((text or "").strip()) - 1
        except ValueError:
            return None
        if selected_index < 0 or selected_index >= len(option_ids):
            return None
        return model.objects.filter(pk=option_ids[selected_index]).first()

    def _property_options_text(self, tenant, properties):
        lines = [f"Tenant found:\n{tenant}\n\nSelect Property"]
        for index, property_obj in enumerate(properties, start=1):
            lines.append(f"{index}. {property_obj.property_name}")
        lines.append(f"{len(properties) + 1}. Back")
        return "\n".join(lines)

    def _unit_options_text(self, property_obj, units):
        lines = [f"Select Unit for {property_obj.property_name}"]
        for index, unit in enumerate(units, start=1):
            lines.append(f"{index}. {unit.unit_number}")
        lines.append(f"{len(units) + 1}. Back")
        return "\n".join(lines)

    def _handle_staff_agreement_link(self, message_log, text, staff_user):
        lowered = (text or "").strip().lower()
        if not (lowered.startswith("agreement view ") or lowered.startswith("agreement edit ")):
            return None
        link_type = WhatsAppExternalLinkToken.LINK_AGREEMENT_VIEW if lowered.startswith("agreement view ") else WhatsAppExternalLinkToken.LINK_AGREEMENT_EDIT
        raw_identifier = text.split(" ", 2)[2] if len(text.split(" ", 2)) == 3 else ""
        cnic_digits = normalize_cnic(raw_identifier)
        tenant = Tenant.objects.filter(cnic_digits=cnic_digits).first()
        if not tenant:
            tenant = next((item for item in Tenant.objects.all() if normalize_cnic(item.cnic) == cnic_digits), None)
        if not tenant:
            return "Tenant not found for that ID number."
        lease = (
            tenant.leases.select_related("unit__property")
            .filter(status="active")
            .order_by("-start_date", "-id")
            .first()
        )
        if not lease:
            return "No approved active lease found for that tenant."
        property_obj = lease.unit.property
        if not staff_can_access_property(staff_user, property_obj):
            log_staff_action(staff_user, message_log.phone_number, "agreement_link_property_blocked", "blocked", property=property_obj, tenant=tenant, lease=lease)
            return "You do not have WhatsApp access to that property's agreement."
        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        token = WhatsAppExternalLinkToken.objects.create(
            link_type=link_type,
            phone_number=message_log.phone_number,
            tenant=tenant,
            staff_user=staff_user,
            target_app_label="leases",
            target_model="lease",
            target_object_id=lease.pk,
            metadata={"lease_id": lease.pk, "property_id": property_obj.pk},
            expires_at=timezone.now() + timedelta(days=7),
        )
        url_name = "leases:public_agreement_view" if link_type == WhatsAppExternalLinkToken.LINK_AGREEMENT_VIEW else "leases:public_agreement_edit"
        link = f"{base_url.rstrip('/')}{reverse(url_name, args=[token.token])}"
        log_staff_action(staff_user, message_log.phone_number, "agreement_link_created", "allowed", property=property_obj, tenant=tenant, lease=lease, link_type=link_type)
        label = "Agreement view link" if link_type == WhatsAppExternalLinkToken.LINK_AGREEMENT_VIEW else "Agreement edit link"
        return f"{label} created.\n\nLink:\n{link}"

    def _consume_tenant_upload_type(self, message_log, conversation, text, selected_lease):
        if conversation.pending_state != "tenant_upload_type":
            return None
        media = PendingWhatsAppMedia.objects.filter(
            pk=conversation.context.get("pending_media_id"),
            status=PendingWhatsAppMedia.STATUS_PENDING,
        ).first()
        if not media:
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "pending_media_id")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return None

        purpose = _upload_purpose_from_text(text)
        if purpose == "cancel":
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "pending_media_id")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return "Upload cancelled. The file remains staged for admin review.", "upload_cancelled", {"pending_media_id": media.pk}
        if not purpose:
            return (
                "Please reply with a number:\n\n"
                "1 Property Photo\n2 Unit Photo\n3 Lease Photo\n4 Tenant Document\n5 Maintenance Photo\n6 Payment Receipt\n7 Police Verification\n8 Cancel",
                "upload_type_retry",
                {"lease": selected_lease, "pending_media_id": media.pk},
            )

        if purpose == "police_verification":
            lease = selected_lease or media.lease
            if not lease:
                return (
                    "Please select your lease first, then send Police Verification again.",
                    "lease_lookup",
                    {"pending_media_id": media.pk},
                )
            media.purpose = PendingWhatsAppMedia.PURPOSE_LEASE
            media.lease = lease
            media.tenant = lease.tenant
            media.property = lease.unit.property
            media.unit = lease.unit
            media.ai_notes = f"{media.ai_notes} Staged as police verification.".strip()
            media.save(update_fields=["purpose", "lease", "tenant", "property", "unit", "ai_notes", "updated_at"])
            if not media.file:
                return (
                    "We received the police verification message, but the file download was not available. Please resend the PDF/image.",
                    "police_verification_media_missing",
                    {"lease": lease, "pending_media_id": media.pk},
                )
            create_pending_police_submission(
                lease,
                media.file,
                PendingPoliceVerificationSubmission.SOURCE_WHATSAPP,
                phone=message_log.phone_number,
                notes="Selected from tenant upload menu.",
                whatsapp_media=media,
            )
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "pending_media_id")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return (
                "Police verification file received and sent for staff approval.",
                "police_verification_media",
                {"lease": lease, "pending_media_id": media.pk},
            )

        media.purpose = purpose
        media.lease = selected_lease or media.lease
        media.tenant = getattr(media.lease, "tenant", None)
        media.property = getattr(getattr(media.lease, "unit", None), "property", None)
        media.unit = getattr(media.lease, "unit", None)
        media.save(update_fields=["purpose", "lease", "tenant", "property", "unit", "updated_at"])
        conversation.pending_state = ""
        self._clear_context_keys(conversation, "pending_media_id")
        conversation.save(update_fields=["pending_state", "context", "updated_at"])

        if purpose == PendingWhatsAppMedia.PURPOSE_PAYMENT:
            return self._stage_payment(message_log, conversation, selected_lease, media, text)
        if purpose == PendingWhatsAppMedia.PURPOSE_MAINTENANCE:
            pending = create_pending_maintenance(message_log, conversation, selected_lease, media=media)
            notify_staff_pending_request("maintenance", pending)
            return (
                "We received your maintenance photo. Please share the issue type and urgency if not already included.",
                "maintenance_media",
                {"lease": selected_lease, "pending_maintenance_id": pending.pk},
            )
        if purpose == PendingWhatsAppMedia.PURPOSE_OTHER:
            notify_staff_pending_request("upload", media)
            return (
                "Thanks. Your upload is staged for admin review.",
                "media_pending",
                {"lease": selected_lease, "pending_media_id": media.pk},
            )
        notify_staff_pending_request("upload", media)
        return (
            "Thanks. Your upload is staged for admin review before attaching it to your lease.",
            "media_pending",
            {"lease": selected_lease, "pending_media_id": media.pk},
        )

    def _consume_staff_upload_type(self, message_log, conversation, text, staff_user):
        if conversation.pending_state != "staff_upload_type":
            return None
        media = PendingWhatsAppMedia.objects.filter(
            pk=conversation.context.get("pending_media_id"),
            status=PendingWhatsAppMedia.STATUS_PENDING,
        ).first()
        if not media:
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "pending_media_id", "staff_upload_hint")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return None

        purpose = _upload_purpose_from_text(text)
        if purpose == "cancel":
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "pending_media_id", "staff_upload_hint")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            log_staff_action(staff_user, message_log.phone_number, "upload_type_cancelled", "pending", pending_media_id=media.pk)
            return "Upload cancelled. The file remains staged for admin review.", "staff_upload_cancelled", {"pending_media_id": media.pk}
        if not purpose:
            return upload_type_menu_text(), "upload_type_retry", {"pending_media_id": media.pk}

        if purpose == "police_verification":
            if not media.lease_id:
                return (
                    "Police verification uploads need a lease. Please attach this file from the tenant lease context or use the pending approvals screen.",
                    "upload_type_retry",
                    {"pending_media_id": media.pk},
                )
            media.purpose = PendingWhatsAppMedia.PURPOSE_LEASE
            media.ai_notes = f"{media.ai_notes} Staged as police verification by staff upload menu.".strip()
            media.save(update_fields=["purpose", "ai_notes", "updated_at"])
            if media.file:
                create_pending_police_submission(
                    media.lease,
                    media.file,
                    PendingPoliceVerificationSubmission.SOURCE_WHATSAPP,
                    phone=message_log.phone_number,
                    notes="Selected from staff upload menu.",
                    whatsapp_media=media,
                )
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "pending_media_id", "staff_upload_hint")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return (
                "Police verification file received and sent for approval.",
                "police_verification_media",
                {"pending_media_id": media.pk},
            )

        media.purpose = purpose
        media.save(update_fields=["purpose", "updated_at"])
        conversation.pending_state = ""
        self._clear_context_keys(conversation, "pending_media_id", "staff_upload_hint")
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        log_staff_action(
            staff_user,
            message_log.phone_number,
            "upload_type_selected",
            "pending",
            pending_media_id=media.pk,
            purpose=purpose,
        )
        return (
            "Upload type saved. The file is staged for admin review and attachment.\n\n"
            f"Type: {media.get_purpose_display()}",
            "staff_upload_type",
            {"pending_media_id": media.pk},
        )

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

    def _consume_payment_apply_lookup(self, message_log, conversation, text, identity):
        pending = PendingWhatsAppPayment.objects.filter(
            pk=conversation.context.get("pending_payment_id"),
            status=PendingWhatsAppPayment.STATUS_PENDING,
        ).first()
        if not pending:
            conversation.pending_state = ""
            self._clear_context_keys(
                conversation,
                "pending_payment_id",
                "pending_media_id",
                "payment_apply_lease_options",
                "payment_apply_retry_count",
            )
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return (
                "I could not find the pending payment receipt anymore. Please send the receipt again if needed.",
                "payment_apply_missing",
                {},
            )

        if conversation.pending_state == "payment_apply_lease_selection":
            lease = self._payment_apply_option_from_number(text, conversation)
            if not lease:
                return (
                    "Please reply with one of the lease numbers shown, or type CANCEL to keep this for admin review.",
                    "payment_apply_selection_retry",
                    {"pending_payment_id": pending.pk},
                )
            return self._assign_pending_payment_to_lease(conversation, pending, lease)

        matches = self._find_leases_for_payment_lookup(text, identity)
        if len(matches) == 1:
            return self._assign_pending_payment_to_lease(conversation, pending, matches[0])
        if len(matches) > 1:
            conversation.pending_state = "payment_apply_lease_selection"
            conversation.context["payment_apply_lease_options"] = [lease.pk for lease in matches[:9]]
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            lines = ["I found more than one possible lease.", "", "Where should this payment be applied?"]
            for index, lease in enumerate(matches[:9], start=1):
                lines.append(f"{index}. {lease.tenant.get_full_name()} - {lease.unit.property.property_name} / {lease.unit.unit_number}")
            lines.append("")
            lines.append("Reply with a number, or CANCEL for admin review.")
            return "\n".join(lines), "payment_apply_selection", {"pending_payment_id": pending.pk}

        retry_count = int(conversation.context.get("payment_apply_retry_count") or 0) + 1
        conversation.context["payment_apply_retry_count"] = retry_count
        if retry_count >= 2:
            pending.ai_notes = (pending.ai_notes + "\nCould not resolve tenant/lease from sender reply; left for manual admin review.").strip()
            pending.save(update_fields=["ai_notes", "updated_at"])
            conversation.pending_state = ""
            self._clear_context_keys(
                conversation,
                "pending_payment_id",
                "pending_media_id",
                "payment_apply_lease_options",
                "payment_apply_retry_count",
            )
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return (
                "I could not match that receipt to a lease. It is saved for admin review.\n\n"
                "Please send Property, Unit, Tenant Name, and Contact Number if you want to add more detail.",
                "payment_apply_manual_review",
                {"pending_payment_id": pending.pk},
            )
        conversation.save(update_fields=["context", "updated_at"])
        return (
            "I could not find a matching active lease from that reply.\n\n"
            "Please send Tenant Name, CNIC, Property + Unit, or Contact Number.\n"
            "Reply CANCEL to keep it for admin review.",
            "payment_apply_retry",
            {"pending_payment_id": pending.pk},
        )

    def _assign_pending_payment_to_lease(self, conversation, pending, lease):
        pending.tenant = lease.tenant
        pending.lease = lease
        pending.property = lease.unit.property
        pending.unit = lease.unit
        pending.ai_notes = (pending.ai_notes + "\nSender identified the lease for this pending payment.").strip()
        pending.save(update_fields=["tenant", "lease", "property", "unit", "ai_notes", "updated_at"])
        conversation.selected_lease = lease
        conversation.selected_property = lease.unit.property
        conversation.selected_unit = lease.unit
        conversation.tenant = lease.tenant
        conversation.pending_state = "pending_payment_confirmation"
        self._clear_context_keys(
            conversation,
            "pending_media_id",
            "payment_apply_lease_options",
            "payment_apply_retry_count",
        )
        conversation.context["pending_payment_id"] = pending.pk
        conversation.save(update_fields=[
            "selected_lease",
            "selected_property",
            "selected_unit",
            "tenant",
            "pending_state",
            "context",
            "updated_at",
        ])
        return _payment_confirmation_text(pending), "payment_pending", {"lease": lease, "tenant": lease.tenant, "pending_payment_id": pending.pk}

    def _payment_apply_option_from_number(self, text, conversation):
        try:
            selected_index = int((text or "").strip()) - 1
        except ValueError:
            return None
        option_ids = conversation.context.get("payment_apply_lease_options") or []
        if selected_index < 0 or selected_index >= len(option_ids):
            return None
        return Lease.objects.select_related("tenant", "unit__property").filter(pk=option_ids[selected_index]).first()

    def _find_leases_for_payment_lookup(self, text, identity):
        query_text = (text or "").strip()
        digits = "".join(ch for ch in query_text if ch.isdigit())
        cnic_digits = normalize_cnic(query_text)
        lowered = query_text.lower()
        today = timezone.localdate()
        leases = Lease.objects.select_related("tenant", "unit__property").filter(
            status="active",
            start_date__lte=today,
            end_date__gte=today,
        )
        query = Q()
        if cnic_digits:
            query |= Q(tenant__cnic_digits=cnic_digits) | Q(tenant__cnic__icontains=query_text)
        if digits:
            suffix = digits[-10:]
            query |= (
                Q(tenant__phone__icontains=suffix)
                | Q(tenant__phone2__icontains=suffix)
                | Q(tenant__phone3__icontains=suffix)
                | Q(tenant__employer_phone__icontains=suffix)
                | Q(tenant__reference_phone_1__icontains=suffix)
                | Q(tenant__reference_phone_2__icontains=suffix)
                | Q(tenant__emergency_contact_phone__icontains=suffix)
                | Q(unit__unit_number__icontains=query_text)
            )
        for token in [item for item in lowered.replace(",", " ").split() if len(item) >= 2]:
            query |= Q(tenant__first_name__icontains=token) | Q(tenant__last_name__icontains=token)
            query |= Q(unit__property__property_name__icontains=token) | Q(unit__unit_number__icontains=token)
        if not query:
            return []
        matches = list(leases.filter(query).distinct().order_by("unit__property__property_name", "unit__unit_number")[:10])
        if not matches and identity.active_leases:
            return list(identity.active_leases)
        return matches

    def _stage_payment(self, message_log, conversation, lease, media, text, ocr_json=None):
        ocr_json = ocr_json or (run_payment_ocr(media, self.ai_config) if media else extract_payment_text_fields(text))
        if not ocr_json.get("amount"):
            extracted = extract_payment_text_fields((ocr_json.get("text") or "") + "\n" + (text or ""))
            ocr_json.update(extracted)
        match = match_payment_to_active_lease(message_log.phone_number, ocr_json)
        matched_lease = lease or match.get("lease")
        duplicate_note = _payment_duplicate_note(matched_lease, ocr_json)
        ai_notes = "\n".join(part for part in [match.get("notes", ""), duplicate_note] if part).strip()
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
            ai_notes=ai_notes,
            original_whatsapp_message=message_log,
            conversation=conversation,
        )
        conversation.pending_state = "pending_payment_confirmation"
        self._clear_context_keys(
            conversation,
            "pending_media_id",
            "payment_apply_lease_options",
            "payment_apply_retry_count",
        )
        conversation.context["pending_payment_id"] = pending.pk
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        notify_staff_pending_request("payment", pending)
        return _payment_confirmation_text(pending), "payment_pending", {"lease": matched_lease, "tenant": getattr(matched_lease, "tenant", None), "pending_payment_id": pending.pk}

    def _stage_unassigned_payment(self, message_log, conversation, media, text, ocr_json):
        pending = PendingWhatsAppPayment.objects.create(
            phone=message_log.phone_number,
            screenshot=getattr(media, "file", None),
            ocr_json=_json_safe(ocr_json),
            amount=ocr_json.get("amount"),
            date=ocr_json.get("date"),
            reference=ocr_json.get("reference", ""),
            bank_information=ocr_json.get("bank_information") or {"channel": _payment_channel(text or ocr_json.get("raw_text", ""))},
            ai_confidence=int(ocr_json.get("confidence") or 0),
            ai_notes="AI classified this upload as a payment receipt, but no lease was matched automatically.",
            original_whatsapp_message=message_log,
            conversation=conversation,
        )
        conversation.pending_state = "payment_apply_lookup"
        conversation.context["pending_payment_id"] = pending.pk
        conversation.context["pending_media_id"] = media.pk
        conversation.context["payment_apply_retry_count"] = 0
        conversation.context.pop("payment_apply_lease_options", None)
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        notify_staff_pending_request("payment", pending)
        channel = (pending.bank_information or {}).get("channel") or "Not detected"
        return (
            "I read this image as a payment receipt.\n\n"
            f"Amount: {pending.amount or 'Not detected'}\n"
            f"Date: {pending.date or 'Not detected'}\n"
            f"Payment Channel: {channel}\n"
            f"Reference: {pending.reference or 'Not detected'}\n\n"
            "Where should this payment be applied?\n"
            "Reply with tenant name, CNIC, property/unit, or lease details.",
            "payment_apply_lookup",
            {"pending_payment_id": pending.pk, "pending_media_id": media.pk},
        )

    def _tenant_welcome_menu(self, lease):
        ctx = build_lease_context(lease)
        tenant_name = ctx.tenant.get_full_name() or str(ctx.tenant)
        return (
            f"Welcome {tenant_name}. Active lease: {ctx.property.property_name} / {ctx.unit.unit_number}.\n\n"
            f"{tenant_menu_text()}"
        )

    def _consume_tenant_invoice_payment_menu(self, conversation, text, lease):
        if conversation.pending_state != "tenant_invoice_payment_menu":
            return None

        lowered = (text or "").strip().lower()
        if lowered in {"6", "back", "menu", "main menu"}:
            conversation.pending_state = ""
            conversation.save(update_fields=["pending_state", "updated_at"])
            return self._tenant_welcome_menu(lease), "tenant_menu", {"lease": lease, "tenant": lease.tenant}
        if lowered in {"1", "outstanding", "outstanding invoice", "outstanding invoices", "invoice", "invoices"}:
            conversation.pending_state = ""
            conversation.save(update_fields=["pending_state", "updated_at"])
            return self._outstanding_invoices_reply(lease), "outstanding_invoices", {"lease": lease, "tenant": lease.tenant}
        if lowered in {"2", "recent", "recent payments", "payments", "payment history"}:
            conversation.pending_state = ""
            conversation.save(update_fields=["pending_state", "updated_at"])
            return self._lease_reply("payments", lease), "payments", {"lease": lease, "tenant": lease.tenant}
        if lowered in {"3", "ledger", "full ledger", "statement"}:
            conversation.pending_state = ""
            conversation.save(update_fields=["pending_state", "updated_at"])
            return self._ledger_link_reply(lease), "ledger", {"lease": lease, "tenant": lease.tenant}
        if lowered in {"4", "upload", "upload receipt", "receipt", "photo"}:
            conversation.pending_state = ""
            conversation.save(update_fields=["pending_state", "updated_at"])
            return (
                "Please upload the payment receipt here, and I will attach it to your active lease for admin review.",
                "upload_prompt",
                {"lease": lease, "tenant": lease.tenant},
            )
        if lowered in {"5", "latest invoice", "last invoice", "request last invoice", "request latest invoice"}:
            conversation.pending_state = ""
            conversation.save(update_fields=["pending_state", "updated_at"])
            return self._latest_invoice_reply(lease), "latest_invoice", {"lease": lease, "tenant": lease.tenant}

        return (
            "Please reply with a number from the Invoice / Payment menu.\n\n"
            + _tenant_invoice_payment_menu_text(),
            "tenant_invoice_payment_menu_retry",
            {"lease": lease, "tenant": lease.tenant},
        )

    def _start_guided_maintenance(self, message_log, conversation, text, lease):
        issue, urgency, confidence = detect_maintenance_issue(text)
        conversation.pending_state = "tenant_maintenance_details"
        conversation.context["maintenance_draft"] = {
            "initial_text": text or "",
            "issue_type": issue,
            "urgency": urgency,
            "confidence": confidence,
        }
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return (
            "Maintenance request\n\n"
            f"I read this as: {issue} ({urgency}).\n\n"
            "Please reply with the location and details in one message.\n"
            "Example: Bathroom pipe leaking, urgent, water is spreading.\n\n"
            "You can also send a clear photo or short video after this.",
            "maintenance_details_prompt",
            {"lease": lease, "tenant": lease.tenant},
        )

    def _consume_guided_maintenance(self, message_log, conversation, text, lease):
        if conversation.pending_state != "tenant_maintenance_details":
            return None
        lowered = (text or "").strip().lower()
        if lowered in {"cancel", "back", "menu", "main menu"}:
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "maintenance_draft")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return self._tenant_welcome_menu(lease), "maintenance_cancelled", {"lease": lease, "tenant": lease.tenant}

        draft = conversation.context.get("maintenance_draft") or {}
        combined_text = "\n".join(part for part in [draft.get("initial_text"), text] if part).strip()
        payload = dict(message_log.payload or {})
        payload["text"] = {"body": combined_text}
        message_log.payload = payload
        issue, urgency, confidence = detect_maintenance_issue(combined_text)
        pending = create_pending_maintenance(message_log, conversation, lease)
        pending.issue_type = issue if issue != "Other" else (draft.get("issue_type") or pending.issue_type)
        pending.urgency = "urgent" if _looks_urgent(combined_text) else (urgency or draft.get("urgency") or pending.urgency)
        pending.description = combined_text
        pending.ai_confidence = max(int(confidence or 0), int(draft.get("confidence") or 0), pending.ai_confidence or 0)
        pending.ai_notes = "Guided WhatsApp maintenance request staged for admin approval."
        pending.save(update_fields=["issue_type", "urgency", "description", "ai_confidence", "ai_notes", "updated_at"])
        conversation.pending_state = "pending_maintenance"
        conversation.context["pending_maintenance_id"] = pending.pk
        self._clear_context_keys(conversation, "maintenance_draft")
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        notify_staff_pending_request("maintenance", pending)
        return (
            "Maintenance request saved for admin review.\n\n"
            f"Issue: {pending.issue_type or 'Other'}\n"
            f"Urgency: {pending.urgency or 'normal'}\n\n"
            "Please send a clear photo or short video if available.",
            "maintenance_request",
            {"lease": lease, "tenant": lease.tenant, "pending_maintenance_id": pending.pk},
        )

    def _handle_tenant_data_intent(self, message_log, conversation, intent, text, lease):
        if intent == "latest_invoice":
            return self._latest_invoice_reply(lease), "latest_invoice", {"lease": lease, "tenant": lease.tenant}
        if intent == "payment_receipt":
            return self._latest_payment_receipt_reply(lease), "payment_receipt", {"lease": lease, "tenant": lease.tenant}
        if intent == "lease_documents":
            return self._lease_documents_reply(lease), "lease_documents", {"lease": lease, "tenant": lease.tenant}
        if intent == "agreement":
            return self._agreement_link_reply(message_log, lease), "agreement", {"lease": lease, "tenant": lease.tenant}
        if intent == "maintenance_status":
            return self._maintenance_status_reply(lease), "maintenance_status", {"lease": lease, "tenant": lease.tenant}
        if intent == "meter":
            return self._meter_reading_reply(lease), "meter", {"lease": lease, "tenant": lease.tenant}
        if intent == "family":
            return self._family_list_reply(lease, message_log.phone_number), "family_list", {"lease": lease, "tenant": lease.tenant}
        if intent == "police_verification":
            link, url = create_police_verification_link(None, lease, phone_number=message_log.phone_number)
            conversation.pending_state = "police_verification_upload"
            conversation.context["police_verification_lease_id"] = lease.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return build_police_whatsapp_message(None, lease, url), "police_verification_link", {"lease": lease, "link_id": link.pk}
        if intent == "contact":
            return "Please tell me what you need, and our office team will follow up.", "contact_office", {"lease": lease, "tenant": lease.tenant}
        if intent == "suggestion":
            return self._start_suggestion_capture(conversation, "WhatsApp Tenant"), "suggestion_prompt", {"lease": lease, "tenant": lease.tenant}
        if intent in {"move_out", "renewal"}:
            return self._sensitive_lease_request_reply(message_log, conversation, intent, text, lease)
        return None

    def _outstanding_invoices_reply(self, lease):
        invoices = list(
            Invoice.objects.filter(lease=lease)
            .exclude(status__in=["paid", "cancelled"])
            .order_by("-due_date", "-issue_date")[:5]
        )
        if not invoices:
            return "No outstanding invoices are recorded for your active lease."
        lines = ["Outstanding invoices:"]
        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        for invoice in invoices:
            amount = invoice.amount or Decimal("0.00")
            token = make_public_invoice_token(invoice.pk)
            link = f"{base_url.rstrip('/')}{reverse('invoices:public_invoice_detail', args=[token])}"
            lines.append(
                f"{invoice.invoice_number}: Rs. {amount} due {invoice.due_date} ({invoice.get_status_display()})\n{link}"
            )
        return "\n".join(lines)

    def _latest_invoice_reply(self, lease):
        invoice = (
            Invoice.objects.filter(lease=lease)
            .exclude(status="cancelled")
            .order_by("-issue_date", "-id")
            .first()
        )
        if not invoice:
            return "No invoice is recorded for your active lease yet."
        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        token = make_public_invoice_token(invoice.pk)
        link = f"{base_url.rstrip('/')}{reverse('invoices:public_invoice_detail', args=[token])}"
        return (
            "Latest invoice\n\n"
            f"Invoice: {invoice.invoice_number}\n"
            f"Amount: Rs. {invoice.amount or Decimal('0.00')}\n"
            f"Due Date: {invoice.due_date or '-'}\n"
            f"Status: {invoice.get_status_display()}\n\n"
            f"Link:\n{link}"
        )

    def _latest_payment_receipt_reply(self, lease):
        from payments.models import Payment

        payment = (
            Payment.objects.filter(lease=lease)
            .select_related("payment_method")
            .order_by("-payment_date", "-id")
            .first()
        )
        if not payment:
            return "No payment receipt is recorded for your active lease yet."
        return (
            "Latest payment receipt\n\n"
            f"Date: {payment.payment_date}\n"
            f"Amount: Rs. {payment.amount}\n"
            f"Method: {getattr(payment.payment_method, 'name', '') or '-'}\n"
            f"Reference: {payment.reference_number or '-'}\n\n"
            "Please contact the office if you need the receipt PDF resent."
        )

    def _ledger_link_reply(self, lease):
        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        link = create_public_ledger_link(lease)
        ledger_link = public_ledger_url(base_url, link)
        return f"Full ledger:\n{ledger_link}"

    def _family_public_link_reply_url(self, lease, phone_number=""):
        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        link = WhatsAppExternalLinkToken.objects.create(
            link_type=WhatsAppExternalLinkToken.LINK_LEASE_FAMILY_ADD,
            phone_number=phone_number or "",
            tenant=lease.tenant,
            target_app_label="leases",
            target_model="Lease",
            target_object_id=lease.pk,
            metadata={"purpose": "lease_family_member_add_or_remove", "source": "whatsapp"},
            expires_at=timezone.now() + timedelta(hours=48),
        )
        return f"{base_url.rstrip('/')}{reverse('leases:public_lease_family_add', args=[link.token])}"

    def _family_list_reply(self, lease, phone_number=""):
        members = list(
            lease.family_members.select_related("family_member", "relationship_type")
            .order_by("sort_order", "family_member__first_name", "family_member__last_name")
        )
        lines = [
            "Family members linked to your lease:",
            f"Lease: {lease.unit.property.property_name} / {lease.unit.unit_number}",
        ]
        if members:
            for index, member in enumerate(members, start=1):
                tenant = member.family_member
                relation = member.relationship_type.name if member.relationship_type else member.relationship
                phone = f" - {format_phone(tenant.phone)}" if tenant.phone else ""
                lines.append(f"{index}. {tenant.get_full_name()} ({relation}){phone}")
        else:
            lines.append("No family members are linked yet.")
        lines.extend([
            "",
            "To add a family member or request removal, open this link:",
            self._family_public_link_reply_url(lease, phone_number),
            "Link is valid for 48 hours and changes need admin approval.",
        ])
        return "\n".join(lines)

    def _lease_documents_reply(self, lease):
        from core.models import GlobalSettings
        from leases.models import LeaseDocument, LeaseFileShareLink

        docs = list(
            LeaseDocument.objects.filter(lease=lease, is_active=True)
            .order_by("category", "-uploaded_at", "-id")[:10]
        )
        if not docs:
            return "No lease documents are currently available for your active lease."
        settings_obj = GlobalSettings.get_solo()
        valid_days = max(1, getattr(settings_obj, "lease_file_share_valid_days", 7) or 7)
        link = LeaseFileShareLink.objects.create(
            lease=lease,
            document=None,
            expires_at=timezone.now() + timedelta(days=valid_days),
        )
        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        share_url = f"{base_url.rstrip('/')}{reverse('leases:public_lease_files_share', args=[link.token])}"
        lines = [
            "Lease documents available:",
            f"Lease: {lease.unit.property.property_name} / {lease.unit.unit_number}",
        ]
        for index, document in enumerate(docs[:5], start=1):
            lines.append(f"{index}. {document.category_label} - {document.display_name or document.original_filename or 'Document'}")
        if len(docs) > 5:
            lines.append(f"...and {len(docs) - 5} more")
        lines.extend(["", "Secure documents link:", share_url, f"Link expires in {valid_days} day(s)."])
        return "\n".join(lines)

    def _agreement_link_reply(self, message_log, lease):
        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        token = WhatsAppExternalLinkToken.objects.create(
            link_type=WhatsAppExternalLinkToken.LINK_AGREEMENT_VIEW,
            phone_number=message_log.phone_number,
            tenant=lease.tenant,
            target_app_label="leases",
            target_model="lease",
            target_object_id=lease.pk,
            metadata={"lease_id": lease.pk, "source": "whatsapp_tenant"},
            expires_at=timezone.now() + timedelta(hours=48),
        )
        link = f"{base_url.rstrip('/')}{reverse('leases:public_agreement_view', args=[token.token])}"
        return (
            "Lease agreement\n\n"
            f"Lease: {lease.unit.property.property_name} / {lease.unit.unit_number}\n"
            f"Status: {'Signed' if lease.is_agreement_signed else 'Not marked signed'}\n\n"
            f"View agreement:\n{link}\n"
            "Link is valid for 48 hours."
        )

    def _maintenance_status_reply(self, lease):
        requests = list(
            MaintenanceRequest.objects.filter(lease=lease)
            .exclude(status__in=["completed", "cancelled"])
            .order_by("-reported_date", "-id")[:5]
        )
        if not requests:
            recent = list(
                MaintenanceRequest.objects.filter(lease=lease)
                .order_by("-reported_date", "-id")[:3]
            )
            if not recent:
                return "No maintenance requests are recorded for your active lease."
            lines = ["Recent maintenance requests:"]
            for index, item in enumerate(recent, start=1):
                lines.append(f"{index}. {item.title} - {item.get_status_display()} - {item.reported_date}")
            return "\n".join(lines)
        lines = ["Open maintenance requests:"]
        for index, item in enumerate(requests, start=1):
            lines.append(f"{index}. {item.title} - {item.get_status_display()} - {item.reported_date}")
        return "\n".join(lines)

    def _meter_reading_reply(self, lease):
        try:
            from smart_meter.models import LiveReading, MeterInstallation
        except Exception:
            return "Please send your latest meter reading or utility bill photo. Our team will review it."

        installations = list(
            MeterInstallation.objects.filter(
                lease=lease,
                is_active=True,
                end_date__isnull=True,
            ).select_related("meter", "unit")[:5]
        )
        if not installations:
            return "No active smart meter is linked to your lease. You can send a meter photo here for office review."
        lines = ["Latest meter reading:"]
        for installation in installations:
            live = LiveReading.objects.filter(meter=installation.meter).first()
            if live:
                lines.append(
                    f"Meter {installation.meter.meter_number}: {live.total_energy or '-'} kWh, "
                    f"Balance Rs. {live.balance if live.balance is not None else '-'} "
                    f"({timezone.localtime(live.ts):%Y-%m-%d %H:%M})"
                )
            else:
                lines.append(f"Meter {installation.meter.meter_number}: no live reading found.")
        lines.append("")
        lines.append("You can also send a meter photo here for office review.")
        return "\n".join(lines)

    def _inspection_sheet_reply(self, lease):
        from leases.models_inspections import LeaseInspection

        inspections = list(
            LeaseInspection.objects.select_related("inspection_type", "property", "unit", "tenant")
            .filter(lease=lease)
            .order_by("-inspection_date", "-id")[:3]
        )
        if not inspections:
            return (
                "No inspection sheet is recorded for your active lease yet.\n\n"
                "Please contact the office if you need a new inspection."
            )

        base_url = getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://tms.sonazconsultancy.online"
        latest = inspections[0]
        lines = [
            "Inspection sheet",
            f"Lease: {lease.unit.property.property_name} / {lease.unit.unit_number}",
            "",
            "Recent inspections:",
        ]
        for index, inspection in enumerate(inspections, start=1):
            lines.append(
                f"{index}. {inspection.inspection_type} - {inspection.inspection_date} - {inspection.get_status_display()}"
            )

        if latest.status != LeaseInspection.STATUS_APPROVED:
            if not latest.public_link_valid:
                latest.public_is_active = True
                latest.public_expires_at = timezone.now() + timedelta(hours=48)
                latest.save(update_fields=["public_is_active", "public_expires_at", "updated_at"])
                latest.add_audit("whatsapp_tenant_public_link_generated", extra={"hours": 48})
            public_link = f"{base_url.rstrip('/')}{reverse('leases:public_inspection_sign', args=[latest.public_token])}"
            lines.extend([
                "",
                "Open latest inspection sheet:",
                public_link,
                "Link is valid for 48 hours.",
            ])
        else:
            lines.extend([
                "",
                "The latest inspection sheet is approved. Please contact the office if you need a signed copy.",
            ])
        return "\n".join(lines)

    def _sensitive_lease_request_reply(self, message_log, conversation, intent, text, lease):
        source = "WhatsApp Tenant Move Out" if intent == "move_out" else "WhatsApp Tenant Renewal"
        ticket = self._create_suggestion_ticket(message_log, conversation, text, identify_sender(message_log.phone_number))
        if ticket:
            return (
                "Thanks. Your request has been sent to the office for review.\n\n"
                f"Request: {'Move-out / vacating' if intent == 'move_out' else 'Lease renewal'}\n"
                f"Reference #{ticket.id}"
            ), intent, {"lease": lease, "tenant": lease.tenant, "suggestion_id": ticket.id, "source": source}
        return (
            "Thanks. We received your request and our office team will review it shortly.",
            intent,
            {"lease": lease, "tenant": lease.tenant, "source": source},
        )

    def _lease_reply(self, intent, lease):
        ctx = build_lease_context(lease)
        if intent == "payments":
            ledger_link = self._ledger_link_reply(lease).split("\n", 1)[1]
            if not ctx.recent_payments:
                return (
                    "No recent payments are recorded for your active lease.\n\n"
                    f"Full ledger:\n{ledger_link}"
                )
            lines = ["Recent payments:"]
            for payment in ctx.recent_payments:
                lines.append(f"{payment.payment_date}: Rs. {payment.amount} ({payment.reference_number or 'no reference'})")
            lines.extend(["", "Full ledger:", ledger_link])
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

    def _openai_text_intent(self, text):
        if self.ai_config.provider != "openai" or not self.ai_config.openai_api_key_configured:
            return "general"
        clean_text = (text or "").strip()
        if not clean_text:
            return "general"
        try:
            from openai import OpenAI
        except ImportError:
            return "general"

        prompt = (
            "Classify this property-management WhatsApp tenant message into one intent. "
            "Return only JSON like {\"intent\":\"inspection\"}. "
            "Allowed intents: "
            + ", ".join(sorted(SAFE_TEXT_INTENTS))
            + ".\n\n"
            f"Message: {clean_text[:500]}"
        )
        try:
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            response = client.responses.create(
                model=self.ai_config.model,
                input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            )
            parsed = _parse_json_object(getattr(response, "output_text", "") or "")
            intent = (parsed.get("intent") or "general").strip().lower()
            return intent if intent in SAFE_TEXT_INTENTS else "general"
        except Exception:
            logger.exception("OpenAI WhatsApp text intent classification failed.")
            return "general"

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
    lowered = (text or "").strip().lower()
    tenant_menu_number_map = {
        "1": "balance",
        "3": "maintenance",
        "4": "lease",
    }
    if lowered in tenant_menu_number_map:
        return tenant_menu_number_map[lowered]
    if _looks_like_suggestion(lowered):
        return "suggestion"
    if _looks_like_contact(lowered):
        return "contact"
    if _looks_like_police_verification(lowered):
        return "police_verification"
    if _looks_like_inspection_request(lowered):
        return "inspection"
    if _looks_like_maintenance_status(lowered):
        return "maintenance_status"
    if _looks_like_latest_invoice(lowered):
        return "latest_invoice"
    if _looks_like_payment_receipt_request(lowered):
        return "payment_receipt"
    if _looks_like_lease_documents(lowered):
        return "lease_documents"
    if _looks_like_agreement_request(lowered):
        return "agreement"
    if _looks_like_meter_request(lowered):
        return "meter"
    if _looks_like_family_request(lowered):
        return "family"
    if _looks_like_move_out(lowered):
        return "move_out"
    if _looks_like_renewal(lowered):
        return "renewal"
    if any(word in lowered for word in ("balance", "outstanding", "rent due", "dues", "baqaya", "remaining", "pending rent", "remaining rent")):
        return "balance"
    if (
        _contains_any_word(lowered, {"available", "availability", "vacancy", "vacant", "khali", "khaali"})
        or any(phrase in lowered for phrase in ("flat available", "rent available", "empty flat", "available unit"))
    ):
        return "availability"
    if any(word in lowered for word in ("payment", "paid", "receipt", "screenshot", "transfer", "easypaisa", "jazzcash", "raast", "bank transfer", "rent paid", "kiraya jama", "jama")):
        return "payment"
    issue, _, confidence = detect_maintenance_issue(lowered)
    if issue != "Other" or confidence >= 75 or any(word in lowered for word in ("maintenance", "repair", "pani", "water", "bijli", "electric", "light", "leak", "not working", "kharab")):
        return "maintenance"
    if any(word in lowered for word in ("history", "payments", "paid before", "payment history", "ledger", "statement")):
        return "payments"
    if any(word in lowered for word in ("lease", "agreement", "contract", "expiry", "expire", "renewal", "deposit", "kiraya nama")):
        return "lease"
    return "general"


def _looks_like_inspection_request(text):
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    inspection_words = (
        "inspection",
        "inspect",
        "condition report",
        "condition sheet",
        "pcr",
        "property condition",
        "move in report",
        "move-out report",
        "move out report",
    )
    if any(word in lowered for word in inspection_words):
        return True
    return "sheet" in lowered and any(word in lowered for word in ("property", "unit", "lease", "flat", "room"))


def _looks_like_latest_invoice(text):
    lowered = (text or "").strip().lower()
    return any(
        phrase in lowered
        for phrase in (
            "latest invoice",
            "last invoice",
            "current invoice",
            "new invoice",
            "invoice copy",
            "send invoice",
            "invoice link",
            "rent bill",
            "bill copy",
        )
    )


def _looks_like_payment_receipt_request(text):
    lowered = (text or "").strip().lower()
    return any(
        phrase in lowered
        for phrase in (
            "payment receipt",
            "receipt copy",
            "rent receipt",
            "last receipt",
            "latest receipt",
            "paid receipt",
        )
    )


def _looks_like_lease_documents(text):
    lowered = (text or "").strip().lower()
    return any(
        phrase in lowered
        for phrase in (
            "lease document",
            "lease documents",
            "documents",
            "my files",
            "tenant files",
            "lease file",
            "lease files",
        )
    )


def _looks_like_agreement_request(text):
    lowered = (text or "").strip().lower()
    return any(
        phrase in lowered
        for phrase in (
            "agreement",
            "contract",
            "kiraya nama",
            "lease copy",
            "signed copy",
        )
    )


def _looks_like_maintenance_status(text):
    lowered = (text or "").strip().lower()
    return (
        any(word in lowered for word in ("status", "update", "progress", "pending"))
        and any(word in lowered for word in ("maintenance", "repair", "complaint", "issue", "request"))
    )


def _looks_like_meter_request(text):
    lowered = (text or "").strip().lower()
    return any(
        phrase in lowered
        for phrase in (
            "meter",
            "meter reading",
            "electric reading",
            "utility bill",
            "electric bill",
            "bijli bill",
            "kwh",
        )
    )


def _looks_like_family_request(text):
    lowered = (text or "").strip().lower()
    return any(word in lowered for word in ("family", "family member", "members", "dependent", "dependant"))


def _looks_like_police_verification(text):
    lowered = (text or "").strip().lower()
    return "police" in lowered or "verification" in lowered


def _looks_like_move_out(text):
    lowered = (text or "").strip().lower()
    return any(
        phrase in lowered
        for phrase in (
            "move out",
            "move-out",
            "vacate",
            "vacating",
            "leave flat",
            "leave unit",
            "end lease",
            "terminate lease",
        )
    )


def _looks_like_renewal(text):
    lowered = (text or "").strip().lower()
    return any(word in lowered for word in ("renew", "renewal", "extend lease", "extension"))


def _looks_like_contact(text):
    lowered = (text or "").strip().lower()
    return any(word in lowered for word in ("contact", "office", "call me", "speak to", "manager", "helpdesk"))


def _looks_like_suggestion(text):
    lowered = (text or "").strip().lower()
    return any(word in lowered for word in ("suggestion", "feedback", "advice", "advise", "idea", "complaint"))


def _contains_any_word(text, words):
    tokens = {token.strip(".,!?;:()[]{}") for token in (text or "").lower().split()}
    return bool(tokens & set(words))


def _tenant_invoice_payment_menu_text():
    return (
        "Invoice / Payment\n\n"
        "1. Outstanding invoices\n"
        "2. Recent payments\n"
        "3. View Ledger\n"
        "4. Upload receipt\n"
        "5. Request Last Invoice\n"
        "6. Back\n\n"
        "Reply with a number."
    )


def notify_staff_pending_request(request_type, pending):
    staff_numbers = _pending_request_staff_numbers(pending)
    if not staff_numbers:
        return
    message = _pending_request_staff_message(request_type, pending)
    service = WhatsAppService()
    for phone in staff_numbers:
        try:
            service.send_text(phone, message)
        except Exception:
            logger.exception("Failed to notify staff about pending WhatsApp %s #%s", request_type, getattr(pending, "pk", None))


def _pending_request_staff_numbers(pending):
    try:
        from core.models import GlobalSettings

        config = GlobalSettings.get_solo()
    except Exception:
        logger.exception("Could not load GlobalSettings for WhatsApp pending request notification.")
        config = None

    if config and not config.whatsapp_pending_request_notifications_enabled:
        return []

    configured = _split_configured_staff_numbers(
        getattr(config, "whatsapp_pending_request_staff_numbers", "") if config else ""
    )
    if configured:
        return configured

    property_obj = getattr(pending, "property", None)
    users = []
    if property_obj:
        users = [
            access.staff_user
            for access in getattr(property_obj, "whatsapp_staff_access", []).filter(is_active=True).select_related("staff_user")
            if access.staff_user and access.staff_user.is_active and access.staff_user.whatsapp_number
        ]
    if not users:
        User = get_user_model()
        users = list(User.objects.filter(is_active=True, is_staff=True, is_superuser=True).exclude(whatsapp_number="")[:5])
    return _unique_phone_numbers(user.whatsapp_number for user in users)


def _split_configured_staff_numbers(raw_numbers):
    cleaned = str(raw_numbers or "").replace(";", ",").replace("\n", ",")
    return [item.strip() for item in cleaned.split(",") if item.strip()]


def _unique_phone_numbers(numbers):
    seen = set()
    unique = []
    for number in numbers:
        normalized = WhatsAppService.normalize_phone_number(number)
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def _pending_request_staff_message(request_type, pending):
    target = _pending_target(pending)
    tenant = getattr(pending, "tenant", None)
    tenant_name = tenant.get_full_name() if tenant and hasattr(tenant, "get_full_name") else str(tenant or "Unmatched")
    lines = [
        "New pending WhatsApp request",
        "",
        f"Type: {request_type.title()}",
        f"Reference: #{getattr(pending, 'pk', '-')}",
        f"Tenant: {tenant_name or 'Unmatched'}",
        f"Property/Unit: {target}",
    ]
    amount = getattr(pending, "amount", None)
    if amount:
        lines.append(f"Amount: Rs. {amount}")
    issue = getattr(pending, "issue_type", "")
    if issue:
        lines.append(f"Issue: {issue}")
    lines.extend(["", "Reply Pending Requests to view the WhatsApp queue."])
    return "\n".join(lines)


def _pending_target(pending):
    property_obj = getattr(pending, "property", None)
    unit = getattr(pending, "unit", None)
    if property_obj and unit:
        return f"{property_obj.property_name} / {unit.unit_number}"
    if property_obj:
        return property_obj.property_name
    lease = getattr(pending, "lease", None)
    lease_unit = getattr(lease, "unit", None)
    lease_property = getattr(lease_unit, "property", None)
    if lease_property and lease_unit:
        return f"{lease_property.property_name} / {lease_unit.unit_number}"
    return "Unmatched"


def _add_tenant_menu_text():
    return (
        "Add Tenant\n\n"
        "1. Send public tenant registration link\n"
        "2. Create tenant draft by WhatsApp\n"
        "3. Back"
    )


def _staff_search_menu_text():
    return (
        "Search\n\n"
        "1. Search Tenant\n"
        "2. Search Lease\n"
        "3. Search Invoice\n"
        "4. Search Property\n"
        "5. Search Unit\n"
        "6. Back\n\n"
        "Reply with a number."
    )


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


def _looks_urgent(text):
    lowered = (text or "").strip().lower()
    return any(word in lowered for word in ("urgent", "emergency", "immediately", "spark", "fire", "flood", "water spreading"))


def _strip_staff_action_words(text):
    lowered = (text or "").strip()
    noise = (
        "please",
        "show",
        "send",
        "get",
        "find",
        "search",
        "tenant",
        "lease",
        "balance",
        "ledger",
        "statement",
        "invoice",
        "link",
        "for",
        "of",
        "to",
    )
    tokens = [token for token in lowered.replace(",", " ").split() if token.lower() not in noise]
    return " ".join(tokens).strip() or lowered


def _payment_duplicate_note(lease, ocr_json):
    if not lease:
        return ""
    amount = ocr_json.get("amount")
    reference = (ocr_json.get("reference") or "").strip()
    payment_date = ocr_json.get("date")
    if not amount and not reference:
        return ""
    try:
        from payments.models import Payment
    except Exception:
        return ""
    existing = Payment.objects.filter(lease=lease)
    if reference:
        existing = existing.filter(reference_number__iexact=reference)
    elif amount and payment_date:
        existing = existing.filter(amount=amount, payment_date=payment_date)
    elif amount:
        existing = existing.filter(amount=amount).order_by("-payment_date")[:1]
    if existing.exists():
        return "Possible duplicate payment found; admin should verify before approval."
    return ""


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


def _parse_json_object(raw_text):
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ocr_looks_like_payment(ocr_json):
    if not ocr_json:
        return False
    bank_info = ocr_json.get("bank_information") or {}
    text = " ".join(
        str(value or "")
        for value in [
            ocr_json.get("text"),
            ocr_json.get("raw_text"),
            ocr_json.get("description"),
            ocr_json.get("reference"),
            bank_info.get("bank"),
            bank_info.get("channel"),
            bank_info.get("receiver_account"),
            bank_info.get("receiver_name"),
        ]
    ).lower()
    payment_words = ("payment", "receipt", "sent", "amount", "easypaisa", "jazzcash", "raast", "bank", "account", "transaction")
    has_payment_words = any(word in text for word in payment_words)
    return bool(ocr_json.get("amount") and (has_payment_words or int(ocr_json.get("confidence") or 0) >= 50))


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
            "1 Property Photo\n2 Unit Photo\n3 Lease Photo\n4 Tenant Document\n5 Maintenance Photo\n6 Payment Receipt\n7 Police Verification\n8 Cancel"
        )
    return "We received your media and staged it for admin review before attaching it to any record."


def _upload_purpose_from_text(text):
    lowered = (text or "").strip().lower()
    choices = {
        "1": PendingWhatsAppMedia.PURPOSE_PROPERTY,
        "property": PendingWhatsAppMedia.PURPOSE_PROPERTY,
        "property photo": PendingWhatsAppMedia.PURPOSE_PROPERTY,
        "property photos": PendingWhatsAppMedia.PURPOSE_PROPERTY,
        "2": PendingWhatsAppMedia.PURPOSE_UNIT,
        "unit": PendingWhatsAppMedia.PURPOSE_UNIT,
        "unit photo": PendingWhatsAppMedia.PURPOSE_UNIT,
        "unit photos": PendingWhatsAppMedia.PURPOSE_UNIT,
        "3": PendingWhatsAppMedia.PURPOSE_LEASE,
        "lease": PendingWhatsAppMedia.PURPOSE_LEASE,
        "lease photo": PendingWhatsAppMedia.PURPOSE_LEASE,
        "lease photos": PendingWhatsAppMedia.PURPOSE_LEASE,
        "lease document": PendingWhatsAppMedia.PURPOSE_LEASE,
        "lease documents": PendingWhatsAppMedia.PURPOSE_LEASE,
        "4": PendingWhatsAppMedia.PURPOSE_LEASE,
        "tenant document": PendingWhatsAppMedia.PURPOSE_LEASE,
        "tenant documents": PendingWhatsAppMedia.PURPOSE_LEASE,
        "5": PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        "maintenance": PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        "maintenance photo": PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        "maintenance photos": PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        "6": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "payment": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "payment receipt": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "receipt": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "7": "police_verification",
        "police": "police_verification",
        "police verification": "police_verification",
        "8": "cancel",
        "cancel": "cancel",
        "other": PendingWhatsAppMedia.PURPOSE_OTHER,
    }
    return choices.get(lowered)


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
