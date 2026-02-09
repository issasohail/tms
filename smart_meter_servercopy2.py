import os
import sys
import socket
import django
from datetime import datetime
from django.utils.timezone import now

# Setup Django environment
sys.path.append(os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tms.settings")  # Update if different
django.setup()

from smart_meter.models import MeterReading, Unit

HOST = '0.0.0.0'
PORT = 6000
reading_buffer = {}


def extract_meter_number(frame):
    """Extracts and formats the 6-byte meter number."""
    if len(frame) < 12:
        return None
    addr = frame[5:11]
    return ''.join(f"{b:02X}" for b in reversed(addr))


def decode_bytes(data_bytes):
    """Decodes BCD with 0x33 offset reversed."""
    return [b - 0x33 for b in data_bytes]


def decode_value(decoded, scale=100):
    """Converts a list of decoded bytes to float."""
    try:
        val = ''.join(f"{b:02X}" for b in decoded[::-1])
        return round(float(val) / scale, 2)
    except Exception:
        return None


def parse_diagnostic_frame(frame):
    """Parses long diagnostic frame into individual readings."""
    try:
        start = frame.find(b'\x68')
        if start == -1 or frame[start + 8] != 0x91:
            raise ValueError("Invalid or unsupported control code")

        voltage_raw = frame[start + 14:start + 18]
        current_raw = frame[start + 26:start + 30]
        power_raw = frame[start + 38:start + 42]
        freq_raw = frame[start + 50:start + 52]
        pf_raw = frame[start + 54:start + 56]

        return {
            "voltage": decode_value(decode_bytes(voltage_raw)),
            "current": decode_value(decode_bytes(current_raw)),
            "power": decode_value(decode_bytes(power_raw)),
            "frequency": decode_value(decode_bytes(freq_raw)),
            "power_factor": decode_value(decode_bytes(pf_raw))
        }
    except Exception as e:
        print(f"❌ Diagnostic parse failed: {e}")
        return None


def start_server():
    print(f"✅ Listening on {HOST}:{PORT} for smart meter data...")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()

        while True:
            print("🟢 Waiting for connection...")
            conn, addr = s.accept()
            print(f"\n🔌 Connection from {addr}")

            with conn:
                while True:
                    data = conn.recv(1024)
                    if not data:
                        break

                    print(f"📩 Raw Data: {data.hex().upper()}")

                    meter_number = extract_meter_number(data)
                    if not meter_number:
                        print("❌ Could not extract meter number")
                        continue

                    try:
                        unit = Unit.objects.get(electric_meter_num=meter_number)
                    except Unit.DoesNotExist:
                        print(f"❌ Unit not found for meter {meter_number}")
                        continue
                    
                    meter = unit.meter     
                       
                    parsed = parse_diagnostic_frame(data)
                    if parsed:
                        MeterReading.objects.create(
                            meter=meter,
                            voltage=parsed["voltage"],
                            current=parsed["current"],
                            power=parsed["power"],
                            frequency=parsed["frequency"],
                            power_factor=parsed["power_factor"],
                            peak_hour=17 <= now().hour <= 22
                        )
                        print(f"💾 Saved diagnostic reading for {unit}")
                    else:
                        print("⚠️ Skipping invalid or incomplete frame")


if __name__ == "__main__":
    start_server()
