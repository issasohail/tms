"""Durable smart-meter command lifecycle.

This service deliberately does not own the TCP socket.  The existing listener remains
responsible for transport; this module owns business-safe queueing, consolidation and
revalidation so manual and automatic relay requests share one queue.
"""
from __future__ import annotations

import hashlib
import uuid
import logging
from dataclasses import dataclass
from datetime import time, timedelta
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from smart_meter.models import LiveReading, Meter, MeterCommand, MeterCreditAccount
from smart_meter.vendor.switch_OnOff import (
    RELAY_CLOSE_COMMAND,
    RELAY_OPEN_COMMAND,
    frame_command,
)

logger = logging.getLogger(__name__)

TERMINAL = {"verified", "cancelled", "expired", "failed", "ok", "error"}
ACTIVE = {"new", "pending", "waiting_online", "claimed", "sent", "acknowledged", "retry", "timeout"}


def _bool_setting(name: str, default: bool = False) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def automatic_cutoff_enabled() -> bool:
    return _bool_setting("METER_ENABLE_AUTOMATIC_CUTOFF", False) and not _bool_setting("METER_EMERGENCY_STOP", False)


def automatic_restore_enabled() -> bool:
    return _bool_setting("METER_ENABLE_AUTOMATIC_RESTORE", False) and not _bool_setting("METER_EMERGENCY_STOP", False)


def automatic_evaluation_enabled() -> bool:
    return _bool_setting("METER_ENABLE_AUTOMATIC_CREDIT_EVALUATION", False)


def _credit_allowlisted(meter_id: int) -> bool:
    allowed = set(getattr(settings, "METER_CREDIT_ALLOWED_METER_IDS", ()) or ())
    return bool(allowed and meter_id in allowed)


def latest_credit_cutoff_command(account: MeterCreditAccount, *, before=None):
    """Return the latest OFF command that proves a credit-control cutoff was attempted.

    A queued/pending OFF command is not enough provenance for an automatic restore. A
    command must have reached the meter, been acknowledged/verified, or retain that
    evidence after being superseded by the compensating ON request.
    """
    queryset = MeterCommand.objects.filter(
        meter_id=account.meter_id,
        related_credit_account=account,
        command_type="relay",
        desired_state="off",
        source="credit_control",
    )
    if before is not None:
        queryset = queryset.filter(created_at__lt=before)
    queryset = queryset.filter(
        Q(status__in=("sent", "acknowledged", "verified", "ok"))
        | Q(acknowledged_at__isnull=False)
        | Q(verified_at__isnull=False)
        | Q(parsed_relay_state="off")
        | Q(
            status="cancelled",
            cancelled_reason__startswith="superseded by on request",
            last_attempt_at__isnull=False,
        )
    )
    return queryset.order_by("-created_at", "-id").first()


def _account_hold_active(account: MeterCreditAccount, now=None) -> bool:
    now = now or timezone.now()
    if account.enforcement_hold_for_period == "indefinite":
        return True
    if account.enforcement_hold_for_period == "current_month":
        stamp = account.enforcement_hold_at or account.updated_at
        return bool(stamp and (stamp.year, stamp.month) == (now.year, now.month))
    return bool(account.enforcement_hold_until and account.enforcement_hold_until > now)


def _in_protected_hours(now=None) -> bool:
    now = timezone.localtime(now or timezone.now())
    def parse(value, fallback):
        try:
            hour, minute = str(value).split(":", 1)
            return time(int(hour), int(minute))
        except Exception:
            return fallback
    start = parse(getattr(settings, "METER_AUTOMATIC_CUTOFF_PROTECTED_START", "20:00"), time(20, 0))
    end = parse(getattr(settings, "METER_AUTOMATIC_CUTOFF_PROTECTED_END", "08:00"), time(8, 0))
    current = now.time().replace(tzinfo=None)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


