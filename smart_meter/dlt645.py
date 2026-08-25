# smart_meter/dlt645.py
"""
DL/T 645 parsing helpers for reply frames.

Frame layout (DL/T645-2007):
    FE FE FE FE (optional wake-ups)
    68 A0 A1 A2 A3 A4 A5 68 C L DATA... CS 16

- Address bytes A0..A5 are little-endian BCD; we render meter_number as reversed hex (e.g. 16 00 51 19 06 25 -> "250619510016")
- DATA field for a read-reply is (DI[4] + PAYLOAD), all bytes offset by +0x33.
- DI is 4 bytes, little-endian; we decode as big-endian hex string (e.g. 0x02 0x80 0x11 0xFF -> "028011FF").
- CS is sum of C + L + DATA (i.e., from the SECOND 0x68+1) modulo 256. We also accept two vendor variants as fallbacks.
"""

from decimal import Decimal, InvalidOperation
import re
from typing import Tuple, Optional, Dict

# ----------------------------
# Helpers
# ----------------------------


def _decode_bcd(raw: bytes, decimals: int = 2) -> float:
    """
    Decode DL/T645-style BCD bytes where each raw byte has +0x33 offset.
    Returns float rounded by string placement of decimal point.
    """
    if not raw:
        return 0.0
    # undo +0x33
    decoded = [(b - 0x33) & 0xFF for b in raw]
    digits = []
    for byte in reversed(decoded):
        hi = (byte >> 4) & 0xF
        lo = byte & 0xF
        # be tolerant of non-BCD nibbles
        if hi > 9:
            hi = 0
        if lo > 9:
            lo = 0
        digits.append(str(hi))
        digits.append(str(lo))
    num = "".join(digits).lstrip("0") or "0"
    if decimals <= 0:
        return float(num)
    if len(num) <= decimals:
        return float("0." + num.zfill(decimals))
    return float(num[:-decimals] + "." + num[-decimals:])


def _decode_hex_no33(raw: bytes) -> str:
    """Subtract 0x33 and return hex string (big-endian) of those bytes reversed to BE."""
    if not raw:
        return ""
    decoded = bytes(((b - 0x33) & 0xFF) for b in raw)
    # status/word etc typically little-endian; show big-endian hex
    return decoded[::-1].hex().upper()


def _extract_meter_number(frame: bytes, start: int) -> str:
    """
    Address bytes are 6 bytes after first 0x68, little-endian BCD-ish.
    We render as reversed hex concatenated, e.g. 16 00 51 19 06 25 -> "250619510016".
    """
    addr = frame[start + 1: start + 7]  # 6 bytes
    return "".join(f"{b:02X}" for b in addr[::-1])


def _extract_di(data: bytes) -> str:
    """DI is first 4 bytes (offset +0x33), little-endian. Return big-endian hex."""
    if len(data) < 4:
        return ""
    di = bytes(((b - 0x33) & 0xFF) for b in data[0:4])
    return di[::-1].hex().upper()


def relay_state_from_status_word(status_word: str):
    """Return ON/OFF from the manufacturer 2024 Wi-Fi status word.

    For DI 0x028011FF, Bit8 is the relay/switch state: 0 = closed
    (power ON), 1 = tripped/open (power OFF). ``status_word`` is
    stored here as a big-endian hexadecimal word.
    """
    sw = str(status_word or "").strip().replace("0x", "").replace("0X", "")
    if not sw:
        return None
    try:
        value = int(sw, 16)
    except (TypeError, ValueError):
        return None
    return "off" if (value & 0x0100) else "on"


def power_protection_from_status_word(status_word: str):
    """Return documented Bit15 power-protection flag, or None if unknown."""
    sw = str(status_word or "").strip().replace("0x", "").replace("0X", "")
    if not sw:
        return None
    try:
        return bool(int(sw, 16) & 0x8000)
    except (TypeError, ValueError):
        return None

# ----------------------------
# Checksum
# ----------------------------


