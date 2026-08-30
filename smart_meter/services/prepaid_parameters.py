"""Authoritative, offline DL/T645 Parameter 1 (070104FF) codec.

This module deliberately delegates wire construction to the audited vendor
builder.  Values passed in and returned by this module are human ``Decimal``
values; +0x33 transformation and protocol scaling remain inside the codec.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

from smart_meter.vendor.build_prepaid_parameters import make070104ff, make_general03_cmd

PARAMETER_DI = "070104FF"
OPERATOR = bytes.fromhex("77665544")
PAYLOAD_LENGTH = 143

DATE_FIELDS = ("priceChgDate", "stepChgDate", "timeAreaChgDate", "timeSecChgDate")
COUNT_LIMITS = {"qtyarea": 2, "qtytimertable": 2, "qtytimer": 8, "qtyprice": 4, "qtystep": 3}
MONEY_FIELDS = ("warnlowbala1", "warnlowbala2", "creditVal", "balancemax", "remainPowerOn")
POWER_FIELDS = ("kwMax",)
PRICE_FIELDS = tuple(f"set{rate}Price{slot}" for rate in (1, 2) for slot in range(1, 5))
STEP_FIELDS = tuple(f"set{rate}Step{slot}" for rate in (1, 2) for slot in range(1, 4))
STEP_PRICE_FIELDS = tuple(f"set{rate}StepPrice{slot}" for rate in (1, 2) for slot in range(1, 5))
FIELD_ORDER = (
    *DATE_FIELDS, *COUNT_LIMITS, "pt", "ct", *MONEY_FIELDS, "kwMax", "sleepKw",
    *PRICE_FIELDS, *STEP_FIELDS[:3], *STEP_PRICE_FIELDS[:4], *STEP_FIELDS[3:], *STEP_PRICE_FIELDS[4:],
)


def _decimal(value: Any, *, places: int, field: str, maximum: str) -> Decimal:
    if isinstance(value, float):
        raise TypeError(f"{field} must be Decimal or a string, never float")
    value = Decimal("0") if value in (None, "") else Decimal(str(value))
    quant = Decimal(1).scaleb(-places)
    if value < 0 or value > Decimal(maximum) or value != value.quantize(quant):
        raise ValueError(f"{field} must be between 0 and {maximum} with at most {places} decimal places")
    return value


def normalise_config(values: dict[str, Any]) -> dict[str, Any]:
    """Validate and fill a complete semantic Parameter 1 configuration."""
    result: dict[str, Any] = {}
    for field in DATE_FIELDS:
        value = int(values.get(field, 0) or 0)
        if value < 0 or value > 9999999999:
            raise ValueError(f"{field} must be a 10-digit date value or 0")
        result[field] = value
    for field, limit in COUNT_LIMITS.items():
        value = int(values.get(field, 0) or 0)
        if not 0 <= value <= limit:
            raise ValueError(f"{field} must be between 0 and {limit}")
        result[field] = value
    for field in ("pt", "ct"):
        value = int(values.get(field, 0) or 0)
        if not 0 <= value <= 999999:
            raise ValueError(f"{field} must be between 0 and 999999")
        result[field] = value
    for field in MONEY_FIELDS:
        result[field] = _decimal(values.get(field, 0), places=2, field=field, maximum="999999.99")
    result["kwMax"] = _decimal(values.get("kwMax", 0), places=4, field="kwMax", maximum="99.9999")
    result["sleepKw"] = int(values.get("sleepKw", 0) or 0)
    if not 0 <= result["sleepKw"] <= 255:
        raise ValueError("sleepKw must be between 0 and 255")
    for field in (*PRICE_FIELDS, *STEP_FIELDS, *STEP_PRICE_FIELDS):
        result[field] = _decimal(values.get(field, 0), places=4, field=field, maximum="9999.9999")
    return result


def build_parameter_payload(config: dict[str, Any]) -> bytes:
    config = normalise_config(config)
    payload = bytearray(PAYLOAD_LENGTH)
    length = make070104ff("", 0, payload, config=config)
    if length != PAYLOAD_LENGTH:
        raise ValueError(f"vendor Parameter 1 builder returned {length}, expected {PAYLOAD_LENGTH}")
    return bytes(payload)


def build_parameter_frame(meter_number: str, config: dict[str, Any]) -> dict[str, Any]:
    payload = build_parameter_payload(config)
    frame = bytes(make_general03_cmd(meter_number, PARAMETER_DI, list(payload), len(payload), ""))
    return {"payload": payload, "frame": frame, "metadata": {"di": PARAMETER_DI, "control": 0x03, "operator": OPERATOR, "payload_length": len(payload)}}


def _decode_bcd(raw: bytes, decimals: int) -> Decimal:
    # Vendor payload fields are little-endian BCD and already +0x33 encoded.
    plain = bytes((byte - 0x33) & 0xFF for byte in raw)
    if any((byte >> 4) > 9 or (byte & 0x0F) > 9 for byte in plain):
        raise ValueError("Parameter 1 payload contains non-BCD data")
    digits = "".join(f"{byte:02X}" for byte in plain[::-1])
    number = Decimal(digits or "0").scaleb(-decimals)
    return number.quantize(Decimal(1).scaleb(-decimals))


def decode_parameter_payload(payload: bytes) -> dict[str, Any]:
    if len(payload) != PAYLOAD_LENGTH:
        raise ValueError("Parameter 1 payload must be exactly 143 bytes")
    pos, result = 0, {}
    for field in DATE_FIELDS:
        result[field] = int(_decode_bcd(payload[pos:pos + 5], 0)); pos += 5
    for field in COUNT_LIMITS:
        value = (payload[pos] - 0x33) & 0xFF
        if value > 0x99 or (value >> 4) > 9 or (value & 0x0F) > 9:
            raise ValueError("Parameter 1 count contains non-BCD data")
        result[field] = (value >> 4) * 10 + (value & 0x0F); pos += 1
    for field in ("pt", "ct"):
        result[field] = int(_decode_bcd(payload[pos:pos + 3], 0)); pos += 3
    for field in MONEY_FIELDS:
        result[field] = _decode_bcd(payload[pos:pos + 4], 2); pos += 4
    result["kwMax"] = _decode_bcd(payload[pos:pos + 3], 4); pos += 3
    result["sleepKw"] = (payload[pos] - 0x33) & 0xFF; pos += 1
    for field in (*PRICE_FIELDS, *STEP_FIELDS[:3], *STEP_PRICE_FIELDS[:4], *STEP_FIELDS[3:], *STEP_PRICE_FIELDS[4:]):
        result[field] = _decode_bcd(payload[pos:pos + 4], 4); pos += 4
    if pos != PAYLOAD_LENGTH:
        raise AssertionError("Parameter 1 field map does not cover payload")
    return result


def classify_write_response(frame: bytes) -> dict[str, Any]:
    """Classify an actual DL/T645 response without guessing C3 error semantics."""
    raw = bytes(frame)
    while raw.startswith(b"\xFE"):
        raw = raw[1:]
    if len(raw) < 10 or raw[0] != 0x68 or raw[7] != 0x68:
        return {"state": "ambiguous", "error": "malformed Parameter 1 response"}
    control = raw[8]
    if control == 0x83:
        return {"state": "accepted", "control": control}
    if control == 0xC3:
        data = raw[10:10 + raw[9]]
        error_byte = ((data[0] - 0x33) & 0xFF) if data else None
        return {"state": "rejected", "control": control, "error_byte": error_byte}
    return {"state": "ambiguous", "control": control, "error": "unexpected Parameter 1 response control code"}
