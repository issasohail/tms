# smart_meter/utils/commands.py
from smart_meter.utils.frames import build_read_028011FF
import time
from smart_meter.utils.control_client import send_via_listener
# builds DL/T645 switch frame
from smart_meter.vendor.switch_OnOff import frame_command

# DL/T645 power control:
#   0x1A = OFF (cut-off)   0x1C = ON (restore)


def send_cutoff_command(meter_number: str, timeout: float = 12.0) -> dict:
    """Turn power OFF (cut off). Returns control server JSON: {'ok': bool, 'reply': 'HEX', 'error': '...'}"""
    frame = frame_command(meter_number, 0x1A)
    return send_via_listener(meter_number, frame, timeout=timeout)


def send_restore_command(meter_number: str, timeout: float = 12.0) -> dict:
    """Turn power ON (restore). Returns control server JSON: {'ok': bool, 'reply': 'HEX', 'error': '...'}"""
    frame = frame_command(meter_number, 0x1C)
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
