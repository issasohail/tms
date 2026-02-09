# smart_meter/management/commands/meter_listener.py

from __future__ import annotations

import datetime
import json
import logging
import os
import queue
import socket
import struct  # for Windows keepalive ioctl
import threading
import time
from datetime import datetime as dt
from pathlib import Path
from typing import Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from smart_meter.dlt645 import parse_frame, verify_checksum
from smart_meter.models import LiveReading, Meter, MeterReading, UnknownMeter

# Try to use a Windows-safe rotating handler; fall back to non-rotating FileHandler
# ==== WINDOWS-SAFE LOGGING (configure once, here) ====
import logging
from pathlib import Path
from django.conf import settings
import os

# Try to use a Windows-safe rotating handler; fall back to non-rotating FileHandler
try:
    from concurrent_log_handler import ConcurrentRotatingFileHandler as _SafeHandler
    _SAFE_HANDLER_KW = dict(maxBytes=10_000_000,
                            backupCount=5, encoding="utf-8")
    _ROTATION_ENABLED = True
except ImportError:
    class _SafeHandler(logging.FileHandler):  # fallback (no rotation)
        def __init__(self, filename, **_):
            super().__init__(filename, encoding="utf-8")
    _SAFE_HANDLER_KW = {}
    _ROTATION_ENABLED = False

# Where to write logs
LOG_DIR = getattr(settings, "LOG_DIR", r"C:\tenant_management_system\logs")
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
# Use a unique file to avoid collisions with any global Django handler
LOG_PATH = str(Path(LOG_DIR) / "meter_listener_worker.log")

# Use one consistent logger name for this command file
logger = logging.getLogger("smart_meter.listener")
logger.setLevel(logging.INFO)
logger.propagate = False  # don't double-log via root

# Helper: does a handler for this exact file already exist on this logger?


def _has_file_handler_for(path: str) -> bool:
    for h in logger.handlers:
        if getattr(h, "baseFilename", None) and os.path.normcase(getattr(h, "baseFilename")) == os.path.normcase(path):
            return True
    return False


# Add the file handler once
if not _has_file_handler_for(LOG_PATH):
    fh = _SafeHandler(LOG_PATH, **_SAFE_HANDLER_KW)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(fh)
    if not _ROTATION_ENABLED:
        logger.warning(
            "concurrent-log-handler not installed; using non-rotating FileHandler.")

# Optional: console output for foreground runs (avoid duplicates)
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(ch)
# ==== END LOGGING SETUP ====

# ==== DIAGNOSTIC: list all handlers touching files ====
try:
    def _describe_handler(h):
        name = getattr(h, "__class__", type(h)).__name__
        fn = getattr(h, "baseFilename", None)
        return f"{name} -> {fn}" if fn else name

    all_logs = {"(root)": logging.getLogger()}
    for lname, lobj in logging.Logger.manager.loggerDict.items():
        if isinstance(lobj, logging.Logger):
            all_logs[lname] = lobj

    logger.info("=== Logging handler inventory (start) ===")
    for lname, lobj in all_logs.items():
        for h in getattr(lobj, "handlers", []):
            logger.info("LOGGER %-30s HANDLER %s", lname, _describe_handler(h))
    logger.info("=== Logging handler inventory (end) ===")
except Exception as _e:
    logger.warning("Handler inventory failed: %s", _e)
# ==== END DIAGNOSTIC ====


CTRL_SHARED_SECRET = os.getenv("METER_CTRL_SECRET")


def _decode_switch_action_from_hex(frame_hex: str) -> Optional[str]:
    """
    Try to detect ON/OFF from a DLT645 switch frame.
    OFF: channel byte 0x4D (0x1A + 0x33), ON: 0x4F (0x1C + 0x33)
    Returns "OFF", "ON", or None if not a switch frame.
    """
    try:
        b = bytes.fromhex(frame_hex)
        i = b.find(b'\x68')
        if i < 0 or i + 10 >= len(b):
            return None
        ctrl = b[i + 8]
        if ctrl != 0x1C:  # switch write
            return None
        # L is at i+9, data starts at i+10; after 8 fixed bytes comes the channel byte
        channel = b[i + 10 + 8]
        if channel == 0x4D:
            return "OFF"
        if channel == 0x4F:
            return "ON"
        return None
    except Exception:
        return None


