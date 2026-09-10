# smart_meter/utils/commands.py
from smart_meter.utils.frames import build_read_028011FF
import time
from smart_meter.utils.control_client import send_via_listener
# builds DL/T645 switch frame
from smart_meter.vendor.switch_OnOff import (
    RELAY_CLOSE_COMMAND,
    RELAY_OPEN_COMMAND,
    frame_command,
)

# DL/T645 power control:
#   0x1A = OFF (cut-off)   0x1B = ON (restore)


def send_cutoff_command(meter_number: str, timeout: float = 12.0) -> dict:
    """Turn power OFF (cut off). Returns control server JSON: {'ok': bool, 'reply': 'HEX', 'error': '...'}"""
    frame = frame_command(meter_number, RELAY_OPEN_COMMAND)
    return send_via_listener(meter_number, frame, timeout=timeout)


def send_restore_command(meter_number: str, timeout: float = 12.0) -> dict:
    """Turn power ON (restore). Returns control server JSON: {'ok': bool, 'reply': 'HEX', 'error': '...'}"""
    frame = frame_command(meter_number, RELAY_CLOSE_COMMAND)
    return send_via_listener(meter_number, frame, timeout=timeout)


def refresh_live(meter_number: str, delay: float = 0.3, timeout: float = 6.0) -> dict:
    try:
        if delay > 0:
            time.sleep(delay)
        frame = build_read_028011FF(meter_number)
        # ❌ was: send_via_listener(meter.meter_number, ...)
        return send_via_listener(meter_number, frame, timeout=timeout, expect_di="028011FF")
    except Exception as e:
        return {"ok": False, "error": str(e)}


def request_instant_live_reading(meter_number: str, timeout: float = 8.0) -> dict:
    """Request one fresh 0x028011FF reading through the durable command queue."""
    from smart_meter.utils.db_send import send_via_db

    frame = build_read_028011FF(meter_number)
    return send_via_db(
        meter_number=str(meter_number),
        frame_hex=frame.hex().upper(),
        timeout=float(timeout),
        expect_di="028011FF",
        command_type="read",
        source="manual",
        reason="instant live 028011FF read",
        max_attempts=1,
    )
