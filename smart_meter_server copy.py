import os
import sys
import socket
import django
from datetime import datetime, timedelta

# Setup Django
sys.path.append(os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tms.settings")
django.setup()

from smart_meter.models import MeterReading, Unit
from django.utils.timezone import now

# In-memory buffer for readings
reading_buffer = {}

# Meter ID → identifier map
IDENTIFIERS = {
    "02010100": "voltage",
    "02020100": "current",
    "02030100": "power",
    "00000000": "total_energy"
}


def extract_meter_number(raw_bytes):
    if len(raw_bytes) < 12:
        return None
    reversed_addr = raw_bytes[5:11]
    return ''.join(f"{b:02X}" for b in reversed(reversed_addr))


def parse_response_frame(frame_bytes):
    """
    Parses a DLT645-2007 response frame and returns a float if it's a valid reading.
    """
    try:
        start = frame_bytes.find(b'\x68')
        if start == -1 or len(frame_bytes) < start + 12:
            raise ValueError("Incomplete or missing start byte")

        control_code = frame_bytes[start + 8]
        if control_code != 0x91:
            raise ValueError(f"Unsupported control code: {control_code:02X}")

        data_len = frame_bytes[start + 9]
        if data_len not in (2, 4):  # limit to 2 or 4 bytes only
            raise ValueError(f"Unsupported data length: {data_len}")

        raw_data = frame_bytes[start + 10:start + 10 + data_len]
        decoded = [b - 0x33 for b in raw_data]

        # Validate decoded content is hex digits only
        hex_str = ''.join(f"{b:02X}" for b in decoded[::-1])
        if not hex_str.isdigit():
            raise ValueError(f"Non-numeric value_str: {hex_str}")

        value = float(hex_str) / 100
        return round(value, 2)

    except Exception as e:
        print(f"❌ Error in parse_response_frame: {e}")
        return None




def identify_data_type(frame_bytes):
    """Extracts and reverses the identifier to determine the data type"""
    start = frame_bytes.find(b'\x68')
    if start == -1 or len(frame_bytes) < start + 14:
        return None
    identifier_raw = frame_bytes[start + 10:start + 14]
    identifier = ''.join(f"{b:02X}" for b in reversed(identifier_raw))
    return IDENTIFIERS.get(identifier, None)


HOST = '0.0.0.0'
PORT = 6000


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
                    data_type = identify_data_type(data)
                    value = parse_response_frame(data)

                    if not meter_number or not data_type:
                        print("❌ Invalid frame, skipping.")
                        continue

                    print(f"🔎 Meter: {meter_number} | {data_type}: {value}")

                    try:
                        unit = Unit.objects.get(electric_meter_num=meter_number)
                    except Unit.DoesNotExist:
                        print(f"❌ No unit found with meter number {meter_number}")
                        continue

                    # Buffer reading
                    buf = reading_buffer.setdefault(meter_number, {})
                    buf[data_type] = value
                    buf["last_seen"] = datetime.now()
                    buf["unit"] = unit

                    # If all readings are available, save
                    if all(k in buf for k in ("voltage", "current", "power", "total_energy")):
                        MeterReading.objects.create(
                            unit=unit,
                            voltage=buf["voltage"],
                            current=buf["current"],
                            power=buf["power"],
                            total_energy=buf["total_energy"],
                            peak_hour=17 <= now().hour <= 22
                        )
                        print(f"💾 Saved full reading for Unit {unit}")

                        # Clear after save
                        reading_buffer[meter_number] = {}

if __name__ == "__main__":
    start_server()
