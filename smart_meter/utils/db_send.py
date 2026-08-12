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
    if meter and command_type == "relay" and desired_state in {"on", "off"}:
        from smart_meter.services.command_lifecycle import queue_relay_command
        cmd = queue_relay_command(
            meter, desired_state, source=source, initiated_by=initiated_by or "",
            reason=reason or "", requires_verification=False,
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
    while time.time() < deadline:
        time.sleep(0.2)
        c = MeterCommand.objects.only("status", "reply_hex", "raw_ack_hex", "error").get(pk=cmd.pk)
        if c.status in done:
            ok = c.status in {"verified", "acknowledged", "ok"}
            return {
                "ok": ok,
                "reply": c.raw_ack_hex or c.reply_hex or "",
                "error": "" if ok else (c.error or c.status),
                "status": c.status,
                "command_id": cmd.pk,
            }
        if c.status == "waiting_online":
            return {
                "ok": False,
                "queued": True,
                "error": "meter offline; command queued until it reconnects",
                "status": c.status,
                "command_id": cmd.pk,
            }
    c = MeterCommand.objects.only("status", "error").get(pk=cmd.pk)
    return {"ok": False, "error": c.error or "timeout", "status": c.status, "command_id": cmd.pk}
