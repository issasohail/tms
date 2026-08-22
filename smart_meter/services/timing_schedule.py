"""Event-based recurring meter schedule layered above billing and credit restrictions."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from smart_meter.models import Meter, MeterCommand, MeterCreditAccount, MeterTimingEvent
from smart_meter.services.command_lifecycle import ACTIVE, queue_relay_command

BLOCKED_CREDIT_STATES = {
    "cutoff_eligible", "cutoff_pending", "cutoff_sent", "disconnected",
    "manual_hold", "data_review_required", "reading_reset_detected",
    "tariff_missing", "stale_reading", "installation_mismatch", "command_failed",
}
FAILED_COMMAND_STATES = {"cancelled", "expired", "failed", "error", "timeout"}
PROVEN_OFF_STATES = {"sent", "acknowledged", "verified", "ok"}


@dataclass(frozen=True)
class ScheduleDecision:
    has_schedule: bool
    allowed_now: bool | None
    desired_state: str | None
    reason: str
    event: MeterTimingEvent | None = None


def _latest_effective_event(meter: Meter, *, at=None):
    at = timezone.localtime(at or timezone.now())
    events = list(MeterTimingEvent.objects.filter(meter=meter, is_enabled=True).order_by("weekday", "event_time", "id"))
    if not events:
        return None
    now_minute = at.weekday() * 1440 + at.hour * 60 + at.minute
    ranked = []
    for event in events:
        minute = event.weekday * 1440 + event.event_time.hour * 60 + event.event_time.minute
        delta = (now_minute - minute) % (7 * 1440)
        ranked.append((delta, -event.id, event))
    return min(ranked, key=lambda item: (item[0], item[1]))[2]


def schedule_allows_power(meter: Meter, *, at=None) -> bool | None:
    """None=no schedule, True=latest event says ON, False=latest event says OFF."""
    event = _latest_effective_event(meter, at=at)
    if not event:
        return None
    return event.command == "on"


def schedule_decision(meter: Meter, *, at=None) -> ScheduleDecision:
    event = _latest_effective_event(meter, at=at)
    if not event:
        return ScheduleDecision(False, None, None, "no enabled timing schedule")
    desired = event.command
    return ScheduleDecision(True, desired == "on", desired, f"latest schedule event is {desired.upper()}", event)


def next_schedule_event(meter: Meter, *, at=None):
    at = timezone.localtime(at or timezone.now())
    events = list(MeterTimingEvent.objects.filter(meter=meter, is_enabled=True).order_by("weekday", "event_time", "id"))
    if not events:
        return None
    now_minute = at.weekday() * 1440 + at.hour * 60 + at.minute
    candidates = []
    for event in events:
        minute = event.weekday * 1440 + event.event_time.hour * 60 + event.event_time.minute
        delta = (minute - now_minute) % (7 * 1440)
        if delta == 0:
            delta = 7 * 1440
        candidates.append((delta, event.id, event))
    delta, _id, event = min(candidates, key=lambda item: (item[0], item[1]))
    return event, delta


def _latest_schedule_off_proven(meter: Meter):
    return MeterCommand.objects.filter(meter=meter, command_type="relay", desired_state="off", source="schedule").filter(
        Q(status__in=PROVEN_OFF_STATES) | Q(status="cancelled", last_attempt_at__isnull=False) | Q(parsed_relay_state="off")
    ).order_by("-created_at", "-id").first()


def schedule_restore_allowed(meter: Meter, *, at=None) -> tuple[bool, str]:
    if schedule_allows_power(meter, at=at) is not True:
        return False, "current timing schedule does not allow power"
    if not meter.is_active:
        return False, "meter is inactive"
    if meter.is_check_meter:
        return False, "audit/check meter cannot be automatically switched"
    if meter.power_status == "on":
        return False, "meter already on"
    schedule_off = _latest_schedule_off_proven(meter)
    if not schedule_off:
        return False, "no proven schedule OFF to restore"
    newer_off = MeterCommand.objects.filter(meter=meter, command_type="relay", desired_state="off", created_at__gt=schedule_off.created_at).exclude(source="schedule").exclude(status__in=FAILED_COMMAND_STATES).exists()
    if newer_off:
        return False, "newer non-schedule OFF command exists"
    if meter.billing_mode == "credit_controlled":
        account = MeterCreditAccount.objects.filter(meter=meter, is_enabled=True).order_by("-activated_at", "-id").first()
        if account:
            try:
                from smart_meter.services.credit_control import evaluate_credit_account
                evaluate_credit_account(account.pk, source="scheduled")
                account.refresh_from_db()
            except Exception as exc:
                return False, f"credit-control evaluation failed: {exc}"
            if account.enforcement_state in BLOCKED_CREDIT_STATES:
                return False, f"credit-control state blocks restore: {account.enforcement_state}"
            cutoff = account.effective_credit_limit * account.cutoff_threshold_percent / Decimal("100")
            if account.current_exposure >= cutoff:
                return False, "credit exposure is at/above cutoff threshold"
    return True, "schedule restore is permitted by current restrictions"


def revalidate_schedule_command(command: MeterCommand):
    from smart_meter.services.command_lifecycle import RevalidationResult
    if not command.meter_id:
        return RevalidationResult(False, "schedule command has no meter", desired_state=command.desired_state)
    meter = command.meter
    allowed = schedule_allows_power(meter)
    if allowed is None:
        return RevalidationResult(False, "timing schedule was removed or disabled", desired_state=command.desired_state)
    if command.desired_state == "off":
        if allowed:
            return RevalidationResult(False, "latest timing event now requires ON", desired_state="off")
        if meter.power_status == "off":
            return RevalidationResult(False, "meter already off", desired_state="off")
        return RevalidationResult(True, "schedule still requires OFF", desired_state="off")
    ok, reason = schedule_restore_allowed(meter)
    return RevalidationResult(ok, reason, desired_state="on")


def enforce_meter_timing_schedule(meter: Meter):
    decision = schedule_decision(meter)
    if not decision.has_schedule or not meter.is_active or meter.is_check_meter:
        return None
    desired = decision.desired_state
    if desired == "off" and meter.power_status != "off":
        return queue_relay_command(meter, "off", source="schedule", requested_by=None)
    if desired == "on" and meter.power_status != "on":
        ok, _reason = schedule_restore_allowed(meter)
        if ok:
            return queue_relay_command(meter, "on", source="schedule", requested_by=None)
    return None


def enforce_all_meter_timing_schedules():
    count = 0
    meter_ids = MeterTimingEvent.objects.filter(is_enabled=True).values_list("meter_id", flat=True).distinct()
    for meter in Meter.objects.filter(pk__in=meter_ids, is_active=True).iterator():
        if enforce_meter_timing_schedule(meter):
            count += 1
    return count


def copy_timing_schedule(source: Meter, target: Meter) -> int:
    source_events = list(MeterTimingEvent.objects.filter(meter=source).order_by("weekday", "event_time", "id"))
    with transaction.atomic():
        MeterTimingEvent.objects.filter(meter=target).delete()
        MeterTimingEvent.objects.bulk_create([
            MeterTimingEvent(meter=target, weekday=e.weekday, event_time=e.event_time, command=e.command, notes=e.notes, is_enabled=e.is_enabled)
            for e in source_events
        ])
    return len(source_events)
