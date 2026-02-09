import os
import sys
import socket
import logging
from datetime import datetime

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# BCD decoding logic


def decode_bcd(raw_data, decimal_places=2):
    decoded = [(b - 0x33) & 0xFF for b in raw_data]
    digits = []
    for byte in reversed(decoded):
        high = (byte >> 4) & 0xF
        low = byte & 0xF
        if high > 9 or low > 9:
            raise ValueError(
                f"Invalid BCD nibble: {byte:02X} => high={high}, low={low}")
        digits.append(str(high))
        digits.append(str(low))
    num_str = ''.join(digits).lstrip("0") or "0"
    if len(num_str) <= decimal_places:
        return float("0." + num_str.zfill(decimal_places))
    return float(num_str[:-decimal_places] + "." + num_str[-decimal_places:])


# Known identifiers
IDENTIFIERS = {
    "02010100": "voltage",
    "02020100": "current",
    "02030100": "power",
    "00000000": "total_energy",
    "028011FF": "bulk_summary"
}


def extract_identifier(data):
    try:
        start = data.find(b'\x68')
        if start == -1:
            return None
        identifier_raw = data[start + 9:start + 13]
        identifier = ''.join(
            f"{(b - 0x33) & 0xFF:02X}" for b in reversed(identifier_raw))
        return IDENTIFIERS.get(identifier)
    except:
        return None


def extract_meter_number(data):
    try:
        start = data.find(b'\x68')
        if start == -1 or len(data) < start + 7:
            raise ValueError(
                "No start byte or too short to extract meter number")
        meter_bytes = data[start + 1:start + 7]
        meter_number = ''.join(f'{b:02X}' for b in reversed(meter_bytes))
        logger.info(f"🆔 Extracted meter number: {meter_number}")
        return meter_number
    except Exception as e:
        logger.error(f"❌ Error extracting meter number: {e}")
        return None


def parse_bulk_summary_frame(data: bytes):
    try:
        start = data.find(b'\x68')
        if start == -1:
            raise ValueError("No start byte")

        control_code = data[start + 7]
        if control_code != 0x91:
            raise ValueError(f"Unexpected control code: {control_code:02X}")

        data_len = data[start + 8]
        raw_data = data[start + 9:start + 9 + data_len]

        fields = {}
        idx = 0

        def read_bcd(n_bytes, dec):
            nonlocal idx
            val = decode_bcd(raw_data[idx:idx + n_bytes], decimal_places=dec)
            idx += n_bytes
            return val

        fields["balance"] = read_bcd(4, 2)
        fields["overdraft"] = read_bcd(4, 2)
        fields["voltage_a"] = read_bcd(2, 1)
        fields["voltage_b"] = read_bcd(2, 1)
        fields["voltage_c"] = read_bcd(2, 3)
        fields["current_a"] = read_bcd(3, 3)
        fields["current_b"] = read_bcd(3, 3)
        fields["current_c"] = read_bcd(3, 3)
        fields["total_power"] = read_bcd(3, 4)
        fields["power_a"] = read_bcd(3, 4)
        fields["power_b"] = read_bcd(3, 4)
        fields["power_c"] = read_bcd(3, 4)
        fields["pf_total"] = read_bcd(2, 3)
        fields["pf_a"] = read_bcd(2, 3)
        fields["pf_b"] = read_bcd(2, 3)
        fields["pf_c"] = read_bcd(2, 3)
        fields["total_energy"] = read_bcd(4, 2)
        fields["peak_total_energy"] = read_bcd(4, 2)
        # ...continue parsing as per spec until running status word...
        fields["status_word"] = raw_data[idx:idx + 2].hex().upper()

        return fields

    except Exception as e:
        logger.error(f"Bulk summary parse error: {e}")
        return None


def parse_response_frame(data: bytes):
    try:
        identifier = extract_identifier(data)
        meter_number = extract_meter_number(data)

        if identifier == "bulk_summary":
            return parse_bulk_summary_frame(data)

        start = data.find(b'\x68')
        if start == -1 or len(data) < start + 12:
            raise ValueError("No start byte or incomplete frame")

        control_code = data[start + 7]
        if control_code not in (0x91, 0x81):
            raise ValueError(
                "Unexpected control code (not a valid reply frame)")

        data_len = data[start + 8]
        if data_len != 4:
            raise ValueError(f"Unsupported data length: {data_len}")

        raw_data = data[start + 9:start + 13]
        value = decode_bcd(raw_data)
        logger.info(f"🔎 Decoded value: {value}")
        return round(value, 2)

    except Exception as e:
        logger.error(f"❌ Error in parse_response_frame: {e}")
        return None


# TCP Server
HOST = '0.0.0.0'
PORT = 6000


def start_server():

    logger.info(f"✅ Listening on {HOST}:{PORT} for smart meter data...")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT))
        s.listen(5)
        while True:
            conn, addr = s.accept()
            logger.info(f"📡 Connection from {addr}")
            with conn:
                buffer = b''
                while True:
                    data = conn.recv(1024)
                    logger.debug("✅ Received data chunk")
                    if not data:
                        break
                    logger.info(
                        f"📩 Raw Data ({len(data)} bytes): {data.hex().upper()}")
                    buffer += data

                    while True:
                        start = buffer.find(b'\x68')
                        end = buffer.find(b'\x16', start)
                        if start == -1 or end == -1:
                            break

                        frame = buffer[start:end + 1]
                        buffer = buffer[end + 1:]

                        if len(frame) < 16:
                            logger.warning("⚠️ Skipping short frame")
                            continue

                        parse_response_frame(frame)


if __name__ == "__main__":
    start_server()
