from django.utils.http import urlencode
import requests
from django.conf import settings
from datetime import datetime, timedelta
import socket
from smart_meter.protocol import build_power_frame
import struct

def send_whatsapp_alert(phone_number, message):
    try:
        url = "https://app.wati.io/api/v1/sendSessionMessage"
        headers = {
            "Authorization": f"Bearer {settings.WATI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "phone": phone_number,
            "message": message
        }

        response = requests.post(url, json=payload, headers=headers)
        print("📤 WhatsApp alert sent:", response.status_code, response.text)
        return response.status_code == 200

    except Exception as e:
        print("❌ WhatsApp alert failed:", e)
        return False


def build_whatsapp_url(phone_number, message):
    """
    Builds a WhatsApp deeplink URL that opens WhatsApp with prefilled message.
    """
    query = urlencode({"text": message})
    return f"https://wa.me/{phone_number}?{query}"



METER_IP = "192.168.100.14"  # replace with actual IP
METER_PORT = 6000

def send_cutoff_command(meter_number):
    cmd = build_power_frame(meter_number, power_on=False)
    _send_to_meter(cmd)

def send_restore_command(meter_number):
    cmd = build_power_frame(meter_number, power_on=True)
    _send_to_meter(cmd)

def _send_to_meter(command_bytes):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((METER_IP, METER_PORT))
            s.sendall(command_bytes)
            print("📤 Sent command:", command_bytes.hex().upper())
    except Exception as e:
        print("❌ Error sending command:", e)



def frame_voltage_request(meter_number, voltage_identifier):
    """
    This function constructs a request frame to read the voltage from an electric meter based on the DLT645 - 2007 protocol.
    :param meter_number: The meter number, which is a string representing the unique identifier of the meter.
    :param voltage_identifier: The identifier for the voltage parameter, which is a hexadecimal string.
    :return: A byte array representing the complete request frame.
    """
    # Preamble: Four bytes of 0xFE
    preamble = b'\xFE' * 4
    # Add 0x68 before and after the meter number
    meter_number_bytes = bytes.fromhex(meter_number)[::-1]
    meter_number_with_markers = b'\x68' + meter_number_bytes + b'\x68'
    # Control code for reading data
    control_code = b'\x11'
    # Data area length
    data_area_length = b'\x04'
    # Convert voltage identifier to bytes
    voltage_identifier_bytes = bytes.fromhex(voltage_identifier)[::-1]
    # Calculate the checksum
    data_to_check = meter_number_with_markers + control_code + data_area_length + voltage_identifier_bytes
    checksum = sum(data_to_check) & 0xFF
    checksum_byte = struct.pack('B', checksum)
    # End code
    end_code = b'\x16'
    # Combine all parts to form the complete frame
    frame = preamble + meter_number_with_markers + control_code + data_area_length + voltage_identifier_bytes + checksum_byte + end_code
    return frame

def parse_response_frame(frame_bytes):
    """Parses the frame and returns the decoded data (voltage, current, etc.)."""
    try:
        # Assuming this is where the frame format is processed
        start = frame_bytes.find(b'\x68')
        if start == -1 or len(frame_bytes) < start + 12:
            raise ValueError("Invalid or too short frame")

        control_code = frame_bytes[start + 8]
        if control_code != 0x91:
            raise ValueError(f"Unsupported control code: {control_code:02X}")

        data_len = frame_bytes[start + 9]
        payload = frame_bytes[start + 10:start + 10 + data_len]

        def decode_bcd(offset: int, length: int = 4) -> float:
            raw = payload[offset:offset + length]
            decoded = [(b - 0x33) & 0xFF for b in raw]  # Decode each byte by subtracting 0x33
            digits = []

            for byte in decoded:
                high = (byte >> 4) & 0xF
                low = byte & 0xF

                # Validate BCD nibbles
                if high > 9 or low > 9:
                    raise ValueError(f"Invalid BCD nibble: {byte:02X} => high={high}, low={low}")

                digits.append(str(high))
                digits.append(str(low))

            num_str = ''.join(digits).lstrip('0') or '0'
            return float(num_str[:-2] + '.' + num_str[-2:])  # 2 decimal places

        return {
            "voltage": decode_bcd(0),
            "current": decode_bcd(8),
            "power": decode_bcd(16),
            "frequency": decode_bcd(24),
            "power_factor": decode_bcd(32),
        }

    except Exception as e:
        print(f"❌ parse_response_frame error: {e}")
        return None


# Example usage
meter_number = '250619510016'
voltage_identifier = '02010100'
request_frame = frame_voltage_request(meter_number, voltage_identifier)
print(f"Request frame: {request_frame.hex()}")
