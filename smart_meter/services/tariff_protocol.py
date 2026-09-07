"""Strict, side-effect-free tariff block codecs for supported DL/T645 meters."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from smart_meter.dlt645 import verify_checksum
from smart_meter.utils.frames import build_read_register

SINGLE_RATE_DI = "070115FF"
MULTI_RATE_DI = "070104FF"
SCHEDULE_DI = "070105FF"
OPERATOR_WIRE = bytes.fromhex("77665544")

SINGLE_PAYLOAD_LENGTH = 63
SINGLE_PRICE_OFFSETS = (23, 27, 31, 35)
MULTI_PAYLOAD_LENGTH = 143
MULTI_RATE_COUNT_OFFSET = 23
MULTI_SET1_PRICE_OFFSETS = (55, 59, 63, 67)


class TariffProtocolError(ValueError):
    pass


def capability_di(capability: str) -> str:
    if capability == "single_rate":
        return SINGLE_RATE_DI
    if capability == "multi_rate":
        return MULTI_RATE_DI
    raise TariffProtocolError("Tariff capability must be confirmed before reading or writing")


def _wire_meter_number(value) -> str:
    """Return a padded BCD address without restricting the stored meter identifier."""
    value = str(value or "").strip()
    if not re.fullmatch(r"\d{1,12}", value):
        raise TariffProtocolError(
            "This stored meter number cannot be encoded as a DL/T645 BCD address; "
            "confirm the meter communication address before tariff operations"
        )
    return value.zfill(12)


def build_tariff_read_frame(meter_number, capability: str) -> bytes:
    return build_read_register(_wire_meter_number(meter_number), capability_di(capability))


def _price(value) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(Decimal("0.0001"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TariffProtocolError("Unit price must be a valid amount with four decimals") from exc
    if result < 0 or result > Decimal("9999.9999"):
        raise TariffProtocolError("Unit price must be between 0.0000 and 9999.9999")
    return result


def encode_price(value) -> bytes:
    scaled = int(_price(value) * 10000)
    digits = f"{scaled:08d}"
    plain = bytes.fromhex(digits)[::-1]
    return bytes((byte + 0x33) & 0xFF for byte in plain)


def decode_price(raw: bytes) -> Decimal:
    if len(raw) != 4:
        raise TariffProtocolError("Tariff price must occupy exactly four bytes")
    plain = bytes(((byte - 0x33) & 0xFF) for byte in raw)[::-1]
    if any((byte >> 4) > 9 or (byte & 0x0F) > 9 for byte in plain):
        raise TariffProtocolError("Tariff price contains invalid BCD data")
    return (Decimal(plain.hex()) / Decimal(10000)).quantize(Decimal("0.0001"))


def _decode_count(byte: int) -> int:
    byte = (byte - 0x33) & 0xFF
    high, low = byte >> 4, byte & 0x0F
    if high > 9 or low > 9:
        raise TariffProtocolError("Active rate count contains invalid BCD data")
    return high * 10 + low


def _encode_count(value: int) -> int:
    if value not in {1, 2, 3, 4}:
        raise TariffProtocolError("Active rate count must be between 1 and 4")
    return (((value // 10) << 4) | (value % 10)) + 0x33


def decode_payload(payload: bytes, capability: str) -> dict:
    expected = SINGLE_PAYLOAD_LENGTH if capability == "single_rate" else MULTI_PAYLOAD_LENGTH
    offsets = SINGLE_PRICE_OFFSETS if capability == "single_rate" else MULTI_SET1_PRICE_OFFSETS
    if len(payload) != expected:
        raise TariffProtocolError(f"{capability_di(capability)} payload must be exactly {expected} bytes")
    values = {"prices": [decode_price(payload[offset:offset + 4]) for offset in offsets]}
    if capability == "multi_rate":
        values["active_rate_count"] = _decode_count(payload[MULTI_RATE_COUNT_OFFSET])
    else:
        values["active_rate_count"] = 1
    return values


def mutate_payload(payload: bytes, capability: str, *, prices, active_rate_count=None, flat=False) -> bytes:
    """Copy and mutate only explicitly supported tariff fields."""
    decode_payload(payload, capability)  # strict length and existing BCD validation
    result = bytearray(payload)
    offsets = SINGLE_PRICE_OFFSETS if capability == "single_rate" else MULTI_SET1_PRICE_OFFSETS
    prices = list(prices)
    if capability == "single_rate" or flat:
        if len(prices) != 1:
            raise TariffProtocolError("Flat configuration requires one unit price")
        prices *= 4
    elif len(prices) != int(active_rate_count or 0):
        raise TariffProtocolError("Provide exactly one price for every active rate")
    if capability == "multi_rate":
        result[MULTI_RATE_COUNT_OFFSET] = _encode_count(int(active_rate_count))
    for offset, value in zip(offsets, prices):
        result[offset:offset + 4] = encode_price(value)
    return bytes(result)


def target_byte_indexes(capability: str, active_rate_count: int = 1, *, flat=False) -> set[int]:
    if capability == "single_rate":
        offsets = SINGLE_PRICE_OFFSETS
    else:
        offsets = MULTI_SET1_PRICE_OFFSETS if flat else MULTI_SET1_PRICE_OFFSETS[:active_rate_count]
    indexes = {index for offset in offsets for index in range(offset, offset + 4)}
    if capability == "multi_rate":
        indexes.add(MULTI_RATE_COUNT_OFFSET)
    return indexes


def build_tariff_write_frame(meter_number, capability: str, payload: bytes) -> bytes:
    meter_number = _wire_meter_number(meter_number)
    expected = SINGLE_PAYLOAD_LENGTH if capability == "single_rate" else MULTI_PAYLOAD_LENGTH
    if len(payload) != expected:
        raise TariffProtocolError(f"Cannot write a tariff payload unless it is exactly {expected} bytes")
    address = bytes.fromhex(meter_number)[::-1]
    encoded_di = bytes((byte + 0x33) & 0xFF for byte in bytes.fromhex(capability_di(capability))[::-1])
    data = encoded_di + OPERATOR_WIRE + payload
    inner = b"\x68" + address + b"\x68\x03" + bytes([len(data)]) + data
    return b"\xFE\xFE\xFE\xFE" + inner + bytes([sum(inner) & 0xFF, 0x16])


def parse_read_reply(frame, *, meter_number, capability: str) -> tuple[bytes, str]:
    raw = bytes.fromhex(frame) if isinstance(frame, str) else bytes(frame)
    start = next((index for index, byte in enumerate(raw) if byte == 0x68), -1)
    ok, _style = verify_checksum(raw, start)
    if not ok or start < 0 or len(raw) < start + 12 or raw[start + 7] != 0x68:
        raise TariffProtocolError("Meter returned a malformed or checksum-invalid tariff frame")
    control, length = raw[start + 8], raw[start + 9]
    data = raw[start + 10:start + 10 + length]
    if control != 0x91 or len(data) != length or len(data) < 4:
        raise TariffProtocolError("Meter did not return a successful tariff read response")
    response_di = bytes(((byte - 0x33) & 0xFF) for byte in data[:4])[::-1].hex().upper()
    if response_di != capability_di(capability):
        raise TariffProtocolError("Tariff response DI does not match the stored meter capability")
    expected_address = bytes.fromhex(_wire_meter_number(meter_number))[::-1]
    if raw[start + 1:start + 7] != expected_address:
        raise TariffProtocolError("Tariff response belongs to a different meter")
    payload = data[4:]
    decode_payload(payload, capability)
    return payload, raw.hex().upper()


def classify_write_reply(frame) -> str:
    if not frame:
        return "transport_only"
    raw = bytes.fromhex(frame) if isinstance(frame, str) else bytes(frame)
    start = next((index for index, byte in enumerate(raw) if byte == 0x68), -1)
    ok, _style = verify_checksum(raw, start)
    if not ok or start < 0:
        return "invalid"
    return {0x83: "acknowledged", 0xC3: "rejected"}.get(raw[start + 8], "invalid")
