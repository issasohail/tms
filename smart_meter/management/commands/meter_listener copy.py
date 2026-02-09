# smart_meter/management/commands/meter_listener.py
import logging
import socket
import threading
from django.utils import timezone
from django.core.management.base import BaseCommand

from smart_meter.dlt645 import parse_frame, verify_checksum
from smart_meter.models import Meter, LiveReading, MeterReading
from smart_meter.models import Meter, UnknownMeter

logger = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 6000
SNAPSHOT_MINUTES = 15  # store historical snapshot at this cadence


class ClientHandler(threading.Thread):
    def __init__(self, conn: socket.socket, addr, debug=False, dump_raw=None, accept_bad=False):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
        self.buffer = b""
        self.debug = debug
        self.dump_raw = dump_raw
        self.accept_bad = accept_bad

    def run(self):
        logger.info(f"📡 Connection from {self.addr}")
        try:
            while True:
                chunk = self.conn.recv(4096)
                if not chunk:
                    break

                if self.debug:
                    logger.debug(
                        f"⬇️ RAW CHUNK {self.addr} ({len(chunk)}B): {chunk.hex().upper()}")

                self.buffer += chunk

                # Frame slicer by L field
                while True:
                    # drop leading FE bytes
                    while self.buffer and self.buffer[0] == 0xFE:
                        self.buffer = self.buffer[1:]
                    if not self.buffer:
                        break

                    # align to first 0x68
                    start = self.buffer.find(b'\x68')
                    if start == -1:
                        self.buffer = b""
                        break
                    if start > 0:
                        self.buffer = self.buffer[start:]
                        start = 0

                    # need at least header to read L
                    if len(self.buffer) < 10:
                        break

                    # second 0x68 must be at index 7 for a valid frame
                    if self.buffer[7] != 0x68:
                        # drift, resync
                        self.buffer = self.buffer[1:]
                        continue

                    L = self.buffer[9]
                    total_len = 12 + L  # 68 addr6 68 C L DATA(L) CS 16

                    if len(self.buffer) < total_len:
                        break  # wait more

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
                            logger.warning(f"Failed to write raw frame: {e}")

                    self.process_frame(frame)

        except Exception as e:
            logger.exception(f"ClientHandler error for {self.addr}: {e}")
        finally:
            try:
                self.conn.close()
            except Exception:
                pass
            logger.info(f"🔌 Connection closed {self.addr}")

    def process_frame(self, frame: bytes):
        start = frame.find(b'\x68')
        ok, cs_style = verify_checksum(frame, start)
        if not ok and not self.accept_bad:
            # detail for debugging
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
        di = parsed.get("di")
        data = parsed.get("data")
        msg = f"📥 Meter {meter_number} DI={di} "
        msg += "(data parsed)" if data else "(no data)"
        if parsed.get("cs_style"):
            msg += f" [cs:{parsed.get('cs_style')}]"
        logger.info(msg)

        # keepalives or unknown DI
        if not data:
            return

        if not ok:
            # accept_bad_checksum was True; we logged for visibility but won't store
            return

        # Resolve meter
        try:
            meter = Meter.objects.get(meter_number=meter_number)
        except Meter.DoesNotExist:
            um, created = UnknownMeter.objects.get_or_create(
                meter_number=meter_number,
                defaults={"last_raw_hex": frame.hex().upper()}
            )
            if not created:
                um.seen_count += 1
                um.last_raw_hex = frame.hex().upper()
                um.status = "new"   # ensure it shows as pending again
                um.save(update_fields=["seen_count",
                        "last_raw_hex", "status", "last_seen"])
            logger.info(
                f"🆕 Unknown meter discovered: {meter_number} (seen {um.seen_count}x)")

            # Don’t error — just skip saving MeterReading
            return False  # skip saving readings until approved

        # Live upsert (includes extended counters)
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
        LiveReading.objects.update_or_create(
            meter=meter, defaults=live_defaults)

        # Historical snapshot on cadence
        now = timezone.now()
        last = meter.readings.order_by('-ts').first()
        take_snapshot = (not last) or (
            (now - last.ts).total_seconds() >= SNAPSHOT_MINUTES * 60)
        if take_snapshot:
            MeterReading.objects.create(
                meter=meter,
                ts=now,
                total_energy=data.get('total_energy'),
                peak_total_energy=data.get('peak_total_energy'),
                valley_total_consumption=data.get('valley_total_consumption'),
                flat_total_consumption=data.get('flat_total_consumption'),
                total_power=data.get('total_power'),
                pf_total=data.get('pf_total'),
                voltage_a=data.get('voltage_a'),
                voltage_b=data.get('voltage_b'),
                voltage_c=data.get('voltage_c'),
                current_a=data.get('current_a'),
                current_b=data.get('current_b'),
                current_c=data.get('current_c'),
            )
            logger.info(
                f"✅ Stored live reading for meter {meter_number} + snapshot")
        else:
            logger.info(f"✅ Stored live reading for meter {meter_number}")


class Command(BaseCommand):
    help = "Start DL/T 645 listener and store live meter readings."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=HOST)
        parser.add_argument("--port", type=int, default=PORT)
        parser.add_argument("--debug", action="store_true",
                            help="Enable verbose hex logging of chunks and frames.")
        parser.add_argument("--dump-raw", dest="dump_raw", default=None,
                            help="Path to append raw frames as hex (one per line).")
        parser.add_argument("--accept-bad-checksum", dest="accept_bad_checksum",
                            action="store_true",
                            help="Parse/log frames even when checksum fails (debug only; values not stored).")

    def handle(self, *args, **opts):
        host = opts.get("host", HOST)
        port = opts.get("port", PORT)
        debug = opts.get("debug", False)
        dump_raw = opts.get("dump_raw")
        accept_bad = opts.get("accept_bad_checksum", False)

        if debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("🔧 Debug logging enabled")

        logger.info(f"✅ Listening on {host}:{port} for DL/T 645 frames...")
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
