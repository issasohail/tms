"""Durable, no-blind-retry lifecycle for prepaid money commands.

This module prepares database records and reconciles replies/readings. It never opens
a socket. The listener remains the only transport owner.
"""
from __future__ import annotations

import time
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from smart_meter.dlt645 import build_charge_frame, verify_checksum
from smart_meter.models import (
    LiveReading,
    Meter,
    MeterCommand,
    MeterPrepaidPilot,
    MeterPrepaidRecharge,
)


MONEY_COMMAND_TYPES = frozenset({"prepaid_recharge", "prepaid_refund"})
MONEY_EXPECTED_DI = {
    "prepaid_recharge": "070102FF",
    "prepaid_refund": "070108FF",
}
CONSUMED_MANUFACTURER_ORDERS = frozenset({
    "1240826202124140",
    "1240826202124141",
})
BALANCE_RECONCILIATION_TOLERANCE = Decimal("0.005")
UNCERTAIN_OPERATOR_MESSAGE = (
    "Prepaid money transmission outcome is uncertain. Do not retry. Verify meter balance."
)


def is_prepaid_money_command(command_or_type) -> bool:
    command_type = (
        command_or_type
        if isinstance(command_or_type, str)
        else getattr(command_or_type, "command_type", "")
    )
    return command_type in MONEY_COMMAND_TYPES


def _transaction_for_command(command: MeterCommand):
    prefix = "prepaid-order:"
    key = command.idempotency_key or ""
    if not key.startswith(prefix):
        return None
    return MeterPrepaidRecharge.objects.filter(transaction_id=key[len(prefix):]).first()


def decode_manufacturer_charge_frame(frame_hex: str) -> dict:
    """Validate and decode the exact manufacturer charge frame fields."""
    try:
        frame = bytes.fromhex((frame_hex or "").strip())
    except ValueError as exc:
        raise ValueError("prepaid money frame must be valid hexadecimal") from exc
    if not frame.startswith(b"\xFE" * 4):
        raise ValueError("prepaid money frame must include four FE wake-up bytes")
    inner = frame[4:]
    if len(inner) < 12 or inner[0] != 0x68 or inner[7] != 0x68:
        raise ValueError("invalid prepaid money DL/T645 header")
    if inner[8] != 0x03 or inner[9] != 0x22 or len(inner) != 12 + 0x22:
        raise ValueError("prepaid money frame must use control 03 and DATA length 22")
    if verify_checksum(inner, 0) != (True, "incl_1st68"):
        raise ValueError("prepaid money frame must use incl_1st68 checksum")

    data = inner[10:44]
    di = bytes(((value - 0x33) & 0xFF) for value in data[0:4])[::-1].hex().upper()
    if di not in MONEY_EXPECTED_DI.values():
        raise ValueError("prepaid money DI must be 070102FF or 070108FF")
    meter_number = inner[1:7][::-1].hex().upper()
    cents = int.from_bytes(
        bytes(((value - 0x33) & 0xFF) for value in data[8:12]),
        byteorder="little",
        signed=False,
    )
    order_number = bytes(
        ((value - 0x33) & 0xFF) for value in data[12:20]
    )[::-1].hex().upper()
    operation = "recharge" if di == "070102FF" else "refund"
    amount = Decimal(cents) / Decimal(100)
    canonical = build_charge_frame(meter_number, operation, order_number, amount)
    if canonical != frame:
        raise ValueError("prepaid money frame is not manufacturer-canonical")
    return {
        "meter_number": meter_number,
        "di": di,
        "operation": operation,
        "order_number": order_number,
        "amount": amount,
    }


def generate_prepaid_order_number() -> str:
    """Return a 16-hex candidate not already persisted or explicitly consumed.

    Final uniqueness is reserved transactionally by ``reserve_prepaid_money_command``;
    callers must not treat this candidate alone as a reservation.
    """
    for _ in range(100):
        candidate = f"{time.time_ns() & 0xFFFFFFFFFFFFFFFF:016X}"
        if candidate in CONSUMED_MANUFACTURER_ORDERS:
            continue
        if MeterPrepaidRecharge.objects.filter(transaction_id=candidate).exists():
            continue
        if MeterCommand.objects.filter(
            idempotency_key=f"prepaid-order:{candidate}"
        ).exists():
            continue
        return candidate
    raise RuntimeError("unable to allocate a unique prepaid order number")


