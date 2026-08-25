import time
from datetime import timedelta
from typing import Optional, Dict, Any
from django.db import transaction
from django.utils import timezone
from smart_meter.models import Meter, MeterCommand


def send_via_db(
    *,
    meter_number: str,
    frame_hex: str,
    timeout: float = 12.0,
    expect_di: Optional[str] = None,
    initiated_by: str = "",
    reason: str = "",
    desired_state: str = "",
    command_type: str = "other",
    source: str = "manual",
) -> Dict[str, Any]:
    """Compatibility API backed by the durable MeterCommand queue.

    Existing manual callers remain synchronous. Offline meters are retained as
    ``waiting_online`` instead of being terminally failed.
    """
    meter = Meter.objects.filter(meter_number=meter_number).first()
    from smart_meter.services.prepaid_money import (
        MONEY_COMMAND_TYPES,
        MONEY_EXPECTED_DI,
        UNCERTAIN_OPERATOR_MESSAGE,
        reserve_prepaid_money_command,
    )

    is_money = command_type in MONEY_COMMAND_TYPES
    if is_money:
        if meter is None:
            raise ValueError("prepaid money command requires an existing meter")
        required_di = MONEY_EXPECTED_DI[command_type]
        cmd = reserve_prepaid_money_command(
            meter=meter,
            frame_hex=frame_hex,
            command_type=command_type,
            expect_di=(expect_di or required_di).upper(),
            timeout=float(timeout),
            initiated_by=initiated_by,
            reason=reason,
            source=source,
        )
    elif meter and command_type == "relay" and desired_state in {"on", "off"}:
        from smart_meter.services.command_lifecycle import queue_relay_command
        cmd = queue_relay_command(
            meter, desired_state, source=source, initiated_by=initiated_by or "",
            reason=reason or "", timeout=float(timeout), requires_verification=True,
        )
        if expect_di and not cmd.expect_di:
            MeterCommand.objects.filter(pk=cmd.pk).update(expect_di=(expect_di or "").upper())
    else:
        with transaction.atomic():
            cmd = MeterCommand.objects.create(
                meter=meter,
                meter_number=meter_number,
                frame_hex=frame_hex.strip().upper(),
                expect_di=(expect_di or "").upper(),
                timeout=float(timeout),
                initiated_by=initiated_by or "",
                reason=reason or "",
                source=source,
                command_type=command_type,
                desired_state=desired_state,
                status="pending",
                expires_at=timezone.now() + timedelta(hours=24),
            )

    deadline = time.time() + max(float(timeout) + 2.0, 5.0)
    done = {"verified", "acknowledged", "ok", "failed", "expired", "cancelled", "error", "timeout"}
    if is_money:
        done.add("sent")
    while time.time() < deadline:
        time.sleep(0.2)
        c = MeterCommand.objects.only(
            "status", "reply_hex", "raw_ack_hex", "error", "command_type"
        ).get(pk=cmd.pk)
        if c.status in done:
            if is_money and c.status == "sent":
                return {
                    "ok": False,
                    "retryable": False,
                    "verification_required": True,
                    "meter_acknowledged": False,
                    "reconciled": False,
                    "reply": c.raw_ack_hex or c.reply_hex or "",
                    "error": c.error or UNCERTAIN_OPERATOR_MESSAGE,
                    "status": c.status,
                    "command_id": cmd.pk,
                }
            if is_money and c.status == "acknowledged":
                return {
                    "ok": False,
                    "retryable": False,
                    "verification_required": True,
                    "meter_acknowledged": True,
                    "reconciled": False,
                    "reply": c.raw_ack_hex or c.reply_hex or "",
                    "error": (
                        "Meter acknowledged the prepaid transaction; awaiting "
                        "authoritative 028011FF balance reconciliation. Do not retry."
                    ),
                    "status": c.status,
                    "command_id": cmd.pk,
                }
            ok = c.status in {"verified", "ok"} or (
                c.command_type != "relay" and c.status == "acknowledged"
            )
            result = {
                "ok": ok,
                "reply": c.raw_ack_hex or c.reply_hex or "",
                "error": "" if ok else (
                    c.error
                    or (
                        "relay command acknowledged but physical state was not verified"
                        if c.status == "acknowledged"
                        else c.status
                    )
                ),
                "status": c.status,
                "command_id": cmd.pk,
            }
            if is_money:
                result.update(
                    retryable=False,
                    verification_required=not ok,
                    meter_acknowledged=bool(c.raw_ack_hex),
                    reconciled=c.status == "verified",
                )
            return result
        if c.status == "waiting_online":
            result = {
                "ok": False,
                "queued": True,
                "error": "meter offline; command queued until it reconnects",
                "status": c.status,
                "command_id": cmd.pk,
            }
            if is_money:
                result.update(retryable=False, verification_required=False)
            return result
    c = MeterCommand.objects.only("status", "error").get(pk=cmd.pk)
    result = {"ok": False, "error": c.error or "timeout", "status": c.status, "command_id": cmd.pk}
    if is_money:
        result.update(
            retryable=False,
            verification_required=True,
            error=c.error or UNCERTAIN_OPERATOR_MESSAGE,
        )
    return result
