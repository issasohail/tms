import os
import sys
import socket
import django
import struct
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging

# Setup Django
sys.path.append(os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tms.settings")
django.setup()

from smart_meter.models import Meter, MeterReading
from django.utils.timezone import now
from django.core.exceptions import ObjectDoesNotExist

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('smart_meter.log')
    ]
)
logger = logging.getLogger(__name__)

# In-memory reading buffer with expiration
reading_buffer = {}
BUFFER_EXPIRY = 300  # 5 minutes buffer expiration

# Enhanced DLT645-2007 identifiers
IDENTIFIERS = {
    "00000000": "total_energy",
    "02010100": "voltage",
    "02010200": "voltage_a",
    "02010300": "voltage_b",
    "02010400": "voltage_c",
    "02020100": "current",
    "02020200": "current_a",
    "02020300": "current_b",
    "02020400": "current_c",
    "02030100": "active_power",
    "02030200": "reactive_power",
    "02031000": "power_factor",
    "02040000": "frequency",
    "00010000": "voltage_thd",
     "028011FF": "bulk_summary"
    # Add more identifiers as needed
}

def extract_meter_number(frame):
    """Robust meter ID extraction with validation"""
    try:
        start_index = frame.find(b'\x68')
        if start_index == -1 or len(frame) < start_index + 12:
            return None
            
        # Meter address is 6 bytes after start marker
        addr_bytes = frame[start_index+1:start_index+7]
        
        # Reverse byte order and convert to hex string
        return ''.join(f"{b:02X}" for b in reversed(addr_bytes))
    except Exception as e:
        logger.error(f"Meter ID extraction error: {e}")
        return None

def identify_data_type(frame):
    """Improved DI identification with frame validation"""
    try:
        start_index = frame.find(b'\x68')
        if start_index == -1:
            return None
            
        # Data identifier is 4 bytes after control field
        di_start = start_index + 10
        if len(frame) < di_start + 4:
            return None
            
        di_bytes = frame[di_start:di_start+4]
        identifier = ''.join(f"{b:02X}" for b in reversed(di_bytes))
        return IDENTIFIERS.get(identifier)
    except Exception as e:
        logger.error(f"DI identification error: {e}")
        return None

def decode_bcd_value(bcd_bytes):
    """
    Robust BCD decoder with:
    - Automatic 0x33 adjustment detection
    - Byte reversal
    - Input validation
    - Error recovery
    """
    if not bcd_bytes:
        return 0.0
    
    try:
        # Detect encoding style by first byte
        adjust_33 = any(b > 0x33 for b in bcd_bytes)
        
        # Process bytes in reverse order
        decoded_str = ""
        for b in reversed(bcd_bytes):
            if adjust_33:
                b = (b - 0x33) & 0xFF
                
            high_nibble = (b >> 4) & 0x0F
            low_nibble = b & 0x0F
            
            # Validate nibbles
            if high_nibble > 9 or low_nibble > 9:
                raise ValueError(f"Invalid BCD nibbles: {high_nibble},{low_nibble}")
                
            decoded_str += f"{high_nibble}{low_nibble}"
        
        # Handle decimal point (last 2 digits are decimals)
        if len(decoded_str) > 2:
            integer_part = decoded_str[:-2].lstrip('0') or '0'
            decimal_part = decoded_str[-2:]
            return float(f"{integer_part}.{decimal_part}")
        return float(f"0.{decoded_str.zfill(2)}")
            
    except Exception as e:
        logger.error(f"BCD decode error: {e} | Bytes: {bcd_bytes.hex()}")
        return 0.0

def parse_frame_values(frame):
    """Parse values from frame based on protocol structure"""
    try:
        start_index = frame.find(b'\x68')
        if start_index == -1:
            return None
            
        # Get payload length
        data_length = frame[start_index + 9]
        payload_start = start_index + 10
        payload_end = payload_start + data_length
        
        if len(frame) < payload_end:
            return None
            
        payload = frame[payload_start:payload_end]
        
        # Parse values based on standard positions (adjust as needed)
        return {
            "voltage": decode_bcd_value(payload[0:4]),
            "current": decode_bcd_value(payload[4:8]),
            "power": decode_bcd_value(payload[8:12]),
            "frequency": decode_bcd_value(payload[12:16]),
            "power_factor": decode_bcd_value(payload[16:20]),
            "total_energy": decode_bcd_value(payload[20:24]),
        }
    except Exception as e:
        logger.error(f"Frame parse error: {e}")
        return None

