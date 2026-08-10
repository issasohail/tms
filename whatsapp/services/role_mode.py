from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q
from django.utils import timezone

from tenants.models import Tenant
from whatsapp.models import (
    WhatsAppConversation,
    WhatsAppStaffActionLog,
    WhatsAppStaffPropertyAccess,
)
from whatsapp.services.tenant_context import find_active_leases_for_phone, normalize_phone
from whatsapp.services.identity.mode_resolver import infer_mode
from whatsapp.services.identity.sender_resolver import resolve_sender


ROLE_GROUP_NAMES = [
    "Guest",
    "Tenant",
    "Staff",
    "Billing Staff",
    "Property Manager",
    "Administrator",
]

MODE_COMMANDS = {"menu", "switch", "switch mode", "staff", "staff mode", "staff inbox", "tenant", "tenant mode", "my account", "my tenant account", "handyman", "handyman mode"}
MODE_TTL_MINUTES = getattr(settings, "WHATSAPP_MODE_SESSION_MINUTES", 60)
TENANT_SIMULATOR_GROUP = "Tenant Simulator"


@dataclass
class SenderIdentity:
    phone_number: str
    staff_user: object = None
    tenant: Tenant = None
    active_leases: list = None

    @property
    def has_staff(self):
        return bool(self.staff_user and self.staff_user.is_active and self.staff_user.is_staff)

    @property
    def has_active_tenant(self):
        return bool(self.tenant and self.active_leases)


def ensure_whatsapp_role_groups():
    for name in getattr(settings, "WHATSAPP_ROLE_GROUP_NAMES", ROLE_GROUP_NAMES):
        Group.objects.get_or_create(name=name)


def identify_sender(phone_number, conversation=None):
    return resolve_sender(phone_number, conversation=conversation)


def resolve_mode(conversation, text, identity):
    command = (text or "").strip().lower()
    choosing_mode = conversation.pending_state == "mode_selection" or not conversation.selected_mode_is_valid
    if command in {"staff", "staff mode", "staff inbox"} and identity.has_staff:
        return _set_mode(conversation, WhatsAppConversation.MODE_STAFF, identity)
    if command in {"handyman", "handyman mode"} and identity.has_handyman:
        return _set_mode(conversation, WhatsAppConversation.MODE_HANDYMAN, identity)
    if command in {"guest", "guest services"} or (command == "4" and choosing_mode):
        return _set_mode(conversation, WhatsAppConversation.MODE_GUEST, identity)
    wants_tenant = command in {"tenant", "tenant mode", "my account", "my tenant account"} or (
        command == "1" and choosing_mode and bool(identity.tenant_matches)
    )
    if wants_tenant and identity.tenant_matches:
        if len(identity.tenant_matches) > 1:
            conversation.selected_mode = ""
            conversation.mode_expires_at = None
            conversation.pending_state = "tenant_identity_selection"
            conversation.context["tenant_identity_options"] = [item.pk for item in identity.tenant_matches]
            conversation.save(update_fields=["selected_mode", "mode_expires_at", "pending_state", "context", "updated_at"])
            return "choose_tenant_identity"
        if identity.has_active_tenant:
            return _set_mode(conversation, WhatsAppConversation.MODE_TENANT, identity)
        return "tenant_no_active_lease"
    if command == "2" and choosing_mode and identity.has_staff:
        return _set_mode(conversation, WhatsAppConversation.MODE_STAFF, identity)
    if command == "3" and choosing_mode and identity.has_handyman:
        return _set_mode(conversation, WhatsAppConversation.MODE_HANDYMAN, identity)
    if command in MODE_COMMANDS:
        conversation.selected_mode = ""
        conversation.mode_expires_at = None
        conversation.pending_state = ""
        conversation.context.pop("selected_tenant_identity_id", None)
        conversation.context.pop("tenant_identity_options", None)
        conversation.context.pop("pending_tenant_identity_id", None)
        conversation.save(update_fields=["selected_mode", "mode_expires_at", "pending_state", "context", "updated_at"])

    _sync_identity(conversation, identity)

    if conversation.selected_mode_is_valid:
        if conversation.selected_mode == WhatsAppConversation.MODE_STAFF and identity.has_staff:
            return conversation.selected_mode
        if conversation.selected_mode == WhatsAppConversation.MODE_TENANT and identity.has_active_tenant:
            return conversation.selected_mode
        if conversation.selected_mode == WhatsAppConversation.MODE_HANDYMAN and identity.has_handyman:
            return conversation.selected_mode
        if conversation.selected_mode == WhatsAppConversation.MODE_GUEST:
            return conversation.selected_mode

    role_count = sum([
        bool(identity.tenant_matches),
        identity.has_staff,
        identity.has_handyman,
    ])
    if role_count > 1:
        inferred_mode, confidence = infer_mode(text, identity)
        if inferred_mode:
            if inferred_mode == WhatsAppConversation.MODE_TENANT and len(identity.tenant_matches) > 1:
                conversation.pending_state = "tenant_identity_selection"
                conversation.context["tenant_identity_options"] = [item.pk for item in identity.tenant_matches]
                conversation.save(update_fields=["pending_state", "context", "updated_at"])
                return "choose_tenant_identity"
            conversation.context["mode_inference_confidence"] = confidence
            return _set_mode(conversation, inferred_mode, identity)
        conversation.pending_state = "mode_selection"
        conversation.save(update_fields=["pending_state", "updated_at"])
        return "choose_mode"
    if len(identity.tenant_matches) > 1:
        conversation.pending_state = "tenant_identity_selection"
        conversation.context["tenant_identity_options"] = [item.pk for item in identity.tenant_matches]
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return "choose_tenant_identity"
    if getattr(identity, "ambiguous", False):
        return "ambiguous_identity"
    if identity.has_staff:
        return _set_mode(conversation, WhatsAppConversation.MODE_STAFF, identity)
    if identity.has_active_tenant:
        return _set_mode(conversation, WhatsAppConversation.MODE_TENANT, identity)
    if identity.has_handyman:
        return _set_mode(conversation, WhatsAppConversation.MODE_HANDYMAN, identity)
    return _set_mode(conversation, WhatsAppConversation.MODE_GUEST, identity)


