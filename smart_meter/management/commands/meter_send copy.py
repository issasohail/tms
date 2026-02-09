# smart_meter/management/commands/meter_send.py
import socket
import logging
from django.core.management.base import BaseCommand

from smart_meter.dlt645 import (
    build_topup_frame,
    build_init_amount_frame,
    build_refund_frame,
    build_read_price_param_frame,   # NEW
    parse_070104ff_prices,          # NEW (optional convenience)
    parse_frame,
    verify_checksum,
)

logger = logging.getLogger(__name__)


def recv_frame(sock: socket.socket, timeout: float = 15.0, max_bytes: int = 65536) -> bytes:
    """
    Read until we see the DL/T645 terminator 0x16 after at least a header,
    or we hit timeout / size cap.
    """
    sock.settimeout(timeout)
    buf = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
        # crude: stop if we’ve seen at least one 0x68 and a trailing 0x16
        if b'\x68' in buf and buf[-1:] == b'\x16' and len(buf) >= 12:
            break
        if len(buf) >= max_bytes:
            break
    return bytes(buf)


class Command(BaseCommand):
    help = "Send a DL/T645 prepaid command (topup/init/refund/readprice) and print the parsed reply."

    def add_arguments(self, p):
        p.add_argument("--host", required=True, help="Gateway/concentrator IP")
        p.add_argument("--port", type=int, required=True,
                       help="Gateway/concentrator port")
        p.add_argument("--meter", required=True,
                       help="Meter number like 250619510016")
        p.add_argument(
            "--op",
            choices=["topup", "init", "refund",
                     "readprice"],  # <- added readprice
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

    def handle(self, *args, **o):
        host = o["host"]
        port = o["port"]
        meter = o["meter"]
        op = o["op"]
        amount = o["amount"]
        orderh = o["order"]

        # Build the request frame
        if op == "topup":
            order = bytes.fromhex(orderh)
            frame = build_topup_frame(meter, amount, order)
        elif op == "init":
            frame = build_init_amount_frame(meter, amount)
        elif op == "refund":
            order = bytes.fromhex(orderh)
            frame = build_refund_frame(meter, amount, order)
        else:  # readprice
            frame = build_read_price_param_frame(meter)

        self.stdout.write(f"Request frame: {frame.hex().upper()}")

        # Send & receive
        with socket.create_connection((host, port), timeout=10) as s:
            s.sendall(frame)
            reply = recv_frame(s, timeout=15.0)

        self.stdout.write(f"Reply frame:   {reply.hex().upper()}")

        # Verify + parse (generic)
        start = reply.find(b'\x68')
        ok, style = verify_checksum(reply, start)
        parsed = parse_frame(reply, accept_bad_checksum=True)
        self.stdout.write(self.style.SUCCESS(f"Checksum {style}, ok={ok}"))
        self.stdout.write(f"Parsed header: {parsed}")

        # Extra decode for readprice
        if op == "readprice":
            prices = parse_070104ff_prices(reply)
            self.stdout.write(self.style.SUCCESS(f"Prices decoded: {prices}"))
