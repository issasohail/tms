"""Strict, read-only DL/T645 diagnostic frame helpers.

This module has no database or socket side effects.  The listener is the only
component allowed to put the frames built here onto an existing meter socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re

from smart_meter.dlt645 import (
    _add_33,
    build_frame,
    parse_bulk_summary_frame,
    verify_checksum,
)


@dataclass(frozen=True)
class DiagnosticRegister:
    label: str
    byte_length: int | None
    decimals: int | None
    unit: str
    signed: bool = False


DIAGNOSTIC_REGISTERS = {
    "00010000": DiagnosticRegister("Total Forward Active Energy", 4, 2, "kWh"),
    "00020000": DiagnosticRegister("Total Reverse Active Energy", 4, 2, "kWh"),
    "02010100": DiagnosticRegister("Phase A Voltage", 2, 1, "V"),
    "02010200": DiagnosticRegister("Phase B Voltage", 2, 1, "V"),
    "02010300": DiagnosticRegister("Phase C Voltage", 2, 1, "V"),
    "02020100": DiagnosticRegister("Phase A Current", 3, 3, "A"),
    "02020200": DiagnosticRegister("Phase B Current", 3, 3, "A"),
    "02020300": DiagnosticRegister("Phase C Current", 3, 3, "A"),
    "02030000": DiagnosticRegister("Total Active Power", 3, 4, "kW", signed=True),
    "02030100": DiagnosticRegister("Phase A Active Power", 3, 4, "kW", signed=True),
    "02030200": DiagnosticRegister("Phase B Active Power", 3, 4, "kW", signed=True),
    "02030300": DiagnosticRegister("Phase C Active Power", 3, 4, "kW", signed=True),
    "028011FF": DiagnosticRegister("Manufacturer Bulk Reading", None, None, "mixed"),
}

DIAGNOSTIC_DI_ALLOWLIST = frozenset(DIAGNOSTIC_REGISTERS)
BULK_UNITS = {
    "balance": "currency", "overdraft": "currency",
    "voltage_a": "V", "voltage_b": "V", "voltage_c": "V",
    "current_a": "A", "current_b": "A", "current_c": "A",
    "total_power": "kW", "power_a": "kW", "power_b": "kW", "power_c": "kW",
    "pf_total": "unitless", "pf_a": "unitless", "pf_b": "unitless", "pf_c": "unitless",
    "total_energy": "kWh", "peak_total_energy": "kWh",
    "peak_total_consumption": "kWh", "valley_total_consumption": "kWh",
    "flat_total_consumption": "kWh", "status_word": "hex",
}
READ_REQUEST_CONTROL = 0x11
READ_RESPONSE_CONTROL = 0x91
ERROR_RESPONSE_CONTROL = 0xD1


def normalize_diagnostic_di(di: str) -> str:
    value = str(di or "").strip().upper()
    if value not in DIAGNOSTIC_DI_ALLOWLIST:
        raise ValueError(f"DI {value or '<empty>'} is not in the diagnostic read allowlist")
    return value


def validate_meter_number(meter_number: str) -> str:
    value = str(meter_number or "").strip().upper()
    if re.fullmatch(r"[0-9A-F]{12}", value) is None:
        raise ValueError("meter number must be exactly 12 hexadecimal characters")
    return value


def build_diagnostic_read_frame(meter_number: str, di: str) -> bytes:
    """Build the only frame type accepted by the diagnostic transport.

    Physical testing of this meter family established the first-68 checksum
    window.  Four FE wake-up bytes are included in the complete TX frame.
    """
    meter_number = validate_meter_number(meter_number)
    di = normalize_diagnostic_di(di)
    frame = build_frame(
        meter_number,
        READ_REQUEST_CONTROL,
        _add_33(bytes.fromhex(di)[::-1]),
        checksum_mode="incl_1st68",
        include_preamble=True,
    )
    validate_diagnostic_request_frame(frame, meter_number=meter_number, di=di)
    return frame


def validate_diagnostic_request_frame(
    frame: bytes, *, meter_number: str, di: str
) -> None:
    """Fail closed if a generated frame is not exactly an allowlisted C=0x11 read."""
    meter_number = validate_meter_number(meter_number)
    di = normalize_diagnostic_di(di)
    inner = frame[4:] if frame.startswith(b"\xFE" * 4) else frame
    if len(inner) != 16 or inner[0] != 0x68 or inner[7] != 0x68:
        raise ValueError("diagnostic request must be one complete 16-byte DL/T645 frame")
    if inner[8] != READ_REQUEST_CONTROL or inner[9] != 4:
        raise ValueError("diagnostic transport permits only C=0x11 four-byte DI reads")
    address = inner[1:7][::-1].hex().upper()
    decoded_di = bytes(((b - 0x33) & 0xFF) for b in inner[10:14])[::-1].hex().upper()
    if address != meter_number or decoded_di != di:
        raise ValueError("diagnostic request address or DI does not match the request")
    if verify_checksum(inner, 0) != (True, "incl_1st68") or inner[-1] != 0x16:
        raise ValueError("diagnostic request checksum or terminator is invalid")


def _strict_bcd_decimal(encoded: bytes, decimals: int, *, signed: bool = False) -> Decimal:
    if not encoded:
        raise ValueError("empty BCD payload")
    plain = bytearray(((byte - 0x33) & 0xFF) for byte in encoded)
    negative = False
    if signed and plain[-1] & 0x80:
        negative = True
        plain[-1] &= 0x7F
    digits = []
    for byte in reversed(plain):
        high, low = byte >> 4, byte & 0x0F
        if high > 9 or low > 9:
            raise ValueError(f"invalid BCD byte 0x{byte:02X}")
        digits.extend((str(high), str(low)))
    text = "".join(digits)
    if decimals:
        text = f"{text[:-decimals] or '0'}.{text[-decimals:].zfill(decimals)}"
    value = Decimal(text)
    return -value if negative else value


def _frame_parts(frame: bytes) -> tuple[bytes, int, int, bytes, str, str]:
    inner = frame
    while inner.startswith(b"\xFE"):
        inner = inner[1:]
    if len(inner) < 12 or inner[0] != 0x68 or inner[7] != 0x68:
        raise ValueError("short or malformed DL/T645 response")
    length = inner[9]
    expected_length = 12 + length
    if len(inner) != expected_length or inner[-1] != 0x16:
        raise ValueError("DL/T645 response length or terminator is invalid")
    checksum_ok, checksum_style = verify_checksum(inner, 0)
    if not checksum_ok:
        raise ValueError("DL/T645 response checksum is invalid")
    data = inner[10:10 + length]
    meter = inner[1:7][::-1].hex().upper()
    returned_di = (
        bytes(((byte - 0x33) & 0xFF) for byte in data[:4])[::-1].hex().upper()
        if len(data) >= 4 else ""
    )
    return inner, inner[8], length, data, meter, returned_di


def _bulk_decimal_data(frame: bytes) -> dict:
    """Decode the fixed 69-byte manufacturer snapshot with strict BCD checks."""
    inner, _control, length, data, _meter, _di = _frame_parts(frame)
    if length != 0x45:
        raise ValueError(f"028011FF payload length {length} is not the captured 0x45 layout")

    pos = 4
    result = {}
    fields = (
        ("balance", 4, 2, False), ("overdraft", 4, 2, False),
        ("voltage_a", 2, 1, False), ("voltage_b", 2, 1, False),
        ("voltage_c", 2, 1, False),
        ("current_a", 3, 3, True), ("current_b", 3, 3, True),
        ("current_c", 3, 3, True),
        ("total_power", 3, 4, True), ("power_a", 3, 4, True),
        ("power_b", 3, 4, True), ("power_c", 3, 4, True),
        ("pf_total", 2, 3, True), ("pf_a", 2, 3, True),
        ("pf_b", 2, 3, True), ("pf_c", 2, 3, True),
        ("total_energy", 4, 2, False),
        ("peak_total_energy", 4, 2, False),
        ("peak_total_consumption", 4, 2, False),
        ("valley_total_consumption", 4, 2, False),
        ("flat_total_consumption", 4, 2, False),
    )
    for name, size, decimal_places, signed in fields:
        result[name] = _strict_bcd_decimal(
            data[pos:pos + size], decimal_places, signed=signed
        )
        pos += size
    if pos + 2 != len(data):
        raise ValueError("028011FF data does not end with its two-byte status word")
    status_plain = bytes(((byte - 0x33) & 0xFF) for byte in data[pos:pos + 2])
    result["status_word"] = status_plain[::-1].hex().upper()
    # Keep this assertion tied to the established parser layout as a regression guard.
    if set(parse_bulk_summary_frame(inner, 0)) - set(result):
        raise ValueError("028011FF parser exposed an unexpected field")
    return result


def decode_diagnostic_response(
    frame: bytes, *, expected_meter: str, expected_di: str
) -> dict:
    """Validate and decode one response without performing persistence."""
    expected_meter = validate_meter_number(expected_meter)
    expected_di = normalize_diagnostic_di(expected_di)
    inner, control, _length, data, meter, returned_di = _frame_parts(frame)
    checksum_ok, checksum_style = verify_checksum(inner, 0)
    base = {
        "meter": meter,
        "requested_di": expected_di,
        "returned_di": returned_di,
        "control_code": f"0x{control:02X}",
        "checksum_ok": checksum_ok,
        "checksum_style": checksum_style,
        "raw_rx": frame.hex().upper(),
    }
    if meter != expected_meter:
        raise ValueError(f"response meter {meter} does not match {expected_meter}")
    if control == ERROR_RESPONSE_CONTROL:
        base.update(status="unsupported", value=None, unit="", error="meter returned C=0xD1")
        return base
    if control != READ_RESPONSE_CONTROL:
        raise ValueError(f"response control 0x{control:02X} is not C=0x91")
    if returned_di != expected_di:
        raise ValueError(f"response DI {returned_di or '<empty>'} does not match {expected_di}")

    register = DIAGNOSTIC_REGISTERS[expected_di]
    if expected_di == "028011FF":
        base.update(status="supported", value=_bulk_decimal_data(inner), unit=BULK_UNITS, error="")
        return base
    payload = data[4:]
    if len(payload) != register.byte_length:
        raise ValueError(
            f"DI {expected_di} payload length {len(payload)} != {register.byte_length}"
        )
    value = _strict_bcd_decimal(payload, register.decimals or 0, signed=register.signed)
    base.update(status="supported", value=value, unit=register.unit, error="")
    return base
