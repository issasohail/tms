"""Credit-control tenant notifications using the existing WhatsApp service."""
from decimal import Decimal
import logging

from django.conf import settings
from django.utils import timezone

from smart_meter.models import MeterCreditAudit
from smart_meter.services.credit_control import bool_setting, notification_muted
from whatsapp.services.whatsapp import WhatsAppService

logger = logging.getLogger(__name__)

NOTIFIABLE_STATES = {
    "warning_1": "Electricity credit warning",
    "warning_2": "Final electricity credit warning",
    "cutoff_eligible": "Electricity cutoff threshold reached",
    "disconnected": "Electricity meter disconnected",
    "reconnect_eligible": "Electricity meter eligible for reconnection",
    "connected": "Electricity meter reconnected",
}


def _tenant_phone(account):
    tenant = getattr(account.lease, "tenant", None)
    return getattr(tenant, "phone", None) or getattr(tenant, "phone2", None) or ""


def _message(account, title):
    tenant = getattr(account.lease, "tenant", None)
    unit = getattr(account.installation, "unit", None)
    prop = getattr(unit, "property", None)
    pct = account.percent_used
    reconnect_amount = max(
        Decimal("0.00"),
        account.current_exposure - (account.effective_credit_limit * account.reconnect_threshold_percent / Decimal("100")),
    ).quantize(Decimal("0.01"))
    return (
        f"{title}\n"
        f"Tenant: {tenant}\n"
        f"Property/Unit: {getattr(prop, 'property_name', prop)} / {getattr(unit, 'unit_number', unit)}\n"
        f"Current electricity exposure: PKR {account.current_exposure:.2f}\n"
        f"Credit limit: PKR {account.effective_credit_limit:.2f} ({pct:.2f}% used)\n"
        f"Remaining credit: PKR {account.remaining_credit:.2f}\n"
        f"Payment needed to reach reconnect level: PKR {reconnect_amount:.2f}\n"
        f"Meter state: {account.meter.power_status.upper()}\n"
        f"As of: {timezone.localtime():%Y-%m-%d %H:%M}"
    )


def maybe_send_credit_notification(account, state=None):
    state = state or account.enforcement_state
    if state not in NOTIFIABLE_STATES:
        return {"sent": False, "reason": "state not notifiable"}
    if not bool_setting("METER_ENABLE_AUTOMATIC_NOTIFICATIONS", False):
        return {"sent": False, "reason": "automatic notifications disabled"}
    if notification_muted(account):
        return {"sent": False, "reason": "notifications muted"}
    phone = _tenant_phone(account)
    if not phone:
        return {"sent": False, "reason": "tenant phone missing"}
    action = f"notification_{state}"
    last_eval = account.last_evaluated_at or account.activated_at or account.created_at
    if MeterCreditAudit.objects.filter(credit_account=account, action_type=action, created_at__gte=last_eval).exists():
        return {"sent": False, "reason": "duplicate suppressed"}
    try:
        response = WhatsAppService().send_text(
            phone,
            _message(account, NOTIFIABLE_STATES[state]),
            tenant=getattr(account.lease, "tenant", None),
        )
        MeterCreditAudit.objects.create(
            action_type=action, meter=account.meter, installation=account.installation,
            lease=account.lease, tenant=getattr(account.lease, "tenant", None),
            credit_account=account, source="automatic", reason="credit-control notification",
            metadata={"state": state, "success": True, "provider_result": str(response)[:500]},
        )
        return {"sent": True, "response": response}
    except Exception as exc:
        logger.exception("meter_credit_notification_failed account=%s state=%s", account.pk, state)
        MeterCreditAudit.objects.create(
            action_type=f"{action}_failed", meter=account.meter, installation=account.installation,
            lease=account.lease, tenant=getattr(account.lease, "tenant", None),
            credit_account=account, source="automatic", reason=str(exc), metadata={"state": state},
        )
        return {"sent": False, "reason": str(exc)}