def append_line(path, line):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = (line.rstrip("\r\n") + "\n")
    with p.open("a", encoding="utf-8", errors="replace", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


LOG_FILE_FRAMES = Path(LOG_DIR) / "meter_raw_frames.log"

# How long we keep an idle connection with no data & no queued TX
IDLE_TIMEOUT = 600  # seconds

# TCP keepalive (helps reap half-open connections)
KA_IDLE = 600      # start keepalive probes after 600s idle
KA_INT = 10        # send a probe every 10s
KA_CNT = 3         # drop after 3 failed probes

# Safety: avoid unbounded memory if someone floods junk
MAX_BUFFER_BYTES = 1024 * 1024  # 1 MB

HOST = "0.0.0.0"
PORT = 6000

# Local control endpoint (for meter_send to talk to)
CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 7000

SNAPSHOT_MINUTES = 15  # store historical snapshot at this cadence

# =========================
# Connection registry & waiter management
# =========================
ACTIVE_HANDLERS: dict[str, "ClientHandler"] = {}
ACTIVE_LOCK = threading.Lock()

# waiters: meter_number -> list of {"q": queue.Queue, "expect_di": str|None}
REPLY_WAITERS: dict[str, list[dict]] = {}
REPLY_LOCK = threading.Lock()


def _register_handler(meter_number: str, handler: "ClientHandler"):
    if not meter_number:
        return
    with ACTIVE_LOCK:
        ACTIVE_HANDLERS[meter_number] = handler


def _unregister_handler(meter_number: Optional[str], handler: "ClientHandler"):
    if not meter_number:
        return
    with ACTIVE_LOCK:
        cur = ACTIVE_HANDLERS.get(meter_number)
        if cur is handler:
            ACTIVE_HANDLERS.pop(meter_number, None)


def _get_handler(meter_number: str) -> Optional["ClientHandler"]:
    with ACTIVE_LOCK:
        return ACTIVE_HANDLERS.get(meter_number)


def _push_waiter(meter_number: str, q: "queue.Queue", expect_di: Optional[str]):
    with REPLY_LOCK:
        REPLY_WAITERS.setdefault(meter_number, []).append(
            {"q": q, "expect_di": (expect_di or "").upper()}
        )


def _deliver_if_match(meter_number: str, di: str, frame: bytes) -> bool:
    di = (di or "").upper()
    with REPLY_LOCK:
        lst = REPLY_WAITERS.get(meter_number) or []
        for i, item in enumerate(lst):
            exp = item["expect_di"]
            if not exp or exp == di:
                lst.pop(i)
                item["q"].put(frame)
                return True
        return False


def handle_frame(addr, raw_bytes, status):
    hex_frame = raw_bytes.hex().upper()
    append_line(
        LOG_FILE_FRAMES,
        f"{dt.now().isoformat()} {addr[0]}:{addr[1]} {status} {hex_frame}",
    )
    logger.info("Saved %d bytes from %s", len(raw_bytes), addr)

# =========================
# ClientHandler per TCP connection
# =========================


class ClientHandler(threading.Thread):
    def __init__(self, conn: socket.socket, addr, debug=False, dump_raw=None, accept_bad=False):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
        self.buffer = b""
        self.debug = debug
        self.dump_raw = dump_raw
        self.accept_bad = accept_bad

        # Outbound frames queue: each item is (frame: bytes, expire_at: float)
        self.tx: queue.Queue = queue.Queue()
        self.alive = True
        self.conn.settimeout(0.5)

        try:
            self.conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, "TCP_KEEPIDLE"):
                self.conn.setsockopt(socket.IPPROTO_TCP,
                                     socket.TCP_KEEPIDLE, KA_IDLE)
                self.conn.setsockopt(socket.IPPROTO_TCP,
                                     socket.TCP_KEEPINTVL, KA_INT)
                self.conn.setsockopt(socket.IPPROTO_TCP,
                                     socket.TCP_KEEPCNT, KA_CNT)
            else:
                # Windows keepalive
                SIO_KEEPALIVE_VALS = 0x98000004
                onoff = 1
                keepalivetime = KA_IDLE * 1000
                keepaliveinterval = KA_INT * 1000
                self.conn.ioctl(SIO_KEEPALIVE_VALS, struct.pack(
                    "III", onoff, keepalivetime, keepaliveinterval))
        except Exception as e:
            logger.debug(f"keepalive setup failed for {self.addr}: {e}")

        # Learned from parsed frames
        self.meter_number: Optional[str] = None
        self.last_seen = time.time()
        self.peer = f"{addr[0]}:{addr[1]}"

    def enqueue_send(self, frame: bytes, expire_at: float | None = None):
        """Queue a frame with an expiry timestamp (epoch seconds). If expire_at is 0/None, send unconditionally."""
        self.tx.put((frame, float(expire_at or 0.0)))

    def run(self):
        logger.info(f"📡 Connection from {self.addr}")
        try:
            while self.alive:
                # ---- Receive ----
                try:
                    chunk = self.conn.recv(4096)
                    if chunk == b"":  # peer closed (EOF)
                        logger.info(f"EOF from {self.addr} — closing")
                        break
                except socket.timeout:
                    chunk = None
                except Exception as e:
                    logger.debug(f"recv error {self.addr}: {e}")
                    break

                if chunk:
                    if self.debug:
                        logger.debug(
                            f"⬇️ RAW CHUNK {self.addr} ({len(chunk)}B): {chunk.hex().upper()}")
                    self.buffer += chunk

                    # Guard against memory abuse
                    if len(self.buffer) > MAX_BUFFER_BYTES:
                        logger.warning(
                            f"Buffer cap exceeded from {self.addr}; dropping connection")
                        break

                    # Frame slicer by L field
                    while True:
                        while self.buffer and self.buffer[0] == 0xFE:
                            self.buffer = self.buffer[1:]
                        if not self.buffer:
                            break

                        start = self.buffer.find(b'\x68')
                        if start == -1:
                            self.buffer = b""
                            break
                        if start > 0:
                            self.buffer = self.buffer[start:]
                            start = 0

                        if len(self.buffer) < 10:
                            break

                        if self.buffer[7] != 0x68:
                            self.buffer = self.buffer[1:]
                            continue

                        L = self.buffer[9]
                        total_len = 12 + L
                        if len(self.buffer) < total_len:
                            break

                        frame = self.buffer[:total_len]
                        self.buffer = self.buffer[total_len:]

                        if self.debug:
                            logger.debug(
                                f"🧱 FRAME {self.addr} ({len(frame)}B): {frame.hex().upper()}")

                        if self.dump_raw:
                            try:
                                with open(self.dump_raw, "a", encoding="utf-8") as f:
                                    f.write(frame.hex().upper() + "\n")
                            except Exception as e:
                                logger.warning(
                                    f"Failed to write raw frame: {e}")

                        self.process_frame(frame)

                else:
                    # No data this tick. If completely idle and no pending TX, time out.
                    if (time.time() - self.last_seen) > IDLE_TIMEOUT and self.tx.empty():
                        logger.info(
                            f"Idle timeout ({IDLE_TIMEOUT}s) {self.addr} — closing")
                        break

                # ---- Transmit any queued frames ----
                try:
                    while True:
                        item = self.tx.get_nowait()
                        # Backward-compat: accept either tuple or raw frame
                        if isinstance(item, tuple):
                            frame, expire_at = item
                        else:
                            frame, expire_at = item, 0.0

                        now = time.time()
                        if expire_at and now > expire_at:
                            logger.warning(
                                "DROP_STALE_TX peer=%s meter=%s age=%.1fs frame=%s",
                                self.peer, getattr(self, "meter_number", None),
                                now - expire_at, frame.hex().upper()
                            )
                            continue  # don't send it

                        logger.info(
                            "TX_TO_METER peer=%s meter=%s len=%s frame=%s",
                            self.peer,
                            getattr(self, "meter_number", None),
                            len(frame),
                            frame.hex().upper()
                        )
                        self.conn.sendall(frame)
                        self.last_seen = now

                except queue.Empty:
                    pass
                except Exception as e:
                    logger.warning(f"Send error to {self.addr}: {e}")
                    break

        except Exception as e:
            logger.exception(f"ClientHandler error for {self.addr}: {e}")
        finally:
            try:
                self.conn.close()
            except Exception:
                pass
            self.alive = False
            if self.meter_number:
                _unregister_handler(self.meter_number, self)
            timestamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            logger.info(f"🔌 Connection closed {self.addr} at {timestamp}")

    def process_frame(self, frame: bytes):
        # In long-lived threads, clear any stale/borrowed DB handles
        close_old_connections()

        start = frame.find(b'\x68')
        ok, cs_style = verify_checksum(frame, start)

        if not ok and not self.accept_bad:
            try:
                ctrl_idx = start + 8
                L = frame[start + 9]
                data_end = (start + 10) + L
                calc_c = (sum(frame[ctrl_idx:data_end]) & 0xFF)
                found = frame[data_end]
                logger.warning(
                    f"Checksum failed (calc=0x{calc_c:02X}, found=0x{found:02X}, L={L}, frame_len={len(frame)})"
                )
            except Exception:
                logger.warning("Checksum failed (unable to compute details)")
            return

        parsed = parse_frame(frame, accept_bad_checksum=self.accept_bad)
        if self.debug:
            logger.debug(f"🧩 parse_frame -> {parsed}")
        if not parsed:
            return

        meter_number = parsed.get("meter_number", "")
        ctrl_code = parsed.get("control_code", 0)
        di = parsed.get("di")
        data = parsed.get("data")

        self.last_seen = time.time()

        # Remember/register meter number for this socket
        if meter_number:
            self.meter_number = meter_number
            _register_handler(meter_number, self)

        msg = f"📥 Meter {meter_number} DI={di} "
        msg += "(data parsed)" if data else "(no data)"
        if parsed.get("cs_style"):
            msg += f" [cs:{parsed.get('cs_style')}]"
        logger.info(
            "%s - %s", timezone.localtime().isoformat(timespec="seconds"), msg)

        # Deliver to a waiting "send-and-wait" caller (but skip keepalives)
        if di != "80808080" and ctrl_code in (0x91, 0x83, 0x9C, 0xDC) and meter_number:
            delivered = _deliver_if_match(meter_number, di, frame)
            if delivered and self.debug:
                logger.debug(
                    f"📤 Delivered reply to waiter for meter {meter_number}")

        # keepalives or unknown DI
        if not data:
            return

        if not ok:
            # accept_bad_checksum True; we logged but won't store
            return

        # Resolve meter for storage
        try:
            with transaction.atomic():
                meter = Meter.objects.get(meter_number=meter_number)
        except Meter.DoesNotExist:
            with transaction.atomic():
                um, created = UnknownMeter.objects.get_or_create(
                    meter_number=meter_number,
                    defaults={"last_raw_hex": frame.hex().upper()}
                )
                if not created:
                    um.seen_count += 1
                    um.last_raw_hex = frame.hex().upper()
                    um.status = "new"
                    um.last_seen = timezone.now()
                    um.save(update_fields=[
                            "seen_count", "last_raw_hex", "status", "last_seen"])
            logger.info(
                f"🆕 Unknown meter discovered: {meter_number} (seen {um.seen_count}x)")
            connection.close()
            return

        # Live upsert
        live_defaults = {
            'balance': data.get('balance'),
            'overdraft': data.get('overdraft'),
            'voltage_a': data.get('voltage_a'),
            'voltage_b': data.get('voltage_b'),
            'voltage_c': data.get('voltage_c'),
            'current_a': data.get('current_a'),
            'current_b': data.get('current_b'),
            'current_c': data.get('current_c'),
            'total_power': data.get('total_power'),
            'power_a': data.get('power_a'),
            'power_b': data.get('power_b'),
            'power_c': data.get('power_c'),
            'pf_total': data.get('pf_total'),
            'pf_a': data.get('pf_a'),
            'pf_b': data.get('pf_b'),
            'pf_c': data.get('pf_c'),
            'total_energy': data.get('total_energy'),
            'peak_total_energy': data.get('peak_total_energy'),
            'valley_total_consumption': data.get('valley_total_consumption'),
            'flat_total_consumption': data.get('flat_total_consumption'),
            'status_word': data.get('status_word'),
            'source_ip':   self.addr[0],
            'source_port': self.addr[1],

            # Extended counters
            'prev1_day_energy':         data.get('prev1_day_energy'),
            'prev1_day_peak_energy':    data.get('prev1_day_peak_energy'),
            'prev1_day_valley_energy':  data.get('prev1_day_valley_energy'),
            'prev1_day_flat_energy':    data.get('prev1_day_flat_energy'),

            'last2_days_energy':        data.get('last2_days_energy'),
            'last2_days_peak_energy':   data.get('last2_days_peak_energy'),
            'last2_days_valley_energy': data.get('last2_days_valley_energy'),
            'last2_days_flat_energy':   data.get('last2_days_flat_energy'),

            'last3_days_energy':        data.get('last3_days_energy'),
            'last3_days_peak_energy':   data.get('last3_days_peak_energy'),
            'last3_days_valley_energy': data.get('last3_days_valley_energy'),
            'last3_days_flat_energy':   data.get('last3_days_flat_energy'),
        }
        with transaction.atomic():
            LiveReading.objects.update_or_create(
                meter=meter, defaults=live_defaults)

        # Historical snapshot on cadence
        now = timezone.now()
        last = meter.readings.order_by('-ts').first()
        take_snapshot = (not last) or (
            (now - last.ts).total_seconds() >= SNAPSHOT_MINUTES * 60)
        if take_snapshot:
            with transaction.atomic():
                MeterReading.objects.create(
                    meter=meter,
                    ts=now,
                    total_energy=data.get('total_energy'),
                    peak_total_energy=data.get('peak_total_energy'),
                    valley_total_consumption=data.get(
                        'valley_total_consumption'),
                    flat_total_consumption=data.get('flat_total_consumption'),
                    total_power=data.get('total_power'),
                    pf_total=data.get('pf_total'),
                    voltage_a=data.get('voltage_a'),
                    voltage_b=data.get('voltage_b'),
                    voltage_c=data.get('voltage_c'),
                    current_a=data.get('current_a'),
                    current_b=data.get('current_b'),
                    current_c=data.get('current_c'),
                    source_ip=self.addr[0],
                    source_port=self.addr[1],
                )
            logger.info("%s ✅ Stored live reading for meter %s",
                        timezone.localtime().isoformat(timespec="seconds"), meter_number)
        else:
            logger.info("%s ✅ Stored live reading for meter %s",
                        timezone.localtime().isoformat(timespec="seconds"), meter_number)

        # IMPORTANT: free the DB handle so threads don't hoard connections
        connection.close()

