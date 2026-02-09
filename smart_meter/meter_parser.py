from smart_meter.models import MeterReading, Meter
import os
import sys
import socket
import django
from datetime import datetime
from django.utils.timezone import now

# Setup Django
sys.path.append(os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tms.settings")
django.setup()


# Identifier mappings
IDENTIFIERS = {
    "02010100": "voltage",
    "02020100": "current",
    "02030100": "power",
    "00000000": "total_energy"
}

# In-memory buffer
reading_buffer = {}

# BCD decoder


def decode_bcd(raw_data):
    decoded = [(b - 0x33) & 0xFF for b in raw_data]
    digits = []
    for byte in decoded:
        digits.append(str((byte >> 4) & 0xF))
        digits.append(str(byte & 0xF))
    num_str = ''.join(digits).lstrip("0") or "0"
    return float(num_str[:-2] + "." + num_str[-2:])

# Frame parser


def parse_response_frame(data: bytes):
    try:
        start = data.find(b'\x68')
        if start == -1 or len(data) < start + 12:
            raise ValueError("No start byte or incomplete frame")

        control_code = data[start + 7]
        if control_code != 0x91:
            raise ValueError("Unexpected control code (not a reply frame)")

        data_len = data[start + 8]
        if data_len != 4:
            raise ValueError(f"Unsupported data length: {data_len}")

        # Extract identifier and payload
        identifier_raw = data[start + 9:start + 13]
        identifier = ''.join(
            f"{(b - 0x33):02X}" for b in reversed(identifier_raw))
        payload = data[start + 13:start + 13 + data_len]

        if identifier not in IDENTIFIERS:
            raise ValueError(f"Unknown identifier: {identifier}")

        value = decode_bcd(payload)
        return IDENTIFIERS[identifier], value

    except Exception as e:
        print(f"❌ Error in parse_response_frame: {e}")
        return None, None

# Extract meter number from frame


def extract_meter_number(data: bytes):
    if len(data) >= 11:
        addr = data[5:11]
        return ''.join(f"{b:02X}" for b in reversed(addr))
    return None


HOST = '0.0.0.0'
PORT = 6000


def start_server():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] ✅ Listening on {HOST}:{PORT} for meter connections...")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()

        while True:
            print("🟢 Waiting for connection...")
            conn, addr = s.accept()
            print(f"🔌 Connection from {addr}")

            with conn:
                while True:
                    data = conn.recv(1024)
                    if not data:
                        break

                    print(f"📩 Raw Data: {data.hex().upper()}")
                    meter_number = extract_meter_number(data)
                    data_type, value = parse_response_frame(data)

                    if not meter_number or not data_type:
                        print(
                            "❗ Unknown data type frame (possibly heartbeat), skipping.")
                        continue

                    print(f"🔎 Meter: {meter_number} | {data_type}: {value}")

                    try:
                        meter = Meter.objects.get(meter_number=meter_number)
                    except Meter.DoesNotExist:
                        print(f"❌ No meter found with number {meter_number}")
                        continue

                    # Buffering
                    buf = reading_buffer.setdefault(meter_number, {})
                    buf[data_type] = value
                    buf["last_seen"] = datetime.now()
                    buf["meter"] = meter

                    # Save only when all fields are present
                    if all(k in buf for k in ("voltage", "current", "power", "total_energy")):
                        MeterReading.objects.create(
                            meter=meter,
                            voltage=buf["voltage"],
                            current=buf["current"],
                            power=buf["power"],
                            total_energy=buf["total_energy"],
                            peak_hour=17 <= now().hour <= 22
                        )
                        print(f"💾 Saved reading for {meter}")
                        reading_buffer[meter_number] = {}  # Reset after save


if __name__ == "__main__":
    start_server()
