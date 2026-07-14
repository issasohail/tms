class WhatsAppCallingService:
    """Provider boundary for future calling; current behavior is intentionally manual."""

    def can_initiate_call(self, tenant_phone, staff_user):
        return False

    def get_call_permission_status(self, tenant_phone):
        return {"supported": False, "mode": "manual", "tenant_phone": tenant_phone}

    def initiate_call(self, tenant_phone, staff_user):
        return {"ok": False, "supported": False, "mode": "manual", "tenant_phone": tenant_phone}

    def process_incoming_call_event(self, payload):
        return {"ok": False, "supported": False, "event": payload or {}}

    def record_call_status(self, payload):
        return {"ok": False, "supported": False, "event": payload or {}}