@dataclass(frozen=True)
class RevalidationResult:
    allowed: bool
    reason: str
    current_exposure: object = None
    current_threshold: object = None
    current_installation: object = None
    current_lease: object = None
    desired_state: str = ""



def _active_installation_matches(account: MeterCreditAccount) -> bool:
    inst = account.installation
    return bool(
        inst.is_active
        and inst.end_date is None
        and inst.meter_id == account.meter_id
        and account.lease_id == inst.lease_id
    )


def _lease_is_active(account: MeterCreditAccount) -> bool:
    return getattr(account.lease, "status", "") == "active"


def still_should_disconnect(command: MeterCommand) -> RevalidationResult:
    account = command.related_credit_account
    if not account or not account.is_enabled:
        return RevalidationResult(False, "credit account inactive", desired_state="off")
    if command.source == "credit_control" and not (
        account.automatic_cutoff and account.automatic_restore and not account.manual_only_cutoff
    ):
        return RevalidationResult(False, "combined automatic cutoff and restore is disabled", desired_state="off")
    if command.source == "credit_control":
        if not automatic_evaluation_enabled():
            return RevalidationResult(False, "automatic credit evaluation feature switch disabled", desired_state="off")
        if not automatic_cutoff_enabled():
            return RevalidationResult(False, "automatic cutoff feature switch disabled", desired_state="off")
        if not _credit_allowlisted(account.meter_id):
            return RevalidationResult(False, "meter is not in METER_CREDIT_ALLOWED_METER_IDS", desired_state="off")
        if _in_protected_hours():
            return RevalidationResult(False, "automatic cutoff blocked by protected hours", desired_state="off")
    if account.meter.billing_mode != "credit_controlled":
        return RevalidationResult(False, "meter is not credit controlled", desired_state="off")
    if account.meter.is_check_meter:
        return RevalidationResult(False, "audit/check meter cannot be automatically disconnected", desired_state="off")
    if not _active_installation_matches(account):
        return RevalidationResult(False, "installation mismatch", account.current_exposure, None, account.installation_id, account.lease_id, "off")
    if not _lease_is_active(account):
        return RevalidationResult(False, "lease is no longer active", account.current_exposure, None, account.installation_id, account.lease_id, "off")
    if account.enforcement_state in {"data_review_required", "reading_reset_detected", "tariff_missing", "stale_reading", "installation_mismatch"}:
        return RevalidationResult(False, f"account blocked by {account.enforcement_state}", account.current_exposure, None, account.installation_id, account.lease_id, "off")
    if _account_hold_active(account):
        return RevalidationResult(False, "enforcement hold active", account.current_exposure, None, account.installation_id, account.lease_id, "off")
    latest = LiveReading.objects.filter(meter=account.meter).first()
    if not latest or not latest.ts or (timezone.now() - latest.ts).total_seconds() > account.stale_after_minutes * 60:
        return RevalidationResult(False, "latest reading is stale", account.current_exposure, None, account.installation_id, account.lease_id, "off")
    threshold = account.effective_credit_limit * account.cutoff_threshold_percent / 100
    if account.current_exposure < threshold:
        return RevalidationResult(False, "exposure below cutoff threshold", account.current_exposure, threshold, account.installation_id, account.lease_id, "off")
    if account.meter.power_status == "off":
        return RevalidationResult(False, "meter already off", account.current_exposure, threshold, account.installation_id, account.lease_id, "off")
    return RevalidationResult(True, "cutoff remains valid", account.current_exposure, threshold, account.installation_id, account.lease_id, "off")


