import logging
import time
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from properties.models import Property, Unit
from tenants.models import Tenant, normalize_cnic
from whatsapp.models import (
    PendingWhatsAppMedia,
    PendingWhatsAppPayment,
    WhatsAppExternalLinkToken,
    WhatsAppAIInteractionLog,
    WhatsAppConversation,
)
from whatsapp.services.ai_config import get_whatsapp_ai_config
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
        identity = identify_sender(message_log.phone_number)
        mode = resolve_mode(conversation, text, identity)

        if mode == "choose_mode":
            return mode_selection_text(), "mode_selection", {"staff_user": identity.staff_user, "tenant": identity.tenant}
        if mode == WhatsAppConversation.MODE_GUEST:
            return self._handle_guest_message(text), "guest", {}
        if mode == WhatsAppConversation.MODE_STAFF:
            return self._handle_staff_message(message_log, conversation, text, message_type, identity), "staff", {
                "staff_user": identity.staff_user,
            }

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
            conversation.pending_state = "tenant_upload_type"
            conversation.context["pending_media_id"] = media.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
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

        lowered = (text or "").strip().lower()
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
            tenant_menu_text(),
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
        today = timezone.localdate()
        if lease and lease.status == "active" and lease.start_date <= today <= lease.end_date:
            return lease
        return None

    def _handle_guest_message(self, text):
        intent = detect_intent(text)
        lowered = (text or "").strip().lower()
        if lowered in {"menu", "hi", "hello", "start", ""}:
            return guest_menu_text()
        if lowered in {"1", "vacant", "vacancy", "available"} or intent == "availability":
            return self._available_units_reply()
        if lowered in {"2", "registration", "tenant registration"}:
            return (
                "Tenant Registration\n\n"
                "1. Ask staff for registration link\n"
                "2. Contact office\n\n"
                "Reply with a number or type your request."
            )
        return guest_menu_text()

    def _handle_staff_message(self, message_log, conversation, text, message_type, identity):
        staff_user = identity.staff_user
        lowered = (text or "").strip().lower()
        if message_type in {"image", "document", "video"}:
            media = create_pending_media(message_log, conversation)
            conversation.pending_state = "staff_upload_type"
            conversation.context["pending_media_id"] = media.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
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
        if lowered in {"menu", "staff", "hi", "hello", "start", ""}:
            log_staff_action(staff_user, message_log.phone_number, "staff_menu", "allowed")
            return staff_menu_text(staff_user)
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
        agreement_response = self._handle_staff_agreement_link(message_log, text, staff_user)
        if agreement_response:
            return agreement_response
        if lowered in {"add lease", "create lease"}:
            return self._start_staff_add_lease(message_log, conversation, staff_user)
        if lowered == "cancel":
            conversation.pending_state = ""
            conversation.context.pop("staff_add_lease", None)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return staff_menu_text(staff_user)
        if lowered in {"9", "switch mode"}:
            conversation.selected_mode = ""
            conversation.mode_expires_at = None
            conversation.pending_state = "mode_selection"
            conversation.save(update_fields=["selected_mode", "mode_expires_at", "pending_state", "updated_at"])
            return mode_selection_text()
        log_staff_action(staff_user, message_log.phone_number, "staff_menu_request", "allowed", text=text[:200])
        return staff_submenu_text(text)

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

    def _start_staff_add_lease(self, message_log, conversation, staff_user):
        conversation.pending_state = "staff_add_lease_tenant_id"
        conversation.context["staff_add_lease"] = {}
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
            conversation.context.pop("pending_media_id", None)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return None

        purpose = _upload_purpose_from_text(text)
        if not purpose:
            return (
                "Please reply with a number:\n\n"
                "1 Property Photos\n2 Unit Photos\n3 Lease Documents\n4 Maintenance\n5 Payment\n6 Other",
                "upload_type_retry",
                {"lease": selected_lease, "pending_media_id": media.pk},
            )

        media.purpose = purpose
        media.lease = selected_lease or media.lease
        media.tenant = getattr(media.lease, "tenant", None)
        media.property = getattr(getattr(media.lease, "unit", None), "property", None)
        media.unit = getattr(media.lease, "unit", None)
        media.save(update_fields=["purpose", "lease", "tenant", "property", "unit", "updated_at"])
        conversation.pending_state = ""
        conversation.context.pop("pending_media_id", None)
        conversation.save(update_fields=["pending_state", "context", "updated_at"])

        if purpose == PendingWhatsAppMedia.PURPOSE_PAYMENT:
            return self._stage_payment(message_log, conversation, selected_lease, media, text)
        if purpose == PendingWhatsAppMedia.PURPOSE_MAINTENANCE:
            pending = create_pending_maintenance(message_log, conversation, selected_lease, media=media)
            return (
                "We received your maintenance photo. Please share the issue type and urgency if not already included.",
                "maintenance_media",
                {"lease": selected_lease, "pending_maintenance_id": pending.pk},
            )
        if purpose == PendingWhatsAppMedia.PURPOSE_OTHER:
            return (
                "Thanks. Your upload is staged for admin review.",
                "media_pending",
                {"lease": selected_lease, "pending_media_id": media.pk},
            )
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
            conversation.context.pop("pending_media_id", None)
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return None

        purpose = _upload_purpose_from_text(text)
        if not purpose:
            return upload_type_menu_text(), "upload_type_retry", {"pending_media_id": media.pk}

        media.purpose = purpose
        media.save(update_fields=["purpose", "updated_at"])
        conversation.pending_state = ""
        conversation.context.pop("pending_media_id", None)
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
    lowered = (text or "").strip().lower()
    tenant_menu_number_map = {
        "1": "balance",
        "2": "payments",
        "3": "maintenance",
        "4": "lease",
    }
    if lowered in tenant_menu_number_map:
        return tenant_menu_number_map[lowered]
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


def _add_tenant_menu_text():
    return (
        "Add Tenant\n\n"
        "1. Send public tenant registration link\n"
        "2. Create tenant draft by WhatsApp\n"
        "3. Back"
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
        "lease document": PendingWhatsAppMedia.PURPOSE_LEASE,
        "lease documents": PendingWhatsAppMedia.PURPOSE_LEASE,
        "4": PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        "maintenance": PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        "maintenance photo": PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        "5": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "payment": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "payment receipt": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "receipt": PendingWhatsAppMedia.PURPOSE_PAYMENT,
        "6": PendingWhatsAppMedia.PURPOSE_OTHER,
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
