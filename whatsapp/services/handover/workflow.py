import re

from django.contrib.auth import get_user_model

from whatsapp.models import WhatsAppHandover, WhatsAppHandoverMessage
from whatsapp.services.handover.lifecycle import (
    accept_handover,
    add_internal_note,
    assign_handover,
    attach_tenant_message,
    close_handover,
    mark_call_requested,
    mark_called,
    return_handover_to_ai,
)
from whatsapp.services.handover.notifications import notify_tenant_message, tenant_name
from whatsapp.services.handover.relay import relay_staff_reply, relay_tenant_media_to_staff
from whatsapp.services.media_processor import create_pending_media
from whatsapp.services.handover.routing import eligible_staff, staff_can_access_handover
from core.utils.identity import format_phone


REFERENCE_RE = re.compile(r"\bWH-\d{4}-[A-Z0-9]{8}\b", re.I)
REPLY_SHORTCUT_RE = re.compile(r"^\s*reply\s*:\s*(.*)$", re.I | re.S)


def active_handover(conversation):
    return conversation.handovers.filter(status__in=WhatsAppHandover.ACTIVE_STATUSES).order_by("-created_at").first()


def handle_active_tenant_message(message_log, conversation, text, media=None, service=None):
    handover = active_handover(conversation)
    if not handover:
        return None
    attach_tenant_message(handover, message_log, text, media=media)
    notify_tenant_message(handover, text or "Tenant sent media.", service=service)
    if media:
        relay_tenant_media_to_staff(handover, media, service=service)
    return (
        f"Your message has been added to {handover.reference} and sent to management. "
        "This case is awaiting staff response."
    )


def handle_staff_handover_media(message_log, conversation, text, staff_user, service=None):
    shortcut_text = _reply_shortcut_text(text)
    if shortcut_text is not None:
        handover = _latest_notified_handover(conversation, staff_user)
        if not handover:
            return "I could not identify the tenant. Use REPLY followed by the handover reference first."
    elif conversation.pending_state == "awaiting_staff_reply":
        handover = WhatsAppHandover.objects.filter(pk=conversation.context.get("handover_id")).first()
    else:
        return None
    if not handover:
        _clear_state(conversation)
        return "That handover is no longer available."
    media = create_pending_media(message_log, conversation, handover.lease)
    try:
        reply_text = shortcut_text if shortcut_text is not None else text
        relay_staff_reply(handover, staff_user, reply_text, source_message=message_log, media=media, service=service)
    except (PermissionError, ValueError) as exc:
        return str(exc)
    _clear_state(conversation)
    if getattr(media, "processing", False):
        return f"Your media reply for {tenant_name(handover)} is downloading and will be sent automatically."
    return f"Your media reply was sent to {tenant_name(handover)}."


