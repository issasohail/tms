from datetime import timedelta

from django.utils import timezone

from core.models import GlobalSettings
from whatsapp.models import WhatsAppHandover, WhatsAppStaffActionLog
from whatsapp.services.handover.notifications import notify_new_handover


def send_due_handover_reminders(service=None):
    """Notify routed staff again without requiring a browser or global broadcast."""
    config = GlobalSettings.get_solo()
    interval = max(1, config.whatsapp_handover_reminder_interval_minutes)
    escalation = max(interval, config.whatsapp_handover_escalation_timeout_minutes)
    now = timezone.now()
    reminded = 0
    escalated = 0
    handovers = WhatsAppHandover.objects.filter(
        status__in=[WhatsAppHandover.STATUS_NEW, WhatsAppHandover.STATUS_NOTIFIED],
        assigned_staff__isnull=True,
    )
    for handover in handovers:
        prior = [
            item for item in WhatsAppStaffActionLog.objects.filter(
                action="handover_reminder", created_at__gte=handover.created_at
            ).order_by("-created_at")
            if (item.details or {}).get("handover_reference") == handover.reference
        ]
        if len(prior) >= config.whatsapp_handover_max_reminders:
            continue
        last_at = prior[0].created_at if prior else handover.created_at
        if last_at > now - timedelta(minutes=interval):
            continue
        notify_new_handover(handover, service=service)
        is_escalation = handover.created_at <= now - timedelta(minutes=escalation)
        WhatsAppStaffActionLog.objects.create(
            phone_number=handover.tenant_phone,
            action="handover_reminder",
            status=WhatsAppStaffActionLog.ACTION_STATUS_ALLOWED,
            property=handover.property,
            tenant=handover.tenant,
            lease=handover.lease,
            details={
                "handover_reference": handover.reference,
                "reminder_number": len(prior) + 1,
                "escalated": is_escalation,
            },
        )
        reminded += 1
        escalated += int(is_escalation)
    return {"reminded": reminded, "escalated": escalated}