def still_should_reconnect(command: MeterCommand) -> RevalidationResult:
    account = command.related_credit_account
    if command.meter_id:
        from smart_meter.services.timing_schedule import schedule_allows_power
        schedule_allowed = schedule_allows_power(command.meter)
        if schedule_allowed is False:
            return RevalidationResult(False, "timing schedule blocks reconnection", desired_state="on")
    if not account or not account.is_enabled:
        return RevalidationResult(False, "credit account inactive", desired_state="on")
    if command.source in {"credit_control", "payment"} and not (
        account.automatic_cutoff and account.automatic_restore and not account.manual_only_cutoff
    ):
        return RevalidationResult(False, "combined automatic cutoff and restore is disabled", desired_state="on")
    if command.source in {"credit_control", "payment"}:
        if not automatic_evaluation_enabled():
            return RevalidationResult(False, "automatic credit evaluation feature switch disabled", desired_state="on")
        if not automatic_restore_enabled():
            return RevalidationResult(False, "automatic restore feature switch disabled", desired_state="on")
        if not _credit_allowlisted(account.meter_id):
            return RevalidationResult(False, "meter is not in METER_CREDIT_ALLOWED_METER_IDS", desired_state="on")
    if account.meter.billing_mode != "credit_controlled":
        return RevalidationResult(False, "meter is not credit controlled", desired_state="on")
    if account.meter.is_check_meter:
        return RevalidationResult(False, "audit/check meter cannot be automatically reconnected", desired_state="on")
    if account.enforcement_state in {"data_review_required", "reading_reset_detected", "tariff_missing", "stale_reading", "installation_mismatch"}:
        return RevalidationResult(False, f"account blocked by {account.enforcement_state}", account.current_exposure, None, account.installation_id, account.lease_id, "on")
    if not _active_installation_matches(account) or not _lease_is_active(account):
        return RevalidationResult(False, "installation or lease mismatch", account.current_exposure, None, account.installation_id, account.lease_id, "on")
    cutoff_origin = latest_credit_cutoff_command(account, before=command.created_at)
    if not cutoff_origin:
        return RevalidationResult(False, "no prior credit-control cutoff command", account.current_exposure, None, account.installation_id, account.lease_id, "on")
    threshold = account.effective_credit_limit * account.reconnect_threshold_percent / 100
    if account.current_exposure >= threshold:
        return RevalidationResult(False, "exposure remains above reconnect threshold", account.current_exposure, threshold, account.installation_id, account.lease_id, "on")
    newer_manual_off = MeterCommand.objects.filter(
        meter_id=command.meter_id,
        command_type="relay",
        desired_state="off",
        source="manual",
        created_at__gt=cutoff_origin.created_at,
    ).exclude(
        status__in=("cancelled", "expired", "failed", "error"),
    ).exists()
    if newer_manual_off:
        return RevalidationResult(False, "newer manual OFF command exists", account.current_exposure, threshold, account.installation_id, account.lease_id, "on")
    if account.meter.power_status == "on":
        return RevalidationResult(False, "meter already on", account.current_exposure, threshold, account.installation_id, account.lease_id, "on")
    return RevalidationResult(True, "reconnection remains valid", account.current_exposure, threshold, account.installation_id, account.lease_id, "on")


def revalidate_command(command: MeterCommand) -> RevalidationResult:
    if command.expires_at and command.expires_at <= timezone.now():
        return RevalidationResult(False, "command expired", desired_state=command.desired_state)
    if command.source == "manual":
        return RevalidationResult(True, "manual command", desired_state=command.desired_state)
    if command.source == "schedule":
        from smart_meter.services.timing_schedule import revalidate_schedule_command
        return revalidate_schedule_command(command)
    if command.desired_state == "off":
        return still_should_disconnect(command)
    if command.desired_state == "on":
        return still_should_reconnect(command)
    return RevalidationResult(True, "non-relay command", desired_state=command.desired_state)


def cancel_obsolete_automatic_commands(meter: Meter, desired_state: str, reason: str) -> int:
    opposite = "on" if desired_state == "off" else "off"
    now = timezone.now()
    qs = MeterCommand.objects.filter(
        meter=meter,
        command_type="relay",
        desired_state=opposite,
        source__in=("credit_control", "payment", "system"),
        status__in=ACTIVE,
    )
    return qs.update(status="cancelled", cancelled_at=now, cancelled_reason=reason, error="")


