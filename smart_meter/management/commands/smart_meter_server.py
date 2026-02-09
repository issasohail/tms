import socket
from smart_meter.meter_parser import parse_response_frame
from smart_meter.models import MeterReading, Unit
from django.utils.timezone import now
import django
import os
import sys
from datetime import datetime

# Setup Django
sys.path.append(os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                      "tenant_management_system.settings")
django.setup()

HOST = '0.0.0.0'
PORT = 60000  # port that meter is configured to send data to


def start_server():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] ✅ Listening on {HOST}:{PORT} for meter connections...")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind((HOST, PORT))
        server_socket.listen()

        while True:
            conn, addr = server_socket.accept()
            print(f"🔌 Connection from {addr}")
            with conn:
                data = conn.recv(1024)
                if data:
                    print(f"📩 Raw data: {data.hex()}")

                    # Try to parse it
                    try:
                        value = parse_response_frame(data)
                        print(f"✅ Parsed value: {value}")

                        # Optional: assign meter number manually for testing
                        meter_number = "250619510016"
                        unit = Unit.objects.get(
                            electric_meter_num=meter_number)

                        MeterReading.objects.create(
                            unit=unit,
                            total_energy=value,
                            voltage=None,
                            current=None,
                            power=None,
                            peak_hour=17 <= now().hour <= 22
                        )
                        print(f"📥 Saved reading for {unit}")
                    except Exception as e:
                        print(f"❌ Failed to parse/save: {e}")


if __name__ == "__main__":
    start_server()
