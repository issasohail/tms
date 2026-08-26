#!/usr/bin/env python3
"""
make_charge -- DLT645 prepaid meter recharge/refund command generator.

Python port of the Java method org.eiot.modules.utils.Command2007#makeCharge.

One function serves both operations; the dataIndex selects the mode:
    recharge: dataIndex = "070102FF"
    refund:   dataIndex = "070108FF"

Examples:
    Topup: meterNum="260305510012"  orderNo="1240826202124138"  money=100
    Refund:   meterNum="260305510012"  orderNo="1240826202124139"  money=10

Frame layout (bytes):
    FE FE FE FE 68
    | meterNo(6, reversed)                       -- address field, raw
    | 68 03 22                                   -- start2, control, length(34)
    | dataIndex(4, reversed, +0x33)
    | operator(77 66 55 44)
    | amount(4, little-endian, +0x33)            -- money * 100, in cents
    | orderNo(8, reversed, +0x33)                -- padded to 16 hex chars
    | MAC1(33 33 33 33)
    | meterNo(6, reversed, +0x33)
    | MAC2(33 33 33 33)
    | checksum(bits[4:end] & 0xFF) 16
"""

PRE_QTY = 4  # number of leading 0xFE preamble bytes


def hex_str_to_bytes(hex_str: str) -> list:
    """Parse a hex string into a list of byte values (0-255)."""
    hex_str = hex_str.strip()
    return [int(hex_str[i:i + 2], 16) for i in range(0, len(hex_str), 2)]


def add_zero_for_num_left(value: str, length: int) -> str:
    """Left-pad the string with '0' characters up to the given length."""
    return value.zfill(length)


def lo_byte33(byte_val: int) -> int:
    """Encode a byte by adding 0x33 (DLT645 data-encoding offset)."""
    return (byte_val + 0x33) & 0xFF


def int_to_buf(int_val: int) -> list:
    """Convert an int to a 4-byte little-endian list."""
    return [(int_val >> (i * 8)) & 0xFF for i in range(4)]


def checksum(buf: list, end_idx: int, beg_idx: int) -> int:
    """Sum bytes in [beg_idx, end_idx) and take the low byte (AcUtil.checkSum)."""
    return sum(buf[beg_idx:end_idx]) & 0xFF


def make_charge(meter_num: str, data_index: str, order_no: str, money) -> list:
    """Build the DLT645 recharge/refund command frame.

    Args:
        meter_num:  meter number as a hex string, e.g. "260305510012".
        data_index: data identifier; "070102FF" = recharge, "070108FF" = refund.
        order_no:   order serial number (hex string), padded to 16 chars.
        money:      amount in yuan (float or int); stored as cents (money * 100).

    Returns:
        List of bytes (0-255) forming the complete frame.
    """
    order_no = add_zero_for_num_left(str(order_no).strip(), 16)
    buf = []

    # Preamble + start byte
    buf.extend([0xFE] * PRE_QTY)
    buf.append(0x68)

    # Meter number (address field), 6 bytes, transmitted in reverse order
    meter = hex_str_to_bytes(meter_num)
    buf.extend(reversed(meter))

    buf.append(0x68)   # start byte 2
    buf.append(0x03)   # control code
    buf.append(0x22)   # data length = 34

    # Data identifier, reversed, each byte offset by +0x33
    ident = hex_str_to_bytes(data_index)
    buf.extend(lo_byte33(b) for b in reversed(ident))

    # Operator code
    buf.extend([0x77, 0x66, 0x55, 0x44])

    # Purchase amount: yuan * 100 -> cents, little-endian, +0x33
    amount = int(float(money) * 100)
    buf.extend(lo_byte33(b) for b in int_to_buf(amount))

    # Order number, 16 hex chars -> 8 bytes, reversed, +0x33
    order = hex_str_to_bytes(order_no)
    buf.extend(lo_byte33(b) for b in reversed(order))

    # MAC1
    buf.extend([0x33, 0x33, 0x33, 0x33])

    # Meter number again, reversed, +0x33
    buf.extend(lo_byte33(b) for b in reversed(meter))

    # MAC2
    buf.extend([0x33, 0x33, 0x33, 0x33])

    # Checksum over bytes [PRE_QTY, end), then terminator
    buf.append(checksum(buf, len(buf), PRE_QTY))
    buf.append(0x16)

    return buf


def to_hex_string(buf: list) -> str:
    """Format a byte list as an uppercase hex string."""
    return ''.join(f'{b:02X}' for b in buf)


if __name__ == '__main__':
    # Recharge example
    recharge = make_charge('260305510012', '070102FF', '1240826202124138', 100)
    print('Recharge:', to_hex_string(recharge))

    # Refund example
    refund = make_charge('260305510012', '070108FF', '1240826202124139', 10)
    print('Refund:  ', to_hex_string(refund))