def mode_selection_text():
    return (
        "This number is registered for more than one role. What would you like to use?\n\n"
        "1. My Tenant Account\n"
        "2. Staff Inbox\n"
        "3. Handyman Account\n"
        "4. Guest Services\n\n"
        "Reply with a number or role name. Unregistered roles remain unavailable. You can type SWITCH MODE later."
    )


def guest_menu_text():
    return (
        "Guest Menu\n\n"
        "1. Vacant units\n"
        "2. Tenant registration\n"
        "3. Contact office\n"
        "4. Suggestion / advice\n\n"
        "Reply with a number or type your request."
    )


def staff_menu_text(user=None):
    simulator_option = "\n11. Act as Tenant <phone or tenant number> (live until EXIT)" if staff_can_simulate_tenant(user) else ""
    return (
        "Staff Inbox / Menu\n\n"
        "Type Handovers to view tenant handovers.\n\n"
        "1. Tenant Management\n"
        "2. Lease Management\n"
        "3. Billing\n"
        "4. Maintenance\n"
        "5. Property / Unit Photos\n"
        "6. Reports\n"
        "7. Upload Documents\n"
        "8. Search\n"
        "9. Switch Mode\n"
        "10. Suggestion / advice"
        f"{simulator_option}\n"
        "12. New Tenant Registration\n\n"
        "Type Pending Requests to view WhatsApp items waiting for review.\n\n"
        "Reply with a number or type your request."
    )


def staff_can_simulate_tenant(user):
    if not user or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=TENANT_SIMULATOR_GROUP).exists()


def tenant_menu_text():
    return (
        "Tenant Menu\n\n"
        "1. Outstanding balance\n"
        "2. Invoice / payment\n"
        "3. Maintenance request\n"
        "4. Lease information\n"
        "5. Meter readings / utility bills\n"
        "6. Upload Payment Receipt\n"
        "7. Vacant units\n"
        "8. Tenant registration\n"
        "9. Contact office\n"
        "10. Suggestion / advice\n"
        "11. Request Last Invoice\n"
        "12. View Ledger\n"
        "13. Upload Unit Photos\n\n"
        "Reply with a number or type your request."
    )


