# smart_meter/utils/frames.py
def _bcd_addr_le(meter_number: str) -> bytes:
    s = "".join(ch for ch in str(meter_number) if ch.isdigit()).zfill(12)
    pairs = [s[i:i+2] for i in range(0, 12, 2)]
    pairs.reverse()  # little-endian BCD as seen in your logs
    return bytes.fromhex("".join(pairs))


def build_read_028011FF(meter_number: str) -> bytes:
    """
    DL/T645-2007 Read frame for DI=028011FF (voltage/current/power + status_word).
    Frame: FE FE FE FE 68 [A0..A5] 68 11 04 [DI+0x33 each] CS 16
    """
    addr = _bcd_addr_le(meter_number)
    di = bytes.fromhex("028011FF")
    di_enc = bytes((b + 0x33) & 0xFF for b in di)  # add 0x33 per spec
    ctrl = 0x11
    L = len(di_enc)  # 4
    body = bytes([ctrl, L]) + di_enc
    cs = sum(body) & 0xFF  # checksum over control+len+data
    return b"\xFE\xFE\xFE\xFE\x68" + addr + b"\x68" + body + bytes([cs, 0x16])