def reserve_prepaid_money_command(
    *,
    meter: Meter,
    frame_hex: str,
    command_type: str,
    expect_di: str,
    timeout: float,
    initiated_by: str = "",
    reason: str = "",
    source: str = "prepaid",
) -> MeterCommand:
    """Persist one monetary order and exactly one command queue entry."""
    if command_type not in MONEY_COMMAND_TYPES:
        raise ValueError("command_type must be prepaid_recharge or prepaid_refund")
    decoded = decode_manufacturer_charge_frame(frame_hex)
    required_di = MONEY_EXPECTED_DI[command_type]
    if decoded["di"] != required_di or (expect_di or "").upper() != required_di:
        raise ValueError(f"{command_type} must use expect_di {required_di}")
    if decoded["meter_number"] != meter.meter_number.upper():
        raise ValueError("prepaid frame meter does not match the selected meter")

    order_number = decoded["order_number"]
    if order_number in CONSUMED_MANUFACTURER_ORDERS:
        raise ValueError(f"prepaid order number {order_number} has already been consumed")
    try:
        pilot = meter.prepaid_pilot
    except MeterPrepaidPilot.DoesNotExist as exc:
        raise ValueError("meter must have an existing prepaid pilot record") from exc
    live = LiveReading.objects.filter(meter=meter).first()
    if live is None or live.balance is None:
        raise ValueError("an authoritative before-balance is required")

    try:
        with transaction.atomic():
            if MeterPrepaidRecharge.objects.filter(transaction_id=order_number).exists():
                raise ValueError(f"prepaid order number {order_number} already exists")
            if MeterPrepaidRecharge.objects.select_for_update().filter(
                pilot=pilot, status__in=("pending", "uncertain")
            ).exists():
                raise ValueError(
                    "this meter already has an unreconciled prepaid money transaction"
                )
            prepaid = MeterPrepaidRecharge.objects.create(
                pilot=pilot,
                transaction_id=order_number,
                manufacturer_sequence=order_number,
                amount=decoded["amount"],
                before_balance=live.balance,
                status="pending",
                reconciliation_note=(
                    f"{decoded['operation']} prepared; awaiting C=83 / DI={required_di} "
                    "meter acknowledgement"
                ),
                raw_command=frame_hex.strip().upper(),
            )
            command = MeterCommand.objects.create(
                meter=meter,
                meter_number=meter.meter_number,
                frame_hex=frame_hex.strip().upper(),
                expect_di=required_di,
                timeout=float(timeout),
                initiated_by=initiated_by or "",
                reason=reason or "",
                source=source,
                command_type=command_type,
                status="pending",
                max_attempts=1,
                requires_verification=True,
                idempotency_key=f"prepaid-order:{order_number}",
                expires_at=timezone.now() + timedelta(hours=24),
            )
    except IntegrityError as exc:
        raise ValueError(f"prepaid order number {order_number} already exists") from exc
    return command


def queue_prepaid_money_transaction(
    *, meter: Meter, operation: str, amount, order_number: str | None = None,
    timeout: float = 12.0, initiated_by: str = "", reason: str = "",
) -> tuple[MeterPrepaidRecharge, MeterCommand]:
    """Build and durably reserve a recharge/refund without performing transport."""
    operation_key = (operation or "").strip().lower()
    command_type = {
        "recharge": "prepaid_recharge",
        "refund": "prepaid_refund",
    }.get(operation_key)
    if command_type is None:
        raise ValueError("operation must be recharge or refund")
    order = order_number or generate_prepaid_order_number()
    frame = build_charge_frame(meter.meter_number, operation_key, order, amount)
    command = reserve_prepaid_money_command(
        meter=meter,
        frame_hex=frame.hex().upper(),
        command_type=command_type,
        expect_di=MONEY_EXPECTED_DI[command_type],
        timeout=timeout,
        initiated_by=initiated_by,
        reason=reason,
    )
    return _transaction_for_command(command), command


def mark_prepaid_uncertain(command: MeterCommand, detail: str) -> None:
    """Stop a monetary command after its first ambiguous enqueue outcome."""
    message = f"{UNCERTAIN_OPERATOR_MESSAGE} Detail: {detail}"
    with transaction.atomic():
        locked = MeterCommand.objects.select_for_update().get(pk=command.pk)
        if locked.status in {"acknowledged", "verified", "cancelled"}:
            return
        locked.status = "sent"
        locked.error = message
        locked.next_attempt_at = None
        locked.max_attempts = 1
        locked.save(
            update_fields=["status", "error", "next_attempt_at", "max_attempts", "updated_at"]
        )
        prepaid = _transaction_for_command(locked)
        if prepaid is not None:
            prepaid.status = "uncertain"
            prepaid.reconciliation_note = message
            prepaid.save(update_fields=["status", "reconciliation_note", "updated_at"])


def mark_prepaid_acknowledged(command: MeterCommand, reply_hex: str) -> None:
    prepaid = _transaction_for_command(command)
    if prepaid is None:
        return
    prepaid.raw_ack = reply_hex
    prepaid.status = "pending"
    if command.command_type == "prepaid_refund":
        prepaid.reconciliation_note = (
            "Meter acknowledged refund with C=83 / DI=070108FF; awaiting "
            "authoritative 028011FF balance reconciliation"
        )
    else:
        prepaid.reconciliation_note = (
            "Meter acknowledged recharge with C=83 / DI=070102FF; awaiting "
            "authoritative 028011FF balance reconciliation"
        )
    prepaid.save(
        update_fields=["raw_ack", "status", "reconciliation_note", "updated_at"]
    )


