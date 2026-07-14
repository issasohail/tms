from django.utils import timezone

from whatsapp.models import WhatsAppHandover, WhatsAppMessageLog
from .safety import safe_summary


def build_safe_context(sender, conversation, lease=None, history_limit=8):
    history = []
    for item in (
        WhatsAppMessageLog.objects.filter(phone_number=conversation.phone_number)
        .exclude(direction=WhatsAppMessageLog.DIRECTION_STATUS)
        .order_by("-created_at")[:history_limit]
    ):
        history.append({
            "direction": item.direction,
            "text": safe_summary(_message_text(item.payload), 240),
        })
    open_handover = conversation.handovers.filter(status__in=WhatsAppHandover.ACTIVE_STATUSES).first()
    tenant = getattr(lease, "tenant", None) or sender.tenant
    unit = getattr(lease, "unit", None)
    return {
        "sender_roles": list(sender.available_modes),
        "active_mode": conversation.selected_mode or sender.active_mode or "",
        "tenant_first_name": getattr(tenant, "first_name", "") if tenant else "",
        "property": getattr(getattr(unit, "property", None), "property_name", "") if unit else "",
        "unit": getattr(unit, "unit_number", "") if unit else "",
        "active_lease": bool(lease),
        "workflow_state": conversation.pending_state,
        "open_handover": open_handover.reference if open_handover else "",
        # Never expose database identifiers or unrelated property records to the
        # model. Tool handlers re-check authorization against the ORM.
        "staff_permission_scope": {
            "property_count": len(sender.property_permissions),
            "restricted": bool(sender.staff_user and not getattr(sender.staff_user, "is_superuser", False)),
        },
        "preferred_language": conversation.preferred_language,
        "current_date": str(timezone.localdate()),
        "timezone": str(timezone.get_current_timezone()),
        "recent_messages": list(reversed(history)),
    }


def _message_text(payload):
    payload = payload or {}
    text = payload.get("text") or {}
    return text.get("body", "") if isinstance(text, dict) else ""
