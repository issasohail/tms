import logging
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import GlobalSettings
from core.public_urls import build_public_url
from whatsapp.models import (
    WhatsAppStaffActionLog,
    WhatsAppStaffPropertyAccess,
    WhatsAppStaffRoutingRule,
)
from whatsapp.services.identity.phone_normalizer import normalize_phone_number
from whatsapp.services.identity.sender_resolver import resolve_sender
from whatsapp.services.whatsapp import WhatsAppService

from properties.models import PhotoLinkRenewalRequest, PublicPhotoLink
from tenants.models import Tenant, TenantInterestType


logger = logging.getLogger(__name__)

LINK_LIFETIME = timedelta(hours=48)
REQUEST_COOLDOWN = timedelta(minutes=15)
IP_RATE_WINDOW = timedelta(hours=1)
IP_RATE_LIMIT = 5
REQUEST_DECISION_LIFETIME = timedelta(hours=48)
COMMAND_RE = re.compile(
    r"^\s*(APPROVE|REJECT)\s+(PLR-[0-9A-F]{8})\s*$", re.IGNORECASE
)


def _target_values(
    gallery_type,
    *,
    property_obj=None,
    unit=None,
    lease=None,
    lease_history=None,
):
    """Return a canonical, internally consistent gallery target."""
    if gallery_type == PublicPhotoLink.GALLERY_PROPERTY:
        if not property_obj or unit or lease or lease_history:
            raise ValueError("Invalid property gallery target.")
        return {
            "property": property_obj,
            "unit": None,
            "lease": None,
            "lease_history": None,
        }

    if gallery_type == PublicPhotoLink.GALLERY_UNIT:
        if not property_obj or not unit or lease or lease_history:
            raise ValueError("Invalid unit gallery target.")
        if unit.property_id != property_obj.pk:
            raise ValueError("Unit does not belong to the property.")
        return {
            "property": property_obj,
            "unit": unit,
            "lease": None,
            "lease_history": None,
        }

    if gallery_type == PublicPhotoLink.GALLERY_LEASE:
        if not property_obj or not lease or lease_history:
            raise ValueError("Invalid lease gallery target.")
        if lease.unit.property_id != property_obj.pk:
            raise ValueError("Lease does not belong to the property.")
        return {
            "property": property_obj,
            "unit": lease.unit,
            "lease": lease,
            "lease_history": None,
        }

    if gallery_type == PublicPhotoLink.GALLERY_LEASE_HISTORY:
        if not property_obj or not lease or not lease_history:
            raise ValueError("Invalid lease-history gallery target.")
        if lease.unit.property_id != property_obj.pk:
            raise ValueError("Lease does not belong to the property.")
        if lease_history.lease_id != lease.pk:
            raise ValueError("Lease history does not belong to the lease.")
        return {
            "property": property_obj,
            "unit": lease.unit,
            "lease": lease,
            "lease_history": lease_history,
        }

    raise ValueError("Unsupported gallery type.")


def create_public_photo_link(
    gallery_type,
    *,
    property_obj=None,
    unit=None,
    lease=None,
    lease_history=None,
    created_by=None,
    renewal_request=None,
    expires_at=None,
):
    values = _target_values(
        gallery_type,
        property_obj=property_obj,
        unit=unit,
        lease=lease,
        lease_history=lease_history,
    )
    return PublicPhotoLink.objects.create(
        gallery_type=gallery_type,
        expires_at=expires_at or timezone.now() + LINK_LIFETIME,
        created_by=created_by,
        renewal_request=renewal_request,
        **values,
    )


def reusable_public_photo_link(
    gallery_type,
    *,
    property_obj=None,
    unit=None,
    lease=None,
    lease_history=None,
    created_by=None,
):
    values = _target_values(
        gallery_type,
        property_obj=property_obj,
        unit=unit,
        lease=lease,
        lease_history=lease_history,
    )
    lookup = {f"{name}_id": getattr(value, "pk", None) for name, value in values.items()}
    existing = (
        PublicPhotoLink.objects.filter(
            gallery_type=gallery_type,
            is_active=True,
            expires_at__gt=timezone.now() + timedelta(minutes=1),
            **lookup,
        )
        .order_by("-expires_at", "-pk")
        .first()
    )
    if existing:
        return existing
    return create_public_photo_link(
        gallery_type,
        created_by=created_by,
        **{
            "property_obj": values["property"],
            "unit": values["unit"],
            "lease": values["lease"],
            "lease_history": values["lease_history"],
        },
    )