def verify_checksum(frame: bytes, start: int) -> Tuple[bool, str]:
    """
    Try several checksum windows. Return (ok, style_name).
    Standard: sum from (second 0x68 + 1) i.e. control code, through last DATA byte.
    """
    if start < 0 or len(frame) < start + 12:
        return False, "short"

    # anchors
    second_68 = start + 7
    ctrl_idx = second_68 + 1
    L = frame[ctrl_idx + 1]
    data_end = ctrl_idx + 2 + L      # index of first byte AFTER DATA
    if data_end >= len(frame):
        return False, "len"

    cs_index = data_end
    term_ok = (frame[-1] == 0x16)   # typical termination
    # 1) Standard: sum C..last DATA
    calc = sum(frame[ctrl_idx: data_end]) & 0xFF
    if cs_index < len(frame) and calc == frame[cs_index]:
        return True, "std"

    # 2) Vendor variant: include second 0x68 in sum
    calc2 = sum(frame[second_68: data_end]) & 0xFF
    if calc2 == frame[cs_index]:
        return True, "incl_2nd68"

    # 3) Vendor variant: include first 0x68 in sum
    calc3 = sum(frame[start: data_end]) & 0xFF
    if calc3 == frame[cs_index]:
        return True, "incl_1st68"

    return False, "fail"

# ----------------------------
# Bulk summary parser (DI 0x028011FF)
# ----------------------------


def parse_bulk_summary_frame(frame: bytes, start_idx: int) -> Dict:
    """
    Parse DI=0x028011FF bulk summary with extended counters.
    Returns dict with present fields (missing omitted).
    """
    L = frame[start_idx + 9]
    dat = frame[start_idx + 10: start_idx + 10 + L]

    # skip the DI bytes at the head of DATA
    pos = 4
    out: Dict = {}

    try:
        # money (4B each, 2dp)
        out["balance"] = _decode_bcd(dat[pos:pos+4], 2)
        pos += 4
        out["overdraft"] = _decode_bcd(dat[pos:pos+4], 2)
        pos += 4

        # voltages (2B each)
        out["voltage_a"] = _decode_bcd(dat[pos:pos+2], 1)
        pos += 2
        out["voltage_b"] = _decode_bcd(dat[pos:pos+2], 1)
        pos += 2
        # vendor spec says 2 bytes but 3dp — keep as found in your device
        out["voltage_c"] = _decode_bcd(dat[pos:pos+2], 3)
        pos += 2

        # currents (3B each, 3dp)
        out["current_a"] = _decode_bcd(dat[pos:pos+3], 3)
        pos += 3
        out["current_b"] = _decode_bcd(dat[pos:pos+3], 3)
        pos += 3
        out["current_c"] = _decode_bcd(dat[pos:pos+3], 3)
        pos += 3

        # power (3B each, 4dp)
        out["total_power"] = _decode_bcd(dat[pos:pos+3], 4)
        pos += 3
        out["power_a"] = _decode_bcd(dat[pos:pos+3], 4)
        pos += 3
        out["power_b"] = _decode_bcd(dat[pos:pos+3], 4)
        pos += 3
        out["power_c"] = _decode_bcd(dat[pos:pos+3], 4)
        pos += 3

        # power factor (2B each, 3dp)
        out["pf_total"] = _decode_bcd(dat[pos:pos+2], 3)
        pos += 2
        out["pf_a"] = _decode_bcd(dat[pos:pos+2], 3)
        pos += 2
        out["pf_b"] = _decode_bcd(dat[pos:pos+2], 3)
        pos += 2
        out["pf_c"] = _decode_bcd(dat[pos:pos+2], 3)
        pos += 2
    except Exception:
        # if short, return whatever we parsed so far
        return out

    # Extended counters (4B, 2dp) — order by manufacturer sheet
    extended_order = [
        # current totals
        "total_energy",
        "peak_total_energy",
        "peak_total_consumption",
        "valley_total_consumption",
        "flat_total_consumption",

        # previous 1 day
        "prev1_day_energy",
        "prev1_day_peak_energy",
        "prev1_day_valley_energy",
        "prev1_day_flat_energy",

        # last 2 days
        "last2_days_energy",
        "last2_days_peak_energy",
        "last2_days_valley_energy",
        "last2_days_flat_energy",

        # last 3 days
        "last3_days_energy",
        "last3_days_peak_energy",
        "last3_days_valley_energy",
        "last3_days_flat_energy",
    ]

    for key in extended_order:
        if pos + 4 <= len(dat):
            out[key] = _decode_bcd(dat[pos:pos+4], 2)
            pos += 4
        else:
            break

    # Optional final status word (2 bytes)
    if pos + 2 <= len(dat):
        out["status_word"] = _decode_hex_no33(dat[pos:pos+2])

    return out

