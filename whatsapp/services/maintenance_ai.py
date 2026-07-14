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


def create_pending_maintenance(message_log, conversation, lease=None, media=None, extracted=None):
    text = _message_text(message_log.payload or {})
    issue, urgency, confidence = detect_maintenance_issue(text)
    extracted = extracted or {}
    issue = str(extracted.get("issue_type") or issue)[:80]
    urgency = str(extracted.get("urgency") or urgency)[:20]
    description = str(extracted.get("description") or text)
    location = str(extracted.get("location") or "").strip()
    if location and location.lower() not in description.lower():
        description = f"{description}\nLocation: {location}".strip()
    if extracted:
        confidence = max(confidence, 75)
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
        description=description,
        ai_confidence=confidence,
        ai_notes=(
            "Maintenance request staged for admin approval."
            + (f" Follow-up: {str(extracted.get('follow_up_question'))[:300]}" if extracted.get("follow_up_question") else "")
        ),
    )
    if media:
        pending.media.add(media)
    return pending


def _message_text(payload):
    text_payload = payload.get("text") or {}
    if isinstance(text_payload, dict):
        return text_payload.get("body", "")
    return ""