def public_link_url(link):
    return build_public_url("public_photo_link", kwargs={"token": link.token})


def public_link_share_text(link):
    expiry = timezone.localtime(link.expires_at).strftime("%d %b %Y, %I:%M %p %Z")
    return (
        "Property photos (secure 48-hour link)\n"
        f"{public_link_url(link)}\n"
        f"Expires: {expiry}"
    )


def _same_target_filter(values):
    return {
        f"{name}_id": getattr(value, "pk", None)
        for name, value in values.items()
    }


def create_renewal_request(
    *,
    gallery_type,
    property_obj,
    requester_name,
    requester_phone,
    unit=None,
    lease=None,
    lease_history=None,
    original_expires_at=None,
    request_ip=None,
    user_agent="",
    interest_type_ids=(),
):
    name = (requester_name or "").strip()
    phone = normalize_phone_number(requester_phone)
    digits = "".join(character for character in phone if character.isdigit())
    if len(name) < 2:
        raise ValueError("Please enter your full name.")
    if not 10 <= len(digits) <= 15:
        raise ValueError("Please enter a valid WhatsApp number.")

    values = _target_values(
        gallery_type,
        property_obj=property_obj,
        unit=unit,
        lease=lease,
        lease_history=lease_history,
    )
    selected_interest_ids = [value for value in interest_type_ids if str(value).isdigit()]
    interests = list(TenantInterestType.objects.filter(
        pk__in=selected_interest_ids,
        is_active=True,
    ).order_by("sort_order", "name"))
    if len(interests) != len(set(map(int, selected_interest_ids))):
        raise ValueError("Please select a valid building type.")
    now = timezone.now()
    if request_ip and PhotoLinkRenewalRequest.objects.filter(
        request_ip=request_ip,
        created_at__gte=now - IP_RATE_WINDOW,
    ).count() >= IP_RATE_LIMIT:
        return None, False

    duplicate = (
        PhotoLinkRenewalRequest.objects.filter(
            requester_phone=phone,
            gallery_type=gallery_type,
            status=PhotoLinkRenewalRequest.STATUS_PENDING,
            created_at__gte=now - REQUEST_COOLDOWN,
            **_same_target_filter(values),
        )
        .order_by("-created_at")
        .first()
    )
    if duplicate:
        return duplicate, False

    tenant = (
        Tenant.objects.filter(Q(phone=phone) | Q(phone2=phone) | Q(phone3=phone))
        .order_by("-is_active", "pk")
        .first()
    )
    if tenant is None:
        name_parts = name.split(None, 1)
        tenant = Tenant.objects.create(
            first_name=name_parts[0],
            last_name=name_parts[1] if len(name_parts) > 1 else "Prospect",
            phone=phone,
            cnic="",
            is_active=False,
            notes="Created from an expired public photo-link request.",
        )
    renewal = PhotoLinkRenewalRequest.objects.create(
        gallery_type=gallery_type,
        requester_name=name[:120],
        requester_phone=phone,
        original_expires_at=original_expires_at,
        request_ip=request_ip,
        user_agent=(user_agent or "")[:500],
        tenant=tenant,
        **values,
    )
    if interests:
        tenant.interested_in.add(*interests)
        renewal.interested_in.add(*interests)
    notify_staff_of_renewal_request(renewal)
    return renewal, True


def eligible_property_staff(property_obj):
    """Return authorized staff in deterministic priority order."""
    users = []
    users.extend(
        access.staff_user
        for access in WhatsAppStaffPropertyAccess.objects.select_related("staff_user")
        .filter(
            property=property_obj,
            is_active=True,
            staff_user__is_active=True,
            staff_user__is_staff=True,
        )
        .order_by("id")
    )
    users.extend(
        rule.staff_user
        for rule in WhatsAppStaffRoutingRule.objects.select_related("staff_user")
        .filter(
            is_active=True,
            staff_user__is_active=True,
            staff_user__is_staff=True,
            department__in=(
                WhatsAppStaffRoutingRule.DEPARTMENT_LEASING,
                WhatsAppStaffRoutingRule.DEPARTMENT_GENERAL,
            ),
        )
        .filter(Q(property=property_obj) | Q(property__isnull=True))
        .order_by("priority", "-property_id", "id")
    )
    config = GlobalSettings.get_solo()
    users.extend(
        [config.whatsapp_leasing_staff, config.whatsapp_default_support_staff]
    )
    users.extend(
        get_user_model().objects.filter(is_active=True, is_superuser=True).order_by("id")[:1]
    )

    result = []
    seen_users = set()
    seen_phones = set()
    for user in users:
        if not user or user.pk in seen_users or not user.is_active or not user.is_staff:
            continue
        if not staff_can_access_property(user, property_obj):
            continue
        phone = normalize_phone_number(getattr(user, "whatsapp_number", ""))
        if not phone or phone in seen_phones:
            continue
        seen_users.add(user.pk)
        seen_phones.add(phone)
        result.append(user)
    return result


