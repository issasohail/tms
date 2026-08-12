# smart_meter/utils/commands.py
import json
import socket

from django.conf import settings

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


def request_instant_live_reading(meter_number: str, timeout: float = 8.0) -> dict:
    """Request one fresh 0x028011FF reading through the listener control port.

    This is separate from ``refresh_live`` so the existing manual ON/OFF and
    legacy refresh path remain unchanged. The normal listener/parser still
    parses and persists the meter response.
    """
    host = getattr(settings, "CONTROL_LISTENER_HOST", "127.0.0.1") or "127.0.0.1"
    port = int(getattr(settings, "CONTROL_LISTENER_PORT", 7000) or 7000)
    frame = build_read_028011FF(meter_number)
    request = {
        "op": "send",
        "meter": str(meter_number),
        "frame": frame.hex().upper(),
        "timeout": float(timeout),
        "expect_di": "028011FF",
    }
    payload = (json.dumps(request) + "\n").encode("utf-8")
    try:
        with socket.create_connection((host, port), timeout=min(timeout, 5.0)) as sock:
            sock.settimeout(timeout + 2.0)
            sock.sendall(payload)
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except socket.timeout:
        return {"ok": False, "error": "Instant reading timed out."}
    except OSError as exc:
        return {"ok": False, "error": f"Meter listener unavailable: {exc}"}

    raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
    if not raw:
        return {"ok": False, "error": "Meter listener returned an empty response."}
    try:
        response = json.loads(raw)
    except ValueError:
        return {"ok": False, "error": "Meter listener returned an invalid response."}
    if not response.get("ok"):
        return {"ok": False, "error": response.get("error") or "Instant reading failed."}
    return response
