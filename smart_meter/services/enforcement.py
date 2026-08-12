"""Manual and guarded automatic credit-control enforcement."""
from datetime import datetime, time, timedelta
from decimal import Decimal
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from smart_meter.models import MeterCreditAccount, MeterCreditAudit, MeterCommand
from smart_meter.services.command_lifecycle import (
    automatic_cutoff_enabled,
    automatic_restore_enabled,
    queue_relay_command,
)
from smart_meter.services.credit_control import enforcement_held, evaluate_credit_account

logger = logging.getLogger(__name__)


def _allowed_meter(account):
    allowed = set(getattr(settings, "METER_CREDIT_ALLOWED_METER_IDS", ()) or ())
    return bool(allowed and account.meter_id in allowed)


def _parse_clock(value, fallback):
    try:
        h, m = str(value).split(":", 1)
        return time(int(h), int(m))
    except Exception:
        return fallback


def in_protected_hours(now=None):
    now = timezone.localtime(now or timezone.now())
    start = _parse_clock(getattr(settings, "METER_AUTOMATIC_CUTOFF_PROTECTED_START", "20:00"), time(20, 0))
    end = _parse_clock(getattr(settings, "METER_AUTOMATIC_CUTOFF_PROTECTED_END", "08:00"), time(8, 0))
    current = now.time().replace(tzinfo=None)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def next_allowed_time(now=None):
    now = timezone.localtime(now or timezone.now())
    end = _parse_clock(getattr(settings, "METER_AUTOMATIC_CUTOFF_PROTECTED_END", "08:00"), time(8, 0))
    candidate = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _require_permission(user, codename):
    if user is None or not getattr(user, "has_perm", lambda _p: False)(f"smart_meter.{codename}"):
        raise PermissionError(f"Permission smart_meter.{codename} is required")


def approve_cutoff(account_id, *, user, reason):
    _require_permission(user, "approve_meter_credit_cutoff")
    if not reason:
        raise ValueError("A reason is required")
    with transaction.atomic():
        account = MeterCreditAccount.objects.select_for_update().select_related("meter", "installation", "lease").get(pk=account_id)
        evaluate_credit_account(account.pk, source="manual")
        account.refresh_from_db()
        threshold = account.effective_credit_limit * account.cutoff_threshold_percent / Decimal("100")
        if account.current_exposure < threshold:
            raise ValueError("Account is below the cutoff threshold")
        if enforcement_held(account):
            raise ValueError("An enforcement hold is active")
        cmd = queue_relay_command(
            account.meter, "off", source="manual", initiated_by=getattr(user, "get_username", lambda: str(user))(),
            reason=reason, credit_account=account, priority=10, requires_verification=True,
        )
        MeterCreditAudit.objects.create(
            action_type="approved_cutoff", meter=account.meter, installation=account.installation,
            lease=account.lease, tenant=getattr(account.lease, "tenant", None), credit_account=account,
            user=user, source="manual", reason=reason, threshold=threshold,
            exposure_after=account.current_exposure, metadata={"command_id": cmd.pk},
        )
        account.enforcement_state = "cutoff_pending"
        account.save(update_fields=["enforcement_state", "updated_at"])
        return cmd


def automatic_enforcement(account_id):
    account = MeterCreditAccount.objects.select_related("meter", "installation", "lease").get(pk=account_id)
    if not account.is_enabled or not _allowed_meter(account):
        return None
    if account.enforcement_state == "cutoff_eligible" and account.automatic_cutoff and not account.manual_only_cutoff:
        if not automatic_cutoff_enabled() or enforcement_held(account):
            return None
        not_before = next_allowed_time() if in_protected_hours() else None
        cmd = queue_relay_command(
            account.meter, "off", source="credit_control", reason="automatic credit limit cutoff",
            credit_account=account, priority=20, requires_verification=True,
        )
        if not_before:
            MeterCommand.objects.filter(pk=cmd.pk).update(not_before=not_before)
        MeterCreditAccount.objects.filter(pk=account.pk).update(enforcement_state="cutoff_pending")
        return cmd
    reconnect_threshold = account.effective_credit_limit * account.reconnect_threshold_percent / Decimal("100")
    uncertain_off = MeterCommand.objects.filter(
        meter=account.meter, command_type="relay", desired_state="off",
        source__in=("credit_control", "system"), status__in=("sent", "acknowledged"),
    ).exists()
    if account.current_exposure < reconnect_threshold and account.automatic_restore and (account.meter.power_status == "off" or uncertain_off):
        if not automatic_restore_enabled():
            return None
        cmd = queue_relay_command(
            account.meter, "on", source="payment" if uncertain_off else "credit_control",
            reason="restore below reconnect threshold; prior OFF outcome uncertain" if uncertain_off else "automatic restore below reconnect threshold",
            credit_account=account, priority=15 if uncertain_off else 20, requires_verification=True,
        )
        MeterCreditAccount.objects.filter(pk=account.pk).update(enforcement_state="reconnect_pending")
        return cmd
    return None
