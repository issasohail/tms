import time
import os
import traceback
import logging
import socket
import struct
from time import sleep


def frame_request(meter_number, identifier):
    """
    Build a request frame using DLT645-2007.
    """
    preamble = b'\xFE' * 4
    start = b'\x68'
    end = b'\x68'
    control_code = b'\x11'
    data_length = b'\x04'
    addr = bytes.fromhex(meter_number)[::-1]
    di = bytes.fromhex(identifier)[::-1]

    # checksum = sum of bytes after preamble, before checksum
    payload = start + addr + end + control_code + data_length + di
    checksum = struct.pack('B', sum(payload) % 256)
    frame = preamble + payload + checksum + b'\x16'
    return frame


def send_meter_request(meter_ip, port, meter_number, identifier):
    """
    Send a read request to the meter and return raw response bytes.
    """
    request = frame_request(meter_number, identifier)
    try:
        with socket.create_connection((meter_ip, port), timeout=5) as sock:
            sock.sendall(request)
            sleep(1)  # Wait for meter to respond
            data = sock.recv(1024)
            return data
    except (socket.timeout, socket.error) as e:
        print(f"❌ Failed to connect to {meter_ip}:{port} - {e}")
        return None


# For testing:
if __name__ == "__main__":
    meter_number = "123456789012"  # must be 12 digits, hex string
    identifier = "00000000"  # e.g., Total Energy (dummy)
    ip = "192.168.100.14"
    port = 60000

    raw = send_meter_request(ip, port, meter_number, identifier)
    print("Raw response:", raw.hex() if raw else "No response")


def frame_write_control(meter_number, identifier, data_bytes):
    preamble = b'\xFE' * 4
    start = b'\x68'
    end = b'\x68'
    control_code = b'\x14'  # write command
    data_length = bytes([len(data_bytes)])
    addr = bytes.fromhex(meter_number)[::-1]
    di = bytes.fromhex(identifier)[::-1]
    data = bytes([(b + 0x33) & 0xFF for b in data_bytes])  # encode data

    payload = start + addr + end + control_code + \
        bytes([4 + len(data)]) + di + data
    checksum = struct.pack("B", sum(payload) % 256)
    return preamble + payload + checksum + b'\x16'


# Add near top of meter_client.py

# Simple file logger for debugging (temporary)
LOG_PATH = os.path.join(os.path.dirname(__file__), 'meter_control.log')
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Example helper to check global disable flag (set environment variable DISABLE_CUTOFFS=1 to disable)


def cutoffs_disabled():
    return os.getenv('DISABLE_CUTOFFS', '0') == '1'


# add near top of file:

logger = logging.getLogger(__name__)
if not logger.handlers:
    # simple console handler if none configured
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(ch)
    logger.setLevel(logging.INFO)


def send_cutoff_command(meter_ip, port, meter_number):
    """
    Send cut-off command to smart meter — guarded by DISABLE_CUTOFF env var.
    """
    # Safety guard: do NOT send cut-off if DISABLE_CUTOFF is set (1)
    if os.getenv("DISABLE_CUTOFF", "0") != "0":
        logger.warning("Cutoff suppressed by DISABLE_CUTOFF: meter=%s ip=%s port=%s caller=%s",
                       meter_number, meter_ip, port, traceback.format_stack()[-3].strip())
        return False

    identifier = "00400001"  # Control Word
    data_bytes = bytes([0x00])  # 0x00 = cut off
    frame = frame_write_control(meter_number, identifier, data_bytes)

    logger.info("About to send CUT-OFF frame meter=%s ip=%s port=%s frame=%s caller=%s",
                meter_number, meter_ip, port, frame.hex(), traceback.format_stack()[-3].strip())

    try:
        with socket.create_connection((meter_ip, port), timeout=5) as sock:
            sock.sendall(frame)
            response = sock.recv(1024)
            logger.info("Cut-off sent. Response: %s",
                        response.hex() if response else None)
            return True
    except Exception as e:
        logger.exception("Failed to send cut-off: %s", e)
        return False


def send_restore_command(meter_ip, port, meter_number):
    """
    Sends a restore power command to the meter.
    """
    identifier = "00400001"
    data_bytes = bytes([0x01])  # 0x01 = restore
    frame = frame_write_control(meter_number, identifier, data_bytes)

    try:
        with socket.create_connection((meter_ip, port), timeout=5) as sock:
            sock.sendall(frame)
            response = sock.recv(1024)
            print("✅ Restore command sent. Response:", response.hex())
            return True
    except Exception as e:
        print(f"❌ Failed to send restore: {e}")
        return False
