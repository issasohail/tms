from celery import shared_task
import socket
import threading
from datetime import datetime, timedelta
from django.utils.timezone import now
from smart_meter.models import MeterReading, Meter, MeterBalance
from smart_meter.meter_client import send_meter_request
from smart_meter.meter_parser import parse_response_frame
from properties.models import Unit
from decimal import Decimal

# Identifiers for different meter data
IDENTIFIERS = {
    "voltage": "02010100",
    "current": "02020100",
    "power": "02030100",
    "energy": "00000000"  # Total kWh
}

METER_IP = "192.168.100.14"  # Customize per meter if needed
PORT = 60000

def poll_all_meters():
    """Poll all smart meters and retrieve data."""
    print("📡 Polling smart meters...")
    smart_units = Unit.objects.filter(is_smart_meter=True)

    for unit in smart_units:
        meter_number = unit.electric_meter_num
        print(f"🔎 Polling Unit {unit.unit_number} | Meter #{meter_number}")

        results = {}
        for label, identifier in IDENTIFIERS.items():
            raw = send_meter_request(METER_IP, PORT, meter_number, identifier)
            value = parse_response_frame(raw)
            print(f"  {label}: {value}")
            results[label] = value

        # Save the data to MeterReading
        MeterReading.objects.create(
            unit=unit,
            voltage=results["voltage"],
            current=results["current"],
            power=results["power"],
            total_energy=results["energy"],
            peak_hour=is_peak_hour()
        )


def deduct_usage_charges():
    """Deduct usage charges from meter balance."""
    print("⚙️ Deducting usage from balances...")
    units = Unit.objects.filter(is_smart_meter=True)

    for unit in units:
        today = now().date()
        yesterday = today - timedelta(days=1)
        readings = MeterReading.objects.filter(
            unit=unit, timestamp__date=yesterday).order_by("timestamp")

        if readings.count() < 2:
            continue

        usage = Decimal(readings.last().total_energy - readings.first().total_energy)
        cost = round(usage * Decimal("7.50"), 2)

        balance, _ = MeterBalance.objects.get_or_create(unit=unit)

        if balance.balance >= cost:
            balance.balance -= cost
        else:
            deficit = cost - balance.balance
            balance.balance = Decimal("0.00")
            if balance.security_deposit >= deficit:
                balance.security_deposit -= deficit
            else:
                print(
                    f"❌ Unit {unit.unit_number}: Balance and deposit exhausted!")

        balance.save()


def is_peak_hour():
    """Check if the current time is within the peak hours."""
    now = datetime.now().hour
    return 17 <= now <= 22  # 5 PM to 10 PM


def start_tcp_server():
    """Start the TCP server in a separate thread."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("0.0.0.0", 6000))  # Listen on all interfaces and port 6000
    server.listen(5)
    print("Server listening on port 6000...")

    while True:
        client_socket, addr = server.accept()
        print(f"Connection received from {addr}")
        
        # Read incoming data
        data = client_socket.recv(1024)  # Adjust the buffer size if needed
        if data:
            meter_number = extract_meter_number(data)
            if meter_number:
                meter = Meter.objects.filter(meter_number=meter_number).first()
                if meter:
                    reading_data = parse_response_frame(data)  # Parse the data frame
                    if reading_data:
                        # Save the reading to the database
                        MeterReading.objects.create(
                            meter=meter,
                            voltage=reading_data.get('voltage'),
                            current=reading_data.get('current'),
                            power=reading_data.get('power'),
                            total_energy=reading_data.get('total_energy'),
                            timestamp=now()
                        )
                        print(f"Reading saved for Meter {meter_number}")
        client_socket.close()


@shared_task
def handle_meter_data():
    """This Celery task will run the TCP server in a background thread."""
    print("🔊 Starting TCP listener in a background thread...")
    thread = threading.Thread(target=start_tcp_server)
    thread.daemon = True  # Ensures the thread will exit when the main program exits
    thread.start()
    return "TCP Server started in background"


def extract_meter_number(data):
    """Extracts the meter number from the incoming data."""
    try:
        return ''.join([f"{b:02X}" for b in data[5:11][::-1]])  # Reverse the meter number
    except Exception as e:
        print(f"Error extracting meter number: {e}")
        return None


def parse_response_frame(data):
    """Parses the frame and returns the decoded data (voltage, current, etc.)."""
    try:
        voltage = parse_value(data, "voltage")
        current = parse_value(data, "current")
        power = parse_value(data, "power")
        total_energy = parse_value(data, "total_energy")
        
        return {
            "voltage": voltage,
            "current": current,
            "power": power,
            "total_energy": total_energy
        }
    except Exception as e:
        print(f"Error parsing response frame: {e}")
        return None


def parse_value(data, field_name):
    """Parses a specific field (voltage, current, etc.) from the data."""
    # Placeholder logic to extract values from the data frame
    # You need to adjust this according to the data format you have
    return None