# =========================
# Local JSON control server
# =========================


class ControlServer(threading.Thread):
    """
    Simple local JSON server on 127.0.0.1:7000.
    - List: {"op":"list"}
      -> {"ok":true,"meters":[{"meter":"...","peer":"ip:port","last_seen":ts}, ...]}
    - Send: {"op":"send","meter":"<meter>","frame":"<HEX>","timeout":12,"expect_di":"028011FF"}
      -> {"ok":true,"reply":"<HEX>"}  or {"ok":false,"error":"..."}
    """

    def __init__(self, host=CONTROL_HOST, port=CONTROL_PORT, debug=False):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.debug = debug

    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port))
            srv.listen(5)
            logger.info(
                f"🛠️  Control server on {self.host}:{self.port} (local JSON)")
            while True:
                conn, addr = srv.accept()
                threading.Thread(target=self.handle_client,
                                 args=(conn, addr), daemon=True).start()

    # Moved inside the class so `self` is valid
    def handle_client(self, conn: socket.socket, addr):
        try:
            conn.settimeout(10)
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk

            req = json.loads(data.decode("utf-8").strip() or "{}")
            op = (req.get("op") or "send").lower()

            if op == "list":
                out = []
                with ACTIVE_LOCK:
                    for m, h in ACTIVE_HANDLERS.items():
                        out.append({
                            "meter": m,
                            "peer": getattr(h, "peer", ""),
                            "last_seen": getattr(h, "last_seen", 0),
                        })
                resp = {"ok": True, "meters": out}

            elif op == "send":
                meter = req.get("meter", "")
                frame_hex = (req.get("frame") or "").upper()
                timeout = float(req.get("timeout", 12.0))
                expect_di = req.get("expect_di")  # may be None

                # OPTIONAL user-intent gating (pass these from the view only)
                allow_switch = bool(req.get("allow_switch", False))
                initiated_by = (req.get("initiated_by") or "").strip()
                reason = (req.get("reason") or "").strip()

                if not meter or not frame_hex:
                    logger.warning(
                        "CONTROL_REQUEST_INVALID peer=%s meter=%s frame_present=%s",
                        f"{addr[0]}:{addr[1]}", meter, bool(frame_hex)
                    )
                    resp = {"ok": False, "error": "meter and frame required"}

                else:
                    h = _get_handler(meter)
                    if not h:
                        resp = {"ok": False,
                                "error": f"meter {meter} not connected"}
                    else:
                        waiter: "queue.Queue" = queue.Queue()
                        _push_waiter(meter, waiter, expect_di)

                        action_guess = _decode_switch_action_from_hex(
                            frame_hex)
                        # Block ON/OFF unless explicitly allowed by the UI
                        if action_guess in ("ON", "OFF") and not (allow_switch and initiated_by):
                            resp = {
                                "ok": False, "error": "switch frames blocked (missing allow_switch/initiated_by)"}
                        else:
                            if action_guess in ("ON", "OFF"):
                                logger.warning(
                                    "!!! SWITCH_SEND peer=%s meter=%s action=%s by=%s reason=%s frame=%s",
                                    f"{addr[0]}:{addr[1]}", meter, action_guess,
                                    initiated_by or "-", reason or "-", frame_hex
                                )

                            logger.info(
                                "CONTROL_REQUEST peer=%s meter=%s action=%s frame=%s timeout=%.1f expect_di=%s",
                                f"{addr[0]}:{addr[1]}", meter, action_guess or "UNKNOWN",
                                frame_hex, timeout, expect_di,
                            )

                            # enqueue with expiry so we don't send after timeout
                            ttl = float(timeout)
                            h.enqueue_send(bytes.fromhex(
                                frame_hex), expire_at=time.time() + ttl)

                            try:
                                reply = waiter.get(timeout=timeout)
                                resp = {"ok": True,
                                        "reply": reply.hex().upper()}
                                logger.info(
                                    "CONTROL_RESULT meter=%s action=%s ok=True reply_len=%s",
                                    meter, action_guess or "UNKNOWN",
                                    len(reply) if isinstance(
                                        reply, (bytes, bytearray)) else "?"
                                )
                            except queue.Empty:
                                logger.warning(
                                    "CONTROL_RESULT meter=%s action=%s ok=False error=timeout",
                                    meter, action_guess or "UNKNOWN"
                                )
                                resp = {"ok": False,
                                        "error": "timeout waiting for reply"}

            else:
                # Unknown op: don't touch frame variables; just return error
                resp = {"ok": False, "error": f"unknown op {op}"}

            # Always send a JSON response
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))

        except Exception as e:
            try:
                conn.sendall(
                    (json.dumps({"ok": False, "error": str(e)}) + "\n").encode("utf-8"))
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

