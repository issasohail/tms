"""Database-controlled guards for DL/T645 prepaid operations."""
from __future__ import annotations

from django.db import OperationalError, ProgrammingError
from django.utils import timezone

from smart_meter.models import (
    LiveReading,
    Meter,
    MeterSettings,
    MeterPrepaidPilot,
    MeterPrepaidParameterRead,
    MeterPrepaidWriteAttempt,
)


class PrepaidProtocolSafetyError(RuntimeError):
    pass


def _database_flag(field_name):
    try:
        value = (
            MeterSettings.objects.filter(pk=1)
            .values_list(field_name, flat=True)
            .first()
        )
    except (OperationalError, ProgrammingError):
        # Fail closed during startup or a partially applied deployment.
        return False
    return bool(value)


def prepaid_reads_enabled():
    return _database_flag("prepaid_reads_enabled")


def prepaid_writes_enabled():
    return _database_flag("prepaid_writes_enabled")


def prepaid_payment_topups_enabled():
    return _database_flag("prepaid_payment_topups_enabled")


def prepaid_allowlisted(meter: Meter) -> bool:
    return bool(meter.is_active and meter.billing_mode == "prepaid_pilot")


def validate_prepaid_read(meter: Meter):
    if meter.billing_mode != "prepaid_pilot":
        raise PrepaidProtocolSafetyError("Meter billing mode is not prepaid_pilot")
    if not prepaid_reads_enabled():
        raise PrepaidProtocolSafetyError("Prepaid reads are disabled in Meter Settings")
    if not prepaid_allowlisted(meter):
        raise PrepaidProtocolSafetyError("Meter is not enabled for prepaid operations")


def read_supported_prepaid_snapshot(meter_id: int):
    meter = Meter.objects.get(pk=meter_id)
    validate_prepaid_read(meter)
    pilot, _ = MeterPrepaidPilot.objects.get_or_create(meter=meter)
    live = LiveReading.objects.filter(meter=meter).first()
    if not live:
        raise PrepaidProtocolSafetyError("No live DL/T645 reading is available for this meter")

    # 028011FF is already used by the production reader and parser.  We record only
    # fields actually parsed from that existing frame; all other prepaid DIs remain
    # explicitly unsupported until manufacturer documentation is supplied.
    supported = [
        ("balance", getattr(live, "balance", None), "currency"),
        ("overdraft", getattr(live, "overdraft", None), "currency"),
        ("authoritative_relay_status", meter.relay_state, "state"),
    ]
    rows = []
    for parameter, value, unit in supported:
        rows.append(MeterPrepaidParameterRead.objects.create(
            pilot=pilot, di="028011FF", parameter=parameter,
            parsed_value="" if value is None else str(value), unit=unit,
            parse_status="parsed" if value is not None else "unsupported",
            metadata={"source": "existing live-reading parser", "authoritative_relay_state": parameter == "authoritative_relay_status"},
        ))
    for parameter in (
        "remaining_prepaid_energy", "current_tariff", "step_tariff_parameters",
        "low_credit_alarm", "overdraft_limit", "purchase_count", "latest_recharge",
        "prepaid_status_flags", "meter_datetime",
    ):
        rows.append(MeterPrepaidParameterRead.objects.create(
            pilot=pilot, di="", parameter=parameter, parsed_value="", unit="",
            parse_status="unsupported",
            metadata={"reason": "exact manufacturer DI/read semantics not yet wired into the production reader"},
        ))
    pilot.status = "read_only"
    pilot.save(update_fields=["status", "updated_at"])
    return rows


def guarded_parameter_write(*, meter_id, parameter, value, user, reason, confirm_meter_number):
    meter = Meter.objects.get(pk=meter_id)
    if meter.meter_number != confirm_meter_number:
        raise PrepaidProtocolSafetyError("Meter-number confirmation does not match")
    if meter.billing_mode != "prepaid_pilot" or not prepaid_allowlisted(meter):
        raise PrepaidProtocolSafetyError("Meter is not an enabled/allowlisted prepaid pilot")
    if not prepaid_writes_enabled():
        raise PrepaidProtocolSafetyError("Prepaid writes are disabled in Meter Settings")
    if not user or not user.has_perm("smart_meter.write_prepaid_parameters"):
        raise PrepaidProtocolSafetyError("User lacks prepaid parameter write permission")
    if not reason:
        raise PrepaidProtocolSafetyError("Audit reason is required")
    pilot, _ = MeterPrepaidPilot.objects.get_or_create(meter=meter)
    attempt = MeterPrepaidWriteAttempt.objects.create(
        pilot=pilot, parameter=parameter, requested_value=str(value), status="failed",
        reason=reason, user=user,
    )
    raise PrepaidProtocolSafetyError(
        "Prepaid write blocked: the manufacturer archive required to identify the exact read-before-write/read-back DI and acknowledgement semantics was not included in this patch input. No bytes were sent."
    )


def prepaid_recharge_disabled_reason():
    return (
        "Automatic prepaid recharge remains disabled. One recharge frame and its C=83 / "
        "DI=070102FF acknowledgement have been physically proven, but any uncertain outcome "
        "must be reconciled from 028011FF and must never be retried. Refund semantics and "
        "initial prepaid activation/configuration remain unproven."
    )