# ----------------------------
# Top-level frame parsing
# ----------------------------


def parse_frame(frame: bytes, accept_bad_checksum: bool = False) -> Optional[dict]:
    """
    Return dict:
      {
        "meter_number": "...",
        "control_code": int,
        "di": "028011FF" or "80808080" etc,
        "data": { ... } or None,
        "cs_style": "std"/"incl_2nd68"/"incl_1st68"/"fail"
      }
    """
    # strip leading FE (wakeups)
    i = 0
    while i < len(frame) and frame[i] == 0xFE:
        i += 1
    frame = frame[i:]

    start = frame.find(b'\x68')
    if start < 0 or len(frame) < start + 12:
        return None

    ok, style = verify_checksum(frame, start)
    # still parse further if accept_bad_checksum; otherwise enforce ok
    if not ok and not accept_bad_checksum:
        return None

    meter_number = _extract_meter_number(frame, start)
    ctrl = frame[start + 8]
    L = frame[start + 9]
    data = frame[start + 10: start + 10 + L]

    di = _extract_di(data) if len(data) >= 4 else ""

    parsed = {
        "meter_number": meter_number,
        "control_code": ctrl,
        "di": di,
        "data": None,
        "cs_style": style,
    }

    # Keep-alive / no payload
    if L == 4 and di == "80808080":
        return parsed

    # Bulk summary with extended counters
    if di == "028011FF":
        parsed["data"] = parse_bulk_summary_frame(frame, start_idx=start)
        return parsed

    # Unknown DI: still return header info
    return parsed

# --- builders for commands (read / write / control) ---

CHECKSUM_MODES = ("std", "incl_2nd68", "incl_1st68")
FRAME_PREFIX = b"\xFE" * 4


def _bcd_bytes_from_amount(amount: float, bytes_count: int = 4, decimals: int = 2) -> bytes:
    """Encode an amount as zero-padded BCD digit pairs, before the +0x33 transform.

    This preserves the project's existing representation (for example Rs 1.00 is
    ``00 00 01 00``). Manufacturer confirmation of the byte order is still required.
    """
    scaled = int(round(amount * (10 ** decimals)))
    if scaled < 0:
        raise ValueError("amount must not be negative")
    s = f"{scaled:0{bytes_count * 2}d}"
    if len(s) > bytes_count * 2:
        raise ValueError(f"amount does not fit in {bytes_count} BCD bytes")
    return bytes((int(s[i]) << 4) | int(s[i + 1]) for i in range(0, len(s), 2))


def _add_33(data: bytes) -> bytes:
    return bytes(((b + 0x33) & 0xFF) for b in data)


def _addr_from_meter_number(meter_number: str) -> bytes:
    """Return a 12-hex-character meter address in DL/T645 on-wire byte order."""
    if len(meter_number) != 12:
        raise ValueError("meter number must be exactly 12 hexadecimal characters")
    try:
        return bytes.fromhex(meter_number)[::-1]
    except ValueError as exc:
        raise ValueError("meter number must be exactly 12 hexadecimal characters") from exc


def calculate_outbound_checksum(frame_without_checksum: bytes, checksum_mode: str) -> int:
    """Calculate one explicit outbound checksum window.

    ``frame_without_checksum`` starts at the first 0x68 and ends at the final DATA
    byte. It must not contain the checksum, terminator, or optional FE preamble.
    """
    if checksum_mode not in CHECKSUM_MODES:
        raise ValueError(f"checksum_mode must be one of {', '.join(CHECKSUM_MODES)}")
    if len(frame_without_checksum) < 10 or frame_without_checksum[0] != 0x68:
        raise ValueError("frame_without_checksum must start with a complete DL/T645 header")
    start_index = {"std": 8, "incl_2nd68": 7, "incl_1st68": 0}[checksum_mode]
    return sum(frame_without_checksum[start_index:]) & 0xFF


def build_frame(
    meter_number: str,
    control: int,
    data_field: bytes,
    *,
    checksum_mode: str = "std",
    include_preamble: bool = False,
) -> bytes:
    """Build a DL/T645 frame with an explicitly selectable checksum window.

    The generic default remains ``std`` for backward compatibility. Manufacturer-
    specific callers must choose their required mode rather than changing this default.
    """
    if not 0 <= control <= 0xFF:
        raise ValueError("control must fit in one byte")
    if len(data_field) > 0xFF:
        raise ValueError("data field is too long")
    body = (
        b"\x68"
        + _addr_from_meter_number(meter_number)
        + b"\x68"
        + bytes([control, len(data_field)])
        + data_field
    )
    checksum = calculate_outbound_checksum(body, checksum_mode)
    frame = body + bytes([checksum, 0x16])
    return (FRAME_PREFIX + frame) if include_preamble else frame


