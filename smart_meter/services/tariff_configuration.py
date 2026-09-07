"""Audited tariff reads and one-shot read/modify/write/read-back workflows."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from smart_meter.models import (
    MeterCommand,
    MeterTariffAudit,
    MeterTariffConfiguration,
)
from smart_meter.services.tariff_protocol import (
    TariffProtocolError,
    build_tariff_read_frame,
    build_tariff_write_frame,
    classify_write_reply,
    decode_payload,
    mutate_payload,
    parse_read_reply,
    target_byte_indexes,
)
from smart_meter.utils.db_send import send_via_db


def _json_values(values):
    return {
        "active_rate_count": values.get("active_rate_count"),
        "prices": [str(value) for value in values.get("prices", [])],
    }


def _configuration(meter):
    return MeterTariffConfiguration.objects.get_or_create(meter=meter)[0]


def _cancel_deferred(command_id):
    if command_id:
        MeterCommand.objects.filter(
            pk=command_id, status__in=("pending", "waiting_online", "retry")
        ).update(
            status="cancelled",
            cancelled_at=timezone.now(),
            cancelled_reason="Tariff operations require a fresh foreground read; deferred send blocked",
        )


def _is_offline_error(exc):
    message = str(exc).lower()
    return "offline" in message or "not connected" in message or "queued" in message


def _read_failure(message, command_id):
    error = TariffProtocolError(message)
    error.command_id = command_id
    return error


def _send_read(meter, user):
    frame = build_tariff_read_frame(meter.meter_number, meter.tariff_capability)
    result = send_via_db(
        meter_number=meter.meter_number,
        frame_hex=frame.hex().upper(),
        expect_di=("070115FF" if meter.tariff_capability == "single_rate" else "070104FF"),
        timeout=12.0,
        initiated_by=getattr(user, "get_username", lambda: "")(),
        reason="Tariff configuration live read",
        command_type="tariff_read",
        source="tariff",
        max_attempts=1,
    )
    command_id = result.get("command_id")
    if result.get("status") in {"waiting_online", "pending"} or "offline" in (result.get("error") or "").lower():
        _cancel_deferred(command_id)
        raise _read_failure("Offline / queued / not verified", command_id)
    if not result.get("ok") or not result.get("reply"):
        raise _read_failure(result.get("error") or "Live tariff read failed", command_id)
    try:
        payload, raw = parse_read_reply(
            result["reply"], meter_number=meter.meter_number, capability=meter.tariff_capability
        )
    except TariffProtocolError as exc:
        exc.command_id = command_id
        raise
    return payload, raw, command_id


def read_current_configuration(*, meter, user):
    audit = MeterTariffAudit.objects.create(
        meter=meter,
        initiating_user=user if getattr(user, "is_authenticated", False) else None,
        capability=meter.tariff_capability,
        configuration_type="read",
        status="failed",
    )
    configuration = _configuration(meter)
    try:
        payload, raw, command_id = _send_read(meter, user)
        values = decode_payload(payload, meter.tariff_capability)
        audit.values_after = _json_values(values)
        audit.raw_read_back_frame = raw
        audit.command_ids = [command_id] if command_id else []
        audit.status = "read"
        audit.completed_at = timezone.now()
        audit.save()
        configuration.last_configuration_read_at = audit.completed_at
        configuration.last_status = "read"
        configuration.save(update_fields=["last_configuration_read_at", "last_status", "updated_at"])
    except Exception as exc:
        command_id = getattr(exc, "command_id", None)
        if command_id:
            audit.command_ids = [command_id]
        audit.status = "offline_queued" if _is_offline_error(exc) else "failed"
        audit.error = str(exc)
        audit.completed_at = timezone.now()
        audit.save(update_fields=["status", "error", "completed_at"])
    return audit


def _save_verified(configuration, *, mode, labels, values, audit):
    configuration.mode = mode
    configuration.active_rate_count = values["active_rate_count"]
    for index, value in enumerate(values["prices"], 1):
        setattr(configuration, f"rate_{index}_price", value)
    for index, label in enumerate(labels, 1):
        setattr(configuration, f"rate_{index}_label", label)
    configuration.last_configuration_read_at = audit.completed_at
    configuration.last_verified_at = audit.completed_at
    configuration.last_status = "verified"
    configuration.save()


def configure_prices(*, meter, user, mode, prices, labels=None, active_rate_count=1):
    if meter.tariff_capability == "unknown":
        raise TariffProtocolError("Tariff capability must be confirmed before writing")
    labels = list(labels or ("Valley", "Flat", "Peak", "Shoulder"))
    prices = [Decimal(str(value)).quantize(Decimal("0.0001")) for value in prices]
    flat = mode == "flat"
    requested = {
        "mode": mode,
        "active_rate_count": int(active_rate_count),
        "labels": labels,
        "prices": [str(value) for value in prices],
    }
    audit = MeterTariffAudit.objects.create(
        meter=meter,
        initiating_user=user if getattr(user, "is_authenticated", False) else None,
        capability=meter.tariff_capability,
        configuration_type=mode,
        requested_values=requested,
        status="failed",
    )
    configuration = _configuration(meter)
    command_ids = []
    try:
        before_payload, before_raw, read_id = _send_read(meter, user)
        if read_id:
            command_ids.append(read_id)
        before = decode_payload(before_payload, meter.tariff_capability)
        audit.values_before = _json_values(before)
        audit.raw_read_before_frame = before_raw
        modified = mutate_payload(
            before_payload,
            meter.tariff_capability,
            prices=prices,
            active_rate_count=active_rate_count,
            flat=flat,
        )
        if modified == before_payload:
            audit.status = "no_change"
            audit.values_after = audit.values_before
            audit.command_ids = command_ids
            audit.completed_at = timezone.now()
            audit.save()
            _save_verified(
                configuration, mode=mode, labels=labels,
                values=before, audit=audit,
            )
            configuration.last_configuration_read_at = audit.completed_at
            configuration.last_status = "no_change"
            configuration.save(update_fields=["last_configuration_read_at", "last_status", "updated_at"])
            return audit

        write_frame = build_tariff_write_frame(
            meter.meter_number, meter.tariff_capability, modified
        )
        audit.raw_write_frame = write_frame.hex().upper()
        audit.status = "sent_pending_verification"
        audit.save(update_fields=["values_before", "raw_read_before_frame", "raw_write_frame", "status"])
        write_result = send_via_db(
            meter_number=meter.meter_number,
            frame_hex=audit.raw_write_frame,
            timeout=12.0,
            initiated_by=getattr(user, "get_username", lambda: "")(),
            reason="One-shot tariff configuration write",
            command_type="tariff_write",
            source="tariff",
            max_attempts=1,
        )
        write_id = write_result.get("command_id")
        if write_id:
            command_ids.append(write_id)
        if write_result.get("status") in {"waiting_online", "pending"}:
            _cancel_deferred(write_id)
            raise TariffProtocolError("Offline / queued / not verified")
        audit.raw_write_reply_frame = write_result.get("reply") or ""
        ack_state = classify_write_reply(audit.raw_write_reply_frame)
        if ack_state == "invalid":
            raise TariffProtocolError("Meter returned an invalid tariff write acknowledgement")

        # Always read back, including after a negative acknowledgement: some firmware
        # has been observed partially applying a rejected parameter write.
        after_payload, after_raw, verify_id = _send_read(meter, user)
        if verify_id:
            command_ids.append(verify_id)
        after = decode_payload(after_payload, meter.tariff_capability)
        audit.values_after = _json_values(after)
        audit.raw_read_back_frame = after_raw
        target_indexes = target_byte_indexes(
            meter.tariff_capability, int(active_rate_count), flat=flat
        )
        target_ok = all(after_payload[index] == modified[index] for index in target_indexes)
        unrelated_ok = all(
            after_payload[index] == before_payload[index]
            for index in range(len(before_payload)) if index not in target_indexes
        )
        audit.completed_at = timezone.now()
        audit.command_ids = command_ids
        if not target_ok or not unrelated_ok:
            audit.status = "unsafe_readback" if not unrelated_ok else "failed"
            audit.error = (
                "Read-back changed bytes outside the requested tariff fields"
                if not unrelated_ok else "Read-back did not match the requested tariff values"
            )
            configuration.last_status = audit.status
            configuration.last_configuration_read_at = audit.completed_at
            configuration.save(update_fields=["last_status", "last_configuration_read_at", "updated_at"])
        else:
            audit.status = "verified"
            verified = decode_payload(modified, meter.tariff_capability)
            _save_verified(
                configuration, mode=mode, labels=labels,
                values=verified, audit=audit,
            )
        audit.save()
    except Exception as exc:
        failed_command_id = getattr(exc, "command_id", None)
        if failed_command_id and failed_command_id not in command_ids:
            command_ids.append(failed_command_id)
        audit.status = "offline_queued" if _is_offline_error(exc) else (
            "sent_pending_verification" if audit.raw_write_frame else "failed"
        )
        audit.error = str(exc)
        audit.command_ids = command_ids
        audit.completed_at = timezone.now()
        audit.save()
        configuration.last_status = audit.status
        configuration.save(update_fields=["last_status", "updated_at"])
    return audit


@transaction.atomic
def save_schedule_draft(*, meter, user, rows, labels, active_rate_count):
    configuration = _configuration(meter)
    configuration.mode = "time_of_use"
    configuration.active_rate_count = active_rate_count
    configuration.schedule_draft = rows
    for index, label in enumerate(labels, 1):
        setattr(configuration, f"rate_{index}_label", label)
    configuration.last_status = "draft"
    configuration.save()
    return MeterTariffAudit.objects.create(
        meter=meter,
        initiating_user=user if getattr(user, "is_authenticated", False) else None,
        capability=meter.tariff_capability,
        configuration_type="schedule_draft",
        requested_values={"rows": rows, "labels": labels, "active_rate_count": active_rate_count},
        status="draft",
        completed_at=timezone.now(),
    )
