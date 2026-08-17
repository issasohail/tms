from django.db import transaction
from django.utils import timezone

from whatsapp.models import WhatsAppConversation, WhatsAppHandover, WhatsAppHandoverMessage
from whatsapp.services.handover.routing import staff_can_access_handover
from whatsapp.services.role_mode import log_staff_action


@transaction.atomic
def create_handover(conversation, message_log, reason, department="general", priority="normal", ai_summary="", media=None):
    locked_conversation = WhatsAppConversation.objects.select_for_update().get(pk=conversation.pk)
    existing = (
        WhatsAppHandover.objects.select_for_update()
        .filter(conversation=locked_conversation, status__in=WhatsAppHandover.ACTIVE_STATUSES)
        .order_by("-created_at")
        .first()
    )
    text = _message_text(message_log)
    if existing:
        attach_tenant_message(existing, message_log, text, media=media)
        return existing, False

    lease = locked_conversation.selected_lease
    handover = WhatsAppHandover.objects.create(
        conversation=locked_conversation,
        tenant=locked_conversation.tenant,
        lease=lease,
        property=getattr(getattr(lease, "unit", None), "property", None),
        unit=getattr(lease, "unit", None),
        tenant_phone=locked_conversation.phone_number,
        priority=priority,
        department=department,
        reason=reason[:160],
        tenant_message=text,
        ai_summary=(ai_summary or text)[:2000],
        last_tenant_message_at=timezone.now(),
    )
    WhatsAppHandoverMessage.objects.create(
        handover=handover,
        source_message=message_log,
        sender_type=WhatsAppHandoverMessage.SENDER_TENANT,
        original_text=text,
        media=media,
        direction=WhatsAppHandoverMessage.DIRECTION_INBOUND,
    )
    locked_conversation.handover_active = True
    locked_conversation.selected_mode = WhatsAppConversation.MODE_HANDOVER
    locked_conversation.pending_state = ""
    locked_conversation.save(update_fields=["handover_active", "selected_mode", "pending_state", "updated_at"])
    return handover, True


@transaction.atomic
def accept_handover(handover, staff_user):
    locked = WhatsAppHandover.objects.select_for_update().get(pk=handover.pk)
    if not staff_can_access_handover(staff_user, locked):
        raise PermissionError("You are not authorized for this handover.")
    if locked.assigned_staff_id and locked.assigned_staff_id != staff_user.pk:
        raise ValueError("This handover has already been accepted by another staff member.")
    if locked.status not in WhatsAppHandover.ACTIVE_STATUSES:
        raise ValueError("This handover is no longer active.")
    locked.assigned_staff = staff_user
    locked.status = WhatsAppHandover.STATUS_ACCEPTED
    locked.accepted_at = locked.accepted_at or timezone.now()
    locked.save(update_fields=["assigned_staff", "status", "accepted_at", "updated_at"])
    locked.conversation.assigned_staff = staff_user
    locked.conversation.handover_active = True
    locked.conversation.save(update_fields=["assigned_staff", "handover_active", "updated_at"])
    log_staff_action(staff_user, getattr(staff_user, "whatsapp_number", ""), "accept_handover", "allowed", property=locked.property, tenant=locked.tenant, lease=locked.lease, handover_reference=locked.reference)
    return locked


def attach_tenant_message(handover, message_log, text, media=None):
    handover.last_tenant_message_at = timezone.now()
    handover.status = WhatsAppHandover.STATUS_WAITING_FOR_STAFF
    handover.save(update_fields=["last_tenant_message_at", "status", "updated_at"])
    return WhatsAppHandoverMessage.objects.create(
        handover=handover,
        source_message=message_log,
        sender_type=WhatsAppHandoverMessage.SENDER_TENANT,
        original_text=text,
        media=media,
        direction=WhatsAppHandoverMessage.DIRECTION_INBOUND,
    )


@transaction.atomic
def mark_call_requested(handover, staff_user):
    from core.models import GlobalSettings
    if not GlobalSettings.get_solo().whatsapp_allow_manual_call_action:
        raise PermissionError("The manual call action is disabled.")
    locked = _locked_authorized(handover, staff_user)
    locked.status = WhatsAppHandover.STATUS_CALL_REQUESTED
    locked.call_requested_at = timezone.now()
    locked.save(update_fields=["status", "call_requested_at", "updated_at"])
    _action(staff_user, locked, "handover_call_selected")
    return locked