def queue_relay_command(
    meter: Meter,
    desired_state: str,
    *,
    source: str = "manual",
    initiated_by: str = "",
    reason: str = "",
    credit_account: Optional[MeterCreditAccount] = None,
    related_payment=None,
    priority: int = 50,
    timeout: float = 12.0,
    expires_in: timedelta = timedelta(hours=24),
    requires_verification: Optional[bool] = None,
) -> MeterCommand:
    if desired_state not in {"on", "off"}:
        raise ValueError("desired_state must be 'on' or 'off'")
    if meter.is_check_meter and source != "manual":
        raise ValueError("automatic relay commands are not allowed for audit/check meters")

    # Device-specific load-switch mapping confirmed by this TMS meter family:
    # 0x1A opens the relay and 0x1B closes/permits closing it.  The frame's
    # outer DL/T645 control code remains 0x1C and must not be changed.
    by_cmd = RELAY_CLOSE_COMMAND if desired_state == "on" else RELAY_OPEN_COMMAND
    frame_hex = frame_command(meter.meter_number, by_cmd).hex().upper()
    account_key = credit_account.pk if credit_account else "none"
    raw_key = f"relay:{meter.pk}:{desired_state}:{source}:{account_key}"
    key = hashlib.sha256(raw_key.encode()).hexdigest()

    with transaction.atomic():
        if source == "schedule":
            opposite = "on" if desired_state == "off" else "off"
            cancel_sources = ["schedule"] if desired_state == "on" else ["schedule", "credit_control", "payment", "system"]
            MeterCommand.objects.filter(
                meter=meter, command_type="relay", desired_state=opposite,
                source__in=cancel_sources, status__in=ACTIVE,
            ).update(
                status="cancelled", cancelled_at=timezone.now(),
                cancelled_reason=f"superseded by schedule {desired_state}: {reason}"[:255], error="",
            )
        else:
            cancel_obsolete_automatic_commands(meter, desired_state, f"superseded by {desired_state} request: {reason}"[:255])
            if source == "manual":
                opposite = "on" if desired_state == "off" else "off"
                MeterCommand.objects.filter(
                    meter=meter,
                    command_type="relay",
                    desired_state=opposite,
                    source="manual",
                    status__in=ACTIVE - {"acknowledged"},
                ).update(
                    status="cancelled",
                    cancelled_at=timezone.now(),
                    cancelled_reason=f"superseded by manual {desired_state} request: {reason}"[:255],
                    error="",
                )
        reusable_statuses = ACTIVE if source != "manual" else ACTIVE - {"acknowledged"}
        existing = MeterCommand.objects.select_for_update().filter(
            meter=meter,
            command_type="relay",
            desired_state=desired_state,
            source=source,
            related_credit_account=credit_account,
            status__in=reusable_statuses,
        ).order_by("-created_at").first()
        if existing:
            return existing
        cmd = MeterCommand.objects.create(
            meter=meter,
            meter_number=meter.meter_number,
            frame_hex=frame_hex,
            command_type="relay",
            desired_state=desired_state,
            source=source,
            priority=priority,
            timeout=float(timeout),
            idempotency_key=f"{key}:{timezone.now():%Y%m%d%H%M%S%f}:{uuid.uuid4().hex[:8]}",
            initiated_by=initiated_by,
            reason=reason,
            related_credit_account=credit_account,
            related_payment=related_payment,
            status="pending",
            expires_at=timezone.now() + expires_in,
            requires_verification=True if requires_verification is None else requires_verification,
        )
    logger.info("meter_command_queued meter=%s command=%s desired=%s source=%s", meter.pk, cmd.pk, desired_state, source)
    return cmd


def mark_command_cancelled(command: MeterCommand, reason: str) -> None:
    MeterCommand.objects.filter(pk=command.pk).update(
        status="cancelled", cancelled_at=timezone.now(), cancelled_reason=reason[:255], error=""
    )
