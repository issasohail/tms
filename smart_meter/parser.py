from datetime import datetime

IDENTIFIERS = {
    "02010100": "voltage",
    "02020100": "current",
    "02030100": "power",
    "00000000": "total_energy",
    "02030000": "power",  # alternate ID
}


def extract_meter_number(frame):
    """Extracts the 6-byte meter number in reverse order."""
    if len(frame) < 12:
        return None
    meter_bytes = frame[5:11]
    return ''.join(f"{b:02X}" for b in reversed(meter_bytes))


def identify_data_type(frame):
    """Identifies data type from the DI (data identifier)."""
    start = frame.find(b'\x68')
    if start == -1 or len(frame) < start + 14:
        return None

    identifier_raw = frame[start + 10:start + 14]
    identifier = ''.join(f"{b:02X}" for b in reversed(identifier_raw))
    return IDENTIFIERS.get(identifier, None)


def bcd_to_decimal(bcd_bytes):
    """Decodes BCD-encoded bytes (after subtracting 0x33)."""
    digits = []
    for b in bcd_bytes:
        val = b - 0x33
        if val < 0 or val > 0x99:
            raise ValueError(f"Non-BCD byte: {val}")
        high = (val >> 4) & 0x0F
        low = val & 0x0F
        digits.append(str(high))
        digits.append(str(low))
    return ''.join(digits)


def parse_response_frame(frame):
    """
    Parses a DLT645-2007 response frame and extracts the numeric value.
    Returns float or None if not valid.
    """
    try:
        start = frame.find(b'\x68')
        if start == -1 or len(frame) < start + 12:
            raise ValueError("Invalid start or too short")

        control_code = frame[start + 8]
        if control_code != 0x91:
            raise ValueError(f"Unsupported control code: {control_code:02X}")

        data_len = frame[start + 9]
        if not (2 <= data_len <= 6):
            raise ValueError(f"Unsupported data length: {data_len}")

        raw_data = frame[start + 14:start + 14 + data_len]
        value_str = bcd_to_decimal(raw_data)

        value = float(value_str) / 100
        return round(value, 2)

    except Exception as e:
        print(f"❌ parse_response_frame error: {e}")
        return None
