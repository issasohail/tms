# smart_meter/management/commands/meter_listener.py

from __future__ import annotations

import datetime
import json
import logging
import os
import queue
import socket
import socketserver
import stat
import struct  # for Windows keepalive ioctl
import threading
import time
from datetime import datetime as dt
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.db.models import Q
from django.utils import timezone

from smart_meter.diagnostic import (
    build_diagnostic_read_frame,
    normalize_diagnostic_di,
    validate_meter_number,
)
from smart_meter.dlt645 import DIRECT_REGISTER_SPECS, parse_frame, verify_checksum
from smart_meter.dlt645 import relay_state_from_status_word
from smart_meter.utils.frames import build_read_028011FF, build_read_register

# ==== WINDOWS-SAFE LOGGING (same as before) ====
from smart_meter.models import (
    LiveReading,
    Meter,
    MeterCommand,  # already importing Meter
    MeterReading,
    MeterRawFrame,
    UnknownMeter,
)
from smart_meter.services.command_lifecycle import revalidate_command
from smart_meter.services.prepaid_money import (
    MONEY_COMMAND_TYPES,
    UNCERTAIN_OPERATOR_MESSAGE,
    acknowledge_late_prepaid_reply,
    is_prepaid_money_command,
    mark_prepaid_acknowledged,
    mark_prepaid_definitive_failure,
    mark_prepaid_reconciliation_uncertain,
    mark_prepaid_uncertain,
    reconcile_prepaid_balance,
)
from smart_meter.services.relay_status import (
    classify_relay_ack,
    parse_authoritative_relay_state,
    sync_authoritative_relay_status,
)
from smart_meter.utils.frames import build_read_028011FF

