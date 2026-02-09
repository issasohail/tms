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

# --- builders for commands (write / control) ---


def _bcd_bytes_from_amount(amount: float, bytes_count: int = 4, decimals: int = 2) -> bytes:
    """
    Convert e.g. 123.45 -> bcd(0000012345) across bytes_count bytes, big-endian digit order,
    then apply +0x33 offset per DL/T645 when you place into DATA.
    """
    scaled = int(round(amount * (10 ** decimals)))
    s = f"{scaled:0{bytes_count*2}d}"  # 2 nibbles per byte
    # big-endian BCD (hi nibble first per digit pairs)
    out = bytearray()
    for i in range(0, len(s), 2):
        out.append((int(s[i]) << 4) | int(s[i+1]))
    return bytes(out)


def _add_33(data: bytes) -> bytes:
    return bytes(((b + 0x33) & 0xFF) for b in data)


def _addr_from_meter_number(meter_number: str) -> bytes:
    """
    Meter number as 12 hex chars (e.g. '250619510016'), return 6 bytes little-endian on-wire.
    """
    # interpret as hex pairs big-endian then reverse to little-endian on wire
    be = bytes.fromhex(meter_number)
    return be[::-1]


def build_frame(meter_number: str, control: int, data_field: bytes) -> bytes:
    """
    Build a full frame: [68][addr6][68][C][L][DATA][CS][16]
    CS = sum from C through last DATA modulo 256 (standard window).
    """
    start = b'\x68'
    addr = _addr_from_meter_number(meter_number)
    head2 = b'\x68'
    C = bytes([control])
    L = bytes([len(data_field)])
    core = C + L + data_field
    cs = bytes([sum(core) & 0xFF])
    return start + addr + head2 + core + cs + b'\x16'


def build_topup_frame(meter_number: str, amount: float, order_no: bytes, operator=b'\x11\x22\x33\x44',
                      mac1=b'\x00\x00\x00\x00', schedule_no=b'\x00'*6, mailing_addr=b'', mac2=b'\x00\x00\x00\x00'):
    # DI little-endian on the wire, but we construct DI as big-endian then reverse and +0x33 in the DATA
    di_be = bytes.fromhex("070102FF")
    di_le = di_be[::-1]
    amt_bcd = _bcd_bytes_from_amount(amount, 4, 2)
    # Assemble DATA before +0x33:
    data_plain = di_le + operator + amt_bcd + order_no + \
        mac1 + schedule_no + mailing_addr + mac2
    data_onwire = _add_33(data_plain)
    return build_frame(meter_number, 0x03, data_onwire)


def build_init_amount_frame(meter_number: str, amount: float, operator=b'\x11\x22\x33\x44'):
    di_be = bytes.fromhex("070103FF")
    di_le = di_be[::-1]
    amt_bcd = _bcd_bytes_from_amount(amount, 4, 2)
    data_plain = di_le + operator + amt_bcd + \
        (b'\x00\x00\x00\x00') + (b'\x00\x00\x00\x00') + (b'\x00\x00\x00\x00')
    data_onwire = _add_33(data_plain)
    return build_frame(meter_number, 0x03, data_onwire)


def build_refund_frame(meter_number: str, amount: float, order_no: bytes, operator=b'\x11\x22\x33\x44'):
    di_be = bytes.fromhex("070108FF")
    di_le = di_be[::-1]
    amt_bcd = _bcd_bytes_from_amount(amount, 4, 2)
    data_plain = di_le + operator + amt_bcd + order_no + \
        (b'\x00\x00\x00\x00') + (b'\x00'*6) + (b'\x00\x00\x00\x00')
    data_onwire = _add_33(data_plain)
    return build_frame(meter_number, 0x03, data_onwire)
# =========================
# WRITE/CONTROL FRAME BUILDERS (prepaid)
# =========================


def _bcd_bytes_from_amount(amount: float, bytes_count: int = 4, decimals: int = 2) -> bytes:
    scaled = int(round(amount * (10 ** decimals)))
    s = f"{scaled:0{bytes_count*2}d}"
    out = bytearray()
    for i in range(0, len(s), 2):
        out.append((int(s[i]) << 4) | int(s[i+1]))
    return bytes(out)


def _add_33(data: bytes) -> bytes:
    return bytes(((b + 0x33) & 0xFF) for b in data)


def _addr_from_meter_number(meter_number: str) -> bytes:
    be = bytes.fromhex(meter_number)
    return be[::-1]


def build_frame(meter_number: str, control: int, data_field: bytes) -> bytes:
    start = b'\x68'
    addr = _addr_from_meter_number(meter_number)
    head2 = b'\x68'
    C = bytes([control])
    L = bytes([len(data_field)])
    core = C + L + data_field
    cs = bytes([sum(core) & 0xFF])
    return start + addr + head2 + core + cs + b'\x16'

# ---- Prepaid commands ----


def build_topup_frame(meter_number: str, amount: float, order_no: bytes,
                      operator=b'\x11\x22\x33\x44',
                      mac1=b'\x00\x00\x00\x00',
                      schedule_no=b'\x00'*6,
                      mailing_addr=b'',
                      mac2=b'\x00\x00\x00\x00'):
    di_be = bytes.fromhex("070102FF")
    di_le = di_be[::-1]
    amt_bcd = _bcd_bytes_from_amount(amount, 4, 2)
    data_plain = di_le + operator + amt_bcd + order_no + \
        mac1 + schedule_no + mailing_addr + mac2
    data_onwire = _add_33(data_plain)
    return build_frame(meter_number, 0x03, data_onwire)


def build_init_amount_frame(meter_number: str, amount: float, operator=b'\x11\x22\x33\x44'):
    di_be = bytes.fromhex("070103FF")
    di_le = di_be[::-1]
    amt_bcd = _bcd_bytes_from_amount(amount, 4, 2)
    data_plain = di_le + operator + amt_bcd + \
        (b'\x00\x00\x00\x00') + (b'\x00\x00\x00\x00') + (b'\x00\x00\x00\x00')
    data_onwire = _add_33(data_plain)
    return build_frame(meter_number, 0x03, data_onwire)


def build_refund_frame(meter_number: str, amount: float, order_no: bytes, operator=b'\x11\x22\x33\x44'):
    di_be = bytes.fromhex("070108FF")
    di_le = di_be[::-1]
    amt_bcd = _bcd_bytes_from_amount(amount, 4, 2)
    data_plain = di_le + operator + amt_bcd + order_no + \
        (b'\x00\x00\x00\x00') + (b'\x00'*6) + (b'\x00\x00\x00\x00')
    data_onwire = _add_33(data_plain)
    return build_frame(meter_number, 0x03, data_onwire)
# ---- generic helpers already added previously ----


def _add_33(data: bytes) -> bytes:
    return bytes(((b + 0x33) & 0xFF) for b in data)


def _addr_from_meter_number(meter_number: str) -> bytes:
    be = bytes.fromhex(meter_number)
    return be[::-1]


def build_frame(meter_number: str, control: int, data_field: bytes) -> bytes:
    start = b'\x68'
    addr = _addr_from_meter_number(meter_number)
    head2 = b'\x68'
    C = bytes([control])
    L = bytes([len(data_field)])
    core = C + L + data_field
    cs = bytes([sum(core) & 0xFF])
    return start + addr + head2 + core + cs + b'\x16'

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
