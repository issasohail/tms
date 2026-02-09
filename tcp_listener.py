import os
import django
import socket
from smart_meter.models import Meter, MeterReading
from datetime import datetime
from smart_meter.meter_client import send_meter_request
from smart_meter.meter_parser import parse_response_frame
from django.utils.timezone import now
from smart_meter.models import Meter, MeterReading

# Set up Django settings environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tms.settings')
django.setup()  # Initialize Django

def listen_for_meter_data():
    # Set up TCP listener
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(('0.0.0.0', 6000))  # Listening on port 6000
    server_socket.listen(5)
    print("TCP Listener is running... Waiting for connections.")

    while True:
        # Accept incoming connections
        client_socket, addr = server_socket.accept()
        print(f"Connection received from {addr}")

        # Read data from client
        data = client_socket.recv(1024)
        if data:
            # Process data and extract meter number
            meter_number = extract_meter_number(data)
            if meter_number:
                meter = Meter.objects.filter(meter_number=meter_number).first()
                if meter:
                    # Parse the received data
                    reading_data = parse_response_frame(data)
                    if reading_data:
                        # Save the data to the database
                        MeterReading.objects.create(
                            meter=meter,
                            voltage=reading_data.get('voltage'),
                            current=reading_data.get('current'),
                            power=reading_data.get('power'),
                            total_energy=reading_data.get('total_energy'),
                            timestamp=now()
                        )
                        print(f"Reading saved for Meter #{meter_number}")

        client_socket.close()

def extract_meter_number(data):
    """Extract the meter number from the incoming data."""
    try:
        return ''.join([f"{b:02X}" for b in data[5:11][::-1]])  # Reverse the meter number
    except Exception as e:
        print(f"Error extracting meter number: {e}")
        return None

