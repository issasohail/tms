# smart_meter/utils/control_client.py
import json
import socket
import os
import logging
import traceback
from datetime import datetime
import datetime
log = logging.getLogger("meter_control")   # define logger first


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 6000

# Configure a small per-module logger (writes to a log file in same folder)
LOG_PATH = os.path.join(os.path.dirname(__file__), "control_client.log")
logger = logging.getLogger("smart_meter.control_client")
if not logger.handlers:
    fh = logging.FileHandler(LOG_PATH)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
    logger.setLevel(logging.INFO)


def cutoffs_disabled() -> bool:
    """
    If DISABLE_CUTOFF is "1" (or non-"0"), treat as disabled.
    """
    return os.getenv("DISABLE_CUTOFF", "0") != "0"


def looks_like_cutoff(frame_hex: str) -> bool:
    """
    Heuristic: the typical control-off frame we saw contains the byte sequence 9C00
    (control byte 0x9C followed by payload 0x00). Also check for the trimmed form.
    This is intentionally conservative but catches the observed offending frames.
    """
    if not frame_hex:
        return False
    h = frame_hex.replace(" ", "").upper()
    # direct sequence we observed in captures
    if "9C00" in h:
        return True
    # if someone uses ASCII or other form with the control identifier inside payload,
    # you can add other checks (e.g., identifier "00400001") if relevant.
    if "00400001" in h:
        return True
    return False


def send_via_listener(meter_number: str,
                      frame: bytes,
                      timeout: float = 20.0,
                      expect_di: str | None = None,
                      allow_switch: bool = False,
                      initiated_by: str | None = None,
                      reason: str | None = None,
                      auth: str | None = None):
    """
    Send a JSON request to the local control listener which will forward 'frame' to the meter.

    Safety features preserved:
      - If DISABLE_CUTOFF=1 and the frame looks like a cut-off, the send is suppressed.
      - Every attempted send is logged to control_client.log with the caller stack frame.
    """
    # --- build frame + basic audit ---
    frame_hex = (frame or b"").hex().upper()
    try:
        caller = traceback.format_stack()[-3].strip()
    except Exception:
        caller = "<unknown>"
    logger.info("REQUEST send_via_listener meter=%s len=%d caller=%s frame=%s",
                meter_number, len(frame or b""), caller, frame_hex)

    # --- safety guard (optional cutoff suppression) ---
    if cutoffs_disabled() and looks_like_cutoff(frame_hex):
        msg = ("Cutoff suppressed by DISABLE_CUTOFF (safety): meter=%s caller=%s frame=%s"
               % (meter_number, caller, frame_hex))
        logger.warning(msg)
        return {"ok": False, "error": "cutoff suppressed by DISABLE_CUTOFF", "suppressed": True}

    # --- request payload sent to ControlServer (port 7000) ---
    req = {
        "op": "send",
        "meter": meter_number,
        "frame": frame_hex,
        "timeout": float(timeout),
    }
    if expect_di:
        req["expect_di"] = expect_di.upper()
    if allow_switch:
        req["allow_switch"] = True
    if initiated_by:
        req["initiated_by"] = initiated_by
    if reason:
        req["reason"] = reason[:120]
    if auth:
        req["auth"] = auth  # optional shared secret
    payload = (json.dumps(req) + "\n").encode("utf-8")

    # --- single, robust socket exchange ---
    try:
        with socket.create_connection((CONTROL_HOST, CONTROL_PORT), timeout=10) as s:
            # wait a bit longer than server's waiter
            s.settimeout(timeout + 5.0)
            logger.info("Opening connection to listener %s:%s for meter=%s",
                        CONTROL_HOST, CONTROL_PORT, meter_number)
            s.sendall(payload)
            # signal end-of-request so server parses
            s.shutdown(socket.SHUT_WR)

            chunks = []
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)

    except socket.timeout:
        logger.exception("Listener timeout for meter=%s", meter_number)
        return {"ok": False, "error": "listener timeout"}
    except Exception as e:
        logger.exception("Listener error for meter=%s: %s", meter_number, e)
        return {"ok": False, "error": str(e)}

    # --- parse response ---
    text = b"".join(chunks).decode("utf-8", errors="replace").strip()
    logger.info(
        "Listener response for smart_meter>utils>control_client.py...meter=%s: %s at %s",
        meter_number,
        text or "<empty>",
        datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    )

    if not text:
        return {"ok": False, "error": "empty response from control server"}
    try:
        return json.loads(text)
    except Exception as e:
        return {"ok": False, "error": f"bad JSON from control server: {e}", "raw": text}


# ---- diagnostics: keep these TEMPORARILY; they must be AFTER the function ----
# try:
#    log.info("control_client imported from: %s", __file__)
#    log.info("send_via_listener firstlineno: %s",
#             send_via_listener.__code__.co_firstlineno)
# except Exception as _diag_err:
    # Don’t crash Django init if logging fails
#    pass