# =========================
# Django management command
# =========================


class Command(BaseCommand):
    help = "Start DL/T 645 listener; store live readings; provide a local control port to send commands."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=HOST,
                            help="Bind address for incoming meter connections")
        parser.add_argument("--port", type=int, default=PORT,
                            help="TCP port for incoming meter connections")
        parser.add_argument("--debug", action="store_true",
                            help="Enable verbose hex logging of chunks and frames.")
        parser.add_argument("--dump-raw", dest="dump_raw",
                            default=None, help="Path to append raw frames as hex.")
        parser.add_argument(
            "--accept-bad-checksum",
            dest="accept_bad_checksum",
            action="store_true",
            help="Parse/log frames even when checksum fails (debug only; values not stored).",
        )

    def handle(self, *args, **opts):
        host = opts.get("host", HOST)
        port = opts.get("port", PORT)
        debug = opts.get("debug", False)
        dump_raw = opts.get("dump_raw")
        accept_bad = opts.get("accept_bad_checksum", False)

        if debug:
            logger.setLevel(logging.DEBUG)
            logger.debug("🔧 Debug logging enabled")

        # Start local control server (JSON over TCP)
        ControlServer(debug=debug).start()

        logger.info("✅ Listening on %s:%s for DL/T 645 frames...", host, port)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            s.listen(50)
            while True:
                conn, addr = s.accept()
                ClientHandler(
                    conn, addr,
                    debug=debug,
                    dump_raw=dump_raw,
                    accept_bad=accept_bad
                ).start()