def handle_staff_handover_message(message_log, conversation, text, staff_user, service=None):
    state = conversation.pending_state
    if state in {"awaiting_staff_reply", "awaiting_handover_note"}:
        handover = WhatsAppHandover.objects.filter(pk=conversation.context.get("handover_id")).first()
        if not handover:
            _clear_state(conversation)
            return "That handover is no longer available."
        if state == "awaiting_staff_reply":
            shortcut_text = _reply_shortcut_text(text)
            reply_text = shortcut_text if shortcut_text is not None else text
            relay_staff_reply(handover, staff_user, reply_text, source_message=message_log, service=service)
            _clear_state(conversation)
            return f"Your reply was sent to {tenant_name(handover)}."
        add_internal_note(handover, staff_user, text)
        _clear_state(conversation)
        return f"Internal note added to {handover.reference}."

    lowered = (text or "").strip().lower()
    shortcut_text = _reply_shortcut_text(text)
    if shortcut_text is not None:
        handover = _latest_notified_handover(conversation, staff_user)
        if not handover:
            return "I could not identify the tenant. Use REPLY followed by the handover reference."
        if shortcut_text:
            try:
                relay_staff_reply(
                    handover,
                    staff_user,
                    shortcut_text,
                    source_message=message_log,
                    service=service,
                )
            except (PermissionError, ValueError) as exc:
                return str(exc)
            return f"Your reply was sent to {tenant_name(handover)}."
        conversation.pending_state = "awaiting_staff_reply"
        conversation.context["handover_id"] = handover.pk
        conversation.save(update_fields=["pending_state", "context", "updated_at"])
        return (
            f"Send your message or media for {tenant_name(handover)}. "
            "It will be delivered from the official TMS WhatsApp number."
        )
    if lowered in {"handovers", "handover", "staff inbox", "inbox", "pending handovers"}:
        return staff_inbox_text(staff_user)

    reference = _reference(text)
    if not reference:
        return None
    handover = WhatsAppHandover.objects.select_related("tenant", "lease", "property", "unit", "assigned_staff").filter(reference__iexact=reference).first()
    if not handover:
        return "Handover reference not found."
    if not staff_can_access_handover(staff_user, handover):
        return "You are not authorized to access this handover."

    command, remainder = _command_and_remainder(text, reference)
    try:
        if command == "accept":
            accept_handover(handover, staff_user)
            return f"Accepted {handover.reference}.\nTenant phone: {format_phone(handover.tenant_phone)}\n\nReply {handover.reference} to respond."
        if command in {"details", "detail", "view"}:
            return handover_details_text(handover)
        if command == "reply":
            if remainder:
                relay_staff_reply(handover, staff_user, remainder, source_message=message_log, service=service)
                return f"Your reply was sent to {tenant_name(handover)}."
            conversation.pending_state = "awaiting_staff_reply"
            conversation.context["handover_id"] = handover.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return f"Send your reply for {handover.reference}. It will be delivered from the official TMS WhatsApp number."
        if command == "call":
            mark_call_requested(handover, staff_user)
            return f"Tenant phone:\n{format_phone(handover.tenant_phone)}\n\nPlease call using WhatsApp or your phone dialer.\n\nSend CALLED {handover.reference} after the call."
        if command in {"called", "mark called"}:
            mark_called(handover, staff_user)
            return f"Call recorded for {handover.reference}."
        if command == "close":
            close_handover(handover, staff_user)
            return f"Closed {handover.reference}."
        if command in {"return", "return ai", "ai"}:
            return_handover_to_ai(handover, staff_user)
            return f"{handover.reference} was returned to the AI assistant."
        if command == "note":
            if remainder:
                add_internal_note(handover, staff_user, remainder)
                return f"Internal note added to {handover.reference}."
            conversation.pending_state = "awaiting_handover_note"
            conversation.context["handover_id"] = handover.pk
            conversation.save(update_fields=["pending_state", "context", "updated_at"])
            return f"Send the internal note for {handover.reference}."
        if command == "assign":
            if not remainder:
                choices = eligible_staff(handover)
                return "Authorized staff:\n" + "\n".join(f"- {item.username}" for item in choices)
            target = get_user_model().objects.filter(username__iexact=remainder.strip(), is_active=True, is_staff=True).first()
            if not target:
                return "Authorized staff user not found."
            assign_handover(handover, staff_user, target)
            return f"{handover.reference} assigned to {target.username}."
    except (PermissionError, ValueError) as exc:
        return str(exc)
    return handover_details_text(handover)


def staff_inbox_text(staff_user):
    new_items = [item for item in WhatsAppHandover.objects.filter(status__in=[WhatsAppHandover.STATUS_NEW, WhatsAppHandover.STATUS_NOTIFIED]).select_related("property", "unit") if staff_can_access_handover(staff_user, item)][:8]
    mine = list(WhatsAppHandover.objects.filter(assigned_staff=staff_user, status__in=WhatsAppHandover.ACTIVE_STATUSES).select_related("property", "unit")[:8])
    lines = ["Staff Inbox", "", "New handovers:"]
    lines.extend(_inbox_line(item) for item in new_items)
    if not new_items:
        lines.append("None")
    lines.extend(["", "My active handovers:"])
    lines.extend(_inbox_line(item) for item in mine)
    if not mine:
        lines.append("None")
    lines.extend(["", "Use DETAILS reference or ACCEPT reference.", "Type Switch Mode to change mode."])
    return "\n".join(lines)


