# smart_meter/dlt645_money.py
from __future__ import annotations

FRAME_PREFIX = bytes([0xFE, 0xFE, 0xFE, 0xFE])
CTRL_WRITE = 0x03  # per manual sections for prepaid write
START, END = 0x68, 0x16

# DIs (little-endian in the frame, before +33h transform)
DI_AMOUNT_INIT = bytes([0xFF, 0x03, 0x01, 0x07])  # 070103FF
DI_POWER_SALE = bytes([0xFF, 0x02, 0x01, 0x07])  # 070102FF
# 028011FF (for reads, ctrl=0x11)
DI_QUERY_COPY = bytes([0xFF, 0x11, 0x80, 0x02])

# Fixed fields from the manual
# “11223344” as little-endian bytes
OPERATOR_CODE_LE = bytes([0x44, 0x33, 0x22, 0x11])
ZERO4 = bytes([0x00, 0x00, 0x00, 0x00])


def _addr_bcd_rev(addr12: str) -> bytes:
    if len(addr12) != 12 or not addr12.isdigit():
        raise ValueError("meter address must be 12 decimal digits")
    # BCD pairs → bytes → reversed (DL/T645 address order)
    pairs = [int(addr12[i:i+2], 16) for i in range(0, 12, 2)]
    return bytes(pairs)[::-1]


def _bcd_amount_cents_le(amount_rupees: float, width_bytes: int = 4) -> bytes:
    cents = int(round(amount_rupees * 100))
    s = f"{cents:0{width_bytes*2}d}"  # zero-padded decimal digits
    # pack two digits per byte, little-endian bytes
    raw = bytes(int(s[i:i+2], 16) for i in range(0, len(s), 2))
    return raw  # already least-significant byte first by construction


def _plus_33h(b: bytes) -> bytes:
    return bytes((x + 0x33) & 0xFF for x in b)


def _checksum(buf: bytes) -> int:
    return sum(buf) & 0xFF


def build_amount_init_frame(addr12: str, amount_rupees: float) -> bytes:
    """
    Write meter 'Provisioned amount' (070103FF). Use 0.00 to reset display balance.
    Data block = ID + Operator + Amount(4, 2dp) + MAC1(00000000) + Purchases(00000000) + MAC2(00000000)
    """
    addr = _addr_bcd_rev(addr12)
    amount = _bcd_amount_cents_le(amount_rupees, 4)

    data = (
        DI_AMOUNT_INIT
        + OPERATOR_CODE_LE
        + amount
        + ZERO4  # MAC1
        + ZERO4  # Purchase count
        + ZERO4  # MAC2
    )
    enc = _plus_33h(data)

    inner = bytearray()
    inner.append(START)
    inner += addr
    inner.append(START)
    inner.append(CTRL_WRITE)      # 0x03
    inner.append(len(enc))
    inner += enc
    inner.append(_checksum(inner[4:]))
    inner.append(END)
    return FRAME_PREFIX + bytes(inner)


def build_power_sale_frame(addr12: str, amount_rupees: float, order_no_8b: bytes) -> bytes:
    """
    Optional: 070102FF purchase with an 8-byte order number.
    The minimal safe block per manual: ID + Operator + Amount(4,2dp) + Order(8) + MAC1(00000000) + Schedule(6?) + MAC2(00000000)
    Fill schedule bytes as needed for your environment (often 6 bytes of address).
    """
    if len(order_no_8b) != 8:
        raise ValueError("order_no_8b must be 8 bytes")

    addr = _addr_bcd_rev(addr12)
    amount = _bcd_amount_cents_le(amount_rupees, 4)
    # pragmatic default; replace if your vendor needs something else
    schedule6 = addr[:6]

    data = (
        DI_POWER_SALE
        + OPERATOR_CODE_LE
        + amount
        + order_no_8b
        + ZERO4              # MAC1
        + schedule6          # 6 bytes
        + ZERO4              # MAC2
    )
    enc = _plus_33h(data)

    inner = bytearray()
    inner.append(START)
    inner += addr
    inner.append(START)
    inner.append(CTRL_WRITE)
    inner.append(len(enc))
    inner += enc
    inner.append(_checksum(inner[4:]))
    inner.append(END)
    return FRAME_PREFIX + bytes(inner)
