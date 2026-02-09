# smart_meter/management/commands/send_prepaid_frame.py
from smart_meter.vendor.prepaid import DLT645_2007_Prepaid
from django.core.management.base import BaseCommand
from django.conf import settings
from decimal import Decimal
import socket
import time
import logging

logger = logging.getLogger(__name__)

try:
    from smart_meter.utils.control_client import send_via_listener
except Exception:
    send_via_listener = None


def _full_param_block(price: Decimal, mirror: bool = True) -> dict:
    p = float(Decimal(price).quantize(Decimal("0.0001")))
    params = {
        "rate_switch_time": 0, "step_switch_time": 0, "timezone_switch_time": 0, "schedule_switch_time": 0,
        "timezone_count": 0, "schedule_count": 0, "time_period_count": 0, "rate_count": 1, "step_count": 0,
        "voltage_ratio": 1, "current_ratio": 1,
        "alarm_amount_1": 0, "alarm_amount_2": 0, "overdraft_limit": 0, "area_amount_limit": 0, "contract_amount_limit": 0,
        "max_load_power_limit": 0, "load_power_delay": 0,
        "rate1_price_1": p, "rate1_price_2": 0.0000, "rate1_price_3": 0.0000, "rate1_price_4": 0.0000,
        "rate2_price_1": 0.0000, "rate2_price_2": 0.0000, "rate2_price_3": 0.0000, "rate2_price_4": 0.0000,
        "step1_value_1": 0, "step1_value_2": 0, "step1_value_3": 0,
        "step1_price_1": 0.0000, "step1_price_2": 0.0000, "step1_price_3": 0.0000, "step1_price_4": 0.0000,
        "step2_value_1": 0, "step2_value_2": 0, "step2_value_3": 0,
        "step2_price_1": 0.0000, "step2_price_2": 0.0000, "step2_price_3": 0.0000, "step2_price_4": 0.0000,
        "step3_value_1": 0, "step3_value_2": 0, "step3_value_3": 0,
        "step3_price_1": 0.0000, "step3_price_2": 0.0000, "step3_price_3": 0.0000, "step3_price_4": 0.0000,
    }
    if mirror:
        params["rate1_price_2"] = p
        params["rate1_price_3"] = p
        params["rate1_price_4"] = p
    return params


class Command(BaseCommand):
    help = "Send a prepaid parameter frame to a meter and print result."

    def add_arguments(self, parser):
        parser.add_argument("meter_number", help="12-digit meter number")
        parser.add_argument("--price", type=Decimal, default=Decimal("3.9990"))
        parser.add_argument("--no-mirror", action="store_true")
        parser.add_argument("--timeout", type=float, default=20.0)

    def handle(self, *args, **opts):
        meter_number = opts["meter_number"]
        price = opts["price"]
        mirror = not opts["no_mirror"]
        timeout = float(opts["timeout"])

        params = _full_param_block(price, mirror=mirror)
        prepaid = DLT645_2007_Prepaid()
        frame = prepaid.build_frame(meter_number, params)
        frame_hex = frame.hex().upper()

        host = getattr(settings, "CONTROL_LISTENER_HOST", None)
        port = getattr(settings, "CONTROL_LISTENER_PORT", None)

        self.stdout.write(self.style.WARNING(f"Listener: {host}:{port}"))
        self.stdout.write(self.style.SUCCESS(f"Frame HEX:\n{frame_hex}"))

        t0 = time.time()
        try:
            if send_via_listener:
                res = send_via_listener(meter_number, frame, timeout=timeout)
            else:
                # Fallback: raw TCP push directly to the listener
                if not host or not port:
                    raise RuntimeError(
                        "No send_via_listener and CONTROL_LISTENER_HOST/PORT not set.")
                with socket.create_connection((host, int(port)), timeout=timeout) as s:
                    s.sendall(frame)
                    s.settimeout(timeout)
                    # read up to 1024 bytes for a reply (adjust if your listener sends more)
                    reply = s.recv(1024)
                res = {"ok": bool(reply), "reply_hex": reply.hex().upper()}
        except Exception as e:
            res = {"ok": False, "error": str(e)}

        dt = (time.time() - t0) * 1000.0
        self.stdout.write(self.style.SUCCESS(f"Result in {dt:.0f} ms: {res}"))