def mark_prepaid_reconciliation_uncertain(
    command: MeterCommand, detail: str
) -> None:
    """Record a post-ACK verification failure without permitting a retry."""
    message = f"Acknowledged but balance reconciliation is uncertain: {detail}. Do not retry."
    with transaction.atomic():
        locked = MeterCommand.objects.select_for_update().get(pk=command.pk)
        if locked.status == "verified":
            return
        locked.status = "acknowledged"
        locked.error = message
        locked.next_attempt_at = None
        locked.max_attempts = 1
        locked.save(
            update_fields=["status", "error", "next_attempt_at", "max_attempts", "updated_at"]
        )
        prepaid = _transaction_for_command(locked)
        if prepaid is not None and prepaid.status != "verified":
            prepaid.status = "uncertain"
            prepaid.reconciliation_note = message
            prepaid.save(update_fields=["status", "reconciliation_note", "updated_at"])


def mark_prepaid_definitive_failure(
    command: MeterCommand, detail: str, reply_hex: str = ""
) -> None:
    """Mirror a failure proven to have occurred before monetary application."""
    prepaid = _transaction_for_command(command)
    if prepaid is None:
        return
    prepaid.status = "failed"
    prepaid.reconciliation_note = f"Definitive failure before application: {detail}"
    if reply_hex:
        prepaid.raw_ack = reply_hex
    prepaid.save(
        update_fields=["status", "reconciliation_note", "raw_ack", "updated_at"]
    )


def acknowledge_late_prepaid_reply(
    meter_number: str, di: str, control_code: int, frame: bytes
) -> MeterCommand | None:
    """Attach a late C=83 money reply after the synchronous waiter has expired."""
    if control_code != 0x83 or di not in MONEY_EXPECTED_DI.values():
        return None
    command_type = "prepaid_recharge" if di == "070102FF" else "prepaid_refund"
    with transaction.atomic():
        command = (
            MeterCommand.objects.select_for_update()
            .filter(
                meter_number=meter_number,
                command_type=command_type,
                expect_di=di,
                status__in=("claimed", "sent"),
                attempt_count=1,
                raw_ack_hex="",
            )
            .order_by("-last_attempt_at", "-created_at")
            .first()
        )
        if command is None:
            return None
        reply_hex = frame.hex().upper()
        command.status = "acknowledged"
        command.reply_hex = reply_hex
        command.raw_ack_hex = reply_hex
        command.acknowledged_at = timezone.now()
        command.error = ""
        command.next_attempt_at = None
        command.save(
            update_fields=[
                "status", "reply_hex", "raw_ack_hex", "acknowledged_at",
                "error", "next_attempt_at", "updated_at",
            ]
        )
        mark_prepaid_acknowledged(command, reply_hex)
        return command


def reconcile_prepaid_balance(meter: Meter, balance) -> list[MeterPrepaidRecharge]:
    """Reconcile acknowledged money commands against an authoritative balance."""
    if balance is None:
        return []
    actual = Decimal(str(balance)).quantize(Decimal("0.01"))
    reconciled = []
    try:
        pilot = meter.prepaid_pilot
    except MeterPrepaidPilot.DoesNotExist:
        return reconciled
    candidates = pilot.recharges.filter(
        status__in=("pending", "uncertain")
    ).exclude(raw_ack="").order_by("created_at")
    for candidate in candidates:
        with transaction.atomic():
            prepaid = MeterPrepaidRecharge.objects.select_for_update().get(
                pk=candidate.pk
            )
            command = MeterCommand.objects.select_for_update().filter(
                idempotency_key=f"prepaid-order:{prepaid.transaction_id}",
                status="acknowledged",
            ).first()
            if command is None or prepaid.before_balance is None:
                continue
            prepaid.after_balance = actual
            operation = "refund" if command.command_type == "prepaid_refund" else "recharge"
            direction = Decimal("-1") if operation == "refund" else Decimal("1")
            expected = (prepaid.before_balance + direction * prepaid.amount).quantize(
                Decimal("0.01")
            )
            difference = abs(actual - expected)
            if difference <= BALANCE_RECONCILIATION_TOLERANCE:
                prepaid.status = "verified"
                prepaid.reconciliation_note = (
                    f"Reconciled from {prepaid.before_balance:.2f} to {actual:.2f} "
                    f"after {operation} {prepaid.amount:.2f}; expected {expected:.2f}"
                )
                command.status = "verified"
                command.verified_at = timezone.now()
                command.error = ""
                command.save(
                    update_fields=["status", "verified_at", "error", "updated_at"]
                )
            else:
                message = (
                    f"Acknowledged {operation} expected balance {expected:.2f}, but "
                    f"authoritative 028011FF reported {actual:.2f}; do not retry"
                )
                prepaid.status = "uncertain"
                prepaid.reconciliation_note = message
                command.error = message
                command.save(update_fields=["error", "updated_at"])
            prepaid.save(
                update_fields=[
                    "after_balance", "status", "reconciliation_note", "updated_at"
                ]
            )
            reconciled.append(prepaid)
    return reconciled