def handover_details_text(handover):
    recent = handover.messages.exclude(direction=WhatsAppHandoverMessage.DIRECTION_INTERNAL).order_by("-created_at")[:5]
    lines = [
        f"Handover {handover.reference}",
        f"Status: {handover.get_status_display()}",
        f"Priority: {handover.get_priority_display()}",
        f"Tenant: {tenant_name(handover)}",
        f"Phone: {format_phone(handover.tenant_phone)}",
        f"Property / Unit: {handover.property or '-'} / {handover.unit or '-'}",
        f"Reason: {handover.reason}",
        f"AI summary: {handover.ai_summary or '-'}",
        "",
        "Recent messages:",
    ]
    for item in reversed(list(recent)):
        lines.append(f"{item.get_sender_type_display()}: {item.original_text or '[media]'}")
    lines.extend(["", f"ACCEPT {handover.reference}", f"REPLY {handover.reference}", f"CALL {handover.reference}", f"NOTE {handover.reference}", f"CLOSE {handover.reference}", f"RETURN {handover.reference}"])
    return "\n".join(lines)


def _reference(text):
    match = REFERENCE_RE.search((text or "").upper())
    return match.group(0).upper() if match else ""


def _reply_shortcut_text(text):
    match = REPLY_SHORTCUT_RE.match(text or "")
    return match.group(1).strip() if match else None


def _latest_notified_handover(conversation, staff_user):
    handover_id = (conversation.context or {}).get("latest_notified_handover_id")
    if handover_id:
        handover = WhatsAppHandover.objects.select_related(
            "tenant", "lease", "property", "unit", "assigned_staff"
        ).filter(pk=handover_id, status__in=WhatsAppHandover.ACTIVE_STATUSES).first()
        if handover and staff_can_access_handover(staff_user, handover):
            return handover

    accessible = []
    for handover in WhatsAppHandover.objects.select_related(
        "tenant", "lease", "property", "unit", "assigned_staff"
    ).filter(status__in=WhatsAppHandover.ACTIVE_STATUSES).order_by("-updated_at")[:20]:
        if staff_can_access_handover(staff_user, handover):
            accessible.append(handover)
            if len(accessible) > 1:
                return None
    return accessible[0] if accessible else None


def _command_and_remainder(text, reference):
    raw = (text or "").strip()
    match = re.search(re.escape(reference), raw, flags=re.IGNORECASE)
    if not match:
        return raw.lower() or "details", ""
    command = raw[:match.start()].strip().lower() or "details"
    remainder = raw[match.end():].strip()
    if remainder.startswith(":"):
        remainder = remainder[1:].strip()
    return command, remainder


def _clear_state(conversation):
    conversation.pending_state = ""
    conversation.context.pop("handover_id", None)
    conversation.save(update_fields=["pending_state", "context", "updated_at"])


def _inbox_line(item):
    return f"- {item.reference} | {item.get_priority_display()} | {item.property or '-'} {item.unit or ''} | {item.reason[:45]}"


HANDOVER_PHRASES = {
    "human": ("Requested a staff member", "general", "normal"),
    "talk to a person": ("Requested a staff member", "general", "normal"),
    "human please": ("Requested a staff member", "general", "normal"),
    "management": ("Requested management", "management", "high"),
    "call me": ("Requested a callback", "general", "high"),
    "mujhe call": ("Requested a callback", "general", "high"),
    "balance is wrong": ("Payment balance disputed", "accounts", "high"),
    "balance wrong": ("Payment balance disputed", "accounts", "high"),
    "already paid": ("Payment disputed; tenant says already paid", "accounts", "high"),
    "i already paid": ("Payment disputed; tenant says already paid", "accounts", "high"),
    "complain": ("Tenant complaint", "management", "high"),
    "complaint": ("Tenant complaint", "management", "high"),
    "legal": ("Legal concern", "management", "urgent"),
    "unsafe": ("Safety concern", "maintenance", "urgent"),
    "fire": ("Emergency or fire concern", "maintenance", "urgent"),
    "you did not understand": ("Assistant failed to understand", "general", "normal"),
    "send this to staff": ("Requested staff review", "general", "normal"),
}


def detect_handover_request(text):
    lowered = (text or "").strip().lower()
    for phrase, result in HANDOVER_PHRASES.items():
        if phrase in lowered:
            return result
    return None