@transaction.atomic
def mark_called(handover, staff_user):
    locked = _locked_authorized(handover, staff_user)
    locked.status = WhatsAppHandover.STATUS_CALLED
    locked.called_at = timezone.now()
    locked.save(update_fields=["status", "called_at", "updated_at"])
    _action(staff_user, locked, "handover_marked_called")
    return locked


@transaction.atomic
def assign_handover(handover, staff_user, target_staff):
    from core.models import GlobalSettings
    if not GlobalSettings.get_solo().whatsapp_allow_handover_reassignment:
        raise PermissionError("Handover reassignment is disabled.")
    locked = _locked_authorized(handover, staff_user)
    if not staff_can_access_handover(target_staff, locked):
        raise PermissionError("The selected staff member is not authorized for this property.")
    locked.assigned_staff = target_staff
    locked.status = WhatsAppHandover.STATUS_ACCEPTED
    locked.accepted_at = timezone.now()
    locked.save(update_fields=["assigned_staff", "status", "accepted_at", "updated_at"])
    locked.conversation.assigned_staff = target_staff
    locked.conversation.save(update_fields=["assigned_staff", "updated_at"])
    _action(staff_user, locked, "handover_reassigned", target_staff_id=target_staff.pk)
    return locked


@transaction.atomic
def close_handover(handover, staff_user):
    from core.models import GlobalSettings
    locked = _locked_authorized(handover, staff_user)
    locked.status = WhatsAppHandover.STATUS_CLOSED
    locked.closed_at = timezone.now()
    locked.save(update_fields=["status", "closed_at", "updated_at"])
    _finish_conversation(locked, return_to_ai=GlobalSettings.get_solo().whatsapp_return_to_ai_after_close)
    _action(staff_user, locked, "close_handover")
    return locked


@transaction.atomic
def return_handover_to_ai(handover, staff_user):
    locked = _locked_authorized(handover, staff_user)
    locked.status = WhatsAppHandover.STATUS_RETURNED_TO_AI
    locked.returned_to_ai_at = timezone.now()
    locked.save(update_fields=["status", "returned_to_ai_at", "updated_at"])
    _finish_conversation(locked, return_to_ai=True)
    _action(staff_user, locked, "return_handover_to_ai")
    return locked


def add_internal_note(handover, staff_user, text):
    if not staff_can_access_handover(staff_user, handover):
        raise PermissionError("You are not authorized for this handover.")
    message = WhatsAppHandoverMessage.objects.create(
        handover=handover,
        sender_type=WhatsAppHandoverMessage.SENDER_STAFF,
        staff_user=staff_user,
        original_text=text,
        direction=WhatsAppHandoverMessage.DIRECTION_INTERNAL,
    )
    _action(staff_user, handover, "handover_internal_note")
    return message


def _locked_authorized(handover, staff_user):
    locked = WhatsAppHandover.objects.select_for_update().get(pk=handover.pk)
    if not staff_can_access_handover(staff_user, locked):
        raise PermissionError("You are not authorized for this handover.")
    if locked.assigned_staff_id and locked.assigned_staff_id != staff_user.pk and not staff_user.is_superuser:
        raise PermissionError("This handover is assigned to another staff member.")
    return locked


def _finish_conversation(handover, return_to_ai):
    conversation = handover.conversation
    conversation.handover_active = False
    conversation.assigned_staff = None
    conversation.pending_state = ""
    conversation.selected_mode = WhatsAppConversation.MODE_TENANT if return_to_ai else conversation.selected_mode
    conversation.save(update_fields=["handover_active", "assigned_staff", "pending_state", "selected_mode", "updated_at"])


def _action(user, handover, action, **details):
    log_staff_action(user, getattr(user, "whatsapp_number", ""), action, "allowed", property=handover.property, tenant=handover.tenant, lease=handover.lease, handover_reference=handover.reference, **details)


def _message_text(message_log):
    payload = message_log.payload or {}
    item = payload.get("text") or {}
    if isinstance(item, dict):
        return item.get("body", "")
    for key in ("image", "document", "audio", "video"):
        item = payload.get(key) or {}
        if isinstance(item, dict) and (item.get("caption") or item.get("filename")):
            return item.get("caption") or item.get("filename")
        if payload.get("type") == key or key in payload:
            labels = {
                "image": "Image message",
                "document": "Document message",
                "audio": "Voice/audio message",
                "video": "Video message",
            }
            return labels[key]
    return ""