def save_reading(meter, values):
    """Save reading with validation and error handling"""
    try:
        # Create reading with all available values
        reading = MeterReading.objects.create(
            meter=meter,
            voltage=values.get("voltage"),
            current=values.get("current"),
            power=values.get("power"),
            frequency=values.get("frequency"),
            power_factor=values.get("power_factor"),
            total_energy=values.get("total_energy"),
            peak_hour=17 <= now().hour <= 22,
        )
        logger.info(f"Saved reading ID: {reading.id} for meter {meter.meter_number}")
        return True
    except InvalidOperation as e:
        logger.error(f"Decimal error: {e}")
    except Exception as e:
        logger.error(f"Database save error: {e}")
    return False

def clean_buffer():
    """Remove stale entries from reading buffer"""
    global reading_buffer
    now_time = datetime.now()
    stale_keys = [
        k for k, v in reading_buffer.items()
        if (now_time - v['timestamp']) > timedelta(seconds=BUFFER_EXPIRY)
    ]
    
    for key in stale_keys:
        logger.info(f"Removing stale buffer: {key}")
        del reading_buffer[key]

def process_frame(frame):
    """Process a single complete frame"""
    meter_number = extract_meter_number(frame)
    if not meter_number:
        logger.warning("Could not extract meter number from frame")
        return False
    
    logger.info(f"Processing frame for meter: {meter_number}")
    
    # Try to parse as full diagnostic frame
    full_values = parse_frame_values(frame)
    if full_values:
        logger.info(f"Parsed full frame values: {full_values}")
        try:
            meter = Meter.objects.get(meter_number=meter_number)
            if save_reading(meter, full_values):
                # Clear buffer if full frame succeeds
                reading_buffer.pop(meter_number, None)
                return True
        except ObjectDoesNotExist:
            logger.error(f"Meter not registered: {meter_number}")
        return False
    
    # Process as single-value frame
    data_type = identify_data_type(frame)
    if not data_type:
        logger.warning("Unknown frame type (possibly heartbeat)")
        return False
    
    # Extract value from frame
    try:
        # Value typically starts after DI (adjust based on your protocol)
        value_start = frame.find(b'\x68') + 14
        value_bytes = frame[value_start:value_start+4]
        value = decode_bcd_value(value_bytes)
        logger.info(f"Parsed {data_type}: {value}")
    except Exception as e:
        logger.error(f"Value extraction failed: {e}")
        return False
    
    # Get/create buffer entry
    buf_entry = reading_buffer.setdefault(meter_number, {
        "timestamp": datetime.now(),
        "values": {}
    })
    
    # Update value
    buf_entry["values"][data_type] = value
    buf_entry["timestamp"] = datetime.now()
    
    # Check if we have complete set
    required_fields = {"voltage", "current", "power", "total_energy"}
    if required_fields.issubset(buf_entry["values"].keys()):
        try:
            meter = Meter.objects.get(meter_number=meter_number)
            if save_reading(meter, buf_entry["values"]):
                # Clear only successful saves
                del reading_buffer[meter_number]
                return True
        except ObjectDoesNotExist:
            logger.error(f"Meter not registered: {meter_number}")
    
    return False

HOST = '0.0.0.0'
PORT = 6000

def start_server():
    logger.info(f"Starting smart meter server on {HOST}:{PORT}")
    
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.settimeout(10)  # Prevents hanging during accept
    server_socket.listen(5)
    
    try:
        while True:
            try:
                logger.info("Waiting for connection...")
                conn, addr = server_socket.accept()
                logger.info(f"Connection from {addr[0]}:{addr[1]}")
                
                with conn:
                    buffer = b''
                    while True:
                        try:
                            data = conn.recv(1024)
                            if not data:
                                break
                            logger.info(f"📩 Raw Data ({len(data)} bytes): {data.hex().upper()}")

                            buffer += data
                            logger.debug(f"Received {len(data)} bytes, buffer: {len(buffer)}")
                            
                            # Process all complete frames in buffer
                            while True:
                                # Find start of frame
                                start_idx = buffer.find(b'\x68')
                                if start_idx == -1:
                                    # No start found, clear buffer
                                    buffer = b''
                                    break
                                
                                # Find end of frame (0x16)
                                end_idx = buffer.find(b'\x16', start_idx)
                                if end_idx == -1:
                                    # Incomplete frame, wait for more data
                                    break
                                
                                # Extract complete frame including start/end markers
                                frame = buffer[start_idx:end_idx+1]
                                buffer = buffer[end_idx+1:]
                                
                                # Process the frame
                                process_frame(frame)
                                
                                # Clean buffer periodically
                                clean_buffer()
                                
                        except socket.timeout:
                            logger.warning("Receive timeout")
                            break
                        except Exception as e:
                            logger.error(f"Connection error: {e}")
                            break
                            
            except socket.timeout:
                logger.warning("Accept timeout, cycling...")
            except Exception as e:
                logger.error(f"Server error: {e}")
                
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    finally:
        server_socket.close()
        logger.info("Server socket closed")

if __name__ == "__main__":
    start_server()
    

