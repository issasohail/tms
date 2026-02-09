# smart_meter/utils/db_send.py
import time
from typing import Optional, Dict, Any
from django.db import transaction
from smart_meter.models import Meter, MeterCommand


def send_via_db(
    *,
    meter_number: str,
    frame_hex: str,
    timeout: float = 12.0,
    expect_di: Optional[str] = None,
    initiated_by: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    """
    Create a MeterCommand row and wait synchronously for the listener poller
    to execute it. Returns {"ok": True, "reply": "..."} or {"ok": False, "error": "..."}.
    """
    with transaction.atomic():
        meter = Meter.objects.filter(meter_number=meter_number).first()
        cmd = MeterCommand.objects.create(
            meter=meter,
            meter_number=meter_number,
            frame_hex=frame_hex.strip().upper(),
            expect_di=(expect_di or "").upper(),
            timeout=float(timeout),
            initiated_by=initiated_by or "",
            reason=reason or "",
            status="new",
        )

    # Wait loop: poll the row status until done or timeout
    deadline = time.time() + max(float(timeout) + 2.0, 5.0)
    while time.time() < deadline:
        time.sleep(0.2)
        c = MeterCommand.objects.only(
            "status", "reply_hex", "error").get(pk=cmd.pk)
        if c.status in ("ok", "timeout", "error"):
            return {
                "ok": (c.status == "ok"),
                "reply": (c.reply_hex or ""),
                "error": ("" if c.status == "ok" else (c.error or c.status)),
                "status": c.status,
            }
    # If still not finished, return timeout
    c = MeterCommand.objects.only("status", "error").get(pk=cmd.pk)
    return {"ok": False, "error": c.error or "timeout", "status": c.status}
