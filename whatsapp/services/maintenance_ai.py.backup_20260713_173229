from whatsapp.models import PendingWhatsAppMaintenance


ISSUE_TYPES = {
    "plumbing": ("plumbing", "Plumbing"),
    "water": ("water leakage", "Water Leakage"),
    "leak": ("water leakage", "Water Leakage"),
    "electric": ("electrical", "Electrical"),
    "light": ("electrical", "Electrical"),
    "ac": ("ac", "AC"),
    "clean": ("cleaning", "Cleaning"),
    "door": ("door", "Door"),
    "window": ("window", "Window"),
}


def detect_maintenance_issue(text):
    lowered = (text or "").lower()
    issue = "Other"
    confidence = 45
    for needle, (_, label) in ISSUE_TYPES.items():
        if needle in lowered:
            issue = label
            confidence = 75
            break

    urgency = "normal"
    if any(word in lowered for word in ("emergency", "urgent", "fire", "spark", "flood")):
        urgency = "urgent"
        confidence = max(confidence, 80)
    return issue, urgency, confidence


def create_pending_maintenance(message_log, conversation, lease=None, media=None):
    text = _message_text(message_log.payload or {})
    issue, urgency, confidence = detect_maintenance_issue(text)
    pending = PendingWhatsAppMaintenance.objects.create(
        conversation=conversation,
        original_whatsapp_message=message_log,
        phone=message_log.phone_number,
        tenant=getattr(lease, "tenant", None),
        lease=lease,
        property=getattr(getattr(lease, "unit", None), "property", None),
        unit=getattr(lease, "unit", None),
        issue_type=issue,
        urgency=urgency,
        description=text,
        ai_confidence=confidence,
        ai_notes="Maintenance request staged for admin approval.",
    )
    if media:
        pending.media.add(media)
    return pending


def _message_text(payload):
    text_payload = payload.get("text") or {}
    if isinstance(text_payload, dict):
        return text_payload.get("body", "")
    return ""