def _require_bytes(name: str, value: bytes, expected_length: Optional[int] = None) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError(f"{name} must be bytes")
    if expected_length is not None and len(value) != expected_length:
        raise ValueError(f"{name} must be exactly {expected_length} bytes")
    return value


CHARGE_DATA_IDENTIFIERS = {
    "recharge": "070102FF",
    "topup": "070102FF",
    "refund": "070108FF",
    "070102ff": "070102FF",
    "070108ff": "070108FF",
}
CHARGE_OPERATOR = bytes.fromhex("77665544")
CHARGE_MAC = bytes.fromhex("33333333")
CHARGE_DATA_LENGTH = 0x22


def _amount_to_cents(amount) -> int:
    """Convert an exact money value to positive integer cents.

    Binary floats are deliberately rejected because a value such as ``1.15`` may
    not have an exact base-2 representation. Decimal, integer, and decimal-string
    inputs reproduce the manufacturer's ``int(money * 100)`` result without
    silently discarding a fractional cent.
    """
    if isinstance(amount, bool) or isinstance(amount, float):
        raise TypeError("amount must be a Decimal, integer, or decimal string, not float")
    try:
        value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("amount must be a valid decimal money value") from exc
    if not value.is_finite():
        raise ValueError("amount must be finite")

    cents = value * 100
    if cents != cents.to_integral_value():
        raise ValueError("amount must not contain fractional cents")
    cents_int = int(cents)
    if cents_int <= 0:
        raise ValueError("amount must be positive")
    if cents_int > 0xFFFFFFFF:
        raise ValueError("amount in cents must fit in four bytes")
    return cents_int


def _charge_data_identifier(operation: str) -> str:
    if not isinstance(operation, str):
        raise TypeError("operation must be recharge, refund, 070102FF, or 070108FF")
    key = operation.strip().lower()
    try:
        return CHARGE_DATA_IDENTIFIERS[key]
    except KeyError as exc:
        raise ValueError("charge DI must be 070102FF (recharge) or 070108FF (refund)") from exc


def _charge_order_bytes(order_number: str) -> bytes:
    if not isinstance(order_number, str):
        raise TypeError("order number must be a hexadecimal string")
    value = order_number.strip()
    if not value:
        raise ValueError("order number must not be empty")
    if len(value) > 16:
        raise ValueError("order number must be at most 16 hexadecimal characters")
    if re.fullmatch(r"[0-9A-Fa-f]+", value) is None:
        raise ValueError("order number must contain only hexadecimal characters")
    try:
        return bytes.fromhex(value.zfill(16))
    except ValueError as exc:
        raise ValueError("order number must contain only hexadecimal characters") from exc


def build_charge_frame(
    meter_number: str,
    operation: str,
    order_number: str,
    amount,
) -> bytes:
    """Build the manufacturer's complete recharge or refund frame.

    The returned frame always includes four FE wake-up bytes, control ``03``, a
    34-byte DATA field, and the manufacturer's checksum window beginning at the
    first ``68`` after the wake-up bytes. This function only builds bytes and has
    no transport or database side effects.
    """
    if not isinstance(meter_number, str) or re.fullmatch(
        r"[0-9A-Fa-f]{12}", meter_number
    ) is None:
        raise ValueError("meter number must be exactly 12 hexadecimal characters")
    meter_address = _addr_from_meter_number(meter_number)
    data_identifier = _charge_data_identifier(operation)
    cents = _amount_to_cents(amount)
    order = _charge_order_bytes(order_number)

    data_field = (
        _add_33(bytes.fromhex(data_identifier)[::-1])
        + CHARGE_OPERATOR
        + _add_33(cents.to_bytes(4, byteorder="little", signed=False))
        + _add_33(order[::-1])
        + CHARGE_MAC
        + _add_33(meter_address)
        + CHARGE_MAC
    )
    if len(data_field) != CHARGE_DATA_LENGTH:
        raise ValueError("manufacturer charge DATA field must be exactly 0x22 bytes")

    frame = build_frame(
        meter_number,
        0x03,
        data_field,
        checksum_mode="incl_1st68",
        include_preamble=True,
    )
    if verify_checksum(frame[4:], 0) != (True, "incl_1st68"):
        raise ValueError("manufacturer charge frame checksum is not incl_1st68")
    return frame