def staff_can_access_property(user, property_obj):
    if not user or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser:
        return True
    if WhatsAppStaffPropertyAccess.objects.filter(
        staff_user=user, property=property_obj, is_active=True
    ).exists():
        return True
    if WhatsAppStaffRoutingRule.objects.filter(
        staff_user=user,
        is_active=True,
        department__in=(
            WhatsAppStaffRoutingRule.DEPARTMENT_LEASING,
            WhatsAppStaffRoutingRule.DEPARTMENT_GENERAL,
        ),
    ).filter(Q(property=property_obj) | Q(property__isnull=True)).exists():
        return True
    config = GlobalSettings.get_solo()
    return user.pk in {
        getattr(config, "whatsapp_leasing_staff_id", None),
        getattr(config, "whatsapp_default_support_staff_id", None),
    }


def staff_can_access_photo_request(user, renewal):
    return bool(renewal.property_id) and staff_can_access_property(user, renewal.property)


def notify_staff_of_renewal_request(renewal):
    recipients = eligible_property_staff(renewal.property)
    if not recipients:
        renewal.whatsapp_status = "unrouted"
        renewal.whatsapp_error = "No authorized staff recipient is configured."
        renewal.save(update_fields=["whatsapp_status", "whatsapp_error", "updated_at"])
        return

    staff = recipients[0]
    renewal.assigned_staff = staff
    target = renewal.property.property_name
    if renewal.unit_id:
        target = f"{target} — Unit {renewal.unit.unit_number}"
    interests = ", ".join(renewal.interested_in.values_list("name", flat=True)) or "Not selected"
    body = (
        "Photo-link renewal request\n"
        f"Reference: {renewal.reference}\n"
        f"Property / unit: {target}\n"
        f"Requester: {renewal.requester_name} ({renewal.requester_phone})\n\n"
        f"Interested in: {interests}\n\n"
        f"Reply APPROVE {renewal.reference} or REJECT {renewal.reference}."
    )
    try:
        result = WhatsAppService(created_by=staff).send_text(
            staff.whatsapp_number,
            body,
            property=renewal.property,
        ) or {}
        renewal.whatsapp_status = "sent" if result.get("ok") else "failed"
        renewal.whatsapp_error = (
            "" if result.get("ok") else str(result.get("error") or "Send failed.")
        )
        if result.get("ok"):
            renewal.notified_at = timezone.now()
    except Exception as exc:  # keep the visitor request even if Meta is unavailable
        logger.exception("Photo-link renewal notification failed reference=%s", renewal.reference)
        renewal.whatsapp_status = "failed"
        renewal.whatsapp_error = str(exc)[:1000]
    renewal.save(
        update_fields=[
            "assigned_staff",
            "whatsapp_status",
            "whatsapp_error",
            "notified_at",
            "updated_at",
        ]
    )


def _inbound_text(message_log):
    payload = message_log.payload or {}
    text = payload.get("text") or {}
    if isinstance(text, dict):
        return str(text.get("body") or "")
    return str(text or "")


def _audit(staff, phone, renewal, action, allowed, detail=""):
    WhatsAppStaffActionLog.objects.create(
        staff_user=staff,
        phone_number=phone,
        action=action,
        property=renewal.property if renewal else None,
        lease=renewal.lease if renewal else None,
        status=(
            WhatsAppStaffActionLog.ACTION_STATUS_ALLOWED
            if allowed
            else WhatsAppStaffActionLog.ACTION_STATUS_BLOCKED
        ),
        details={
            "reference": renewal.reference if renewal else "",
            "detail": detail,
        },
    )


