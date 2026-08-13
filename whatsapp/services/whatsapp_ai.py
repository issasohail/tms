import json
import logging
import re
import time
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from core.public_urls import build_public_url
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
    WhatsAppStaffActionLog,
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
from whatsapp.services.payment_claim import (
    build_payment_claim_reply,
    is_awaiting_payment_receipt_active,
    is_payment_claim,
    resolve_tenant_and_last_lease,
    set_awaiting_payment_receipt,
)
from whatsapp.services.media_processor import create_pending_media, run_payment_ocr
from whatsapp.services.estamp_processor import (
    inspect_estamp_pdf,
    match_properties,
    match_unit,
    unlock_estamp_pdf,
)
from whatsapp.services.payment_matching import extract_payment_text_fields, match_payment_to_active_lease
from whatsapp.services.role_mode import (
    guest_menu_text,
    identify_sender,
    log_staff_action,
    mode_selection_text,
    resolve_mode,
    staff_can_access_property,
    staff_can_simulate_tenant,
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


STAFF_SEARCH_STOPWORDS = {
    "this", "is", "for", "the", "and", "of", "to", "a", "an", "please", "kindly",
    "photo", "photos", "lease", "upload", "document", "documents", "tenant",
}


def _staff_search_tokens(value, minimum_length=2):
    return [
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) >= minimum_length and token not in STAFF_SEARCH_STOPWORDS
    ]


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

    def _clear_staff_upload_state(self, conversation):
        self._clear_context_keys(
            conversation,
            "staff_upload_kind",
            "staff_upload_batch_key",
            "staff_upload_property_id",
            "staff_upload_unit_id",
            "staff_upload_lease_id",
            "staff_upload_target_options",
            "staff_upload_target_label",
        )

    def handle_inbound_message(self, message_log):
        started = time.monotonic()
        conversation = self._conversation_for(message_log)
        intent = "unknown"
        response = ""
        metadata = {}
        error_text = ""
        try:
            response, intent, metadata = self._handle(message_log, conversation)
            response = self._staff_tenant_assist_response(conversation, response)
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
            invoice_jpg = metadata.get("invoice_jpg")
            if invoice_jpg:
                try:
                    from whatsapp.views import _invoice_jpg_attachment

                    image_bytes, filename = _invoice_jpg_attachment(invoice_jpg)
                    self.service.send_image_bytes(
                        message_log.phone_number,
                        image_bytes,
                        filename=filename,
                        caption=f"Invoice {invoice_jpg.invoice_number}",
                        tenant=metadata.get("tenant"),
                        lease=metadata.get("lease"),
                        invoice=invoice_jpg,
                    )
                except Exception:
                    logger.exception(
                        "Could not send invoice JPG for invoice %s",
                        getattr(invoice_jpg, "pk", None),
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
        lowered_text = (text or "").strip().lower()
        simulation = (conversation.context or {}).get("staff_tenant_simulation")
        if simulation and lowered_text in {
            "exit", "exist", "to staff", "exit tenant", "exit simulation", "stop simulation",
            "staff", "staff mode", "staff inbox",
        }:
            conversation.context.pop("staff_tenant_simulation", None)
            self._clear_context_keys(conversation, "lease_options", "selected_tenant_identity_id")
            conversation.selected_mode = ""
            conversation.mode_expires_at = None
            conversation.pending_state = ""
            conversation.tenant = None
            conversation.selected_lease = None
            conversation.selected_property = None
            conversation.selected_unit = None
            conversation.save(update_fields=[
                "selected_mode", "mode_expires_at", "pending_state", "tenant",
                "selected_lease", "selected_property", "selected_unit", "context", "updated_at",
            ])
            actual_identity = identify_sender(message_log.phone_number, conversation=conversation)
            if actual_identity.has_staff:
                resolve_mode(conversation, "staff", actual_identity)
                log_staff_action(
                    actual_identity.staff_user,
                    message_log.phone_number,
                    "tenant_simulation_ended",
                    "allowed",
                    simulated_tenant_id=simulation.get("tenant_id"),
                )
                return staff_menu_text(actual_identity.staff_user), "staff_tenant_simulation_ended", {
                    "staff_user": actual_identity.staff_user,
                }
            return guest_menu_text(), "staff_tenant_simulation_ended", {}
        identity = identify_sender(message_log.phone_number, conversation=conversation)

        # Authorized staff may enter live tenant-assist mode directly, even while
        # the conversation is waiting at role or tenant-account selection.
        tenant_test_identifier = self._tenant_test_command_identifier(text)
        if tenant_test_identifier is not None and identity.has_staff:
            staff_user = identity.staff_user
            if not staff_can_simulate_tenant(staff_user):
                log_staff_action(
                    staff_user,
                    message_log.phone_number,
                    "tenant_simulation_blocked",
                    "blocked",
                    reason="Tenant Simulator group required",
                )
                return (
                    "Act as Tenant is restricted. Ask an administrator to enable it for your staff user.",
                    "staff_tenant_simulation_blocked",
                    {"staff_user": staff_user},
                )
            resolve_mode(conversation, "staff", identity)
            self._start_staff_tenant_simulator(message_log, conversation, staff_user)
            response = self._consume_staff_tenant_simulator(
                message_log, conversation, tenant_test_identifier, staff_user
            )
            return response, "staff", {"staff_user": staff_user}

        if simulation:
            # LIVE tenant simulation is pinned to the tenant explicitly selected by staff.
            # If another workflow changes conversation.tenant or its lease, never silently
            # continue as a different tenant. Close the corrupted/mismatched simulation
            # and return to Staff Mode so a new tenant must be selected explicitly.
            simulated_tenant_id = simulation.get("tenant_id")
            selected_lease_tenant_id = getattr(conversation.selected_lease, "tenant_id", None)
            tenant_context_mismatch = bool(
                not simulated_tenant_id
                or getattr(conversation, "tenant_id", None) != simulated_tenant_id
                or (selected_lease_tenant_id is not None and selected_lease_tenant_id != simulated_tenant_id)
            )
            if tenant_context_mismatch:
                started_by_staff_id = simulation.get("started_by_staff_id")
                conversation.context.pop("staff_tenant_simulation", None)
                self._clear_context_keys(
                    conversation,
                    "lease_options",
                    "selected_tenant_identity_id",
                    "pending_media_id",
                    "pending_payment_id",
                    "payment_apply_lease_options",
                    "payment_apply_retry_count",
                    "payment_receipt_review",
                    "pending_maintenance_id",
                    "maintenance_draft",
                )
                conversation.selected_mode = WhatsAppConversation.MODE_STAFF
                conversation.mode_expires_at = timezone.now() + timedelta(
                    minutes=getattr(settings, "WHATSAPP_MODE_SESSION_MINUTES", 60)
                )
                conversation.pending_state = ""
                conversation.tenant = None
                conversation.selected_lease = None
                conversation.selected_property = None
                conversation.selected_unit = None
                conversation.save(update_fields=[
                    "selected_mode", "mode_expires_at", "pending_state", "tenant",
                    "selected_lease", "selected_property", "selected_unit", "context", "updated_at",
                ])
                actual_identity = identify_sender(message_log.phone_number, conversation=conversation)
                staff_user = actual_identity.staff_user if actual_identity.has_staff else None
                if staff_user and staff_user.pk == started_by_staff_id:
                    resolve_mode(conversation, "staff", actual_identity)
                    log_staff_action(
                        staff_user,
                        message_log.phone_number,
                        "tenant_simulation_context_mismatch",
                        "blocked",
                        simulated_tenant_id=simulated_tenant_id,
                    )
                    return (
                        "The previous Act as Tenant session was closed because its tenant context changed. "
                        "Start Act as Tenant again for the tenant you want to manage.\n\n"
                        + staff_menu_text(staff_user),
                        "staff_tenant_simulation_context_mismatch",
                        {"staff_user": staff_user},
                    )
                return guest_menu_text(), "staff_tenant_simulation_context_mismatch", {}

            simulator_staff = identity.staff_user
            selected_property = getattr(getattr(conversation.selected_lease, "unit", None), "property", None)
            simulation_allowed = bool(
                simulator_staff
                and simulator_staff.pk == simulation.get("started_by_staff_id")
                and staff_can_simulate_tenant(simulator_staff)
                and (not selected_property or staff_can_access_property(simulator_staff, selected_property))
            )
            if not simulation_allowed:
                conversation.context.pop("staff_tenant_simulation", None)
                self._clear_context_keys(conversation, "lease_options")
                conversation.selected_mode = WhatsAppConversation.MODE_STAFF
                conversation.mode_expires_at = timezone.now() + timedelta(
                    minutes=getattr(settings, "WHATSAPP_MODE_SESSION_MINUTES", 60)
                )
                conversation.pending_state = ""
                conversation.tenant = None
                conversation.selected_lease = None
                conversation.selected_property = None
                conversation.selected_unit = None
                conversation.save()
                if simulator_staff:
                    log_staff_action(
                        simulator_staff,
                        message_log.phone_number,
                        "tenant_simulation_access_revoked",
                        "blocked",
                        simulated_tenant_id=simulation.get("tenant_id"),
                    )
                return (
                    "Act as Tenant was closed because its staff or property permission is no longer active.\n\n"
                    + staff_menu_text(simulator_staff),
                    "staff_tenant_simulation_blocked",
                    {"staff_user": simulator_staff},
                )
            # Keep staff tenant-assist mode active until the staff member explicitly
            # exits, including after the normal role-mode session timeout.
            conversation.selected_mode = WhatsAppConversation.MODE_TENANT
            conversation.mode_expires_at = timezone.now() + timedelta(
                minutes=getattr(settings, "WHATSAPP_MODE_SESSION_MINUTES", 60)
            )
            conversation.save(update_fields=["selected_mode", "mode_expires_at", "updated_at"])

        # Active tenant handovers suppress substantive AI replies and relay updates to staff.
        staff_switch = (text or "").strip().lower() in {"staff", "staff mode", "staff inbox"}
        if conversation.handover_active and identity.has_active_tenant and not staff_switch:
            media = None
            if message_type in {"image", "document", "video", "audio"}:
                media = create_pending_media(message_log, conversation, conversation.selected_lease)
            reply = handle_active_tenant_message(message_log, conversation, text, media=media, service=self.service)
            if reply:
                return reply, "handover_tenant_update", {"tenant": identity.tenant, "lease": conversation.selected_lease}

        if message_type == "unsupported":
            if conversation.pending_state == "staff_waiting_upload":
                # Meta may emit an unsupported item between album/media webhooks.
                # Keep the active Property / Unit / Lease upload batch open so
                # every following photo remains in one approval group until DONE.
                return "", "staff_upload_unsupported_ignored", {"staff_user": identity.staff_user}

            pending_maintenance_id = conversation.context.get("pending_maintenance_id")
            if conversation.pending_state in {"tenant_maintenance_details", "pending_maintenance"} or pending_maintenance_id:
                # Album sends can contain a non-media/unsupported webhook between
                # otherwise valid images/videos. Do not let that event clear or
                # re-route an in-progress maintenance request. The durable
                # pending_maintenance_id is honored even if the visible state is stale;
                # the next attachment restores pending_maintenance automatically.
                return "", "maintenance_unsupported_ignored", {
                    "tenant": identity.tenant,
                    "lease": conversation.selected_lease,
                    "pending_maintenance_id": pending_maintenance_id,
                }

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

        if not conversation.pending_state and is_payment_claim(text):
            payment_claim_response = self._handle_payment_claim(message_log, conversation, text)
            if payment_claim_response:
                return payment_claim_response

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
        previous_mode = conversation.selected_mode
        mode = resolve_mode(conversation, text, identity)

        if previous_mode == WhatsAppConversation.MODE_STAFF and mode != WhatsAppConversation.MODE_STAFF:
            self._clear_staff_upload_state(conversation)
            if mode != "choose_mode" and conversation.pending_state.startswith("staff_"):
                conversation.pending_state = ""
            conversation.save(update_fields=["pending_state", "context", "updated_at"])

        if mode == "ambiguous_identity":
            return (
                "This WhatsApp number matches more than one account. For privacy, no account details were opened. Our staff will review the identity match.",
                "ambiguous_identity",
                {},
            )
        if mode == "choose_tenant_identity":
            return self._tenant_identity_options_text(identity.tenant_matches), "tenant_identity_selection", {}
        if mode == "tenant_no_active_lease":
            return (
                "This tenant account has no current active approved lease. You can switch to Staff Mode or contact management to correct the lease record.",
                "tenant_no_active_lease",
                {},
            )
        if mode == "choose_mode":
            return mode_selection_text(), "mode_selection", {"staff_user": identity.staff_user, "tenant": identity.tenant}
        if was_mode_selection and mode == WhatsAppConversation.MODE_GUEST:
            return guest_menu_text(), "guest", {}
        if mode == WhatsAppConversation.MODE_GUEST:
            return self._handle_guest_message(message_log, conversation, text), "guest", {}
        if was_mode_selection and mode == WhatsAppConversation.MODE_STAFF:
            return staff_menu_text(identity.staff_user), "staff", {
                "staff_user": identity.staff_user,
            }
        if mode == WhatsAppConversation.MODE_STAFF:
            return self._handle_staff_message(message_log, conversation, text, message_type, identity), "staff", {
                "staff_user": identity.staff_user,
            }
        if mode == WhatsAppConversation.MODE_HANDYMAN:
            return (
                "Handyman Mode\n\nUse your configured profile-photo, ID-card, invoice, or job-photo command. Type MENU to see this message again.",
                "handyman",
                {"handyman_id": identity.handyman.pk},
            )

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

        if was_mode_selection and lowered in {"1", "tenant", "continue as tenant"}:
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
                return self._prepare_payment_receipt_confirmation(
                    message_log, conversation, selected_lease, media, text
                )
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
                _tenant_media_confirmation_text(media),
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

        if lowered in {
            "13",
            "upload photo",
            "upload photos",
            "upload unit photo",
            "upload unit photos",
            "unit photo",
            "unit photos",
        }:
            return (
                self._unit_photo_upload_link_reply(lease),
                "unit_photo_upload_link",
                {"lease": lease, "tenant": lease.tenant},
            )

        if lowered in {"11", "request last invoice", "request latest invoice"}:
            invoice = self._latest_invoice_for_lease(lease)
            metadata = {"lease": lease, "tenant": lease.tenant}
            if invoice:
                metadata["invoice_jpg"] = invoice
            return self._latest_invoice_reply(lease), "latest_invoice", metadata
        if lowered in {"12", "view ledger"}:
            return self._ledger_link_reply(lease), "ledger", {"lease": lease, "tenant": lease.tenant}

        if _looks_like_invoice_issue(text):
            return (
                self._invoice_issue_reply(lease),
                "invoice_issue",
                {"lease": lease, "tenant": lease.tenant},
            )
        if _looks_like_invoice_detail(text):
            invoice = self._latest_invoice_for_lease(lease)
            metadata = {"lease": lease, "tenant": lease.tenant}
            if invoice:
                metadata["invoice_jpg"] = invoice
            return self._latest_invoice_reply(lease), "latest_invoice", metadata

        # Resolve explicit bill requests from TMS accounting before the language-model
        # router can confuse an electricity bill with a live meter reading.
        if _looks_like_electric_bill_request(text):
            return (
                self._latest_electric_bill_reply(lease),
                "electric_bill",
                {"lease": lease, "tenant": lease.tenant},
            )
        if _looks_like_contextual_details(text):
            invoice = self._latest_invoice_for_lease(lease)
            metadata = {"lease": lease, "tenant": lease.tenant}
            if invoice:
                metadata["invoice_jpg"] = invoice
            return self._latest_invoice_reply(lease), "latest_invoice", metadata

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
            conversation.pending_state = "tenant_waiting_payment_receipt"
            conversation.save(update_fields=["pending_state", "updated_at"])
            return (
                "Please upload the payment receipt screenshot here. I will read its amount, date, and reference, then submit it for bank verification.",
                "payment_receipt_upload_prompt",
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
        expects_payment_receipt = is_awaiting_payment_receipt_active(conversation)
        if self._should_start_staff_estamp(
            message_log, conversation, message_type, identity
        ):
            return self._start_staff_estamp_media(
                message_log, conversation, identity.staff_user
            )
        is_staff_mode = (
            identity.has_staff
            and conversation.selected_mode == WhatsAppConversation.MODE_STAFF
        )
        staff_upload_kind = conversation.context.get("staff_upload_kind")
        if is_staff_mode and conversation.pending_state == "staff_upload_target_confirmation":
            # Sending media is an unambiguous confirmation of the target the staff
            # member just selected. Do not discard the file when YES was skipped.
            self._confirm_staff_upload_target(
                message_log, conversation, identity.staff_user
            )
            staff_upload_kind = conversation.context.get("staff_upload_kind")
            if conversation.pending_state != "staff_waiting_upload":
                return (
                    "The selected upload target is no longer valid or accessible. "
                    "Please start again from the Staff Menu.",
                    "staff_upload_target_invalid",
                    {"staff_user": identity.staff_user},
                )
        if (
            is_staff_mode
            and conversation.pending_state == "staff_waiting_upload"
            and staff_upload_kind
        ):
            purpose_map = {
                PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO: PendingWhatsAppMedia.PURPOSE_PROPERTY,
                PendingWhatsAppMedia.TARGET_UNIT_PHOTO: PendingWhatsAppMedia.PURPOSE_UNIT,
                PendingWhatsAppMedia.TARGET_LEASE_PHOTO: PendingWhatsAppMedia.PURPOSE_LEASE,
                PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT: PendingWhatsAppMedia.PURPOSE_LEASE,
            }
            batch_key_text = conversation.context.get("staff_upload_batch_key")
            try:
                batch_key = uuid.UUID(str(batch_key_text))
            except (TypeError, ValueError, AttributeError):
                batch_key = None
            property_obj = Property.objects.filter(
                pk=conversation.context.get("staff_upload_property_id")
            ).first()
            unit = Unit.objects.select_related("property").filter(
                pk=conversation.context.get("staff_upload_unit_id")
            ).first()
            lease = Lease.objects.select_related("tenant", "unit__property").filter(
                pk=conversation.context.get("staff_upload_lease_id")
            ).first()
            target_property = (
                property_obj
                or getattr(unit, "property", None)
                or getattr(getattr(lease, "unit", None), "property", None)
            )
            valid_target = bool(
                staff_upload_kind in purpose_map
                and batch_key
                and target_property
                and staff_can_access_property(identity.staff_user, target_property)
            )
            if staff_upload_kind == PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO:
                valid_target = valid_target and property_obj == target_property
                unit = None
                lease = None
            elif staff_upload_kind == PendingWhatsAppMedia.TARGET_UNIT_PHOTO:
                valid_target = valid_target and unit is not None and unit.property_id == target_property.pk
                if lease and lease.unit_id != unit.pk:
                    valid_target = False
            else:
                valid_target = (
                    valid_target
                    and lease is not None
                    and lease.unit.property_id == target_property.pk
                )
                unit = getattr(lease, "unit", None)

            if not valid_target:
                media = create_pending_media(message_log, conversation)
                media.purpose = PendingWhatsAppMedia.PURPOSE_OTHER
                media.target_kind = ""
                media.batch_key = None
                media.submitted_by_staff = identity.staff_user
                media.tenant = None
                media.lease = None
                media.property = None
                media.unit = None
                media.ai_notes = (
                    f"{media.ai_notes} Guided staff upload state was incomplete; "
                    "the file was staged without a target."
                ).strip()
                media.save(update_fields=[
                    "purpose", "target_kind", "batch_key", "submitted_by_staff",
                    "tenant", "lease", "property", "unit", "ai_notes", "updated_at",
                ])
                conversation.pending_state = "staff_upload_type"
                self._clear_staff_upload_state(conversation)
                conversation.context["pending_media_id"] = media.pk
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                notify_staff_pending_request("upload", media)
                return (
                    "The previous upload target was incomplete or expired, so this file was kept "
                    "without a property, unit, or lease. Please choose its upload type.",
                    "staff_upload_target_invalid",
                    {"staff_user": identity.staff_user, "pending_media_id": media.pk},
                )

            media = create_pending_media(message_log, conversation)
            media.purpose = purpose_map[staff_upload_kind]
            media.target_kind = staff_upload_kind
            media.batch_key = batch_key
            media.submitted_by_staff = identity.staff_user
            media.property = target_property
            media.unit = unit or getattr(lease, "unit", None)
            media.lease = lease
            media.tenant = getattr(lease, "tenant", None)
            media.ai_notes = f"{media.ai_notes} Submitted by staff for approval as {media.get_target_kind_display()}.".strip()
            media.save(update_fields=[
                "purpose", "target_kind", "batch_key", "submitted_by_staff", "property", "unit", "lease", "tenant", "ai_notes", "updated_at"
            ])
            count = PendingWhatsAppMedia.objects.filter(batch_key=media.batch_key).count()
            return (
                f"Photo/file {count} added to this approval batch. Send more files or reply DONE.",
                "staff_upload_batched",
                {"staff_user": identity.staff_user, "pending_media_id": media.pk, "batch_key": str(media.batch_key)},
            )

        selected_lease = self._selected_active_lease(conversation) if not is_staff_mode else None
        if (
            not is_staff_mode
            and conversation.selected_mode == WhatsAppConversation.MODE_TENANT
            and not selected_lease
            and identity.active_leases
            and len(identity.active_leases) == 1
        ):
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
        if is_staff_mode:
            media.submitted_by_staff = identity.staff_user
            media.tenant = None
            media.lease = None
            media.property = None
            media.unit = None
            media.save(update_fields=[
                "submitted_by_staff", "tenant", "lease", "property", "unit", "updated_at",
            ])
        if expects_payment_receipt:
            media.purpose = PendingWhatsAppMedia.PURPOSE_PAYMENT
            media.ai_notes = f"{media.ai_notes} Tenant selected Upload Payment Receipt.".strip()
            media.save(update_fields=["purpose", "ai_notes", "updated_at"])
        if conversation.pending_state == "tenant_maintenance_details":
            # Tenant picked "Maintenance request" and sent photos/videos before
            # typing a description. Trust conversation state over the caption
            # keyword guesser (detect_media_purpose), which otherwise misses
            # captionless media and drops it back into the generic upload menu.
            media.purpose = PendingWhatsAppMedia.PURPOSE_MAINTENANCE
            media.ai_notes = f"{media.ai_notes} Routed to maintenance because the tenant was mid-flow.".strip()
            media.save(update_fields=["purpose", "ai_notes", "updated_at"])
            pending = create_pending_maintenance(message_log, conversation, selected_lease, media=media)
            conversation.pending_state = "pending_maintenance"
            conversation.context["pending_maintenance_id"] = pending.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            notify_staff_pending_request("maintenance", pending)
            return (
                "Got it — I'll treat this as part of the same maintenance request. "
                "Send more photos/videos, tell me the issue if you haven't already, or reply DONE to submit.",
                "maintenance_media",
                {"lease": selected_lease, "pending_maintenance_id": pending.pk},
            )
        pending_maintenance_id = conversation.context.get("pending_maintenance_id")
        if pending_maintenance_id:
            try:
                pending = PendingWhatsAppMaintenance.objects.filter(
                    pk=pending_maintenance_id,
                    conversation=conversation,
                    status=PendingWhatsAppMaintenance.STATUS_PENDING,
                ).first()
            except (TypeError, ValueError):
                pending = None
            if pending:
                # The request id is the durable marker for an open media batch.
                # A greeting/empty event may clear pending_state, but DONE and
                # CANCEL explicitly remove this id. Restore the state here so
                # subsequent album items cannot fall through to receipt OCR or
                # the generic upload menu.
                conversation.pending_state = "pending_maintenance"
                self._clear_context_keys(conversation, "pending_media_id")
                conversation.save(
                    update_fields=["pending_state", "context", "updated_at"]
                )
                media.purpose = PendingWhatsAppMedia.PURPOSE_MAINTENANCE
                media.lease = selected_lease or pending.lease
                media.tenant = pending.tenant
                media.property = pending.property
                media.unit = pending.unit
                media.ai_notes = f"{media.ai_notes} Attached to guided WhatsApp maintenance request #{pending.pk}.".strip()
                media.save(update_fields=["purpose", "lease", "tenant", "property", "unit", "ai_notes", "updated_at"])
                pending.media.add(media)
                attachment_count = pending.media.count()
                return (
                    f"Photo/file {attachment_count} was added to the same maintenance request. Send more media or reply DONE.",
                    "maintenance_media_attached",
                    {"lease": pending.lease, "tenant": pending.tenant, "pending_maintenance_id": pending.pk},
                )
            self._clear_context_keys(conversation, "pending_maintenance_id")
            if conversation.pending_state == "pending_maintenance":
                conversation.pending_state = ""
            conversation.save(
                update_fields=["pending_state", "context", "updated_at"]
            )
        ocr_json = run_payment_ocr(media, self.ai_config) if message_type == "image" else {"engine": "skipped", "confidence": 0}
        if expects_payment_receipt or media.purpose == PendingWhatsAppMedia.PURPOSE_PAYMENT or _ocr_looks_like_payment(ocr_json):
            media.purpose = PendingWhatsAppMedia.PURPOSE_PAYMENT
            media.ai_confidence = max(media.ai_confidence or 0, int(ocr_json.get("confidence") or 0), 85 if ocr_json.get("amount") else 0)
            media.ai_notes = f"{media.ai_notes} AI classified this upload as a payment receipt.".strip()
            media.save(update_fields=["purpose", "ai_confidence", "ai_notes", "updated_at"])
            if selected_lease:
                return self._prepare_payment_receipt_confirmation(
                    message_log, conversation, selected_lease, media, text, ocr_json=ocr_json
                )
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

        state = "staff_upload_type" if is_staff_mode else "tenant_upload_type"
        conversation.pending_state = state
        conversation.context["pending_media_id"] = media.pk
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        notify_staff_pending_request("upload", media)
        return (
            _media_confirmation_text(media) if is_staff_mode else _tenant_media_confirmation_text(media),
            "media_pending",
            {"lease": selected_lease, "pending_media_id": media.pk, "ocr": ocr_json},
        )

    def _should_start_staff_estamp(
        self, message_log, conversation, message_type, identity
    ):
        if message_type != "document" or not identity.has_staff:
            return False
        if identity.has_active_tenant and (
            conversation.selected_mode != WhatsAppConversation.MODE_STAFF
        ):
            return False
        if (
            conversation.pending_state == "staff_waiting_upload"
            and conversation.context.get("staff_upload_kind")
        ):
            return False
        if conversation.pending_state in {
            "staff_upload_target_confirmation",
            "police_verification_upload",
        }:
            return False
        payload = message_log.payload or {}
        document = payload.get("document") or {}
        mime_type = (document.get("mime_type") or "").split(";", 1)[0].lower()
        filename = (document.get("filename") or "").lower()
        return mime_type == "application/pdf" or filename.endswith(".pdf")

    def _start_staff_estamp_media(self, message_log, conversation, staff_user):
        media = create_pending_media(message_log, conversation)
        media.purpose = PendingWhatsAppMedia.PURPOSE_LEASE
        media.target_kind = PendingWhatsAppMedia.TARGET_LEASE_ESTAMP
        media.submitted_by_staff = staff_user
        media.ai_notes = (
            f"{media.ai_notes} Received as a direct staff E-Stamp PDF; "
            "lease confirmation is pending."
        ).strip()
        media.save(
            update_fields=[
                "purpose",
                "target_kind",
                "submitted_by_staff",
                "ai_notes",
                "updated_at",
            ]
        )

        properties = self._staff_accessible_properties(staff_user)
        if not properties:
            media.status = PendingWhatsAppMedia.STATUS_REJECTED
            media.ai_notes = (
                f"{media.ai_notes} Rejected because the staff user has no "
                "assigned WhatsApp property access."
            )
            media.save(update_fields=["status", "ai_notes", "updated_at"])
            return (
                "Your staff number is verified, but no WhatsApp property access "
                "is assigned. The E-Stamp was not submitted.",
                "staff_estamp_no_property_access",
                {"pending_media_id": media.pk, "staff_user": staff_user},
            )

        try:
            inspection = inspect_estamp_pdf(media.file, ai_config=self.ai_config)
        except ValidationError as exc:
            error_list = getattr(exc, "error_list", None) or []
            error_code = getattr(error_list[0], "code", "") if error_list else ""
            if error_code == "password_required":
                self._clear_estamp_context(conversation)
                conversation.context["staff_estamp_pending_media_id"] = media.pk
                conversation.pending_state = "staff_estamp_password"
                conversation.save(
                    update_fields=["pending_state", "context", "updated_at"]
                )
                media.ai_notes = (
                    f"{media.ai_notes} Encrypted PDF received; waiting for the "
                    "submitting staff member to provide the password."
                ).strip()
                media.save(update_fields=["ai_notes", "updated_at"])
                return (
                    "This E-Stamp PDF is password protected. Please enter the "
                    "PDF password, or reply CANCEL.",
                    "staff_estamp_password",
                    {"pending_media_id": media.pk, "staff_user": staff_user},
                )
            media.status = PendingWhatsAppMedia.STATUS_REJECTED
            media.ai_notes = f"{media.ai_notes} PDF validation failed."
            media.save(update_fields=["status", "ai_notes", "updated_at"])
            return (
                exc.messages[0],
                "staff_estamp_invalid_pdf",
                {"pending_media_id": media.pk, "staff_user": staff_user},
            )

        return self._continue_staff_estamp_inspection(
            conversation,
            media,
            staff_user,
            inspection,
            properties,
        )

    def _continue_staff_estamp_inspection(
        self, conversation, media, staff_user, inspection, properties=None
    ):
        properties = properties or self._staff_accessible_properties(staff_user)
        media.ai_notes = (
            f"{media.ai_notes} Notes inspected using {inspection['source']}; "
            f"{inspection['page_count']} PDF page(s)."
        )
        media.save(update_fields=["ai_notes", "updated_at"])
        self._clear_estamp_context(conversation)
        conversation.context["staff_estamp_pending_media_id"] = media.pk
        conversation.context["staff_estamp_property_options"] = [
            property_obj.pk for property_obj in properties
        ]

        matches = match_properties(inspection["notes_text"], properties)
        confident = [
            item for item in matches
            if item["score"] >= 82
        ]
        if len(confident) == 1:
            property_obj = confident[0]["property"]
            units = self._estamp_units(property_obj, staff_user)
            unit_matches = match_unit(inspection["notes_text"], units)
            candidate_unit = unit_matches[0] if len(unit_matches) == 1 else None
            return self._set_estamp_property_confirmation(
                conversation,
                staff_user,
                property_obj,
                candidate_unit=candidate_unit,
                source_label="the E-Stamp Notes",
            )

        conversation.pending_state = "staff_estamp_property_lookup"
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        if confident:
            return (
                "I found more than one possible property in the E-Stamp Notes.\n\n"
                + self._estamp_property_list(properties),
                "staff_estamp_property_lookup",
                {"pending_media_id": media.pk, "staff_user": staff_user},
            )
        return (
            "I could not confidently identify the property from the E-Stamp "
            "Notes.\n\nEnter the property and unit, for example:\n"
            "F35 Room 7\n\nOr reply LIST to select from your assigned properties.",
            "staff_estamp_property_lookup",
            {"pending_media_id": media.pk, "staff_user": staff_user},
        )

    def _consume_staff_estamp_state(
        self, message_log, conversation, text, staff_user
    ):
        state = conversation.pending_state
        lowered = (text or "").strip().lower()
        pending = PendingWhatsAppMedia.objects.filter(
            pk=conversation.context.get("staff_estamp_pending_media_id"),
            status=PendingWhatsAppMedia.STATUS_PENDING,
            submitted_by_staff=staff_user,
            target_kind=PendingWhatsAppMedia.TARGET_LEASE_ESTAMP,
        ).first()
        if not pending:
            self._clear_estamp_context(conversation)
            conversation.pending_state = ""
            conversation.save(
                update_fields=["pending_state", "context", "updated_at"]
            )
            return (
                "This E-Stamp upload session is no longer available.",
                "staff_estamp_missing",
                {"staff_user": staff_user},
            )
        if lowered in {"cancel", "cancel upload"}:
            pending.status = PendingWhatsAppMedia.STATUS_REJECTED
            pending.ai_notes = (
                f"{pending.ai_notes} Cancelled by the submitting staff member "
                "before lease confirmation."
            )
            pending.save(update_fields=["status", "ai_notes", "updated_at"])
            self._clear_estamp_context(conversation)
            conversation.pending_state = ""
            conversation.save(
                update_fields=["pending_state", "context", "updated_at"]
            )
            return (
                "E-Stamp upload cancelled.",
                "staff_estamp_cancelled",
                {"pending_media_id": pending.pk, "staff_user": staff_user},
            )

        if state == "staff_estamp_password":
            password = (text or "").strip()
            if not password:
                return (
                    "Please enter the PDF password, or reply CANCEL.",
                    "staff_estamp_password",
                    {"pending_media_id": pending.pk, "staff_user": staff_user},
                )
            self._redact_estamp_password_message(message_log)
            try:
                unlock_estamp_pdf(pending.file, password)
                inspection = inspect_estamp_pdf(
                    pending.file, ai_config=self.ai_config
                )
            except ValidationError as exc:
                error_list = getattr(exc, "error_list", None) or []
                error_code = (
                    getattr(error_list[0], "code", "") if error_list else ""
                )
                if error_code in {"password_required", "wrong_password"}:
                    return (
                        "The PDF password is incorrect. Please try again, or "
                        "reply CANCEL.",
                        "staff_estamp_password",
                        {
                            "pending_media_id": pending.pk,
                            "staff_user": staff_user,
                        },
                    )
                return (
                    exc.messages[0],
                    "staff_estamp_password_error",
                    {"pending_media_id": pending.pk, "staff_user": staff_user},
                )
            pending.ai_notes = (
                f"{pending.ai_notes} PDF unlocked and rewritten without a "
                "password."
            ).strip()
            pending.save(update_fields=["ai_notes", "updated_at"])
            return self._continue_staff_estamp_inspection(
                conversation,
                pending,
                staff_user,
                inspection,
            )

        if state == "staff_estamp_property_confirm":
            if lowered in {"yes", "y", "confirm", "correct"}:
                property_obj = Property.objects.filter(
                    pk=conversation.context.get("staff_estamp_property_id")
                ).first()
                if (
                    not property_obj
                    or not staff_can_access_property(staff_user, property_obj)
                ):
                    return (
                        "You no longer have access to that property. Reply LIST "
                        "to choose another property or CANCEL.",
                        "staff_estamp_property_blocked",
                        {"pending_media_id": pending.pk, "staff_user": staff_user},
                    )
                candidate_unit = Unit.objects.filter(
                    pk=conversation.context.get("staff_estamp_unit_id"),
                    property=property_obj,
                ).first()
                return self._show_estamp_leases(
                    conversation,
                    staff_user,
                    property_obj,
                    candidate_unit=candidate_unit,
                )
            if lowered in {"no", "n", "wrong"}:
                conversation.pending_state = "staff_estamp_property_lookup"
                conversation.context.pop("staff_estamp_property_id", None)
                conversation.context.pop("staff_estamp_unit_id", None)
                conversation.save(
                    update_fields=["pending_state", "context", "updated_at"]
                )
                return (
                    "Enter the correct property and unit, or reply LIST.",
                    "staff_estamp_property_lookup",
                    {"pending_media_id": pending.pk, "staff_user": staff_user},
                )
            return (
                "Reply YES if this is the correct property, NO to choose another, "
                "or CANCEL.",
                "staff_estamp_property_confirm",
                {"pending_media_id": pending.pk, "staff_user": staff_user},
            )

        if state == "staff_estamp_property_lookup":
            properties = self._staff_accessible_properties(staff_user)
            option_ids = conversation.context.get(
                "staff_estamp_property_options"
            ) or [item.pk for item in properties]
            property_obj = self._option_from_number(
                text, Property, option_ids
            )
            unit_hint = ""
            if property_obj not in properties:
                property_obj = None
            if not property_obj and lowered not in {"list", "properties"}:
                property_obj, unit_hint = self._resolve_staff_property_unit_text(
                    text, properties
                )
            if not property_obj:
                return (
                    self._estamp_property_list(properties),
                    "staff_estamp_property_lookup",
                    {"pending_media_id": pending.pk, "staff_user": staff_user},
                )
            candidate_unit = None
            if unit_hint:
                candidate_unit = self._match_unit_text(
                    unit_hint,
                    property_obj,
                    self._estamp_units(property_obj, staff_user),
                )
            return self._set_estamp_property_confirmation(
                conversation,
                staff_user,
                property_obj,
                candidate_unit=candidate_unit,
                source_label="your entry",
            )

        if state == "staff_estamp_lease_selection":
            lease_ids = conversation.context.get("staff_estamp_lease_options") or []
            lease = self._option_from_number(text, Lease, lease_ids)
            if not lease or not staff_can_access_property(
                staff_user, lease.unit.property
            ):
                return (
                    "Reply with one of the lease numbers shown, or CANCEL.",
                    "staff_estamp_lease_selection",
                    {"pending_media_id": pending.pk, "staff_user": staff_user},
                )
            return self._set_estamp_lease_confirmation(conversation, lease)

        if state == "staff_estamp_lease_confirm":
            if lowered in {"yes", "y", "confirm", "correct"}:
                lease = Lease.objects.select_related(
                    "tenant", "unit__property"
                ).filter(
                    pk=conversation.context.get("staff_estamp_lease_id"),
                    status__in=["active", "pending_approval"],
                ).first()
                if (
                    not lease
                    or not staff_can_access_property(
                        staff_user, lease.unit.property
                    )
                ):
                    return (
                        "That lease is no longer available to your staff account. "
                        "Reply NO to choose again or CANCEL.",
                        "staff_estamp_lease_blocked",
                        {"pending_media_id": pending.pk, "staff_user": staff_user},
                    )
                pending.lease = lease
                pending.tenant = lease.tenant
                pending.property = lease.unit.property
                pending.unit = lease.unit
                pending.ai_notes = (
                    f"{pending.ai_notes} Lease #{lease.pk} confirmed by "
                    f"{staff_user.get_username()} through WhatsApp."
                )
                pending.save(
                    update_fields=[
                        "lease",
                        "tenant",
                        "property",
                        "unit",
                        "ai_notes",
                        "updated_at",
                    ]
                )
                self._clear_estamp_context(conversation)
                conversation.pending_state = ""
                conversation.save(
                    update_fields=["pending_state", "context", "updated_at"]
                )
                notify_staff_pending_request("upload", pending)
                log_staff_action(
                    staff_user,
                    message_log.phone_number,
                    "estamp_upload_submitted",
                    "pending",
                    property=lease.unit.property,
                    tenant=lease.tenant,
                    lease=lease,
                    pending_media_id=pending.pk,
                )
                return (
                    "E-Stamp confirmed and submitted for administrator approval.\n\n"
                    f"Property: {lease.unit.property.property_name}\n"
                    f"Unit: {lease.unit.unit_number}\n"
                    f"Tenant: {lease.tenant.get_full_name()}\n"
                    f"Lease: {lease.start_date:%d-%m-%Y} to "
                    f"{lease.end_date:%d-%m-%Y}",
                    "staff_estamp_submitted",
                    {
                        "pending_media_id": pending.pk,
                        "staff_user": staff_user,
                        "lease": lease,
                    },
                )
            if lowered in {"no", "n", "wrong"}:
                conversation.pending_state = "staff_estamp_property_lookup"
                conversation.context.pop("staff_estamp_property_id", None)
                conversation.context.pop("staff_estamp_unit_id", None)
                conversation.context.pop("staff_estamp_lease_id", None)
                conversation.context.pop("staff_estamp_lease_options", None)
                conversation.save(
                    update_fields=["pending_state", "context", "updated_at"]
                )
                return (
                    "Enter the correct property and unit, or reply LIST.",
                    "staff_estamp_property_lookup",
                    {"pending_media_id": pending.pk, "staff_user": staff_user},
                )
            return (
                "Reply YES to submit this E-Stamp for the displayed lease, "
                "NO to choose again, or CANCEL.",
                "staff_estamp_lease_confirm",
                {"pending_media_id": pending.pk, "staff_user": staff_user},
            )
        return None

    def _redact_estamp_password_message(self, message_log):
        payload = dict(message_log.payload or {})
        text_payload = payload.get("text")
        if isinstance(text_payload, dict):
            text_payload = dict(text_payload)
            text_payload["body"] = "[PDF password redacted]"
            payload["text"] = text_payload
        message_log.payload = payload
        message_log.save(update_fields=["payload", "updated_at"])

    def _set_estamp_property_confirmation(
        self,
        conversation,
        staff_user,
        property_obj,
        *,
        candidate_unit=None,
        source_label,
    ):
        conversation.context["staff_estamp_property_id"] = property_obj.pk
        if candidate_unit:
            conversation.context["staff_estamp_unit_id"] = candidate_unit.pk
        else:
            conversation.context.pop("staff_estamp_unit_id", None)
        conversation.save(update_fields=["context", "updated_at"])
        return self._show_estamp_leases(
            conversation,
            staff_user,
            property_obj,
            candidate_unit=candidate_unit,
            source_label=source_label,
        )

    def _show_estamp_leases(
        self,
        conversation,
        staff_user,
        property_obj,
        *,
        candidate_unit=None,
        source_label=None,
    ):
        leases = list(
            self._staff_accessible_leases(staff_user)
            .filter(
                unit__property=property_obj,
                status__in=["active", "pending_approval"],
            )
            .order_by("unit__unit_number", "-start_date", "-id")
        )
        if candidate_unit:
            candidate_leases = [
                lease for lease in leases if lease.unit_id == candidate_unit.pk
            ]
            if len(candidate_leases) == 1:
                return self._set_estamp_lease_confirmation(
                    conversation,
                    candidate_leases[0],
                    source_label=source_label,
                )
            if candidate_leases:
                leases = candidate_leases
        if not leases:
            conversation.pending_state = "staff_estamp_property_lookup"
            conversation.context.pop("staff_estamp_property_id", None)
            conversation.context.pop("staff_estamp_unit_id", None)
            conversation.save(
                update_fields=["pending_state", "context", "updated_at"]
            )
            return (
                f"No active or pending lease was found for "
                f"{property_obj.property_name}. Enter another property/unit or "
                "reply CANCEL.",
                "staff_estamp_no_lease",
                {"property": property_obj},
            )
        if len(leases) == 1:
            return self._set_estamp_lease_confirmation(
                conversation,
                leases[0],
                source_label=source_label,
            )
        conversation.pending_state = "staff_estamp_lease_selection"
        conversation.context["staff_estamp_lease_options"] = [
            lease.pk for lease in leases[:20]
        ]
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        lines = []
        if source_label:
            lines.extend(
                [
                    f"I found this property from {source_label}:",
                    "",
                    f"Property: {property_obj.property_name}",
                ]
            )
            if candidate_unit:
                lines.append(f"Possible unit: {candidate_unit.unit_number}")
            lines.append("")
        lines.append(
            f"Select the current tenant/lease for {property_obj.property_name}:"
        )
        for index, lease in enumerate(leases[:20], start=1):
            lines.append(
                f"{index}. Current tenant: {lease.tenant.get_full_name()} - "
                f"Unit {lease.unit.unit_number} - "
                f"{lease.start_date:%d-%m-%Y} to {lease.end_date:%d-%m-%Y}"
            )
        lines.append("\nReply with a number or CANCEL.")
        return "\n".join(lines), "staff_estamp_lease_selection", {
            "property": property_obj
        }

    def _set_estamp_lease_confirmation(
        self, conversation, lease, *, source_label=None
    ):
        conversation.pending_state = "staff_estamp_lease_confirm"
        conversation.context["staff_estamp_lease_id"] = lease.pk
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        found_property = (
            f"I found this property from {source_label}:\n\n"
            if source_label else ""
        )
        return (
            f"{found_property}Confirm E-Stamp lease:\n\n"
            f"Property: {lease.unit.property.property_name}\n"
            f"Unit: {lease.unit.unit_number}\n"
            f"Current tenant: {lease.tenant.get_full_name()}\n"
            f"Lease: {lease.start_date:%d-%m-%Y} to "
            f"{lease.end_date:%d-%m-%Y}\n\n"
            "Attach this E-Stamp to this lease? Reply YES or NO.",
            "staff_estamp_lease_confirm",
            {"lease": lease},
        )

    def _estamp_units(self, property_obj, staff_user):
        unit_ids = (
            self._staff_accessible_leases(staff_user)
            .filter(
                unit__property=property_obj,
                status__in=["active", "pending_approval"],
            )
            .values_list("unit_id", flat=True)
        )
        return list(
            Unit.objects.filter(pk__in=unit_ids)
            .select_related("property")
            .order_by("unit_number")
        )

    def _estamp_property_list(self, properties):
        lines = ["Select one of your assigned properties:"]
        for index, property_obj in enumerate(properties, start=1):
            lines.append(f"{index}. {property_obj.property_name}")
        lines.append("\nReply with a number, enter property/unit text, or CANCEL.")
        return "\n".join(lines)

    def _clear_estamp_context(self, conversation):
        self._clear_context_keys(
            conversation,
            "staff_estamp_pending_media_id",
            "staff_estamp_property_options",
            "staff_estamp_property_id",
            "staff_estamp_unit_id",
            "staff_estamp_lease_options",
            "staff_estamp_lease_id",
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
        if conversation.pending_state.startswith("staff_estamp_"):
            if not identity.has_staff:
                self._clear_estamp_context(conversation)
                conversation.pending_state = ""
                conversation.save(
                    update_fields=["pending_state", "context", "updated_at"]
                )
                return (
                    "This E-Stamp session requires a verified staff number.",
                    "staff_estamp_staff_verification_failed",
                    {},
                )
            return self._consume_staff_estamp_state(
                message_log, conversation, text, identity.staff_user
            )
        if conversation.pending_state == "tenant_identity_selection":
            return self._consume_tenant_identity_selection(conversation, text, identity)
        if conversation.pending_state == "tenant_identity_verify":
            return self._consume_tenant_identity_verification(conversation, text)
        if conversation.pending_state == "payment_receipt_confirmation":
            return self._consume_payment_receipt_confirmation(message_log, conversation, text)
        if conversation.pending_state == "pending_maintenance":
            pending = PendingWhatsAppMaintenance.objects.filter(
                pk=conversation.context.get("pending_maintenance_id"),
                status=PendingWhatsAppMaintenance.STATUS_PENDING,
            ).first()
            if lowered in {"done", "submit", "finished", "finish"}:
                conversation.pending_state = ""
                self._clear_context_keys(conversation, "pending_maintenance_id")
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                if not pending:
                    return "There is no open maintenance request to submit.", "maintenance_missing", {}
                return (
                    f"Maintenance request submitted for approval with {pending.media.count()} attachment(s).",
                    "maintenance_submitted",
                    {"tenant": pending.tenant, "lease": pending.lease, "pending_maintenance_id": pending.pk},
                )
            if lowered in {"cancel", "cancel request"}:
                if pending:
                    pending.status = PendingWhatsAppMaintenance.STATUS_REJECTED
                    pending.ai_notes = f"{pending.ai_notes} Cancelled by sender before approval.".strip()
                    pending.save(update_fields=["status", "ai_notes", "updated_at"])
                conversation.pending_state = ""
                self._clear_context_keys(conversation, "pending_maintenance_id")
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return "Maintenance request cancelled.", "maintenance_cancelled", {}
            if lowered in {"new request", "new maintenance request"}:
                conversation.pending_state = ""
                self._clear_context_keys(conversation, "pending_maintenance_id")
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return "Please describe the new maintenance issue, then send its photos.", "maintenance_new_request", {}
            if pending and (text or "").strip():
                # Media-first requests start with no description at all. Treat
                # any other text sent while the batch is open as detail to add,
                # rather than dropping it or bouncing to an unrelated menu.
                issue, urgency, confidence = detect_maintenance_issue(text)
                pending.description = f"{pending.description}\n{text}".strip() if pending.description else text
                if pending.issue_type in {"", "Other"} and issue != "Other":
                    pending.issue_type = issue
                if urgency == "urgent":
                    pending.urgency = urgency
                pending.ai_confidence = max(pending.ai_confidence or 0, confidence)
                pending.save(update_fields=["description", "issue_type", "urgency", "ai_confidence", "updated_at"])
                return (
                    "Added to the request. Send more photos/videos or reply DONE to submit.",
                    "maintenance_description_added",
                    {"pending_maintenance_id": pending.pk},
                )
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

    def _tenant_identity_options_text(self, tenants):
        tenants = list(tenants)
        lines = ["More than one tenant account uses this number. Choose your account:"]
        for index, tenant in enumerate(tenants, start=1):
            tenant_leases = (
                Lease.objects.select_related("unit__property")
                .filter(
                    Q(tenant_id=tenant.pk)
                    | Q(family_members__family_member_id=tenant.pk)
                    | Q(legacy_family_members__tenant_id=tenant.pk),
                    status="active",
                    start_date__lte=timezone.localdate(),
                    end_date__gte=timezone.localdate(),
                )
                .distinct()
                .order_by("unit__property__property_name", "unit__unit_number", "id")
            )
            locations = [
                f"{lease.unit.property.property_name} / Unit {lease.unit.unit_number}"
                for lease in tenant_leases
            ]
            account_label = ", ".join(locations) or "No active property"
            lines.append(f"{index}. {account_label}")
        lines.append("Reply with a number to open that property account.")
        return "\n\n".join([lines[0], "\n".join(lines[1:])])

    def _consume_tenant_identity_selection(self, conversation, text, identity):
        if (text or "").strip().lower() in {"cancel", "back", "switch mode", "menu"}:
            conversation.pending_state = "mode_selection"
            self._clear_context_keys(conversation, "tenant_identity_options", "pending_tenant_identity_id")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return mode_selection_text(), "mode_selection", {}
        eligible_tenants = list(identity.tenant_matches)
        eligible_ids = [tenant.pk for tenant in eligible_tenants]
        if conversation.context.get("tenant_identity_options") != eligible_ids:
            conversation.context["tenant_identity_options"] = eligible_ids
            conversation.save(update_fields=["context", "updated_at"])
        if len(eligible_tenants) == 1:
            return self._activate_selected_tenant(conversation, eligible_tenants[0])
        if not eligible_tenants:
            conversation.pending_state = "mode_selection"
            self._clear_context_keys(conversation, "tenant_identity_options", "pending_tenant_identity_id")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return mode_selection_text(), "mode_selection", {}
        try:
            index = int((text or "").strip()) - 1
        except ValueError:
            return self._tenant_identity_options_text(eligible_tenants), "tenant_identity_selection", {}
        option_ids = eligible_ids
        if index < 0 or index >= len(option_ids):
            return self._tenant_identity_options_text(eligible_tenants), "tenant_identity_selection", {}
        from tenants.models import Tenant

        tenant = Tenant.objects.filter(pk=option_ids[index], is_active=True).first()
        if not tenant:
            return "That tenant account is no longer active. Type BACK and choose another account.", "tenant_identity_inactive", {}
        return self._activate_selected_tenant(conversation, tenant)

    def _activate_selected_tenant(self, conversation, tenant):
        conversation.context["selected_tenant_identity_id"] = tenant.pk
        self._clear_context_keys(conversation, "pending_tenant_identity_id", "tenant_identity_options")
        conversation.pending_state = ""
        conversation.tenant = tenant
        conversation.save(update_fields=["tenant", "pending_state", "context", "updated_at"])
        selected_identity = identify_sender(conversation.phone_number, conversation=conversation)
        if not selected_identity.has_active_tenant:
            return (
                "This property account has no current active approved lease. Type SWITCH MODE to choose another available role.",
                "tenant_no_active_lease",
                {"tenant": tenant},
            )
        resolve_mode(conversation, "tenant", selected_identity)
        if len(selected_identity.active_leases) == 1:
            lease = selected_identity.active_leases[0]
            conversation.selected_lease = lease
            conversation.selected_property = lease.unit.property
            conversation.selected_unit = lease.unit
            conversation.save(update_fields=["selected_lease", "selected_property", "selected_unit", "updated_at"])
            return self._tenant_welcome_menu(lease), "tenant_welcome", {"tenant": tenant, "lease": lease}

        conversation.context["lease_options"] = [lease.pk for lease in selected_identity.active_leases]
        conversation.pending_state = "lease_selection"
        conversation.save(update_fields=["context", "pending_state", "updated_at"])
        return lease_option_lines(selected_identity.active_leases), "lease_lookup", {"tenant": tenant}

    def _consume_tenant_identity_verification(self, conversation, text):
        from tenants.models import Tenant

        lowered = (text or "").strip().lower()
        if lowered in {"back", "cancel"}:
            conversation.pending_state = "tenant_identity_selection"
            conversation.context.pop("pending_tenant_identity_id", None)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            tenants = Tenant.objects.filter(pk__in=conversation.context.get("tenant_identity_options") or []).order_by("id")
            return self._tenant_identity_options_text(tenants), "tenant_identity_selection", {}
        tenant = Tenant.objects.filter(pk=conversation.context.get("pending_tenant_identity_id"), is_active=True).first()
        supplied = "".join(ch for ch in str(text or "") if ch.isdigit())
        cnic_digits = "".join(ch for ch in str(getattr(tenant, "cnic", "") or "") if ch.isdigit())
        if not tenant or len(supplied) != 4 or not cnic_digits.endswith(supplied):
            return "Those digits did not match. Please try again or type BACK.", "tenant_identity_verify_failed", {}
        return self._activate_selected_tenant(conversation, tenant)

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
        if identity.has_handyman:
            return "Handyman Mode\n\nSend a configured handyman command to continue."
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

    def _handle_payment_claim(self, message_log, conversation, text):
        tenant, lease, lease_status = resolve_tenant_and_last_lease(message_log.phone_number)
        reply = build_payment_claim_reply(tenant, lease)
        if tenant:
            set_awaiting_payment_receipt(conversation, tenant, lease)
        WhatsAppStaffActionLog.objects.create(
            staff_user=None,
            phone_number=message_log.phone_number,
            action="payment_claim_received",
            status=WhatsAppStaffActionLog.ACTION_STATUS_ALLOWED,
            property=getattr(getattr(lease, "unit", None), "property", None),
            tenant=tenant,
            lease=lease,
            details={"lease_status": lease_status, "message_text": safe_summary(text, 300)},
        )
        return reply, "payment_claim", {"tenant": tenant, "lease": lease, "lease_status": lease_status}

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
        if (
            conversation.pending_state
            in {
                "staff_upload_target_query",
                "staff_upload_target_selection",
                "staff_upload_target_confirmation",
                "staff_waiting_upload",
            }
            and lowered not in {"menu", "staff", "hi", "hello", "start", ""}
        ):
            upload_state_response = self._consume_staff_menu_state(
                message_log, conversation, text, staff_user
            )
            if upload_state_response:
                return upload_state_response
        if lowered in {"cancel", "back"}:
            conversation.pending_state = ""
            self._clear_staff_upload_state(conversation)
            self._clear_context_keys(
                conversation,
                "staff_add_lease",
                "staff_search_action",
                "staff_search_options",
                "staff_upload_hint",
                "staff_lease_target",
            )
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return staff_menu_text(staff_user)
        if lowered in {"menu", "staff", "hi", "hello", "start", ""}:
            conversation.pending_state = ""
            self._clear_staff_upload_state(conversation)
            self._clear_context_keys(
                conversation,
                "staff_add_lease",
                "staff_search_action",
                "staff_search_options",
                "staff_upload_hint",
                "staff_lease_target",
            )
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            log_staff_action(staff_user, message_log.phone_number, "staff_menu", "allowed")
            return staff_menu_text(staff_user)
        if lowered == "tenant testing" or lowered == "test tenant" or (lowered == "11" and not conversation.pending_state):
            return self._start_staff_tenant_simulator(message_log, conversation, staff_user)
        if lowered in {
            "new tenant registration",
            "tenant registration link",
            "send tenant registration link",
        } or (lowered == "12" and not conversation.pending_state):
            return self._create_registration_link_for_staff(message_log, conversation, staff_user)
        if lowered in {"upload property photo", "upload property photos", "property photo", "property photos"}:
            return self._start_staff_upload_target_search(
                conversation,
                PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO,
                "Send the building/property name to select the photo target.",
                staff_user,
            )
        if lowered in {"upload unit photo", "upload unit photos", "unit photo", "unit photos"}:
            return self._start_staff_upload_target_search(
                conversation,
                PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
                "Send the property and unit to select the unit target.",
                staff_user,
            )
        if lowered in {"upload lease photo", "upload lease photos", "lease photo", "lease photos"}:
            return self._start_staff_lease_target(conversation, staff_user, "lease_photo")
        natural_staff_response = self._handle_staff_natural_language(message_log, conversation, text, staff_user)
        if natural_staff_response:
            return natural_staff_response
        if "ledger" in lowered or "statement" in lowered:
            return self._start_staff_lease_target(conversation, staff_user, "lease_ledger")
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
            self._clear_staff_upload_state(conversation)
            conversation.pending_state = "staff_waiting_upload"
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
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
            self._clear_staff_upload_state(conversation)
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
        link = build_public_url("tenants:tenant_public_registration_new")
        conversation.pending_state = ""
        conversation.save(update_fields=["pending_state", "updated_at"])
        log_staff_action(
            staff_user,
            message_log.phone_number,
            "tenant_registration_link_created",
            "allowed",
            link=link,
        )
        return (
            "Tenant registration link created.\n\n"
            f"Link:\n{link}\n\n"
            "After submission:\n"
            "Pending Approval"
        )

    def _start_staff_tenant_simulator(self, message_log, conversation, staff_user):
        if not staff_can_simulate_tenant(staff_user):
            log_staff_action(
                staff_user,
                message_log.phone_number,
                "tenant_simulation_blocked",
                "blocked",
                reason="Tenant Simulator group required",
            )
            return (
                "Act as Tenant is restricted. Ask an administrator to enable it for your staff user."
            )
        conversation.pending_state = "staff_tenant_simulator_lookup"
        self._clear_staff_upload_state(conversation)
        self._clear_context_keys(
            conversation,
            "staff_tenant_simulator_options",
            "pending_media_id",
            "pending_payment_id",
            "payment_apply_lease_options",
            "payment_apply_retry_count",
            "payment_receipt_review",
            "pending_maintenance_id",
            "maintenance_draft",
        )
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return (
            "Act as Tenant (Live)\n\n"
            "Send the tenant's phone number, CNIC, tenant number, or name. Only active leases in "
            "properties you can access are available.\n\n"
            "You will see the same live flow as the tenant. Requests and uploads you submit will "
            "be recorded for that tenant and replies will return to this staff number.\n"
            "Reply BACK to cancel."
        )

    def _tenant_test_command_identifier(self, text):
        match = re.fullmatch(r"\s*tenant\s+#?\s*(.+?)\s*", text or "", flags=re.IGNORECASE)
        if not match:
            return None
        identifier = match.group(1).strip()
        lowered = identifier.lower()
        if lowered in {"mode", "management", "account", "registration", "menu"}:
            return None
        digits = "".join(ch for ch in identifier if ch.isdigit())
        if len(digits) >= 7 or re.fullmatch(r"#?\s*\d{1,6}", identifier):
            return identifier
        return None

    def _consume_staff_tenant_simulator(self, message_log, conversation, text, staff_user):
        if not staff_can_simulate_tenant(staff_user):
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "staff_tenant_simulator_options")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return "Act as Tenant access is no longer assigned to your staff user."

        if conversation.pending_state == "staff_tenant_simulator_selection":
            options = conversation.context.get("staff_tenant_simulator_options") or []
            try:
                index = int((text or "").strip()) - 1
            except ValueError:
                conversation.pending_state = "staff_tenant_simulator_lookup"
                conversation.save(update_fields=["pending_state", "updated_at"])
                return self._consume_staff_tenant_simulator(message_log, conversation, text, staff_user)
            if index < 0 or index >= len(options):
                return "That tenant number is not in the list. Choose a number, send another phone/CNIC, or type BACK."
            tenant = Tenant.objects.filter(pk=options[index], is_active=True).first()
            return self._activate_staff_tenant_simulator(message_log, conversation, staff_user, tenant)

        tenants = self._staff_tenant_identifier_matches(staff_user, text)
        if not tenants:
            return (
                "No active tenant matched that phone, CNIC, tenant number, or name in properties "
                "you can access. Try another identifier or type BACK."
            )
        if len(tenants) == 1:
            return self._activate_staff_tenant_simulator(message_log, conversation, staff_user, tenants[0])
        conversation.pending_state = "staff_tenant_simulator_selection"
        conversation.context["staff_tenant_simulator_options"] = [tenant.pk for tenant in tenants[:9]]
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        lines = ["More than one active tenant matched. Choose one:"]
        for index, tenant in enumerate(tenants[:9], start=1):
            lease = self._staff_current_accessible_leases(staff_user).filter(tenant=tenant).first()
            location = f" - {lease.unit.property.property_name} / {lease.unit.unit_number}" if lease else ""
            lines.append(f"{index}. {tenant.get_full_name()}{location}")
        lines.append("\nReply with a number or send another phone/CNIC.")
        return "\n".join(lines)

    def _staff_current_accessible_leases(self, staff_user):
        today = timezone.localdate()
        return self._staff_accessible_leases(staff_user).filter(
            status="active",
            start_date__lte=today,
            end_date__gte=today,
        ).order_by("unit__property__property_name", "unit__unit_number", "-start_date", "-id")

    def _staff_tenant_identifier_matches(self, staff_user, text):
        query_text = (text or "").strip()
        lowered = query_text.lower()
        digits = "".join(ch for ch in query_text if ch.isdigit())
        scoped_tenant_ids = set(self._staff_current_accessible_leases(staff_user).values_list("tenant_id", flat=True))
        query = Q()
        if len(digits) >= 7:
            suffix = digits[-10:]
            query |= Q(phone__icontains=suffix) | Q(phone2__icontains=suffix) | Q(phone3__icontains=suffix)
        cnic_digits = normalize_cnic(query_text)
        if len(cnic_digits) == 13:
            query |= Q(cnic_digits=cnic_digits)
        tenant_number = re.fullmatch(r"(?:tenant\s*)?#?\s*(\d{1,6})", lowered)
        if tenant_number:
            query |= Q(pk=int(tenant_number.group(1)))
        for token in [item for item in _staff_search_tokens(lowered, minimum_length=4) if not item.isdigit()]:
            query |= Q(first_name__icontains=token) | Q(last_name__icontains=token)
        if not query:
            return []
        return list(Tenant.objects.filter(query, pk__in=scoped_tenant_ids, is_active=True).distinct().order_by("first_name", "last_name", "id")[:10])

    def _activate_staff_tenant_simulator(self, message_log, conversation, staff_user, tenant):
        if not tenant:
            return "That tenant is no longer available. Send another phone/CNIC or type BACK."
        leases = list(self._staff_current_accessible_leases(staff_user).filter(tenant=tenant))
        if not leases:
            return "That tenant has no current active lease in properties you can access."
        conversation.context["staff_tenant_simulation"] = {
            "tenant_id": tenant.pk,
            "started_by_staff_id": staff_user.pk,
            "started_at": timezone.now().isoformat(),
        }
        self._clear_staff_upload_state(conversation)
        self._clear_context_keys(
            conversation,
            "staff_tenant_simulator_options",
            "pending_media_id",
            "pending_payment_id",
            "payment_apply_lease_options",
            "payment_apply_retry_count",
            "payment_receipt_review",
            "pending_maintenance_id",
            "maintenance_draft",
        )
        conversation.selected_mode = WhatsAppConversation.MODE_TENANT
        conversation.mode_expires_at = timezone.now() + timedelta(
            minutes=getattr(settings, "WHATSAPP_MODE_SESSION_MINUTES", 60)
        )
        conversation.pending_state = ""
        conversation.staff_user = staff_user
        conversation.tenant = tenant
        conversation.selected_lease = leases[0] if len(leases) == 1 else None
        conversation.selected_property = leases[0].unit.property if len(leases) == 1 else None
        conversation.selected_unit = leases[0].unit if len(leases) == 1 else None
        if len(leases) > 1:
            conversation.context["lease_options"] = [lease.pk for lease in leases]
            conversation.pending_state = "lease_selection"
        conversation.save()
        log_staff_action(
            staff_user,
            message_log.phone_number,
            "tenant_simulation_started",
            "allowed",
            tenant=tenant,
            lease=leases[0] if len(leases) == 1 else None,
            property=leases[0].unit.property if len(leases) == 1 else None,
        )
        header = (
            "ACTING AS TENANT (LIVE)\n"
            f"Tenant: {tenant.get_full_name()}\n"
            "Requests are submitted for this tenant; replies stay on your staff phone.\n"
            "Type EXIT to return to Staff Mode.\n\n"
        )
        if len(leases) == 1:
            return header + self._tenant_welcome_menu(leases[0])
        return header + lease_option_lines(leases)

    def _consume_staff_menu_state(self, message_log, conversation, text, staff_user):
        state = conversation.pending_state
        lowered = (text or "").strip().lower()
        if state in {"staff_tenant_simulator_lookup", "staff_tenant_simulator_selection"}:
            return self._consume_staff_tenant_simulator(message_log, conversation, text, staff_user)
        if state == "staff_selected_lease_menu":
            return self._consume_staff_selected_lease_menu(
                message_log, conversation, text, staff_user
            )
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
                return self._start_staff_lease_target(conversation, staff_user, "lease_renew")
            if lowered in {"3", "end lease", "terminate"}:
                return self._start_staff_lease_target(conversation, staff_user, "lease_end")
            if lowered in {"4", "view lease", "view"}:
                return self._start_staff_lease_target(conversation, staff_user, "lease_view")
            if lowered in {"5", "upload lease document", "upload"}:
                conversation.pending_state = "staff_lease_upload_kind"
                conversation.save(update_fields=["pending_state", "updated_at"])
                return "Upload to Lease\n\n1. Lease Document\n2. Lease Photos\n3. Back\n\nReply with a number."
            if lowered in {"6", "lease ledger", "ledger"}:
                return self._start_staff_lease_target(conversation, staff_user, "lease_ledger")
            if lowered in {"7", "lease balance", "balance"}:
                return self._start_staff_lease_target(conversation, staff_user, "lease_balance")
            if lowered in {"8", "agreement", "agreement view", "agreement edit"}:
                return self._start_staff_lease_target(conversation, staff_user, "lease_agreement")
            if lowered in {"9", "back"}:
                conversation.pending_state = ""
                conversation.save(update_fields=["pending_state", "updated_at"])
                return staff_menu_text(staff_user)
            return "Please choose a Lease Management option by number, or type BACK."

        if state == "staff_lease_upload_kind":
            if lowered in {"1", "document", "lease document", "upload lease document"}:
                return self._start_staff_lease_target(conversation, staff_user, "lease_upload")
            if lowered in {"2", "photo", "photos", "lease photo", "lease photos", "upload lease photos"}:
                return self._start_staff_lease_target(conversation, staff_user, "lease_photo")
            if lowered in {"3", "back", "cancel"}:
                conversation.pending_state = "staff_lease_management"
                conversation.save(update_fields=["pending_state", "updated_at"])
                return staff_submenu_text("2")
            return "Please choose 1 for Lease Document, 2 for Lease Photos, or 3 to go back."

        if state in {"staff_lease_target_property", "staff_lease_target_unit"}:
            return self._consume_staff_lease_target(message_log, conversation, text, staff_user)

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
            kind_map = {
                "1": PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO, "property photo": PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO, "property photos": PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO,
                "2": PendingWhatsAppMedia.TARGET_UNIT_PHOTO, "unit photo": PendingWhatsAppMedia.TARGET_UNIT_PHOTO, "unit photos": PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
                "3": PendingWhatsAppMedia.TARGET_LEASE_PHOTO, "lease photo": PendingWhatsAppMedia.TARGET_LEASE_PHOTO, "lease photos": PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
                "4": PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT, "tenant document": PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT, "tenant documents": PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT,
            }
            if lowered in kind_map:
                kind = kind_map[lowered]
                prompt = "Send the building/property name to select the photo target." if kind == PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO else "Send the property and unit to select the unit target." if kind == PendingWhatsAppMedia.TARGET_UNIT_PHOTO else "Send tenant name, property, unit, phone, or CNIC to select the lease target."
                response = self._start_staff_upload_target_search(conversation, kind, prompt, staff_user)
                hinted_property_id = conversation.context.pop(
                    "staff_property_media_property_hint_id", None
                )
                if hinted_property_id:
                    hinted_property = next(
                        (
                            item
                            for item in self._staff_accessible_properties(staff_user)
                            if item.pk == hinted_property_id
                        ),
                        None,
                    )
                    conversation.save(update_fields=["context", "updated_at"])
                    if hinted_property:
                        return self._consume_staff_upload_target_query(
                            message_log, conversation, hinted_property.property_name, staff_user
                        )
                return response
            if lowered in {"5", "view photos"}:
                return self._staff_property_media_summary(message_log, staff_user)
            if lowered in {"6", "back"}:
                conversation.pending_state = ""
                conversation.context.pop("staff_property_media_property_hint_id", None)
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return staff_menu_text(staff_user)

            # Accept the property before the photo-type prompt. Staff commonly send
            # "F35" first and then choose Property Photos or Unit Photos.
            properties = self._staff_accessible_properties(staff_user)
            property_obj, unit_hint = self._resolve_staff_property_unit_text(text, properties)
            if property_obj is not None and not unit_hint:
                conversation.context["staff_property_media_property_hint_id"] = property_obj.pk
                conversation.save(update_fields=["context", "updated_at"])
                return (
                    f"Property selected: {property_obj.property_name}.\n\n"
                    f"{staff_submenu_text('5')}"
                )
            return "Please choose a Property Menu option by number, type a property name, or type BACK."

        if state == "staff_upload_target_query":
            return self._consume_staff_upload_target_query(message_log, conversation, text, staff_user)
        if state == "staff_upload_target_selection":
            return self._consume_staff_upload_target_selection(message_log, conversation, text, staff_user)
        if state == "staff_upload_target_confirmation":
            if lowered in {"yes", "y", "confirm"}:
                return self._confirm_staff_upload_target(
                    message_log, conversation, staff_user
                )
            if lowered == "back":
                kind = conversation.context.get("staff_upload_kind")
                return self._start_staff_upload_target_search(
                    conversation,
                    kind,
                    "Send the property and target again.",
                    staff_user,
                )
            if lowered == "cancel":
                conversation.pending_state = ""
                self._clear_staff_upload_state(conversation)
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return staff_menu_text(staff_user)
            label = conversation.context.get("staff_upload_target_label") or "the selected target"
            return (
                f"Confirm upload target: {label}. "
                "Reply YES to begin uploading or BACK to change it."
            )
        if state == "staff_waiting_upload":
            if lowered in {"done", "submit", "finished", "finish"}:
                batch_key = conversation.context.get("staff_upload_batch_key")
                count = PendingWhatsAppMedia.objects.filter(batch_key=batch_key).count() if batch_key else 0
                first_batch_item = (
                    PendingWhatsAppMedia.objects.filter(batch_key=batch_key)
                    .order_by("created_at", "pk")
                    .first()
                    if batch_key
                    else None
                )
                conversation.pending_state = ""
                self._clear_staff_upload_state(conversation)
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                if first_batch_item and count > 0:
                    notify_staff_pending_request("upload", first_batch_item)
                    return (
                        f"Upload batch submitted for approval with {count} file(s).\n\n"
                        f"{staff_menu_text(staff_user)}",
                        "staff_upload_submitted",
                        {"staff_user": staff_user},
                    )
                return (
                    "No files were received, so nothing was submitted for approval.\n\n"
                    f"{staff_menu_text(staff_user)}",
                    "staff_upload_empty",
                    {"staff_user": staff_user},
                )
            if lowered in {"cancel", "back"}:
                conversation.pending_state = ""
                self._clear_staff_upload_state(conversation)
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return (
                    "Upload session closed. Any files already sent remain pending for approval.\n\n"
                    f"{staff_menu_text(staff_user)}"
                )
            return "Please send another file, or reply DONE to submit the batch for approval."

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

    def _start_staff_upload_target_search(self, conversation, kind, prompt, staff_user=None):
        staff_user = staff_user or conversation.staff_user
        valid_kinds = {
            PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO,
            PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
            PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
            PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT,
        }
        if kind not in valid_kinds:
            conversation.pending_state = ""
            self._clear_staff_upload_state(conversation)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return "The upload type was missing or invalid. Please start again from the Staff Menu."
        properties = self._staff_accessible_properties(staff_user)
        if not properties:
            conversation.pending_state = ""
            self._clear_staff_upload_state(conversation)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return "No WhatsApp property access is assigned to your staff user."
        conversation.pending_state = "staff_upload_target_query"
        self._clear_staff_upload_state(conversation)
        conversation.context["staff_upload_kind"] = kind
        conversation.context["staff_upload_target_options"] = [
            {"type": "property", "id": item.pk, "label": item.property_name}
            for item in properties
        ]
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return self._staff_upload_property_options_text(properties, kind)

    def _staff_upload_property_options_text(self, properties, kind, unit_hint=""):
        if unit_hint:
            lines = [f"Which property contains unit {unit_hint}?"]
        elif kind == PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO:
            lines = ["Select a property for the property photos:"]
        else:
            lines = ["Select the target property:"]
        for index, property_obj in enumerate(properties, start=1):
            lines.append(f"{index}. {property_obj.property_name}")
        lines.extend([
            "",
            "Or type property and unit together, for example:",
            "f35-1 or f56-room7",
            "You can also send the tenant name, phone, or CNIC.",
            "",
            "Reply with the list number shown above; this is not the flat/unit number.",
            "Type BACK to cancel.",
        ])
        return "\n".join(lines)

    def _consume_staff_upload_target_query(self, message_log, conversation, text, staff_user):
        if (text or "").strip().lower() in {"back", "cancel"}:
            conversation.pending_state = "staff_property_media_menu"
            self._clear_staff_upload_state(conversation)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return staff_submenu_text("5")
        kind = conversation.context.get("staff_upload_kind")
        properties = self._staff_accessible_properties(staff_user)
        if not properties:
            conversation.pending_state = ""
            self._clear_staff_upload_state(conversation)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return (
                "No WhatsApp property access is assigned to your staff user.\n\n"
                f"{staff_menu_text(staff_user)}"
            )

        property_obj, unit_hint = self._resolve_staff_property_unit_text(text, properties)
        if property_obj is None:
            property_ids = [item.pk for item in properties]
            property_obj = self._option_from_number(text, Property, property_ids)

        if property_obj is None:
            tenants = self._staff_tenant_identifier_matches(staff_user, text)
            tenant_ids = [tenant.pk for tenant in tenants]
            leases = list(
                self._staff_current_accessible_leases(staff_user)
                .filter(tenant_id__in=tenant_ids)
                .order_by("unit__property__property_name", "unit__unit_number", "-start_date", "-id")
            )
            options = self._staff_upload_options_from_leases(kind, leases)
            if len(options) == 1:
                return self._select_staff_upload_target(message_log, conversation, staff_user, options[0])
            if options:
                conversation.pending_state = "staff_upload_target_selection"
                conversation.context["staff_upload_target_options"] = options[:9]
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                lines = ["Select upload target:"] + [
                    f"{index}. {item['label']}" for index, item in enumerate(options[:9], start=1)
                ]
                lines.append(
                    "\nReply with the list number shown above; this is not the flat/unit number."
                )
                return "\n".join(lines)
            unit_hint = unit_hint or self._unit_only_hint(text)
            return self._staff_upload_property_options_text(properties, kind, unit_hint)

        if not staff_can_access_property(staff_user, property_obj):
            return "You do not have WhatsApp access to that property."
        if kind == PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO:
            return self._select_staff_upload_target(
                message_log,
                conversation,
                staff_user,
                {"type": "property", "id": property_obj.pk, "label": property_obj.property_name},
            )

        units = list(Unit.objects.filter(property=property_obj).order_by("unit_number")[:50])
        if not units:
            return f"No units are configured for {property_obj.property_name}."
        if unit_hint:
            unit = self._match_unit_text(unit_hint, property_obj, units)
            if unit:
                return self._finish_staff_upload_unit_target(
                    message_log, conversation, staff_user, unit, kind
                )
        conversation.pending_state = "staff_upload_target_selection"
        conversation.context["staff_upload_target_options"] = [
            {"type": "unit", "id": unit.pk, "label": f"{property_obj.property_name} / {unit.unit_number}"}
            for unit in units
        ]
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return self._staff_lease_unit_options_text(property_obj, units, unit_hint)

    def _staff_upload_options_from_leases(self, kind, leases):
        options = []
        seen = set()
        for lease in leases:
            if kind == PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO:
                option = {"type": "property", "id": lease.unit.property_id, "label": lease.unit.property.property_name}
            elif kind == PendingWhatsAppMedia.TARGET_UNIT_PHOTO:
                option = {"type": "unit", "id": lease.unit_id, "label": f"{lease.unit.property.property_name} / {lease.unit.unit_number}"}
            else:
                option = {"type": "lease", "id": lease.pk, "label": f"{lease.tenant.get_full_name()} - {lease.unit.property.property_name} / {lease.unit.unit_number}"}
            key = (option["type"], option["id"])
            if key not in seen:
                seen.add(key)
                options.append(option)
        return options

    def _consume_staff_upload_target_selection(self, message_log, conversation, text, staff_user):
        lowered = (text or "").strip().lower()
        if lowered == "back":
            kind = conversation.context.get("staff_upload_kind")
            return self._start_staff_upload_target_search(
                conversation,
                kind,
                "Send the property and target again.",
                staff_user,
            )
        if lowered == "cancel":
            conversation.pending_state = ""
            self._clear_staff_upload_state(conversation)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return staff_menu_text(staff_user)
        options = conversation.context.get("staff_upload_target_options") or []
        try:
            index = int((text or "").strip()) - 1
        except ValueError:
            query = self._selector_key(text)
            matching_options = [
                option
                for option in options
                if query and query == self._selector_key(option.get("label"))
            ]
            if len(matching_options) == 1:
                option = matching_options[0]
            else:
                unit_options = [option for option in options if option.get("type") == "unit"]
                units = list(
                    Unit.objects.select_related("property").filter(
                        pk__in=[option.get("id") for option in unit_options]
                    )
                )
                property_obj = units[0].property if units else None
                unit = (
                    self._match_unit_text(text, property_obj, units)
                    if property_obj and all(item.property_id == property_obj.pk for item in units)
                    else None
                )
                option = next(
                    (item for item in unit_options if item.get("id") == getattr(unit, "pk", None)),
                    None,
                )
            if not option:
                return (
                    "Reply with the list number shown above; this is not the flat/unit number. "
                    "You may also type the exact displayed target, or type BACK."
                )
        else:
            if index < 0 or index >= len(options):
                return "That target number is not in the list. Please choose again."
            option = options[index]
        kind = conversation.context.get("staff_upload_kind")
        if option.get("type") == "unit" and kind != PendingWhatsAppMedia.TARGET_UNIT_PHOTO:
            unit = Unit.objects.select_related("property").filter(pk=option.get("id")).first()
            if not unit:
                return "That unit is no longer available. Type BACK and try again."
            return self._finish_staff_upload_unit_target(
                message_log, conversation, staff_user, unit, kind
            )
        return self._select_staff_upload_target(message_log, conversation, staff_user, option)

    def _finish_staff_upload_unit_target(self, message_log, conversation, staff_user, unit, kind):
        if kind == PendingWhatsAppMedia.TARGET_UNIT_PHOTO:
            return self._select_staff_upload_target(
                message_log,
                conversation,
                staff_user,
                {"type": "unit", "id": unit.pk, "label": f"{unit.property.property_name} / {unit.unit_number}"},
            )
        lease = self._staff_current_accessible_leases(staff_user).filter(unit=unit).first()
        if not lease:
            return f"No active lease was found for {unit.property.property_name} / {unit.unit_number}. Choose another unit or type BACK."
        return self._select_staff_upload_target(
            message_log,
            conversation,
            staff_user,
            {"type": "lease", "id": lease.pk, "label": f"{unit.property.property_name} / {unit.unit_number} - {lease.tenant.get_full_name()}"},
        )

    def _select_staff_upload_target(self, message_log, conversation, staff_user, option):
        property_obj = None
        target_lease = None
        unit = None
        self._clear_context_keys(
            conversation,
            "staff_upload_batch_key",
            "staff_upload_property_id",
            "staff_upload_unit_id",
            "staff_upload_lease_id",
            "staff_upload_target_options",
            "staff_upload_target_label",
        )
        if option["type"] == "property":
            property_obj = Property.objects.filter(pk=option["id"]).first()
            conversation.context["staff_upload_property_id"] = option["id"]
        elif option["type"] == "unit":
            unit = Unit.objects.select_related("property").filter(pk=option["id"]).first()
            property_obj = getattr(unit, "property", None)
            conversation.context["staff_upload_unit_id"] = option["id"]
            if unit:
                target_lease = self._staff_current_accessible_leases(
                    staff_user
                ).filter(unit=unit).first()
                if target_lease:
                    conversation.context["staff_upload_lease_id"] = target_lease.pk
        else:
            lease = Lease.objects.select_related("unit__property").filter(pk=option["id"]).first()
            target_lease = lease
            property_obj = getattr(getattr(lease, "unit", None), "property", None)
            conversation.context["staff_upload_lease_id"] = option["id"]
        if not property_obj or not staff_can_access_property(staff_user, property_obj):
            log_staff_action(staff_user, message_log.phone_number, "photo_upload_target_blocked", "blocked", property=property_obj)
            conversation.pending_state = ""
            self._clear_staff_upload_state(conversation)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return "You do not have WhatsApp access to that target."
        conversation.context["staff_upload_property_id"] = property_obj.pk
        if target_lease:
            conversation.context["staff_upload_unit_id"] = target_lease.unit_id
        conversation.context["staff_upload_target_label"] = option["label"]
        conversation.pending_state = "staff_upload_target_confirmation"
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return (
            f"Confirm upload target: {option['label']}. "
            "Reply YES to begin uploading or BACK to change it."
        )

    def _confirm_staff_upload_target(self, message_log, conversation, staff_user):
        kind = conversation.context.get("staff_upload_kind")
        property_obj = Property.objects.filter(
            pk=conversation.context.get("staff_upload_property_id")
        ).first()
        unit = Unit.objects.select_related("property").filter(
            pk=conversation.context.get("staff_upload_unit_id")
        ).first()
        target_lease = Lease.objects.select_related("tenant", "unit__property").filter(
            pk=conversation.context.get("staff_upload_lease_id")
        ).first()
        label = conversation.context.get("staff_upload_target_label") or ""
        valid_kinds = {
            PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO,
            PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
            PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
            PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT,
        }
        valid_target = bool(
            kind in valid_kinds
            and property_obj
            and staff_can_access_property(staff_user, property_obj)
        )
        if kind == PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO:
            unit = None
            target_lease = None
        elif kind == PendingWhatsAppMedia.TARGET_UNIT_PHOTO:
            valid_target = valid_target and unit is not None and unit.property_id == property_obj.pk
            if target_lease and target_lease.unit_id != getattr(unit, "pk", None):
                valid_target = False
        else:
            valid_target = (
                valid_target
                and target_lease is not None
                and target_lease.unit.property_id == property_obj.pk
            )
            unit = getattr(target_lease, "unit", None)
        if not valid_target:
            conversation.pending_state = ""
            self._clear_staff_upload_state(conversation)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return (
                "The selected upload target is no longer valid or accessible. "
                "Please start again from the Staff Menu.\n\n"
                f"{staff_menu_text(staff_user)}"
            )

        conversation.context["staff_upload_batch_key"] = str(uuid.uuid4())
        conversation.pending_state = "staff_waiting_upload"
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        option = {
            "type": (
                "property"
                if kind == PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO
                else "unit"
                if kind == PendingWhatsAppMedia.TARGET_UNIT_PHOTO
                else "lease"
            ),
            "id": (
                property_obj.pk
                if kind == PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO
                else unit.pk
                if kind == PendingWhatsAppMedia.TARGET_UNIT_PHOTO
                else target_lease.pk
            ),
            "label": label,
        }
        log_staff_action(
            staff_user,
            message_log.phone_number,
            "photo_upload_target_selected",
            "pending",
            property=property_obj,
            target=option,
        )
        link_text = ""
        if (
            kind == PendingWhatsAppMedia.TARGET_UNIT_PHOTO
            and target_lease
        ):
            upload_link = self._create_unit_photo_upload_link(target_lease)
            link_text = (
                "\n\nSecure gallery upload link (no login required):\n"
                f"{upload_link}\n\n"
                "Open it or forward it to the tenant. The lease, property, and "
                "unit are already selected."
            )
        return (
            f"Target selected: {label}"
            f"{link_text}\n\n"
            "You can also send photos/files directly in WhatsApp. Reply DONE "
            "when finished. Each file will wait for approval."
        )

    def _create_unit_photo_upload_link(self, lease):
        from properties.public_upload_links import make_unit_photo_upload_token

        token = make_unit_photo_upload_token(lease)
        return build_public_url("properties:public_unit_photo_upload", args=[token])

    def _unit_photo_upload_link_reply(self, lease):
        link = self._create_unit_photo_upload_link(lease)
        return (
            "Upload Unit Photos\n\n"
            f"Lease: #{lease.pk} - {lease.tenant}\n"
            f"Property: {lease.unit.property.property_name}\n"
            f"Unit: {lease.unit.unit_number}\n\n"
            "Open this secure link and choose photos from your gallery:\n"
            f"{link}\n\n"
            "No login is required. The destination is already selected and "
            "the link expires after 48 hours."
        )

    def _start_staff_lease_target(self, conversation, staff_user, action):
        properties = self._staff_accessible_properties(staff_user)
        if not properties:
            return "No WhatsApp property access is assigned to your staff user."
        conversation.pending_state = "staff_lease_target_property"
        conversation.context["staff_lease_target"] = {
            "action": action,
            "property_options": [item.pk for item in properties],
        }
        self._clear_context_keys(conversation, "staff_search_action", "staff_search_options")
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return self._staff_lease_property_options_text(properties, action)

    def _staff_lease_property_options_text(self, properties, action, unit_hint=""):
        labels = {
            "lease_select": "select",
            "lease_renew": "renew",
            "lease_end": "end",
            "lease_view": "view",
            "lease_upload": "upload a document for",
            "lease_photo": "upload photos for",
            "lease_ledger": "open the ledger for",
            "lease_balance": "check the balance for",
            "lease_agreement": "open the agreement for",
        }
        if unit_hint:
            lines = [f"Which property contains unit {unit_hint}?"]
        else:
            lines = [f"Select a property to {labels.get(action, 'open')} a lease:"]
        for index, property_obj in enumerate(properties, start=1):
            lines.append(f"{index}. {property_obj.property_name}")
        lines.extend([
            "",
            "Or type property and unit together, for example:",
            "f35-7 or f56-room7",
        ])
        return "\n".join(lines)

    def _consume_staff_lease_target(self, message_log, conversation, text, staff_user):
        data = conversation.context.get("staff_lease_target") or {}
        action = data.get("action") or "lease_view"
        properties = list(Property.objects.filter(pk__in=data.get("property_options") or []).order_by("property_name"))
        properties = [item for item in properties if staff_can_access_property(staff_user, item)]
        if not properties:
            conversation.pending_state = ""
            conversation.context.pop("staff_lease_target", None)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return "Your WhatsApp property access is no longer available."

        if conversation.pending_state == "staff_lease_target_property":
            property_obj, unit_hint = self._resolve_staff_property_unit_text(text, properties)
            if property_obj is None:
                property_obj = self._option_from_number(text, Property, data.get("property_options") or [])
            if property_obj is None:
                if self._looks_like_staff_tenant_identifier(text):
                    tenants = self._staff_tenant_identifier_matches(staff_user, text)
                    if len(tenants) == 1:
                        lease = self._staff_current_accessible_leases(staff_user).filter(
                            tenant=tenants[0]
                        ).order_by("-start_date", "-id").first()
                        if lease:
                            return self._complete_staff_lease_target(
                                message_log, conversation, staff_user, lease, action
                            )
                    if len(tenants) > 1:
                        return "More than one active tenant uses that identifier. Send the full phone number or CNIC."
                unit_hint = unit_hint or self._unit_only_hint(text)
                if unit_hint:
                    data["unit_hint"] = unit_hint
                    conversation.context["staff_lease_target"] = data
                    conversation.save(update_fields=["context", "updated_at"])
                    return self._staff_lease_property_options_text(properties, action, unit_hint)
                return "Please choose a property number, type its name, or send property and unit together (for example f56-room7)."
            if not staff_can_access_property(staff_user, property_obj):
                return "You do not have WhatsApp access to that property."
            unit_hint = unit_hint or data.get("unit_hint") or ""
            units = list(Unit.objects.filter(property=property_obj).order_by("unit_number")[:50])
            if not units:
                return f"No units are configured for {property_obj.property_name}."
            if unit_hint:
                unit = self._match_unit_text(unit_hint, property_obj, units)
                if unit:
                    return self._finish_staff_lease_target(message_log, conversation, staff_user, unit, action)
            data.update({
                "property_id": property_obj.pk,
                "unit_options": [unit.pk for unit in units],
            })
            conversation.context["staff_lease_target"] = data
            conversation.pending_state = "staff_lease_target_unit"
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return self._staff_lease_unit_options_text(property_obj, units, unit_hint)

        property_obj = Property.objects.filter(pk=data.get("property_id")).first()
        if not property_obj or not staff_can_access_property(staff_user, property_obj):
            return "The selected property is no longer available. Type BACK and try again."
        units = list(Unit.objects.filter(pk__in=data.get("unit_options") or [], property=property_obj).order_by("unit_number"))
        unit = self._option_from_number(text, Unit, data.get("unit_options") or [])
        if unit is None:
            unit = self._match_unit_text(text, property_obj, units)
        if not unit:
            return self._staff_lease_unit_options_text(property_obj, units, text)
        return self._finish_staff_lease_target(message_log, conversation, staff_user, unit, action)

    def _staff_lease_unit_options_text(self, property_obj, units, unmatched=""):
        lines = [f"Select unit for {property_obj.property_name}:"]
        for index, unit in enumerate(units, start=1):
            lines.append(f"{index}. {unit.unit_number}")
        if unmatched:
            lines.extend(["", f"I could not match unit '{unmatched}' exactly. Please choose from the list."])
        lines.extend([
            "",
            "Reply with the list number shown above; this is not the flat/unit number.",
            "You may also type the exact unit shown above.",
            "Reply BACK to return to the Staff Menu.",
        ])
        return "\n".join(lines)

    def _resolve_staff_property_unit_text(self, text, properties):
        normalized = self._selector_key(text)
        if not normalized:
            return None, ""
        matches = []
        for property_obj in properties:
            property_key = self._selector_key(property_obj.property_name)
            property_code = self._property_code(property_obj.property_name)
            for matched_key in {property_key, property_code}:
                position = normalized.find(matched_key) if matched_key else -1
                if position >= 0:
                    matches.append((len(matched_key), -position, property_obj, matched_key, position))
        if not matches:
            return None, ""
        _length, _position_rank, property_obj, matched_key, position = max(
            matches, key=lambda item: (item[0], item[1])
        )
        unit_hint = self._unit_only_hint(text)
        if not unit_hint and position == 0:
            unit_hint = self._strip_selector_words(normalized[len(matched_key):])
        return property_obj, unit_hint

    def _match_unit_text(self, text, property_obj, units):
        query = self._selector_key(text)
        if not query:
            return None
        property_keys = {self._selector_key(property_obj.property_name), self._property_code(property_obj.property_name)}
        candidates = []
        for unit in units:
            unit_key = self._selector_key(unit.unit_number)
            aliases = {unit_key, self._strip_selector_words(unit_key)}
            for property_key in property_keys:
                if property_key and unit_key.startswith(property_key):
                    remainder = unit_key[len(property_key):]
                    aliases.update({remainder, self._strip_selector_words(remainder)})
            normalized_aliases = {self._normalize_selector_digits(alias) for alias in aliases if alias}
            query_variants = {
                self._normalize_selector_digits(query),
                self._normalize_selector_digits(self._strip_selector_words(query)),
            }
            if normalized_aliases & query_variants:
                candidates.append(unit)
        return candidates[0] if len(candidates) == 1 else None

    def _unit_only_hint(self, text):
        lowered = (text or "").strip().lower()
        match = re.search(r"\b(?:flat|unit|room)\s*#?\s*([a-z0-9]+(?:-[a-z0-9]+)?)", lowered)
        return self._selector_key(match.group(1)) if match else ""

    def _selector_key(self, value):
        return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

    def _property_code(self, value):
        match = re.search(r"[a-z]+\s*[-#]?\s*\d+", str(value or "").lower())
        return self._selector_key(match.group(0)) if match else ""

    def _strip_selector_words(self, value):
        result = str(value or "")
        for word in ("unit", "flat", "room", "number", "no"):
            result = result.replace(word, "")
        return result

    def _normalize_selector_digits(self, value):
        return re.sub(r"\d+", lambda match: str(int(match.group(0))), str(value or ""))

    def _finish_staff_lease_target(self, message_log, conversation, staff_user, unit, action):
        lease_qs = self._staff_accessible_leases(staff_user).filter(unit=unit)
        if action in {"lease_renew", "lease_end", "lease_upload", "lease_photo", "lease_agreement"}:
            lease = lease_qs.filter(status="active").order_by("-start_date", "-id").first()
        else:
            lease = lease_qs.order_by("-start_date", "-id").first()
        if not lease:
            return f"No {'active ' if action in {'lease_renew', 'lease_end', 'lease_upload', 'lease_photo', 'lease_agreement'} else ''}lease was found for {unit.property.property_name} / {unit.unit_number}. Choose another unit or type BACK."

        return self._complete_staff_lease_target(message_log, conversation, staff_user, lease, action)

    def _complete_staff_lease_target(self, message_log, conversation, staff_user, lease, action):
        unit = lease.unit
        conversation.context.pop("staff_lease_target", None)
        if action in {"lease_upload", "lease_photo"}:
            conversation.context["staff_upload_kind"] = (
                PendingWhatsAppMedia.TARGET_LEASE_PHOTO
                if action == "lease_photo"
                else PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT
            )
            conversation.pending_state = "staff_upload_target_selection"
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return self._select_staff_upload_target(
                message_log,
                conversation,
                staff_user,
                {"type": "lease", "id": lease.pk, "label": f"{unit.property.property_name} / {unit.unit_number} - {lease.tenant.get_full_name()}"},
            )
        conversation.pending_state = ""
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        if action == "lease_agreement":
            view_link = self._create_staff_agreement_link_for_lease(message_log, staff_user, lease, WhatsAppExternalLinkToken.LINK_AGREEMENT_VIEW)
            edit_link = self._create_staff_agreement_link_for_lease(message_log, staff_user, lease, WhatsAppExternalLinkToken.LINK_AGREEMENT_EDIT)
            response = f"Agreement Links\n\n{unit.property.property_name} / {unit.unit_number}\nTenant: {lease.tenant.get_full_name()}\n\nView:\n{view_link}\n\nEdit:\n{edit_link}"
        elif action == "lease_select":
            response = (
                "Unit selected\n\n"
                f"{unit.property.property_name} / {unit.unit_number}\n"
                f"Tenant: {lease.tenant.get_full_name()}"
            )
        else:
            response = self._staff_lease_action_reply(message_log, staff_user, lease, action)
        return self._staff_selected_lease_result(conversation, lease, response)

    def _staff_selected_lease_result(self, conversation, lease, response):
        conversation.selected_lease = lease
        conversation.selected_property = lease.unit.property
        conversation.selected_unit = lease.unit
        conversation.pending_state = "staff_selected_lease_menu"
        conversation.context["staff_selected_lease_id"] = lease.pk
        conversation.save(update_fields=[
            "selected_lease", "selected_property", "selected_unit",
            "pending_state", "context", "updated_at",
        ])
        return f"{response}\n\n{self._staff_selected_lease_menu_text(lease)}"

    def _staff_selected_lease_menu_text(self, lease):
        return (
            f"Selected: {lease.unit.property.property_name} / {lease.unit.unit_number}\n\n"
            "1. Lease information\n"
            "2. Balance\n"
            "3. Ledger\n"
            "4. Agreement\n"
            "5. Last payment\n"
            "6. Tenant details\n"
            "7. Renew lease\n"
            "8. End lease\n"
            "9. Upload document\n"
            "10. Change unit\n"
            "11. Change property\n"
            "12. Staff menu\n\n"
            "Reply with a number or type your request."
        )

    def _consume_staff_selected_lease_menu(self, message_log, conversation, text, staff_user):
        lease = Lease.objects.select_related("tenant", "unit__property").filter(
            pk=conversation.context.get("staff_selected_lease_id") or conversation.selected_lease_id
        ).first()
        if not lease or not staff_can_access_property(staff_user, lease.unit.property):
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "staff_selected_lease_id")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return "The selected lease is no longer available.\n\n" + staff_menu_text(staff_user)

        lowered = (text or "").strip().lower()
        action_aliases = {
            "1": "lease_view", "lease": "lease_view", "lease information": "lease_view", "view lease": "lease_view",
            "2": "lease_balance", "balance": "lease_balance", "show balance": "lease_balance",
            "3": "lease_ledger", "ledger": "lease_ledger", "statement": "lease_ledger", "view ledger": "lease_ledger",
            "4": "lease_agreement", "agreement": "lease_agreement", "view agreement": "lease_agreement",
            "7": "lease_renew", "renew": "lease_renew", "renew lease": "lease_renew",
            "8": "lease_end", "end": "lease_end", "end lease": "lease_end", "terminate": "lease_end",
            "9": "lease_upload", "upload": "lease_upload", "upload document": "lease_upload",
        }
        action = action_aliases.get(lowered)
        if not action and "balance" in lowered:
            action = "lease_balance"
        elif not action and ("ledger" in lowered or "statement" in lowered):
            action = "lease_ledger"
        elif not action and "agreement" in lowered:
            action = "lease_agreement"
        elif not action and "renew" in lowered:
            action = "lease_renew"
        elif not action and ("end lease" in lowered or "terminate" in lowered):
            action = "lease_end"
        elif not action and "upload" in lowered and "document" in lowered:
            action = "lease_upload"
        if action:
            return self._complete_staff_lease_target(
                message_log, conversation, staff_user, lease, action
            )
        if lowered in {"5", "last payment", "latest payment", "last receipt", "payment receipt"} or (
            ("last" in lowered or "latest" in lowered) and ("payment" in lowered or "receipt" in lowered)
        ):
            payment = lease.payments.order_by("-payment_date", "-id").first()
            if payment:
                response = (
                    "Last Payment\n\n"
                    f"Date: {payment.payment_date}\n"
                    f"Amount: Rs. {payment.amount}\n"
                    f"Reference: {payment.reference_number or '-'}\n"
                    f"Method: {payment.payment_method or '-'}"
                )
            else:
                response = "No payment record was found for the selected lease."
            return self._staff_selected_lease_result(conversation, lease, response)
        if lowered in {"6", "tenant", "tenant details", "view tenant"} or "tenant detail" in lowered:
            response = self._staff_tenant_action_reply(
                message_log, staff_user, lease.tenant, "tenant_view"
            )
            return self._staff_selected_lease_result(conversation, lease, response)
        if lowered in {"10", "change unit", "another unit", "select unit"}:
            units = list(Unit.objects.filter(property=lease.unit.property).order_by("unit_number")[:50])
            conversation.context["staff_lease_target"] = {
                "action": "lease_select",
                "property_options": [lease.unit.property_id],
                "property_id": lease.unit.property_id,
                "unit_options": [unit.pk for unit in units],
            }
            conversation.pending_state = "staff_lease_target_unit"
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return self._staff_lease_unit_options_text(lease.unit.property, units)
        if lowered in {"11", "change property", "another property", "select property"}:
            return self._start_staff_lease_target(conversation, staff_user, "lease_select")
        if lowered in {"12", "staff menu", "main menu"}:
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "staff_selected_lease_id")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return staff_menu_text(staff_user)
        return "Please choose an option for the selected unit.\n\n" + self._staff_selected_lease_menu_text(lease)

    def _looks_like_staff_tenant_identifier(self, text):
        value = (text or "").strip().lower()
        digits = "".join(ch for ch in value if ch.isdigit())
        return len(digits) >= 7 or bool(re.fullmatch(r"tenant\s*#?\s*\d{1,6}", value))

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
            if self._looks_like_staff_tenant_identifier(text):
                tenant_matches = self._staff_tenant_identifier_matches(staff_user, text)
                tenant_ids = [tenant.pk for tenant in tenant_matches]
                property_ids = self._staff_current_accessible_leases(staff_user).filter(
                    tenant_id__in=tenant_ids
                ).values_list("unit__property_id", flat=True)
                properties = list(Property.objects.filter(pk__in=property_ids).distinct().order_by("property_name"))
            else:
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
        action = conversation.context.get("staff_search_action") or ""
        try:
            selected_index = int((text or "").strip()) - 1
        except ValueError:
            conversation.pending_state = "staff_search_query"
            conversation.context.pop("staff_search_options", None)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return self._consume_staff_search_query(
                message_log,
                conversation,
                _strip_staff_action_words(text),
                staff_user,
            )
        options = conversation.context.get("staff_search_options") or []
        if selected_index < 0 or selected_index >= len(options):
            digits = "".join(ch for ch in str(text or "") if ch.isdigit())
            if len(digits) >= 7:
                conversation.pending_state = "staff_search_query"
                conversation.context.pop("staff_search_options", None)
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return self._consume_staff_search_query(message_log, conversation, text, staff_user)
            return "That result number is not in the list. Please choose again or type BACK."
        selected = options[selected_index]
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
            for token in _staff_search_tokens(query, minimum_length=4):
                tenant_query |= Q(first_name__icontains=token) | Q(last_name__icontains=token)
        if len(cnic_digits) == 13:
            tenant_query |= Q(cnic_digits=cnic_digits) | Q(cnic__icontains=query)
        if len(digits) >= 7:
            suffix = digits[-10:]
            tenant_query |= Q(phone__icontains=suffix) | Q(phone2__icontains=suffix) | Q(phone3__icontains=suffix)
        if tenant_query:
            scoped_lease_tenant_ids = set(self._staff_accessible_leases(staff_user).values_list("tenant_id", flat=True))
            tenant_ids.update(Tenant.objects.filter(tenant_query, pk__in=scoped_lease_tenant_ids).values_list("pk", flat=True))
        return list(Tenant.objects.filter(pk__in=tenant_ids).order_by("first_name", "last_name")[:10])

    def _staff_search_leases(self, staff_user, text):
        query_text = (text or "").strip()
        digits = "".join(ch for ch in query_text if ch.isdigit())
        cnic_digits = normalize_cnic(query_text)
        query = Q()
        if len(cnic_digits) == 13:
            query |= Q(tenant__cnic_digits=cnic_digits) | Q(tenant__cnic__icontains=query_text)
        if len(digits) >= 7:
            suffix = digits[-10:]
            query |= Q(tenant__phone__icontains=suffix) | Q(tenant__phone2__icontains=suffix) | Q(tenant__phone3__icontains=suffix)
        matching_unit_ids = self._staff_unit_ids_matching_text(staff_user, query_text)
        if matching_unit_ids:
            query |= Q(unit_id__in=matching_unit_ids)
        search_tokens = _staff_search_tokens(query_text)
        for token in search_tokens:
            if len(token) >= 4:
                query |= Q(tenant__first_name__icontains=token) | Q(tenant__last_name__icontains=token)
            query |= Q(unit__property__property_name__icontains=token) | Q(unit__unit_number__icontains=token)
        if not query:
            return []
        return list(self._staff_accessible_leases(staff_user).filter(query).distinct().order_by("-start_date", "-id")[:10])

    def _staff_search_invoices(self, staff_user, text):
        leases = self._staff_accessible_leases(staff_user)
        query = (text or "").strip()
        invoice_query = Q()
        if query:
            invoice_query |= Q(invoice_number__icontains=query)
        for token in _staff_search_tokens(query):
            if len(token) >= 4:
                invoice_query |= Q(lease__tenant__first_name__icontains=token) | Q(lease__tenant__last_name__icontains=token)
            invoice_query |= Q(lease__unit__property__property_name__icontains=token) | Q(lease__unit__unit_number__icontains=token)
        if not invoice_query:
            return []
        return list(Invoice.objects.select_related("lease__tenant", "lease__unit__property").filter(invoice_query, lease__in=leases).order_by("-issue_date", "-id")[:10])

    def _staff_search_units(self, staff_user, text):
        property_ids = [item.pk for item in self._staff_accessible_properties(staff_user)]
        units = Unit.objects.select_related("property")
        if not staff_user.is_superuser:
            units = units.filter(property_id__in=property_ids)
        query = (text or "").strip()
        exact_ids = self._staff_unit_ids_matching_text(staff_user, query)
        if exact_ids:
            return list(units.filter(pk__in=exact_ids).order_by("property__property_name", "unit_number")[:10])
        unit_query = Q()
        if query:
            unit_query |= Q(unit_number__icontains=query)
        for token in _staff_search_tokens(query):
            unit_query |= Q(property__property_name__icontains=token) | Q(unit_number__icontains=token)
        if not unit_query:
            return []
        return list(units.filter(unit_query).order_by("property__property_name", "unit_number")[:10])

    def _staff_unit_ids_matching_text(self, staff_user, text):
        query_text = (text or "").strip()
        unit_phrase_match = re.search(r"\b(?:flat|unit|room)\s*#?\s*0*([a-z0-9-]+)", query_text.lower())
        unit_phrase = unit_phrase_match.group(0) if unit_phrase_match else query_text
        matched_ids = []
        for property_obj in self._staff_accessible_properties(staff_user):
            units = list(Unit.objects.filter(property=property_obj).order_by("unit_number"))
            unit = self._match_unit_text(unit_phrase, property_obj, units)
            if unit:
                matched_ids.append(unit.pk)
        return matched_ids

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
            link = create_public_ledger_link(lease, phone_number=message_log.phone_number, staff_user=staff_user)
            ledger_link = public_ledger_url(link)
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
        link = build_public_url("invoices:public_invoice_detail", args=[token])
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
        return build_public_url("leases:public_lease_create", args=[token.token])

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
        link = self._create_staff_agreement_link_for_lease(message_log, staff_user, lease, link_type)
        label = "Agreement view link" if link_type == WhatsAppExternalLinkToken.LINK_AGREEMENT_VIEW else "Agreement edit link"
        return f"{label} created.\n\nLink:\n{link}"

    def _create_staff_agreement_link_for_lease(self, message_log, staff_user, lease, link_type):
        property_obj = lease.unit.property
        token = WhatsAppExternalLinkToken.objects.create(
            link_type=link_type,
            phone_number=message_log.phone_number,
            tenant=lease.tenant,
            staff_user=staff_user,
            target_app_label="leases",
            target_model="lease",
            target_object_id=lease.pk,
            metadata={"lease_id": lease.pk, "property_id": property_obj.pk},
            expires_at=timezone.now() + timedelta(days=7),
        )
        url_name = "leases:public_agreement_view" if link_type == WhatsAppExternalLinkToken.LINK_AGREEMENT_VIEW else "leases:public_agreement_edit"
        link = build_public_url(url_name, args=[token.token])
        log_staff_action(staff_user, message_log.phone_number, "agreement_link_created", "allowed", property=property_obj, tenant=lease.tenant, lease=lease, link_type=link_type)
        return link

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

        purpose = _tenant_upload_purpose_from_text(text)
        if purpose == "cancel":
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "pending_media_id")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return "Upload cancelled. The file remains staged for admin review.", "upload_cancelled", {"pending_media_id": media.pk}
        if not purpose:
            return (
                "Please reply with a number:\n\n"
                "1 Unit Photo\n2 Tenant Document\n3 Maintenance Photo\n4 Payment Receipt\n5 Police Verification\n6 Lease Photo\n7 Cancel",
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
            return self._prepare_payment_receipt_confirmation(
                message_log, conversation, selected_lease, media, text
            )
        if purpose == PendingWhatsAppMedia.PURPOSE_MAINTENANCE:
            pending = create_pending_maintenance(message_log, conversation, selected_lease, media=media)
            conversation.pending_state = "pending_maintenance"
            conversation.context["pending_maintenance_id"] = pending.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
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
        simulation = (conversation.context or {}).get("staff_tenant_simulation")
        if simulation:
            today = timezone.localdate()
            lease = Lease.objects.select_related("tenant", "unit__property").filter(
                pk=option_ids[selected_index],
                tenant_id=simulation.get("tenant_id"),
                status="active",
                start_date__lte=today,
                end_date__gte=today,
            ).first()
            if lease and not staff_can_access_property(conversation.staff_user, lease.unit.property):
                lease = None
        else:
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
        for token in _staff_search_tokens(query_text):
            if len(token) >= 4:
                query |= Q(tenant__first_name__icontains=token) | Q(tenant__last_name__icontains=token)
            query |= Q(unit__property__property_name__icontains=token) | Q(unit__unit_number__icontains=token)
        if not query:
            return []
        matches = list(leases.filter(query).distinct().order_by("unit__property__property_name", "unit__unit_number")[:10])
        if not matches and identity.active_leases:
            return list(identity.active_leases)
        return matches

    def _prepare_payment_receipt_confirmation(
        self, message_log, conversation, lease, media, text, ocr_json=None
    ):
        ocr_json = dict(
            ocr_json
            or (run_payment_ocr(media, self.ai_config) if media else extract_payment_text_fields(text))
        )
        validation = _payment_receipt_validation(ocr_json)
        if media and not validation["is_valid"]:
            return self._route_unreadable_receipt_to_staff(
                conversation, media, validation
            )
        ocr_json.update(validation["normalized_data"])
        if not ocr_json.get("amount") or not ocr_json.get("date"):
            fallback_text = ocr_json.get("text") or ""
            if _upload_purpose_from_text(text) != PendingWhatsAppMedia.PURPOSE_PAYMENT:
                fallback_text = f"{fallback_text}\n{text or ''}".strip()
            extracted = extract_payment_text_fields(fallback_text)
            for field in ("amount", "date", "reference"):
                if not ocr_json.get(field) and extracted.get(field):
                    ocr_json[field] = extracted[field]

        recognized_amount = ocr_json.get("amount")
        recognized_date = ocr_json.get("date")
        ocr_json["ocr_amount"] = recognized_amount
        ocr_json["ocr_date"] = recognized_date
        media.purpose = PendingWhatsAppMedia.PURPOSE_PAYMENT
        media.lease = lease or media.lease
        media.tenant = getattr(media.lease, "tenant", None)
        media.property = getattr(getattr(media.lease, "unit", None), "property", None)
        media.unit = getattr(media.lease, "unit", None)
        media.ai_confidence = max(
            media.ai_confidence or 0,
            int(ocr_json.get("confidence") or 0),
            85 if recognized_amount else 0,
        )
        media.ai_notes = (
            f"{media.ai_notes} Classified as a payment receipt; awaiting sender confirmation."
        ).strip()
        media.save(
            update_fields=[
                "purpose", "lease", "tenant", "property", "unit",
                "ai_confidence", "ai_notes", "updated_at",
            ]
        )

        review = {
            "media_id": media.pk,
            "lease_id": getattr(media.lease, "pk", None),
            "ocr": _json_safe(ocr_json),
            "ocr_amount": str(recognized_amount) if recognized_amount is not None else "",
            "ocr_date": recognized_date.isoformat() if hasattr(recognized_date, "isoformat") else str(recognized_date or ""),
            "tenant_amount": "",
            "tenant_date": "",
            "property_name": getattr(media.property, "property_name", ""),
            "unit_number": getattr(media.unit, "unit_number", ""),
        }
        conversation.pending_state = "payment_receipt_confirmation"
        conversation.context["payment_receipt_review"] = review
        conversation.context["pending_media_id"] = media.pk
        conversation.context.pop("pending_payment_id", None)
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return (
            _payment_receipt_review_text(review),
            "payment_receipt_confirmation",
            {
                "lease": media.lease,
                "tenant": media.tenant,
                "pending_media_id": media.pk,
                "receipt_media_handled": True,
            },
        )

    def _route_unreadable_receipt_to_staff(self, conversation, media, validation):
        problem_fields = validation["missing_fields"] + validation["invalid_fields"]
        media.purpose = PendingWhatsAppMedia.PURPOSE_PAYMENT
        media.ai_notes = (
            f"{media.ai_notes} Receipt OCR requires staff review; required fields: "
            f"{', '.join(problem_fields) or 'invalid structured output'}."
        ).strip()
        media.save(update_fields=["purpose", "ai_notes", "updated_at"])
        conversation.pending_state = ""
        self._clear_context_keys(conversation, "payment_receipt_review", "pending_media_id")
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        notify_staff_pending_request("upload", media)
        logger.warning(
            "Receipt OCR routed to staff: message_id=%s missing=%s invalid=%s",
            getattr(media.original_whatsapp_message, "wa_message_id", ""),
            ",".join(validation["missing_fields"]) or "-",
            ",".join(validation["invalid_fields"]) or "-",
        )
        return (
            "We could not reliably read all required payment details from this receipt.\n"
            "Your receipt has been saved for staff review.",
            "payment_receipt_staff_review",
            {
                "lease": media.lease,
                "tenant": media.tenant,
                "pending_media_id": media.pk,
                "receipt_media_handled": True,
                "ocr_validation": {
                    "missing_fields": validation["missing_fields"],
                    "invalid_fields": validation["invalid_fields"],
                },
            },
        )

    def _consume_payment_receipt_confirmation(self, message_log, conversation, text):
        review = dict((conversation.context or {}).get("payment_receipt_review") or {})
        if not review:
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "payment_receipt_review", "pending_media_id")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return "The payment receipt review expired. Please upload the image again.", "payment_review_missing", {}

        lowered = (text or "").strip().lower()
        if lowered in {"cancel", "back", "no"}:
            conversation.pending_state = ""
            self._clear_context_keys(conversation, "payment_receipt_review", "pending_media_id")
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return "Payment submission cancelled. The image remains available for staff review.", "payment_cancelled", {}

        corrected_amount = _payment_amount_correction(text)
        if corrected_amount is not None:
            review["tenant_amount"] = str(corrected_amount)
            conversation.context["payment_receipt_review"] = review
            conversation.save(update_fields=["context", "updated_at"])
            return (
                _payment_receipt_review_text(review, "The payment amount was corrected."),
                "payment_receipt_correction",
                {"pending_media_id": review.get("media_id")},
            )

        corrected_date = _payment_date_correction(text)
        if corrected_date is not None:
            review["tenant_date"] = corrected_date.isoformat()
            conversation.context["payment_receipt_review"] = review
            conversation.save(update_fields=["context", "updated_at"])
            return (
                _payment_receipt_review_text(review, "The payment date was corrected."),
                "payment_receipt_correction",
                {"pending_media_id": review.get("media_id")},
            )

        if not _looks_like_yes(text):
            return (
                _payment_receipt_review_text(review, "Please confirm or send a correction."),
                "payment_receipt_confirmation",
                {"pending_media_id": review.get("media_id")},
            )

        final_amount = _review_decimal(review.get("tenant_amount") or review.get("ocr_amount"))
        final_date = _review_date(review.get("tenant_date") or review.get("ocr_date"))
        reference = (review.get("ocr") or {}).get("reference")
        final_validation = _payment_receipt_validation(
            {"amount": final_amount, "date": final_date, "reference": reference}
        )
        if not final_validation["is_valid"]:
            problem_fields = final_validation["missing_fields"] + final_validation["invalid_fields"]
            missing = ", ".join(problem_fields)
            return (
                _payment_receipt_review_text(
                    review,
                    f"I could not confirm the required {missing}. Staff review is required.",
                ),
                "payment_receipt_confirmation",
                {"pending_media_id": review.get("media_id")},
            )

        media = PendingWhatsAppMedia.objects.filter(
            pk=review.get("media_id"), status=PendingWhatsAppMedia.STATUS_PENDING
        ).first()
        lease = Lease.objects.select_related("tenant", "unit__property").filter(
            pk=review.get("lease_id"), status="active"
        ).first()
        if not media or not lease:
            return "The receipt or active lease is no longer available. Please upload it again.", "payment_review_missing", {}

        ocr_json = dict(review.get("ocr") or {})
        ocr_json["amount"] = final_amount
        ocr_json["date"] = final_date
        ocr_json["ocr_amount"] = review.get("ocr_amount") or None
        ocr_json["ocr_date"] = review.get("ocr_date") or None
        ocr_json["tenant_amount"] = str(final_amount)
        ocr_json["tenant_date"] = final_date.isoformat()
        amount_corrected = bool(review.get("tenant_amount"))
        date_corrected = bool(review.get("tenant_date"))
        ocr_json["tenant_corrected_amount"] = amount_corrected
        ocr_json["tenant_corrected_date"] = date_corrected
        audit_notes = [
            f"OCR recognized amount: Rs. {review.get('ocr_amount') or 'Not detected'}.",
            f"Tenant confirmed amount: Rs. {final_amount:,.2f}.",
            f"OCR recognized date: {review.get('ocr_date') or 'Not detected'}.",
            f"Tenant confirmed date: {final_date.strftime('%d-%m-%Y')}.",
        ]
        if amount_corrected or date_corrected:
            audit_notes.append("The tenant corrected the OCR result before confirmation.")
        ocr_json["notes"] = " ".join(
            part for part in [ocr_json.get("notes", ""), *audit_notes] if part
        )

        response, _intent, metadata = self._stage_payment(
            message_log, conversation, lease, media, text, ocr_json=ocr_json
        )
        pending = PendingWhatsAppPayment.objects.filter(pk=metadata.get("pending_payment_id")).first()
        if pending:
            pending.confirmed_by_tenant = True
            pending.status = PendingWhatsAppPayment.STATUS_CONFIRMED
            pending.save(update_fields=["confirmed_by_tenant", "status", "updated_at"])
        return response, "payment_confirmed", metadata

    def _stage_payment(self, message_log, conversation, lease, media, text, ocr_json=None):
        ocr_json = ocr_json or (run_payment_ocr(media, self.ai_config) if media else extract_payment_text_fields(text))
        if not ocr_json.get("amount"):
            fallback_text = ocr_json.get("text") or ""
            if not media:
                fallback_text += "\n" + (text or "")
            extracted = extract_payment_text_fields(fallback_text)
            for field in ("amount", "date", "reference", "raw_text"):
                if not ocr_json.get(field) and extracted.get(field):
                    ocr_json[field] = extracted[field]
        match = match_payment_to_active_lease(message_log.phone_number, ocr_json)
        matched_lease = lease or match.get("lease")
        duplicate_note = _payment_duplicate_note(matched_lease, ocr_json)
        ai_notes = "\n".join(
            part for part in [ocr_json.get("notes", ""), match.get("notes", ""), duplicate_note] if part
        ).strip()
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
            ai_confidence=max(int(ocr_json.get("confidence") or 0), match.get("confidence", 0)),
            ai_notes=ai_notes,
            original_whatsapp_message=getattr(media, "original_whatsapp_message", None) or message_log,
            conversation=conversation,
        )
        conversation.pending_state = "" if media else "pending_payment_confirmation"
        self._clear_context_keys(
            conversation,
            "pending_media_id",
            "payment_apply_lease_options",
            "payment_apply_retry_count",
            "payment_receipt_review",
        )
        conversation.context["pending_payment_id"] = pending.pk
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        notify_staff_pending_request("payment", pending)
        response = _payment_received_text(pending) if media else _payment_confirmation_text(pending)
        return response, "payment_pending", {"lease": matched_lease, "tenant": getattr(matched_lease, "tenant", None), "pending_payment_id": pending.pk}

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

    def _staff_tenant_assist_response(self, conversation, response):
        simulation = (conversation.context or {}).get("staff_tenant_simulation")
        if not simulation or not response or response.startswith("ACTING AS TENANT (LIVE)"):
            return response
        tenant = conversation.tenant
        lease = conversation.selected_lease
        tenant_name = tenant.get_full_name() if tenant else f"Tenant #{simulation.get('tenant_id')}"
        location = "Select an active lease below"
        if lease and lease.unit_id:
            location = f"{lease.unit.property.property_name} / {lease.unit.unit_number}"
        return (
            "ACTING AS TENANT (LIVE)\n"
            f"Tenant: {tenant_name}\n"
            f"Property/Unit: {location}\n"
            "Type EXIT to return to Staff Mode.\n\n"
            f"{response}"
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
            conversation.pending_state = "tenant_waiting_payment_receipt"
            conversation.save(update_fields=["pending_state", "updated_at"])
            return (
                "Please upload the payment receipt screenshot here. I will read its amount, date, and reference, then submit it for bank verification.",
                "payment_receipt_upload_prompt",
                {"lease": lease, "tenant": lease.tenant},
            )
        if lowered in {"5", "latest invoice", "last invoice", "request last invoice", "request latest invoice"}:
            conversation.pending_state = ""
            conversation.save(update_fields=["pending_state", "updated_at"])
            invoice = self._latest_invoice_for_lease(lease)
            metadata = {"lease": lease, "tenant": lease.tenant}
            if invoice:
                metadata["invoice_jpg"] = invoice
            return self._latest_invoice_reply(lease), "latest_invoice", metadata

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
        if intent == "electric_bill":
            return self._latest_electric_bill_reply(lease), "electric_bill", {"lease": lease, "tenant": lease.tenant}
        if intent == "latest_invoice":
            invoice = self._latest_invoice_for_lease(lease)
            metadata = {"lease": lease, "tenant": lease.tenant}
            if invoice:
                metadata["invoice_jpg"] = invoice
            return self._latest_invoice_reply(lease), "latest_invoice", metadata
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
        for invoice in invoices:
            amount = invoice.amount or Decimal("0.00")
            token = make_public_invoice_token(invoice.pk)
            link = build_public_url("invoices:public_invoice_detail", args=[token])
            lines.append(
                f"{invoice.invoice_number}: Rs. {amount} due {invoice.due_date} ({invoice.get_status_display()})\n{link}"
            )
        return "\n".join(lines)

    def _latest_invoice_for_lease(self, lease):
        return (
            Invoice.objects.filter(lease=lease)
            .exclude(status="cancelled")
            .order_by("-issue_date", "-id")
            .first()
        )

    def _latest_invoice_reply(self, lease):
        invoice = self._latest_invoice_for_lease(lease)
        if not invoice:
            return "No invoice is recorded for your active lease yet."
        token = make_public_invoice_token(invoice.pk)
        link = build_public_url("invoices:public_invoice_detail", args=[token])
        return (
            "Latest invoice\n\n"
            f"Invoice: {invoice.invoice_number}\n"
            f"Amount: Rs. {invoice.amount or Decimal('0.00')}\n"
            f"Due Date: {invoice.due_date or '-'}\n"
            f"Status: {invoice.get_status_display()}\n\n"
            f"Link:\n{link}"
        )

    def _latest_electric_bill_reply(self, lease):
        electricity_item_filter = (
            Q(items__category__name__icontains="electric")
            | Q(items__description__icontains="electric")
            | Q(items__description__icontains="meter")
        )
        invoice = (
            Invoice.objects.filter(lease=lease)
            .exclude(status="cancelled")
            .filter(electricity_item_filter)
            .distinct()
            .order_by("-issue_date", "-id")
            .first()
        )
        if not invoice:
            return "No electricity charge is recorded on an invoice for your active lease yet."

        electric_items = [
            item
            for item in invoice.items.select_related("category").all()
            if (
                "electric" in ((getattr(item.category, "name", "") or "").lower())
                or "electric" in ((item.description or "").lower())
                or "meter" in ((item.description or "").lower())
            )
        ]
        electric_amount = sum(
            (item.amount or Decimal("0.00") for item in electric_items),
            Decimal("0.00"),
        )
        token = make_public_invoice_token(invoice.pk)
        link = build_public_url("invoices:public_invoice_detail", args=[token])
        return (
            "Current electricity bill\n\n"
            f"Electricity: Rs. {electric_amount}\n"
            f"Invoice: {invoice.invoice_number}\n"
            f"Due Date: {invoice.due_date or '-'}\n"
            f"Status: {invoice.get_status_display()}\n\n"
            f"View invoice:\n{link}"
        )

    def _invoice_issue_reply(self, lease):
        invoice = self._latest_invoice_for_lease(lease)
        if not invoice:
            return (
                "I can help with the invoice issue, but no invoice is recorded for your "
                "active lease yet. Please tell me what you expected or contact the office."
            )
        token = make_public_invoice_token(invoice.pk)
        link = build_public_url("invoices:public_invoice_detail", args=[token])
        return (
            "I can help with the invoice issue. Here is your latest invoice:\n\n"
            f"Invoice: {invoice.invoice_number}\n"
            f"Amount: Rs. {invoice.amount or Decimal('0.00')}\n"
            f"Due Date: {invoice.due_date or '-'}\n"
            f"Status: {invoice.get_status_display()}\n\n"
            f"View invoice: {link}\n\n"
            "Please tell me what looks wrong (amount, charge, due date, or another item), "
            "or send a screenshot. If you already paid, reply 'I have paid' and I will "
            "check the latest posted payment and current balance and ask for your receipt."
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
        link = create_public_ledger_link(lease)
        ledger_link = public_ledger_url(link)
        return f"Full ledger:\n{ledger_link}"

    def _family_public_link_reply_url(self, lease, phone_number=""):
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
        return build_public_url("leases:public_lease_family_add", args=[link.token])

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
        share_url = build_public_url(
            "leases:public_lease_files_share", args=[link.token]
        )
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
        link = build_public_url("leases:public_agreement_view", args=[token.token])
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
            public_link = build_public_url(
                "leases:public_inspection_sign", args=[latest.public_token]
            )
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
        if ctx.balance > 0:
            return (
                f"Your outstanding balance for {ctx.property.property_name} - Unit {ctx.unit.unit_number} is Rs. {ctx.balance}."
            )
        if ctx.balance < 0:
            return (
                f"Your account for {ctx.property.property_name} - Unit {ctx.unit.unit_number} has a credit of Rs. {abs(ctx.balance)}. "
                "There is no outstanding amount due."
            )
        return (
            f"Your account for {ctx.property.property_name} - Unit {ctx.unit.unit_number} is fully paid. Outstanding balance: Rs. 0.00."
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
    if _looks_like_electric_bill_request(lowered):
        return "electric_bill"
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


def _looks_like_invoice_issue(text):
    lowered = (text or "").strip().lower()
    if "invoice" not in lowered and "bill" not in lowered:
        return False
    return any(
        word in lowered
        for word in ("issue", "problem", "wrong", "incorrect", "error", "dispute", "question")
    )


def _looks_like_invoice_detail(text):
    lowered = (text or "").strip().lower()
    return any(
        phrase in lowered
        for phrase in (
            "invoice detail",
            "invoice details",
            "view invoice",
            "show invoice",
            "my invoice",
            "invoice information",
        )
    )

def _looks_like_electric_bill_request(text):
    lowered = (text or "").strip().lower()
    bill_word = any(word in lowered for word in ("bill", "invoice"))
    electric_word = any(
        phrase in lowered
        for phrase in (
            "electric",
            "electricity",
            "bijli",
            "bijlee",
            # Common speech-to-text / typing error observed in the exported chat.
            "election bill",
        )
    )
    return bill_word and electric_word


def _looks_like_contextual_details(text):
    lowered = re.sub(r"\s+", " ", (text or "").strip().lower())
    return lowered in {
        "detail",
        "details",
        "detail bhejo",
        "details bhejo",
        "detail bhej na",
        "details bhej na",
        "detail baj na",
        "details baj na",
        "details send",
        "send details",
    }


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
    conversation = getattr(pending, "conversation", None)
    staff_numbers = _pending_request_staff_numbers(pending)
    if conversation:
        simulation = (conversation.context or {}).get("staff_tenant_simulation")
        if simulation:
            # Staff testing/assisting via "Act as Tenant" already sees every reply
            # on this same phone; don't also send them the staff pending-request
            # alert for their own simulated action. Other staff numbers still
            # get notified normally.
            self_number = WhatsAppService.normalize_phone_number(conversation.phone_number)
            staff_numbers = [number for number in staff_numbers if number != self_number]
    submitted_by_staff = getattr(pending, "submitted_by_staff", None)
    submitted_number = WhatsAppService.normalize_phone_number(
        getattr(submitted_by_staff, "whatsapp_number", "")
    )
    if submitted_number:
        staff_numbers = [number for number in staff_numbers if number != submitted_number]
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


def _payload_value_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _payload_text(payload):
    text_payload = payload.get("text") or {}
    if isinstance(text_payload, dict) and text_payload.get("body"):
        return _payload_value_text(text_payload.get("body"))
    if payload.get("type") == "button":
        button = payload.get("button") or {}
        button_text = _payload_value_text(button.get("text", ""))
        button_payload = _payload_value_text(button.get("payload", ""))
        if button_payload and button_text.strip().casefold() in {"", "quick reply", "button"}:
            return button_payload
        return button_text or button_payload
    if payload.get("type") == "interactive":
        interactive = payload.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return _payload_value_text(reply.get("title") or reply.get("id") or "")
    for media_type in ("image", "document", "video"):
        media = payload.get(media_type) or {}
        if media.get("caption"):
            return _payload_value_text(media.get("caption"))
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
    if "is_payment_receipt" in ocr_json:
        if ocr_json.get("is_payment_receipt") and int(ocr_json.get("confidence") or 0) >= 35:
            return True
        if not ocr_json.get("is_payment_receipt"):
            return False
    bank_info = ocr_json.get("bank_information") or {}
    text = " ".join(
        str(value or "")
        for value in [
            ocr_json.get("text"),
            ocr_json.get("raw_text"),
            ocr_json.get("description"),
            ocr_json.get("document_type"),
            ocr_json.get("reference"),
            bank_info.get("bank"),
            bank_info.get("channel"),
            bank_info.get("receiver_account"),
            bank_info.get("receiver_name"),
        ]
    ).lower()
    payment_words = (
        "payment", "receipt", "sent", "amount", "easypaisa", "jazzcash", "raast",
        "bank", "account", "transaction", "transfer", "transferred", "successful",
    )
    has_payment_words = any(word in text for word in payment_words)
    return bool(ocr_json.get("amount") and (has_payment_words or int(ocr_json.get("confidence") or 0) >= 50))


def _payment_receipt_validation(ocr_json):
    from whatsapp.services.openai_ocr import validate_payment_receipt

    validation = (ocr_json or {}).get("validation") or {}
    if {"is_valid", "missing_fields", "invalid_fields"}.issubset(validation):
        return {
            "is_valid": bool(validation["is_valid"]),
            "missing_fields": list(validation["missing_fields"]),
            "invalid_fields": list(validation["invalid_fields"]),
            "normalized_data": dict(ocr_json or {}),
        }
    return validate_payment_receipt(ocr_json)


def _payment_confirmation_text(pending):
    prop = getattr(pending.property, "property_name", "") or "Not detected"
    unit = getattr(pending.unit, "unit_number", "") or "Not detected"
    channel = (pending.bank_information or {}).get("channel") or "Not detected"
    account_name = (
        (pending.bank_information or {}).get("receiver_name")
        or (pending.ocr_json or {}).get("sender_name")
        or "Not detected"
    )
    return (
        "I read this image as a payment receipt.\n\n"
        f"Account Name: {account_name}\n"
        f"Amount: Rs. {pending.amount or 'Not detected'}\n"
        f"Date: {pending.date or 'Not detected'}\n"
        f"Payment Channel: {channel}\n"
        f"Reference Number: {pending.reference or 'Not detected'}\n\n"
        "You are sending this payment receipt to apply toward:\n"
        f"Property: {prop}\n"
        f"Unit: {unit}\n"
        f"Amount: Rs. {pending.amount or 'Not detected'}\n\n"
        "Is this correct? Reply YES to confirm.\n"
        "Reply OTHER if this belongs to another property/unit."
    )


def _payment_receipt_review_text(review, notice=""):
    amount_value = _review_decimal(review.get("tenant_amount") or review.get("ocr_amount"))
    date_value = _review_date(review.get("tenant_date") or review.get("ocr_date"))
    amount = f"Rs. {amount_value:,.2f}" if amount_value is not None else "Not detected"
    payment_date = date_value.strftime("%d-%m-%Y") if date_value else "Not detected"
    reference = (review.get("ocr") or {}).get("reference") or "Not detected"
    lines = []
    if notice:
        lines.extend([notice, ""])
    lines.extend(
        [
            "I recognized this image as a payment receipt.",
            "",
            f"Amount: {amount}",
            f"Date: {payment_date}",
            f"Reference: {reference}",
        ]
    )
    if review.get("property_name") or review.get("unit_number"):
        lines.append(
            "Apply to: "
            f"{review.get('property_name') or 'Not detected'} / {review.get('unit_number') or 'Not detected'}"
        )
    if review.get("tenant_amount") and review.get("ocr_amount"):
        original_amount = _review_decimal(review.get("ocr_amount"))
        if original_amount is not None:
            lines.append(f"OCR originally read: Rs. {original_amount:,.2f}")
    if review.get("tenant_date") and review.get("ocr_date"):
        original_date = _review_date(review.get("ocr_date"))
        if original_date:
            lines.append(f"OCR originally read date: {original_date.strftime('%d-%m-%Y')}")
    lines.extend(
        [
            "",
            "Is this correct? Reply YES to submit it for payment approval.",
            "To correct it, reply AMOUNT <correct amount> or DATE DD-MM-YYYY.",
            "Reply CANCEL to stop.",
        ]
    )
    return "\n".join(lines)


def _payment_received_text(pending):
    amount = f"{pending.amount:,.2f}" if pending.amount is not None else "Not detected"
    payment_date = pending.date.strftime("%d-%m-%Y") if pending.date else "Not detected"
    reference = pending.reference or "Not detected"
    return (
        "Payment receipt received.\n\n"
        f"Amount: Rs. {amount}\n"
        f"Date: {payment_date}\n"
        f"Reference: {reference}\n\n"
        "It has been submitted for pending approval. You will receive confirmation shortly after bank verification."
    )


def _payment_amount_correction(text):
    match = re.fullmatch(
        r"\s*(?:(?:correct(?:ed)?\s+)?amount(?:\s+is)?|rs\.?|pkr)?\s*[:=-]?\s*"
        r"([0-9][0-9,]*(?:\.\d{1,2})?)\s*",
        text or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", ""))
    except (ValueError, ArithmeticError):
        return None
    return amount if amount > 0 else None


def _payment_date_correction(text):
    match = re.fullmatch(
        r"\s*(?:date(?:\s+is)?\s*[:=-]?\s*)?"
        r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*",
        text or "",
        flags=re.IGNORECASE,
    )
    return _review_date(match.group(1)) if match else None


def _review_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (ValueError, ArithmeticError):
        return None


def _review_date(value):
    if not value:
        return None
    text = str(value).strip()
    for date_format in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return timezone.datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    return None


def _tenant_media_confirmation_text(media):
    if media.purpose == PendingWhatsAppMedia.PURPOSE_OTHER:
        return (
            "We received your media. What would you like to do?\n\n"
            "1 Unit Photo\n2 Tenant Document\n3 Maintenance Photo\n"
            "4 Payment Receipt\n5 Police Verification\n6 Lease Photo\n7 Cancel"
        )
    return "We received your media and staged it for admin review before attaching it to any record."


def _tenant_upload_purpose_from_text(text):
    lowered = (text or "").strip().lower()
    choices = {
        "1": PendingWhatsAppMedia.PURPOSE_UNIT,
        "unit": PendingWhatsAppMedia.PURPOSE_UNIT,
        "unit photo": PendingWhatsAppMedia.PURPOSE_UNIT,
        "unit photos": PendingWhatsAppMedia.PURPOSE_UNIT,
        "2": PendingWhatsAppMedia.PURPOSE_LEASE,
        "tenant document": PendingWhatsAppMedia.PURPOSE_LEASE,
        "tenant documents": PendingWhatsAppMedia.PURPOSE_LEASE,
        "3": PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        "maintenance": PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        "maintenance photo": PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        "4": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "payment": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "payment receipt": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "receipt": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "5": "police_verification",
        "police": "police_verification",
        "police verification": "police_verification",
        "6": PendingWhatsAppMedia.PURPOSE_LEASE,
        "lease": PendingWhatsAppMedia.PURPOSE_LEASE,
        "lease photo": PendingWhatsAppMedia.PURPOSE_LEASE,
        "lease document": PendingWhatsAppMedia.PURPOSE_LEASE,
        "7": "cancel",
        "cancel": "cancel",
    }
    return choices.get(lowered)


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
    # Meta delivers album items as independent webhooks. Serializing each phone's
    # conversation prevents concurrent workers from opening one maintenance draft
    # per photo before the first photo has saved the shared pending state.
    with transaction.atomic():
        locked_message = WhatsAppMessageLog.objects.select_for_update().get(pk=message_log.pk)
        processing = dict((locked_message.api_response or {}).get("ai_processing") or {})
        if processing.get("state") == "complete":
            logger.info(
                "Ignored duplicate WhatsApp processing message_id=%s state=complete",
                locked_message.wa_message_id,
            )
            return
        api_response = dict(locked_message.api_response or {})
        api_response["ai_processing"] = {
            "state": "processing",
            "started_at": timezone.now().isoformat(),
        }
        locked_message.api_response = api_response
        locked_message.save(update_fields=["api_response", "updated_at"])
        conversation, _ = WhatsAppConversation.objects.get_or_create(
            phone_number=locked_message.phone_number,
            defaults={"last_message_at": timezone.now()},
        )
        WhatsAppConversation.objects.select_for_update().get(pk=conversation.pk)
        WhatsAppAIAssistant().handle_inbound_message(locked_message)
        locked_message.refresh_from_db(fields=["api_response"])
        api_response = dict(locked_message.api_response or {})
        api_response["ai_processing"] = {
            "state": "complete",
            "completed_at": timezone.now().isoformat(),
        }
        locked_message.api_response = api_response
        locked_message.save(update_fields=["api_response", "updated_at"])