def build_topup_frame(
    meter_number: str,
    amount,
    order_number: str,
) -> bytes:
    """Compatibility name for the manufacturer's recharge operation."""
    return build_charge_frame(meter_number, "recharge", order_number, amount)


def build_init_amount_frame(
    meter_number: str,
    amount: float,
    *,
    operator: bytes,
    mac1: bytes,
    purchase_count: bytes,
    mac2: bytes,
    checksum_mode: str,
    include_preamble: bool = False,
) -> bytes:
    """Build a 070103FF structure with no implicit MAC or purchase-count values."""
    plain = (
        bytes.fromhex("070103FF")[::-1]
        + _require_bytes("operator", operator, 4)
        + _bcd_bytes_from_amount(amount, 4, 2)
        + _require_bytes("mac1", mac1, 4)
        + _require_bytes("purchase_count", purchase_count, 4)
        + _require_bytes("mac2", mac2, 4)
    )
    return build_frame(
        meter_number, 0x03, _add_33(plain),
        checksum_mode=checksum_mode, include_preamble=include_preamble,
    )


def build_refund_frame(
    meter_number: str,
    amount,
    order_number: str,
) -> bytes:
    """Compatibility name for the manufacturer's refund operation."""
    return build_charge_frame(meter_number, "refund", order_number, amount)

# ---- READ price parameter: DI=070104FF, C=0x11 ----


def build_read_price_param_frame(meter_number: str):
    di_be = bytes.fromhex("070104FF")
    di_le = di_be[::-1]
    data_onwire = _add_33(di_le)  # DATA is DI only for a read
    return build_frame(meter_number, 0x11, data_onwire)

# ---- (Optional) parse the tail prices from a 070104FF reply ----


def _decode_bcd_33(raw: bytes, decimals: int) -> float:
    decoded = [(b - 0x33) & 0xFF for b in raw]
    digits = []
    for byte in reversed(decoded):
        hi = (byte >> 4) & 0xF
        lo = byte & 0xF
        if hi > 9:
            hi = 0
        if lo > 9:
            lo = 0
        digits.append(str(hi))
        digits.append(str(lo))
    s = "".join(digits).lstrip("0") or "0"
    if decimals <= 0:
        return float(s)
    if len(s) <= decimals:
        return float("0." + s.zfill(decimals))
    return float(s[:-decimals] + "." + s[-decimals:])


def parse_070104ff_prices(reply_frame: bytes) -> dict:
    """
    Minimal parser: assumes reply is 0x91 with DI=070104FF and that the last
    16 bytes of DATA correspond to:
      - rate1_price (4B, 4dp)
      - rate2_price (4B, 4dp)
      - rate1_kwh   (4B, 4dp)
      - rate2_kwh   (4B, 4dp)
    If your device orders differently, adjust the slicing.
    """
    start = reply_frame.find(b'\x68')
    if start < 0 or len(reply_frame) < start + 12:
        return {}
    L = reply_frame[start+9]
    data = reply_frame[start+10:start+10+L]
    # verify DI
    di = bytes(((b - 0x33) & 0xFF) for b in data[:4])[::-1].hex().upper()
    if di != "070104FF":
        return {}
    if len(data) < 4 + 16:
        return {"di": di}  # payload too short
    tail = data[-16:]  # last 4 fields
    r1p = _decode_bcd_33(tail[0:4], 4)
    r2p = _decode_bcd_33(tail[4:8], 4)
    r1k = _decode_bcd_33(tail[8:12], 4)
    r2k = _decode_bcd_33(tail[12:16], 4)
    return {"di": di, "rate1_price": r1p, "rate2_price": r2p, "rate1_kwh": r1k, "rate2_kwh": r2k}


# ---- Generic READ for any DI (e.g., 028011FF, 070104FF, etc.) ----
def build_read_frame_for_di(meter_number: str, di_hex: str) -> bytes:
    """Generic READ (C=0x11) for any DI like '028011FF' or '070104FF'."""
    di_be = bytes.fromhex(di_hex)
    di_le = di_be[::-1]
    return build_frame(meter_number, 0x11, _add_33(di_le))