def _approved_link_text(renewal):
    return (
        f"Approved {renewal.reference}. Send this secure link to the requester:\n\n"
        f"{public_link_share_text(renewal.fresh_link)}"
    )


def _send_approval_after_commit(renewal_id, staff_id, phone):
    renewal = PhotoLinkRenewalRequest.objects.select_related(
        "fresh_link", "property"
    ).get(pk=renewal_id)
    staff = get_user_model().objects.get(pk=staff_id)
    try:
        result = WhatsAppService(created_by=staff).send_text(
            phone,
            _approved_link_text(renewal),
            property=renewal.property,
            lease=renewal.lease,
        ) or {}
        status = "approved_sent" if result.get("ok") else "approved_send_failed"
        error = "" if result.get("ok") else str(result.get("error") or "Send failed.")
    except Exception as exc:
        logger.exception("Approved photo-link delivery failed reference=%s", renewal.reference)
        status = "approved_send_failed"
        error = str(exc)[:1000]
    PhotoLinkRenewalRequest.objects.filter(pk=renewal_id).update(
        whatsapp_status=status,
        whatsapp_error=error,
        updated_at=timezone.now(),
    )


def handle_staff_photo_link_command(message_log):
    match = COMMAND_RE.match(_inbound_text(message_log))
    if not match:
        return None

    command, reference = match.groups()
    command = command.upper()
    phone = normalize_phone_number(message_log.phone_number)
    sender = resolve_sender(phone, log_ambiguity=False)
    staff = sender.staff_user
    if not staff:
        _audit(None, phone, None, f"photo_link_{command.lower()}", False, "unrecognized_staff")
        return "This command is restricted to authorized property staff."

    with transaction.atomic():
        renewal = (
            PhotoLinkRenewalRequest.objects.select_for_update()
            .select_related("property", "unit", "lease__unit", "lease_history", "fresh_link")
            .filter(reference=reference.upper())
            .first()
        )
        if not renewal:
            _audit(staff, phone, None, f"photo_link_{command.lower()}", False, "unknown_reference")
            return "Photo-link request not found."
        if not staff_can_access_photo_request(staff, renewal):
            _audit(
                staff,
                phone,
                renewal,
                f"photo_link_{command.lower()}",
                False,
                "property_access_denied",
            )
            return "You are not authorized for this property's photo links."

        if (
            renewal.status == PhotoLinkRenewalRequest.STATUS_PENDING
            and renewal.created_at <= timezone.now() - REQUEST_DECISION_LIFETIME
        ):
            renewal.status = PhotoLinkRenewalRequest.STATUS_EXPIRED
            renewal.decided_at = timezone.now()
            renewal.save(update_fields=["status", "decided_at", "updated_at"])

        if renewal.status != PhotoLinkRenewalRequest.STATUS_PENDING:
            _audit(staff, phone, renewal, f"photo_link_{command.lower()}", True, "idempotent")
            return f"{renewal.reference} is already {renewal.get_status_display().lower()}."

        renewal.decided_by = staff
        renewal.decided_at = timezone.now()
        if command == "REJECT":
            renewal.status = PhotoLinkRenewalRequest.STATUS_REJECTED
            renewal.whatsapp_status = "rejected"
            renewal.whatsapp_error = ""
            renewal.save(
                update_fields=[
                    "status", "decided_by", "decided_at", "whatsapp_status",
                    "whatsapp_error", "updated_at",
                ]
            )
            _audit(staff, phone, renewal, "photo_link_reject", True)
            return f"Rejected {renewal.reference}. No new link was created."

        fresh_link = create_public_photo_link(
            renewal.gallery_type,
            property_obj=renewal.property,
            unit=renewal.unit,
            lease=renewal.lease,
            lease_history=renewal.lease_history,
            created_by=staff,
            renewal_request=renewal,
        )
        renewal.status = PhotoLinkRenewalRequest.STATUS_APPROVED
        renewal.fresh_link = fresh_link
        renewal.whatsapp_status = "approved_pending_send"
        renewal.whatsapp_error = ""
        renewal.save(
            update_fields=[
                "status", "decided_by", "decided_at", "fresh_link",
                "whatsapp_status", "whatsapp_error", "updated_at",
            ]
        )
        _audit(staff, phone, renewal, "photo_link_approve", True)
        transaction.on_commit(
            lambda: _send_approval_after_commit(renewal.pk, staff.pk, phone)
        )
    return ""