try:
    from concurrent_log_handler import ConcurrentRotatingFileHandler as _SafeHandler

    _SAFE_HANDLER_KW = dict(maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    _ROTATION_ENABLED = True
except ImportError:

    class _SafeHandler(logging.FileHandler):  # fallback (no rotation)
        def __init__(self, filename, **_):
            super().__init__(filename, encoding="utf-8")

    _SAFE_HANDLER_KW = {}
    _ROTATION_ENABLED = False

LOG_DIR = getattr(settings, "LOG_DIR", settings.BASE_DIR / "logs")
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
LOG_PATH = str(Path(LOG_DIR) / "meter_listener_worker.log")

logger = logging.getLogger("smart_meter.listener")
logger.setLevel(logging.INFO)
logger.propagate = False


def _has_file_handler_for(path: str) -> bool:
    for h in logger.handlers:
        if getattr(h, "baseFilename", None) and os.path.normcase(
            h.baseFilename
        ) == os.path.normcase(path):
            return True
    return False


if not _has_file_handler_for(LOG_PATH):
    fh = _SafeHandler(LOG_PATH, **_SAFE_HANDLER_KW)
    fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(fh)
    if not _ROTATION_ENABLED:
        logger.warning(
            "concurrent-log-handler not installed; using non-rotating FileHandler."
        )

if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    ch = logging.StreamHandler()
    ch.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(ch)

# Diagnostic inventory (kept)
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
# ==== END LOGGING SETUP ====

CTRL_SHARED_SECRET = os.getenv("METER_CTRL_SECRET")


def _decode_switch_action_from_hex(frame_hex: str) -> str | None:
    try:
        b = bytes.fromhex(frame_hex)
        i = b.find(b"\x68")
        if i < 0 or i + 10 >= len(b):
            return None
        ctrl = b[i + 8]
        if ctrl != 0x1C:  # switch write
            return None
        channel = b[i + 10 + 8]
        if channel == 0x4D:
            return "OFF"
        if channel == 0x4E:
            return "ON"
        return None
    except Exception:
        return None


def append_line(path, line):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = line.rstrip("\r\n") + "\n"
    with p.open("a", encoding="utf-8", errors="replace", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


LOG_FILE_FRAMES = Path(LOG_DIR) / "meter_raw_frames.log"

# -------------------------
# Connection policy
# -------------------------

# UPDATED: do NOT auto-close quiet sockets. Keep them persistent.
IDLE_TIMEOUT = 0  # seconds; 0/False => never close just because idle

# TCP keepalive tuning (helps survive NATs)
KA_IDLE = 600  # start keepalive probes after 600s idle
KA_INT = 10  # send a probe every 10s
KA_CNT = 3  # drop after 3 failed probes

# Application heartbeats are disabled unless a vendor-supplied complete frame is
# explicitly configured.  A bare DI such as 028011FF is not a DL/T645 frame.
HEARTBEAT_INTERVAL = getattr(settings, "METER_HEARTBEAT_INTERVAL", 0)  # seconds
HEARTBEAT_FRAME_HEX = getattr(settings, "METER_HEARTBEAT_FRAME_HEX", "")

MAX_BUFFER_BYTES = 1024 * 1024  # 1 MB (unchanged)

HOST = "0.0.0.0"
PORT = 6000

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 7000

SNAPSHOT_MINUTES = 15  # unchanged
BIDIRECTIONAL_ENERGY_METERS = {"260305510019", "260305510020", "260305510021"}

# =========================
# Connection registry & waiter management
# =========================
ACTIVE_HANDLERS: dict[str, ClientHandler] = {}
ACTIVE_LOCK = threading.Lock()

REPLY_WAITERS: dict[str, list[dict]] = {}
REPLY_LOCK = threading.Lock()
METER_REQUEST_LOCKS: dict[str, threading.Lock] = {}
METER_REQUEST_LOCKS_LOCK = threading.Lock()
DIAGNOSTIC_LAST_TX: dict[str, float] = {}
DIAGNOSTIC_MIN_INTERVAL = 0.5
DIAGNOSTIC_SOCKET_PATH = str(
    getattr(settings, "METER_DIAGNOSTIC_SOCKET", "/tmp/tms-meter-diagnostic.sock")
)


def _meter_request_lock(meter_number: str) -> threading.Lock:
    with METER_REQUEST_LOCKS_LOCK:
        return METER_REQUEST_LOCKS.setdefault(meter_number, threading.Lock())


def _register_handler(meter_number: str, handler: ClientHandler):
    if not meter_number:
        return
    # UPDATED: ensure a single live connection per meter. Replace atomically.
    with ACTIVE_LOCK:
        old = ACTIVE_HANDLERS.get(meter_number)
        if old and old is not handler:
            try:
                logger.info(
                    "ðŸ” Meter %s reconnected from %s; closing old peer %s",
                    meter_number,
                    handler.peer,
                    getattr(old, "peer", "?"),
                )
                old.close(reason="replaced")  # politely stop the old thread/socket
            except Exception:
                pass
        ACTIVE_HANDLERS[meter_number] = handler
    # Wake deferred DB commands after the socket identity is known. This is
    # fail-open for the reading path; the periodic poller remains the fallback.
    try:
        waiting = MeterCommand.objects.filter(
            meter_number=meter_number,
            status="waiting_online",
        )
        ambiguous_money = list(waiting.filter(
            command_type__in=MONEY_COMMAND_TYPES,
            attempt_count__gt=0,
        ))
        for command in ambiguous_money:
            mark_prepaid_uncertain(
                command, "meter connection changed after the first enqueue attempt"
            )
        waiting.exclude(
            command_type__in=MONEY_COMMAND_TYPES,
            attempt_count__gt=0,
        ).update(
            status="pending",
            next_attempt_at=None,
        )
    except Exception as exc:
        logger.debug(
            "Unable to wake deferred commands for %s: %s",
            meter_number,
            exc,
        )


def _unregister_handler(meter_number: str | None, handler: ClientHandler):
    if not meter_number:
        return
    with ACTIVE_LOCK:
        cur = ACTIVE_HANDLERS.get(meter_number)
        if cur is handler:
            ACTIVE_HANDLERS.pop(meter_number, None)


def _get_handler(meter_number: str) -> ClientHandler | None:
    with ACTIVE_LOCK:
        return ACTIVE_HANDLERS.get(meter_number)


def _push_waiter(
    meter_number: str,
    q: queue.Queue,
    expect_di: str | None,
    expect_controls=None,
    persist_reply: bool = True,
    *,
    consume: bool = False,
    accept_negative_without_di: bool = False,
):
    with REPLY_LOCK:
        REPLY_WAITERS.setdefault(meter_number, []).append(
            {
                "q": q,
                "expect_di": (expect_di or "").upper(),
                "expect_controls": frozenset(expect_controls or ()),
                "persist_reply": bool(persist_reply),
                "consume": bool(consume),
                "accept_negative_without_di": bool(accept_negative_without_di),
            }
        )


def _deliver_if_match(
    meter_number: str,
    di: str,
    control_code: int,
    frame: bytes,
    *,
    consume_only: bool = False,
):
    di = (di or "").upper()
    with REPLY_LOCK:
        lst = REPLY_WAITERS.get(meter_number) or []
        for i, item in enumerate(lst):
            if consume_only and not item.get("consume"):
                continue
            exp = item["expect_di"]
            controls = item.get("expect_controls") or frozenset()
            if controls and control_code not in controls:
                continue
            negative_without_di = (
                item.get("accept_negative_without_di")
                and control_code == 0xD1
                and not di
            )
            if exp and exp != di and not negative_without_di:
                continue
            if exp or controls:
                lst.pop(i)
                item["q"].put(frame)
                if item.get("consume"):
                    return 2
                return item
        return None


def _remove_waiter(meter_number: str, q: queue.Queue) -> None:
    with REPLY_LOCK:
        waiters = REPLY_WAITERS.get(meter_number) or []
        REPLY_WAITERS[meter_number] = [item for item in waiters if item["q"] is not q]
        if not REPLY_WAITERS[meter_number]:
            REPLY_WAITERS.pop(meter_number, None)


def perform_diagnostic_read(meter_number: str, di: str, timeout: float = 8.0) -> dict:
    """Send one allowlisted C=0x11 read through an existing active handler.

    The response waiter is consuming: the normal listener persistence pipeline
    stops after handing this exact address/DI response to the diagnostic caller.
    """
    meter_number = validate_meter_number(meter_number)
    di = normalize_diagnostic_di(di)
    timeout = float(timeout)
    if not 1.0 <= timeout <= 30.0:
        raise ValueError("diagnostic timeout must be between 1 and 30 seconds")

    with _meter_request_lock(meter_number):
        handler = _get_handler(meter_number)
        if handler is None or not handler.alive:
            return {"ok": False, "status": "offline", "error": "meter is not connected"}

        elapsed = time.monotonic() - DIAGNOSTIC_LAST_TX.get(meter_number, 0.0)
        if elapsed < DIAGNOSTIC_MIN_INTERVAL:
            time.sleep(DIAGNOSTIC_MIN_INTERVAL - elapsed)

        frame = build_diagnostic_read_frame(meter_number, di)
        waiter = queue.Queue(maxsize=1)
        transport_q = queue.Queue(maxsize=1)
        _push_waiter(
            meter_number,
            waiter,
            di,
            expect_controls={0x91, 0xD1},
            consume=True,
            accept_negative_without_di=True,
        )
        handler.enqueue_send(
            frame,
            expire_at=time.time() + timeout,
            transport_q=transport_q,
        )
        DIAGNOSTIC_LAST_TX[meter_number] = time.monotonic()

        try:
            transport_ok, transport_error = transport_q.get(timeout=timeout)
        except queue.Empty:
            _remove_waiter(meter_number, waiter)
            return {
                "ok": False,
                "status": "timed_out",
                "error": "timeout waiting for socket transmission",
                "tx": frame.hex().upper(),
            }
        if not transport_ok:
            _remove_waiter(meter_number, waiter)
            return {
                "ok": False,
                "status": "invalid_response",
                "error": transport_error or "socket transmission failed",
                "tx": frame.hex().upper(),
            }
        try:
            reply = waiter.get(timeout=timeout)
        except queue.Empty:
            _remove_waiter(meter_number, waiter)
            return {
                "ok": False,
                "status": "timed_out",
                "error": "timeout waiting for matching meter/DI response",
                "tx": frame.hex().upper(),
            }
        return {
            "ok": True,
            "status": "received",
            "meter": meter_number,
            "di": di,
            "tx": frame.hex().upper(),
            "rx": reply.hex().upper(),
        }


def process_diagnostic_request(request) -> dict:
    if not isinstance(request, dict):
        raise ValueError("diagnostic request must be a JSON object")
    if set(request) - {"meter", "di", "timeout"}:
        raise ValueError("diagnostic request contains unsupported fields")
    return perform_diagnostic_read(
        request.get("meter", ""),
        request.get("di", ""),
        request.get("timeout", 8.0),
    )


class DiagnosticRequestHandler(socketserver.StreamRequestHandler):
    """Local Unix-socket JSON interface; it never accepts caller-supplied frames."""

    def handle(self):
        try:
            raw = self.rfile.readline(4097)
            if not raw or len(raw) > 4096:
                raise ValueError("diagnostic request must be one JSON line under 4096 bytes")
            result = process_diagnostic_request(json.loads(raw.decode("utf-8")))
        except Exception as exc:
            result = {"ok": False, "status": "rejected", "error": str(exc)}
        self.wfile.write((json.dumps(result, separators=(",", ":")) + "\n").encode("utf-8"))


_ThreadingUnixStreamServer = getattr(
    socketserver, "ThreadingUnixStreamServer", None
)

if _ThreadingUnixStreamServer is not None:
    class DiagnosticUnixServer(_ThreadingUnixStreamServer):
        daemon_threads = True
else:
    # AF_UNIX stream servers are unavailable on some Python/Windows builds.
    # Keeping the module importable allows local checks and tests to exercise
    # the protocol code; production Linux still uses the real Unix server.
    DiagnosticUnixServer = None


def start_diagnostic_server(path: str = DIAGNOSTIC_SOCKET_PATH):
    """Start the owner-only local diagnostic IPC endpoint in a daemon thread."""
    if DiagnosticUnixServer is None:
        raise RuntimeError("diagnostic Unix socket is not supported on this platform")
    if os.path.lexists(path):
        mode = os.lstat(path).st_mode
        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f"refusing to replace non-socket diagnostic path: {path}")
        os.unlink(path)
    server = DiagnosticUnixServer(path, DiagnosticRequestHandler)
    os.chmod(path, 0o600)
    threading.Thread(
        target=server.serve_forever,
        name="meter-readonly-diagnostic-server",
        daemon=True,
    ).start()
    logger.info("Read-only meter diagnostic socket listening at %s", path)
    return server


def handle_frame(addr, raw_bytes, status):
    hex_frame = raw_bytes.hex().upper()
    append_line(
        LOG_FILE_FRAMES,
        f"{dt.now().isoformat()} {addr[0]}:{addr[1]} {status} {hex_frame}",
    )
    logger.info("Saved %d bytes from %s", len(raw_bytes), addr)


# -------------------------
# TCP keepalive helper
# -------------------------


def _enable_tcp_keepalive(conn: socket.socket):
    try:
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, KA_IDLE)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, KA_INT)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, KA_CNT)
        else:
            # Windows keepalive
            SIO_KEEPALIVE_VALS = 0x98000004
            onoff = 1
            keepalivetime = KA_IDLE * 1000
            keepaliveinterval = KA_INT * 1000
            conn.ioctl(
                SIO_KEEPALIVE_VALS,
                struct.pack("III", onoff, keepalivetime, keepaliveinterval),
            )
    except Exception as e:
        logger.debug(f"keepalive setup failed: {e}")