def staff_submenu_text(text):
    lowered = (text or "").strip().lower()
    if lowered in {"1", "tenant", "tenant management", "add tenant"}:
        return (
            "Tenant Management\n\n"
            "1. Add New Tenant\n"
            "2. Search Tenant\n"
            "3. Tenant Balance\n"
            "4. Tenant Ledger\n"
            "5. Tenant Documents\n"
            "6. Send WhatsApp\n"
            "7. View Tenant\n"
            "8. Back\n\n"
            "Reply with a number or type your request."
        )
    if lowered in {"2", "lease", "lease management", "add lease", "create lease"}:
        return (
            "Lease Management\n\n"
            "1. Create Lease\n"
            "2. Renew Lease\n"
            "3. End Lease\n"
            "4. View Lease\n"
            "5. Upload Lease Document / Photos\n"
            "6. Lease Ledger\n"
            "7. Lease Balance\n"
            "8. Agreement View/Edit\n"
            "9. Back\n\n"
            "Reply with a number or type your request."
        )
    if lowered in {"3", "billing"}:
        return (
            "Billing Menu\n\n"
            "1. Outstanding Tenants\n"
            "2. Invoice Link\n"
            "3. Monthly Billing Status\n"
            "4. Electric Billing\n"
            "5. Missing Meter Readings\n"
            "6. Payment Verification\n"
            "7. Water Charges\n"
            "8. Back\n\n"
            "Reply with a number or type your request."
        )
    if lowered in {"5", "photos", "property", "property photos", "unit photos"}:
        return (
            "Property Menu\n\n"
            "1. Upload Property Photos\n"
            "2. Upload Unit Photos\n"
            "3. Upload Lease Photos\n"
            "4. Upload Tenant Documents\n"
            "5. View Photos\n"
            "6. Back\n\n"
            "Reply with a number or type your request."
        )
    if lowered in {"9", "switch", "switch mode"}:
        return mode_selection_text()
    return staff_menu_text()


def upload_type_menu_text():
    return (
        "What is this upload for?\n\n"
        "1. Property Photo\n"
        "2. Unit Photo\n"
        "3. Lease Photo\n"
        "4. Tenant Document\n"
        "5. Maintenance Photo\n"
        "6. Payment Receipt\n"
        "7. Police Verification\n"
        "8. Cancel"
    )


def user_role_name(user):
    if not user:
        return ""
    if user.is_superuser:
        return "Administrator"
    group_names = set(user.groups.values_list("name", flat=True))
    for role in ("Administrator", "Property Manager", "Billing Staff", "Staff"):
        if role in group_names:
            return role
    return "Staff" if user.is_staff else ""


def staff_can_access_property(user, property_obj):
    if not user or not property_obj:
        return False
    if user.is_superuser:
        return True
    if not user.is_active or not user.is_staff:
        return False
    return WhatsAppStaffPropertyAccess.objects.filter(
        staff_user=user,
        property=property_obj,
        is_active=True,
    ).exists()


def log_staff_action(user, phone_number, action, status, **details):
    return WhatsAppStaffActionLog.objects.create(
        staff_user=user if getattr(user, "is_authenticated", True) else None,
        phone_number=phone_number or "",
        role_name=user_role_name(user),
        selected_mode=WhatsAppConversation.MODE_STAFF,
        action=action,
        property=details.pop("property", None),
        tenant=details.pop("tenant", None),
        lease=details.pop("lease", None),
        status=status,
        details=details,
    )


def _set_mode(conversation, mode, identity):
    _sync_identity(conversation, identity)
    conversation.selected_mode = mode
    conversation.mode_expires_at = timezone.now() + timedelta(minutes=MODE_TTL_MINUTES)
    conversation.pending_state = "" if conversation.pending_state == "mode_selection" else conversation.pending_state
    conversation.context = {
        **(conversation.context or {}),
        "identity_resolution": {
            "staff_matches": len(identity.staff_matches),
            "tenant_matches": len(identity.tenant_matches),
            "ambiguous": identity.ambiguous,
        },
    }
    conversation.save(update_fields=[
        "selected_mode",
        "mode_expires_at",
        "pending_state",
        "staff_user",
        "tenant",
        "context",
        "updated_at",
    ])
    return mode


def _sync_identity(conversation, identity):
    conversation.staff_user = identity.staff_user
    conversation.tenant = identity.tenant


def _find_staff_user(digits):
    if not digits:
        return None
    User = get_user_model()
    candidates = User.objects.filter(is_active=True, is_staff=True).exclude(whatsapp_number="")
    for user in candidates:
        if _phone_matches(digits, user.whatsapp_number):
            return user
    return None


def _find_tenant(digits):
    if not digits:
        return None
    suffix = digits[-10:]
    return (
        Tenant.objects.filter(
            Q(phone__icontains=suffix) |
            Q(phone2__icontains=suffix) |
            Q(phone3__icontains=suffix) |
            Q(emergency_contact_phone__icontains=suffix)
        )
        .filter(is_active=True)
        .order_by("-updated_at", "-id")
        .first()
    )


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phone_matches(target_digits, candidate):
    candidate_digits = _digits(candidate)
    if not candidate_digits:
        return False
    return candidate_digits.endswith(target_digits[-10:]) or target_digits.endswith(candidate_digits[-10:])
