TENANT_MODE_HINTS = (
    "my balance", "mera balance", "my payment", "meri payment", "my lease",
    "my invoice", "my account", "my tenant account",
)
STAFF_MODE_HINTS = (
    "staff inbox", "pending handover", "pending tenant", "assign handover",
    "show handover", "accept handover", "staff mode",
)


def infer_mode(text, sender_context, minimum_confidence=75):
    lowered = (text or "").strip().lower()
    tenant_score = 95 if any(item in lowered for item in TENANT_MODE_HINTS) else 0
    staff_score = 95 if any(item in lowered for item in STAFF_MODE_HINTS) else 0
    if tenant_score >= minimum_confidence and "tenant" in sender_context.available_modes:
        return "tenant", tenant_score
    if staff_score >= minimum_confidence and "staff" in sender_context.available_modes:
        return "staff", staff_score
    return "", max(tenant_score, staff_score)