# =========================
# ClientHandler per TCP connection
# =========================


class ClientHandler(threading.Thread):
    def __init__(
        self, conn: socket.socket, addr, debug=False, dump_raw=None, accept_bad=False
    ):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
        self.buffer = b""
        self.debug = debug
        self.dump_raw = dump_raw
        self.accept_bad = accept_bad

        # Outbound frames queue: (frame, expire_at[, transport_q])
        self.tx: queue.Queue = queue.Queue()
        self.alive = True
        # UPDATED: longer read timeout to reduce churn
        self.conn.settimeout(30.0)
        _enable_tcp_keepalive(self.conn)

        # Learned from parsed frames
        self.meter_number: str | None = None
        self.last_seen = time.time()
        self.peer = f"{addr[0]}:{addr[1]}"
        self.disconnect_reason = "shutdown"

        # UPDATED: heartbeat thread control
        self._hb_stop = threading.Event()

    def enqueue_send(
        self,
        frame: bytes,
        expire_at: float | None = None,
        transport_q: queue.Queue | None = None,
    ):
        self.tx.put((frame, float(expire_at or 0.0), transport_q))

    def _sender_loop(self):
        """Serialize queued writes independently of the blocking receive loop."""
        while self.alive:
            try:
                item = self.tx.get(timeout=0.5)
            except queue.Empty:
                continue

            if item is None:
                return

            if isinstance(item, tuple):
                if len(item) == 3:
                    frame, expire_at, transport_q = item
                else:
                    frame, expire_at = item
                    transport_q = None
            else:
                frame, expire_at = item, 0.0
                transport_q = None

            now = time.time()
            if expire_at and now > expire_at:
                logger.warning(
                    "DROP_STALE_TX peer=%s meter=%s age=%.1fs frame=%s",
                    self.peer,
                    getattr(self, "meter_number", None),
                    now - expire_at,
                    frame.hex().upper(),
                )
                if transport_q is not None:
                    try:
                        transport_q.put_nowait(
                            (False, "frame expired before socket send")
                        )
                    except queue.Full:
                        pass
                continue

            logger.info(
                "TX_TO_METER peer=%s meter=%s len=%s frame=%s",
                self.peer,
                getattr(self, "meter_number", None),
                len(frame),
                frame.hex().upper(),
            )
            try:
                self.conn.sendall(frame)
            except Exception as send_exc:
                if transport_q is not None:
                    try:
                        transport_q.put_nowait(
                            (False, f"socket send failed: {send_exc}")
                        )
                    except queue.Full:
                        pass
                logger.warning("Send error to %s: %s", self.addr, send_exc)
                self.close(reason="send_error")
                return

            if transport_q is not None:
                try:
                    transport_q.put_nowait((True, ""))
                except queue.Full:
                    pass
            self.last_seen = now

    # UPDATED: clean close that other code can call
    def close(self, reason="shutdown"):
        self.disconnect_reason = reason
        self.alive = False
        self._hb_stop.set()
        self.tx.put(None)
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass

    # UPDATED: application-level heartbeat to keep NAT paths warm
    def _heartbeat_loop(self):
        if HEARTBEAT_INTERVAL <= 0:
            return
        try:
            hb = bytes.fromhex(HEARTBEAT_FRAME_HEX) if HEARTBEAT_FRAME_HEX else None
        except ValueError:
            logger.error("Invalid configured meter heartbeat; application heartbeat disabled")
            return
        if not hb or len(hb) < 12 or not hb.startswith(b"\xFE" * 4):
            logger.error("Configured meter heartbeat is not a complete DL/T645 frame; disabled")
            return
        first_68 = hb.find(b"\x68", 4)
        if first_68 < 0 or len(hb) <= first_68 + 9 or hb[first_68 + 7] != 0x68:
            logger.error("Configured meter heartbeat has invalid DL/T645 framing; disabled")
            return
        if len(hb) != 12 + hb[first_68 + 9]:
            logger.error("Configured meter heartbeat length is invalid; disabled")
            return
        while self.alive and not self._hb_stop.wait(HEARTBEAT_INTERVAL):
            # only ping if quiet for a while
            if time.time() - self.last_seen >= HEARTBEAT_INTERVAL * 0.5:
                logger.debug("ðŸ’“ HB â†’ %s", self.meter_number or self.peer)
                try:
                    self.enqueue_send(hb, expire_at=time.time() + HEARTBEAT_INTERVAL)
                except Exception:
                    # if we cannot enqueue, loop will likely exit soon anyway
                    pass

    def run(self):
        # Django request middleware does not run for management-command threads.
        # Explicitly bracket this long-lived worker so it never inherits or leaves
        # behind a thread-local database connection.
        close_old_connections()
        logger.info("TCP_CONNECTED peer=%s", self.peer)
        threading.Thread(
            target=self._heartbeat_loop,
            name=f"hb@{self.addr[0]}:{self.addr[1]}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._sender_loop,
            name=f"tx@{self.addr[0]}:{self.addr[1]}",
            daemon=True,
        ).start()
        try:
            while self.alive:
                # ---- Receive ----
                try:
                    chunk = self.conn.recv(4096)
                    if chunk == b"":  # peer closed (EOF)
                        self.disconnect_reason = "eof"
                        break
                except TimeoutError:
                    chunk = None
                except Exception as e:
                    if self.alive:
                        self.disconnect_reason = "recv_error"
                    logger.warning("TCP_RECV_ERROR peer=%s error=%s", self.peer, e)
                    break

                if chunk:
                    if self.debug:
                        logger.debug(
                            f"â¬‡ï¸ RAW CHUNK {self.addr} ({len(chunk)}B): {chunk.hex().upper()}"
                        )
                    self.buffer += chunk

                    # Guard against memory abuse
                    if len(self.buffer) > MAX_BUFFER_BYTES:
                        self.disconnect_reason = "buffer_cap"
                        logger.warning(
                            f"Buffer cap exceeded from {self.addr}; dropping connection"
                        )
                        break

                    # Frame slicer by L field
                    while True:
                        while self.buffer and self.buffer[0] == 0xFE:
                            self.buffer = self.buffer[1:]
                        if not self.buffer:
                            break

                        start = self.buffer.find(b"\x68")
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
                                f"ðŸ§± FRAME {self.addr} ({len(frame)}B): {frame.hex().upper()}"
                            )

                        if self.dump_raw:
                            try:
                                with open(self.dump_raw, "a", encoding="utf-8") as f:
                                    f.write(frame.hex().upper() + "\n")
                            except Exception as e:
                                logger.warning(f"Failed to write raw frame: {e}")

                        self.process_frame(frame)

                # UPDATED: removed idle-close block entirely.
                # We only exit on explicit peer close or I/O error.

                # recv() wakes on its 30-second timeout even when the meter is
                # quiet. This lets the handler retire connections older than
                # CONN_MAX_AGE without closing the persistent TCP meter session.
                close_old_connections()

        except Exception as e:
            self.disconnect_reason = (
                "db_error" if isinstance(e, DatabaseError) else "handler_error"
            )
            logger.exception(f"ClientHandler error for {self.addr}: {e}")
        finally:
            self._hb_stop.set()
            self.tx.put(None)
            try:
                self.conn.close()
            except Exception:
                pass
            self.alive = False
            if self.meter_number:
                _unregister_handler(self.meter_number, self)
            close_old_connections()
            connection.close()
            logger.info(
                "TCP_DISCONNECTED meter=%s peer=%s reason=%s",
                self.meter_number or "unknown",
                self.peer,
                self.disconnect_reason,
            )

    def process_frame(self, frame: bytes):
        # A persistent meter socket can outlive CONN_MAX_AGE by hours or days.
        # Treat each frame as one unit of DB work, including early returns and
        # exceptions, so the handler's thread-local connection stays bounded.
        #
        # Never retire/close a Django DB connection while it is inside an atomic
        # transaction. Django TestCase uses an outer atomic block, and closing the
        # connection there marks the transaction as needing rollback.
        if not connection.in_atomic_block:
            close_old_connections()

        try:
            self._process_frame(frame)

        except DatabaseError as exc:
            logger.exception(
                "DB_ERROR meter=%s peer=%s operation=frame_persistence error=%s",
                self.meter_number or "unknown",
                self.peer,
                exc,
            )

            # A failed DB connection must not kill the persistent meter TCP session.
            # Retire the failed connection so the next frame can open a clean one.
            # Do not forcibly close a connection owned by an outer atomic block.
            if not connection.in_atomic_block:
                connection.close()

        finally:
            if not connection.in_atomic_block:
                close_old_connections()

    def _process_frame(self, frame: bytes):

        start = frame.find(b"\x68")
        ok, cs_style = verify_checksum(frame, start)

        if not ok and not self.accept_bad:
            try:
                ctrl_idx = start + 8
                L = frame[start + 9]
                data_end = (start + 10) + L
                calc_c = sum(frame[ctrl_idx:data_end]) & 0xFF
                found = frame[data_end]
                logger.warning(
                    f"Checksum failed (calc=0x{calc_c:02X}, found=0x{found:02X}, L={L}, frame_len={len(frame)})"
                )
            except Exception:
                logger.warning("Checksum failed (unable to compute details)")
            return

        parsed = parse_frame(frame, accept_bad_checksum=self.accept_bad)
        if self.debug:
            logger.debug(f"ðŸ§© parse_frame -> {parsed}")
        if not parsed:
            return

        meter_number = parsed.get("meter_number", "")
        ctrl_code = parsed.get("control_code", 0)
        di = parsed.get("di")
        data = parsed.get("data")

        # Diagnostic replies are intercepted before handler registration or any
        # model lookup/update. parse_frame has already enforced checksum, address,
        # control and DI extraction; the caller performs the stricter value decode.
        if (
            di != "80808080"
            and ctrl_code in (0x91, 0xD1)
            and meter_number
            and _deliver_if_match(
                meter_number, di, ctrl_code, frame, consume_only=True
            ) == 2
        ):
            logger.info(
                "DIAGNOSTIC_RX_CONSUMED meter=%s di=%s frame=%s",
                meter_number,
                di or "none",
                frame.hex().upper(),
            )
            return

        if not di:
            logger.info(
                "RAW_UNPARSED_RX meter=%s peer=%s control_code=0x%02X len=%s frame=%s",
                meter_number or self.meter_number or "unknown",
                self.peer,
                ctrl_code,
                len(frame),
                frame.hex().upper(),
            )

        self.last_seen = time.time()

        # Remember/register meter number for this socket
        if meter_number:
            self.meter_number = meter_number
            _register_handler(meter_number, self)

        msg = f"ðŸ“¥ Meter {meter_number} DI={di} "
        msg += "(data parsed)" if data else "(no data)"
        if parsed.get("cs_style"):
            msg += f" [cs:{parsed.get('cs_style')}]"
        logger.info("%s - %s", timezone.localtime().isoformat(timespec="seconds"), msg)

        # Deliver to a waiting "send-and-wait" caller (but skip keepalives)
        if di != "80808080" and ctrl_code in (0x91, 0xD1, 0x83, 0x9C, 0xDC) and meter_number:
            matched_waiter = _deliver_if_match(
                meter_number, di, ctrl_code, frame
            )
            if not matched_waiter:
                acknowledge_late_prepaid_reply(meter_number, di, ctrl_code, frame)
            if matched_waiter and self.debug:
                logger.debug(f"ðŸ“¤ Delivered reply to waiter for meter {meter_number}")

            if (
                matched_waiter
                and matched_waiter != 2
                and not matched_waiter.get("persist_reply", True)
            ):
                logger.info(
                    "VALIDATED_NONPERSISTENT_REPLY meter=%s DI=%s",
                    meter_number,
                    di,
                )
                return

        if not data:
            return

        if not ok:
            return

        # Resolve meter for storage
        try:
            with transaction.atomic():
                meter = Meter.objects.get(meter_number=meter_number)
        except Meter.DoesNotExist:
            with transaction.atomic():
                um, created = UnknownMeter.objects.get_or_create(
                    meter_number=meter_number,
                    defaults={"last_raw_hex": frame.hex().upper()},
                )
                if not created:
                    um.seen_count += 1
                    um.last_raw_hex = frame.hex().upper()
                    um.status = "new"
                    um.last_seen = timezone.now()
                    um.save(
                        update_fields=[
                            "seen_count",
                            "last_raw_hex",
                            "status",
                            "last_seen",
                        ]
                    )
            logger.info(
                f"ðŸ†• Unknown meter discovered: {meter_number} (seen {um.seen_count}x)"
            )
            return

        # Keep every valid decoded frame in an append-only ledger.  The bulk
        # 028011FF layout varies by firmware, so its extended values are kept
        # as raw reported data and never gain billing/reconciliation authority.
        trust = (
            MeterRawFrame.TRUST_AUTHORITATIVE
            if di in DIRECT_REGISTER_SPECS
            else MeterRawFrame.TRUST_REPORTED_UNVERIFIED
        )
        frame_start = frame.find(b"\x68")
        frame_data_length = (
            frame[frame_start + 9]
            if frame_start >= 0 and len(frame) > frame_start + 9
            else 0
        )
        MeterRawFrame.objects.create(
            meter=meter,
            received_at=timezone.now(),
            source_ip=self.addr[0],
            source_port=self.addr[1],
            control_code=ctrl_code,
            data_identifier=di or "",
            data_length=frame_data_length,
            raw_frame_hex=frame.hex().upper(),
            checksum_style=parsed.get("cs_style") or "",
            decoded_data={key: str(value) for key, value in data.items()},
            trust_classification=trust,
        )

        # Live upsert.  Only a valid status word from the documented
        # 0x028011FF response may replace the last confirmed relay status.
        status_word = data.get("status_word") if di == "028011FF" else None
        relay_state = parse_authoritative_relay_state(status_word)
        if data.get("forward_active_energy_kwh") is not None:
            # ``total_energy`` remains the authoritative tenant-billing register.
            # Reverse energy is deliberately never netted into this value.
            data["total_energy"] = data["forward_active_energy_kwh"]
        elif di == "028011FF" and data.get("total_energy") is not None:
            if (
                meter_number in BIDIRECTIONAL_ENERGY_METERS
                or meter.reading_profile == Meter.READING_PROFILE_TOTAL_AND_PER_PHASE
                or meter.reverse_energy_capability == Meter.REVERSE_CAPABILITY_SUPPORTED
            ):
                # On a confirmed bidirectional meter the bulk register combines
                # forward and reverse energy, so direct registers remain authoritative.
                data.pop("total_energy", None)
            else:
                # Ordinary billing meters only report this cumulative bulk value.
                # Preserve the legacy billing value and expose it in the Energy column.
                data["forward_active_energy_kwh"] = data["total_energy"]

        live_field_names = {
            field.name for field in LiveReading._meta.concrete_fields
        } - {"id", "meter", "ts"}
        live_defaults = {
            key: value
            for key, value in data.items()
            if key in live_field_names and key != "status_word" and value is not None
        }
        live_defaults.update(source_ip=self.addr[0], source_port=self.addr[1])
        if relay_state is not None:
            live_defaults["status_word"] = status_word
        with transaction.atomic():
            live_reading, _live_created = LiveReading.objects.update_or_create(
                meter=meter, defaults=live_defaults
            )
        if (
            di == "00020000"
            and data.get("reverse_active_energy_kwh") is not None
            and meter.reverse_energy_capability != Meter.REVERSE_CAPABILITY_SUPPORTED
        ):
            meter.reverse_energy_capability = Meter.REVERSE_CAPABILITY_SUPPORTED
            meter.save(update_fields=["reverse_energy_capability"])
        if di == "028011FF" and data.get("balance") is not None:
            reconcile_prepaid_balance(meter, data.get("balance"))
        if relay_state is not None:
            sync_authoritative_relay_status(meter, status_word)

        # Historical snapshot on cadence
        now = timezone.now()
        last = meter.readings.order_by("-ts").first()
        take_snapshot = (not last) or (
            (now - last.ts).total_seconds() >= SNAPSHOT_MINUTES * 60
        )
        history_field_names = {
            field.name for field in MeterReading._meta.concrete_fields
        } - {"id", "meter", "ts"}
        history_values = {
            key: value
            for key, value in data.items()
            if key in history_field_names and value is not None
        }
        history_values.update(source_ip=self.addr[0], source_port=self.addr[1])
        if take_snapshot:
            with transaction.atomic():
                MeterReading.objects.create(
                    meter=meter,
                    ts=now,
                    **history_values,
                )
            logger.info(
                "%s âœ… Stored live reading for meter %s",
                timezone.localtime().isoformat(timespec="seconds"),
                meter_number,
            )
        elif di in DIRECT_REGISTER_SPECS and last is not None:
            # Direct polling returns one DI per frame. Merge the cycle into the
            # current cadence row so the two energy totals and phases correlate.
            for key, value in history_values.items():
                setattr(last, key, value)
            last.save(update_fields=list(history_values))
            logger.info(
                "Merged DI=%s into current snapshot for meter %s",
                di,
                meter_number,
            )
        else:
            logger.info(
                "%s âœ… Stored live reading for meter %s",
                timezone.localtime().isoformat(timespec="seconds"),
                meter_number,
            )

        # Bulk frames do not reliably contain the separate reverse register.
        # Queue confirmed direct forward/reverse reads once per snapshot period.
        if di == "028011FF" and meter_number in BIDIRECTIONAL_ENERGY_METERS:
            poll_after = now - datetime.timedelta(minutes=SNAPSHOT_MINUTES)
            if not MeterCommand.objects.filter(
                meter=meter, source="energy_auto_poll", created_at__gte=poll_after
            ).exists():
                cycle_key = now.strftime("%Y%m%d%H") + str(now.minute // SNAPSHOT_MINUTES)
                for register, label in (("00010000", "Forward active energy"), ("00020000", "Reverse active energy")):
                    MeterCommand.objects.get_or_create(
                        idempotency_key=f"energy-auto:{meter_number}:{register}:{cycle_key}",
                        defaults={
                            "meter": meter, "meter_number": meter_number,
                            "frame_hex": build_read_register(meter_number, register).hex().upper(),
                            "expect_di": register, "timeout": 12.0,
                            "command_type": "read", "source": "energy_auto_poll",
                            "priority": 70, "max_attempts": 2,
                            "reason": f"Automatic {label} collection",
                            "initiated_by": "meter_listener",
                        },
                    )

        # Credit-control observation is intentionally fail-open and occurs only
        # after normal live/history persistence has completed.  It only debounces
        # a DB evaluation request; no accounting, WhatsApp, or relay work happens
        # in the parser/listener path.
        try:
            from smart_meter.services.credit_control import request_credit_evaluation

            request_credit_evaluation(meter, live_reading)

        except Exception as exc:
            logger.warning(
                "credit_evaluation_enqueue_failed meter=%s error=%s",
                meter_number,
                exc,
            )


# =========================
# Django management command
# =========================


class DbCommandPoller(threading.Thread):
    """Durable command worker using the existing active socket registry.

    Offline meters remain queued. Automatic relay commands are revalidated
    immediately before transmission. Reading storage does not depend on this worker.
    """

    daemon = True

    def __init__(self, interval=0.3, debug=False):
        super().__init__()
        self.interval = interval
        self.debug = debug
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        logger.info("ðŸ—‚ï¸  DB command poller started")
        while not self._stop.is_set():
            try:
                close_old_connections()
                now = timezone.now()
                ambiguous_money = list(MeterCommand.objects.filter(
                    status__in=("new", "pending", "retry", "waiting_online"),
                    command_type__in=MONEY_COMMAND_TYPES,
                    attempt_count__gt=0,
                ))
                for command in ambiguous_money:
                    mark_prepaid_uncertain(
                        command, "legacy retry suppressed before redispatch"
                    )
                with transaction.atomic():
                    qs = (
                        MeterCommand.objects.select_for_update(skip_locked=True)
                        .filter(
                            status__in=("new", "pending", "retry", "waiting_online")
                        )
                        .filter(Q(not_before__isnull=True) | Q(not_before__lte=now))
                        .filter(
                            Q(next_attempt_at__isnull=True)
                            | Q(next_attempt_at__lte=now)
                        )
                        .order_by("priority", "created_at")[:5]
                    )
                    cmds = list(qs)
                    for cmd in cmds:
                        if cmd.expires_at and cmd.expires_at <= now:
                            cmd.status = "expired"
                            cmd.error = "command expired before dispatch"
                            cmd.save(update_fields=["status", "error", "updated_at"])
                            if is_prepaid_money_command(cmd):
                                mark_prepaid_definitive_failure(cmd, cmd.error)
                        else:
                            cmd.status = "claimed"
                            cmd.save(update_fields=["status", "updated_at"])
                for cmd in cmds:
                    if cmd.status == "claimed":
                        meter_no = (cmd.meter_number or "").strip()
                        if meter_no:
                            with _meter_request_lock(meter_no):
                                self._process_command(cmd)
                        else:
                            self._process_command(cmd)
            except Exception as e:
                logger.warning("DbCommandPoller loop error: %s", e)
            finally:
                close_old_connections()
                self._stop.wait(self.interval)

    def _process_command(self, cmd: MeterCommand):
        is_money = is_prepaid_money_command(cmd)
        enqueued = False
        try:
            meter_no = (cmd.meter_number or "").strip()
            if not meter_no:
                self._fail(cmd, "meter_number missing")
                return

            result = revalidate_command(cmd)
            if not result.allowed:
                self._cancel(cmd, result.reason)
                return

            h = _get_handler(meter_no)
            if not h:
                self._wait_online(cmd, f"meter {meter_no} not connected")
                return

            is_relay = getattr(cmd, "command_type", "") == "relay" and getattr(
                cmd, "desired_state", ""
            ) in {"on", "off"}
            waiter = None
            if is_relay:
                waiter = queue.Queue()
                _push_waiter(meter_no, waiter, None, expect_controls={0x9C, 0xDC})
            elif (cmd.expect_di or "").strip():
                waiter = queue.Queue()
                _push_waiter(
                    meter_no,
                    waiter,
                    cmd.expect_di,
                    expect_controls={0x83} if is_money else {0x91, 0xD1},
                    persist_reply=getattr(cmd, "source", "") != "energy_probe",
                    accept_negative_without_di=not is_money,
                )
            try:
                frame = bytes.fromhex(cmd.frame_hex.strip())
            except Exception:
                self._fail(cmd, "invalid frame hex")
                return

            ttl = float(cmd.timeout or 12.0)
            now = timezone.now()
            MeterCommand.objects.filter(pk=cmd.pk).update(
                attempt_count=cmd.attempt_count + 1,
                last_attempt_at=now,
                error="",
            )
            transport_q = queue.Queue(maxsize=1)
            h.enqueue_send(
                frame,
                expire_at=time.time() + ttl,
                transport_q=transport_q,
            )
            enqueued = True

            try:
                transport_ok, transport_error = transport_q.get(timeout=ttl)
            except queue.Empty:
                if waiter is not None:
                    _remove_waiter(meter_no, waiter)
                self._retry_or_fail(cmd, "timeout waiting for socket transmission")
                return

            if not transport_ok:
                if waiter is not None:
                    _remove_waiter(meter_no, waiter)
                self._retry_or_fail(
                    cmd, transport_error or "socket transmission failed"
                )
                return

            if is_relay:
                try:
                    reply = waiter.get(timeout=ttl)
                except queue.Empty:
                    _remove_waiter(meter_no, waiter)
                    self._retry_or_fail(cmd, "timeout waiting for relay acknowledgement")
                    return

                parsed_ack = parse_frame(reply)
                ack_result = classify_relay_ack(parsed_ack, meter_no)
                if ack_result is None:
                    self._retry_or_fail(cmd, "invalid or mismatched relay acknowledgement")
                    return
                if ack_result == "failed":
                    self._fail(cmd, "meter returned negative relay acknowledgement 0xDC")
                    return

                self._ack(cmd, reply.hex().upper())
                self._verify_relay(cmd, h, ttl)
            elif (cmd.expect_di or "").strip():
                if is_money:
                    MeterCommand.objects.filter(pk=cmd.pk).update(
                        status="sent", next_attempt_at=None
                    )
                try:
                    reply = waiter.get(timeout=ttl)
                    self._ack(cmd, reply.hex().upper())
                    if is_money:
                        self._verify_prepaid_balance(cmd, h, ttl)
                except queue.Empty:
                    _remove_waiter(meter_no, waiter)
                    self._retry_or_fail(cmd, "timeout waiting for reply")
            else:
                # Transport acknowledgement means sendall() completed on the live
                # socket. Non-relay compatibility commands do not request a reply.
                self._ack(cmd, "")
        except Exception as e:
            if is_money and not enqueued:
                self._fail(cmd, str(e))
            else:
                self._retry_or_fail(cmd, str(e))

    def _verify_relay(self, cmd, handler, ttl):
        meter_no = (cmd.meter_number or "").strip()
        status_frame = build_read_028011FF(meter_no)
        status_hex = status_frame.hex().upper()
        waiter = queue.Queue()
        _push_waiter(
            meter_no,
            waiter,
            "028011FF",
            expect_controls={0x91, 0x83},
        )
        MeterCommand.objects.filter(pk=cmd.pk).update(status_query_hex=status_hex)

        transport_q = queue.Queue(maxsize=1)
        handler.enqueue_send(
            status_frame,
            expire_at=time.time() + ttl,
            transport_q=transport_q,
        )
        try:
            transport_ok, transport_error = transport_q.get(timeout=ttl)
        except queue.Empty:
            _remove_waiter(meter_no, waiter)
            self._verification_unconfirmed(cmd, "status query socket transmission timed out")
            return
        if not transport_ok:
            _remove_waiter(meter_no, waiter)
            self._verification_unconfirmed(
                cmd, transport_error or "status query socket transmission failed"
            )
            return

        try:
            reply = waiter.get(timeout=ttl)
        except queue.Empty:
            _remove_waiter(meter_no, waiter)
            self._verification_unconfirmed(cmd, "relay-status verification timed out")
            return

        parsed = parse_frame(reply)
        if (
            not parsed
            or parsed.get("meter_number") != meter_no
            or parsed.get("di") != "028011FF"
        ):
            self._verification_unconfirmed(cmd, "relay-status response did not match command meter/DI")
            return
        status_word = (parsed.get("data") or {}).get("status_word")
        meter = Meter.objects.filter(pk=cmd.meter_id, meter_number=meter_no).first()
        if meter is None:
            self._verification_unconfirmed(cmd, "relay-status command meter no longer exists")
            return
        relay_state = sync_authoritative_relay_status(
            meter,
            status_word,
            command=cmd,
            status_reply_hex=reply.hex().upper(),
        )
        if relay_state is None:
            self._verification_unconfirmed(cmd, "fresh status response had no relay state")

    def _verify_prepaid_balance(self, cmd, handler, ttl):
        """Fetch one authoritative balance after a prepaid C=83 acknowledgement."""
        meter_no = (cmd.meter_number or "").strip()
        status_frame = build_read_028011FF(meter_no)
        status_hex = status_frame.hex().upper()
        waiter = queue.Queue()
        _push_waiter(
            meter_no,
            waiter,
            "028011FF",
            expect_controls={0x91, 0x83},
        )
        MeterCommand.objects.filter(pk=cmd.pk).update(status_query_hex=status_hex)

        transport_q = queue.Queue(maxsize=1)
        handler.enqueue_send(
            status_frame,
            expire_at=time.time() + ttl,
            transport_q=transport_q,
        )
        try:
            transport_ok, transport_error = transport_q.get(timeout=ttl)
        except queue.Empty:
            _remove_waiter(meter_no, waiter)
            mark_prepaid_reconciliation_uncertain(
                cmd, "028011FF query socket transmission timed out"
            )
            return
        if not transport_ok:
            _remove_waiter(meter_no, waiter)
            mark_prepaid_reconciliation_uncertain(
                cmd, transport_error or "028011FF query socket transmission failed"
            )
            return

        try:
            reply = waiter.get(timeout=ttl)
        except queue.Empty:
            _remove_waiter(meter_no, waiter)
            mark_prepaid_reconciliation_uncertain(
                cmd, "028011FF balance verification timed out"
            )
            return

        parsed = parse_frame(reply)
        data = (parsed or {}).get("data") or {}
        if (
            not parsed
            or parsed.get("meter_number") != meter_no
            or parsed.get("di") != "028011FF"
            or data.get("balance") is None
        ):
            mark_prepaid_reconciliation_uncertain(
                cmd, "fresh response did not contain the command meter's 028011FF balance"
            )
            return
        meter = Meter.objects.filter(pk=cmd.meter_id, meter_number=meter_no).first()
        if meter is None:
            mark_prepaid_reconciliation_uncertain(
                cmd, "command meter no longer exists"
            )
            return
        reconcile_prepaid_balance(meter, data["balance"])

    def _verification_unconfirmed(self, cmd, reason):
        MeterCommand.objects.filter(pk=cmd.pk).update(
            status="acknowledged",
            error=f"acknowledged but not verified: {reason}",
        )

    def _ack(self, cmd, reply_hex):
        with transaction.atomic():
            cmd = MeterCommand.objects.select_for_update().get(pk=cmd.pk)
            if cmd.status == "cancelled":
                return
            cmd.status = "acknowledged" if cmd.status not in ("ok",) else cmd.status
            cmd.reply_hex = reply_hex or ""
            cmd.raw_ack_hex = reply_hex or ""
            cmd.acknowledged_at = timezone.now()
            cmd.error = ""
            cmd.save(
                update_fields=[
                    "status",
                    "reply_hex",
                    "raw_ack_hex",
                    "acknowledged_at",
                    "error",
                    "updated_at",
                ]
            )
            if is_prepaid_money_command(cmd):
                mark_prepaid_acknowledged(cmd, reply_hex or "")

    def _wait_online(self, cmd, msg):
        with transaction.atomic():
            cmd = MeterCommand.objects.select_for_update().get(pk=cmd.pk)
            cmd.status = "waiting_online"
            cmd.error = msg
            cmd.next_attempt_at = timezone.now() + datetime.timedelta(seconds=10)
            cmd.save(update_fields=["status", "error", "next_attempt_at", "updated_at"])

    def _retry_or_fail(self, cmd, msg):
        if is_prepaid_money_command(cmd):
            mark_prepaid_uncertain(cmd, msg)
            return
        with transaction.atomic():
            cmd = MeterCommand.objects.select_for_update().get(pk=cmd.pk)
            if cmd.status == "cancelled":
                return
            if cmd.attempt_count >= cmd.max_attempts:
                cmd.status = "failed"
                cmd.error = msg
                cmd.next_attempt_at = None
            else:
                cmd.status = "retry"
                cmd.error = msg
                delay = min(300, 2 ** max(1, cmd.attempt_count))
                cmd.next_attempt_at = timezone.now() + datetime.timedelta(seconds=delay)
            cmd.save(update_fields=["status", "error", "next_attempt_at", "updated_at"])

    def _cancel(self, cmd, msg):
        with transaction.atomic():
            cmd = MeterCommand.objects.select_for_update().get(pk=cmd.pk)
            cmd.status = "cancelled"
            cmd.cancelled_at = timezone.now()
            cmd.cancelled_reason = msg[:255]
            cmd.error = ""
            cmd.save(
                update_fields=[
                    "status",
                    "cancelled_at",
                    "cancelled_reason",
                    "error",
                    "updated_at",
                ]
            )

    def _fail(self, cmd, msg, reply_hex=""):
        with transaction.atomic():
            cmd = MeterCommand.objects.select_for_update().get(pk=cmd.pk)
            if cmd.status == "cancelled":
                return
            cmd.status = "failed"
            cmd.error = msg
            cmd.raw_ack_hex = reply_hex or ""
            cmd.reply_hex = reply_hex or ""
            cmd.save(
                update_fields=[
                    "status", "error", "raw_ack_hex", "reply_hex", "updated_at"
                ]
            )
            if is_prepaid_money_command(cmd):
                mark_prepaid_definitive_failure(cmd, msg, reply_hex or "")


class MeterTimingSchedulePoller(threading.Thread):
    """Evaluate recurring meter operating windows without requiring cron."""

    def __init__(self, interval=30.0):
        super().__init__(name="meter-timing-schedule-poller", daemon=True)
        self.interval = interval
        self._stop = threading.Event()

    def run(self):
        logger.info("Meter timing schedule poller started")
        while not self._stop.is_set():
            try:
                close_old_connections()
                from smart_meter.services.timing_schedule import enforce_all_timing_schedules
                queued = enforce_all_timing_schedules()
                if queued:
                    logger.info("Meter timing scheduler queued %s command(s)", len(queued))
            except Exception:
                logger.exception("Meter timing schedule poller error")
            finally:
                close_old_connections()
                self._stop.wait(self.interval)


class Command(BaseCommand):
    help = "Start DL/T 645 listener; store live readings; provide a local control port to send commands."

    def add_arguments(self, parser):
        parser.add_argument(
            "--host", default=HOST, help="Bind address for incoming meter connections"
        )
        parser.add_argument(
            "--port",
            type=int,
            default=PORT,
            help="TCP port for incoming meter connections",
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Enable verbose hex logging of chunks and frames.",
        )
        parser.add_argument(
            "--dump-raw",
            dest="dump_raw",
            default=None,
            help="Path to append raw frames as hex.",
        )
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
            logger.debug("ðŸ”§ Debug logging enabled")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            s.listen(50)

            # Bind the production meter port before starting any side threads or
            # touching the diagnostic socket. A mistakenly launched second
            # listener therefore fails without disrupting the running listener.
            DbCommandPoller(debug=debug).start()
            MeterTimingSchedulePoller().start()
            start_diagnostic_server()
            logger.info("âœ… Listening on %s:%s for DL/T 645 frames...", host, port)

            while True:
                conn, addr = s.accept()
                # keepalives at accept-time too (belt + suspenders)
                _enable_tcp_keepalive(conn)
                ClientHandler(
                    conn, addr, debug=debug, dump_raw=dump_raw, accept_bad=accept_bad
                ).start()

