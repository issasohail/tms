"""Guarded DL/T645 prepaid pilot facade.

Only protocol operations already evidenced by this repository are exposed.  Recharge
and verified parameter writes stay disabled because the manufacturer protocol archive
referenced by the implementation prompt was not included in the supplied snapshot.
"""
from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from smart_meter.models import (
    LiveReading,
    Meter,
    MeterPrepaidPilot,
    MeterPrepaidParameterRead,
    MeterPrepaidWriteAttempt,
)


class PrepaidProtocolSafetyError(RuntimeError):
    pass


def _enabled(name):
    value = getattr(settings, name, False)
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def prepaid_allowlisted(meter: Meter) -> bool:
    return meter.pk in set(getattr(settings, "METER_PREPAID_ALLOWED_METER_IDS", ()) or ())


def validate_prepaid_read(meter: Meter):
    if meter.billing_mode != "prepaid_pilot":
        raise PrepaidProtocolSafetyError("Meter billing mode is not prepaid_pilot")
    if not _enabled("METER_ENABLE_PREPAID_READS"):
        raise PrepaidProtocolSafetyError("METER_ENABLE_PREPAID_READS is disabled")
    if not prepaid_allowlisted(meter):
        raise PrepaidProtocolSafetyError("Meter is not in METER_PREPAID_ALLOWED_METER_IDS")


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
    if not _enabled("METER_ENABLE_PREPAID_WRITES"):
        raise PrepaidProtocolSafetyError("METER_ENABLE_PREPAID_WRITES is disabled")
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
        "DL/T645 recharge is disabled: the supplied project snapshot does not contain the manufacturer documentation "
        "that defines the exact recharge DI/control code, purchase sequence, authentication/encryption and replay semantics."
    )
