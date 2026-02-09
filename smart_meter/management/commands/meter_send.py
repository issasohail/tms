# smart_meter/management/commands/meter_send.py
import socket
import json
import logging
from django.core.management.base import BaseCommand

from smart_meter.dlt645 import (
    build_topup_frame,
    build_init_amount_frame,
    build_refund_frame,
    build_read_price_param_frame,  # DI 070104FF
    build_read_frame_for_di,       # generic DI reader
    parse_frame,
    verify_checksum,
)

logger = logging.getLogger(__name__)


def call_control(host: str, port: int, payload: dict, timeout: float):
    """Send one JSON request to the local control server and return parsed JSON."""
    body = json.dumps(payload).encode("utf-8")
    with socket.create_connection((host, port), timeout=10) as s:
        # ensure client waits longer than server's waiter
        s.settimeout(timeout + 5.0)
        s.sendall(body)
        s.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    text = data.decode("utf-8").strip()
    return json.loads(text) if text else {"ok": False, "error": "empty response"}


class Command(BaseCommand):
    help = "Send DL/T645 command via the local control server (127.0.0.1:7000) or a raw gateway."

    def add_arguments(self, p):
        p.add_argument("--host", required=True,
                       help="Control server or gateway IP")
        p.add_argument("--port", type=int, required=True,
                       help="Control server or gateway port")
        p.add_argument(
            "--meter", help="Meter number like 241203510008 (12 hex chars)")
        p.add_argument(
            "--op",
            choices=["topup", "init", "refund", "readprice", "readdi", "list"],
            default="topup",
            help="Operation to perform",
        )
        p.add_argument("--amount", type=float, default=0.0,
                       help="Amount for topup/init/refund")
        p.add_argument(
            "--order",
            default="0000000000000000",
            help="Order number as 16 hex chars (8 bytes) for topup/refund",
        )
        p.add_argument("--timeout", type=float, default=25.0,
                       help="Seconds to wait for a meter reply")
        p.add_argument("--wakeup", action="store_true",
                       help="Prefix FE FE FE FE before the frame")
        p.add_argument(
            "--di",
            default="028011FF",
            help="DI hex for --op readdi (e.g., 028011FF for summary, 070104FF for price params)",
        )

    def handle(self, *a, **o):
        host = o["host"]
        port = o["port"]
        timeout = float(o["timeout"])
        op = o["op"]

        # Control op: list connected meters (no frame required)
        if op == "list":
            resp = call_control(host, port, {"op": "list"}, timeout=timeout)
            if not resp.get("ok"):
                raise SystemExit(f"Control error: {resp.get('error')}")
            meters = resp.get("meters", [])
            if not meters:
                self.stdout.write("No meters connected.")
            else:
                self.stdout.write("Connected meters:")
                for m in meters:
                    self.stdout.write(
                        f"  {m['meter']}  @ {m['peer']}  last_seen={m['last_seen']:.0f}")
            return

        # For other ops we need a meter and will build a frame
        meter = o.get("meter")
        if not meter:
            raise SystemExit("--meter is required for this operation")

        amount = o["amount"]
        order_hex = o["order"]

        # Build request frame for the selected op
        if op == "topup":
            frame = build_topup_frame(meter, amount, bytes.fromhex(order_hex))
            expect_di = None  # reply is typically 0x83 with echo
        elif op == "init":
            frame = build_init_amount_frame(meter, amount)
            expect_di = None
        elif op == "refund":
            frame = build_refund_frame(meter, amount, bytes.fromhex(order_hex))
            expect_di = None
        elif op == "readprice":
            frame = build_read_price_param_frame(meter)  # DI 070104FF as read
            expect_di = "070104FF"
        elif op == "readdi":
            di_hex = o["di"]
            frame = build_read_frame_for_di(meter, di_hex)
            expect_di = di_hex.upper()
        else:
            raise SystemExit(f"Unsupported op: {op}")

        if o["wakeup"]:
            frame = (b"\xFE" * 4) + frame

        frame_hex = frame.hex().upper()
        self.stdout.write(f"Request frame: {frame_hex}")

        # Use our local control server if targeting 127.0.0.1:7000
        if host == "127.0.0.1" and port == 7000:
            payload = {
                "op": "send",
                "meter": meter,
                "frame": frame_hex,
                "timeout": timeout,
                "expect_di": expect_di,
            }
            resp = call_control(host, port, payload, timeout=timeout)
            if not resp.get("ok"):
                raise SystemExit(f"Control error: {resp.get('error')}")
            reply_hex = resp["reply"]
            self.stdout.write(f"Reply frame:   {reply_hex}")
            reply = bytes.fromhex(reply_hex)
        else:
            # Raw gateway path
            with socket.create_connection((host, port), timeout=10) as s:
                s.settimeout(timeout + 5.0)
                s.sendall(frame)
                reply = s.recv(65535)
            self.stdout.write(f"Reply frame:   {reply.hex().upper()}")

        # Verify + parse generic header so you can see DI and meter number
        start = reply.find(b'\x68')
        ok, style = verify_checksum(reply, start)
        parsed = parse_frame(reply, accept_bad_checksum=True)
        self.stdout.write(self.style.SUCCESS(f"Checksum {style}, ok={ok}"))
        self.stdout.write(f"Parsed header: {parsed}")
