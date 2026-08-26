#!/usr/bin/env python3
# -*- coding: utf-8 -*-

def hex_str_to_bytes(hex_str):
    return [int(hex_str[i:i + 2], 16) for i in range(0, len(hex_str), 2)]

def copy_buf(dst, offset, src):
    for b in reversed(src):
        dst[offset] = b
        offset += 1

def get_byte_bcd(value):
    return ((value // 10) * 16 + (value % 10)) & 0xFF

def lo_byte_33(b):
    return (b + 0x33) & 0xFF

# --------------------------------------------------------------------------
# Obj2* type-conversion helpers (null / empty string -> default value)
# --------------------------------------------------------------------------

def obj2_long(obj):
    if obj is None or str(obj).strip() == "":
        return 0
    return int(str(obj).strip())


def obj2_int(obj):
    if obj is None or str(obj).strip() == "":
        return 0
    return int(str(obj).strip())


def obj2_double(obj):
    if obj is None or str(obj).strip() == "":
        return 0.0
    return float(str(obj).strip())


# --------------------------------------------------------------------------
# Mock data 
# --------------------------------------------------------------------------

def mock_meter_config():
    return {
        # 4 switch dates (rate / ladder / time-zone / time-section), YYYYMMDD
        "priceChgDate": 20240101,     # rate switch date
        "stepChgDate": 20240101,      # ladder pricing switch date
        "timeAreaChgDate": 20240101,  # second time-zone set switch date
        "timeSecChgDate": 20240101,   # second time-period set switch date

        # quantity metadata
        "qtyarea": 2,        # number of time zones (max 2)
        "qtytimertable": 1,  # number of timer tables (max 2)
        "qtytimer": 6,       # number of timer segments (max 8)
        "qtyprice": 4,       # number of tariffs (max 4)
        "qtystep": 3,        # number of ladder steps (max 3)

        # transformer ratios
        "pt": 1,             # voltage ratio
        "ct": 1,             # current ratio

        # amount limits (yuan, x100 -> cents on the wire)
        "warnlowbala1": 50.00,     # warning amount 1
        "warnlowbala2": 20.00,     # warning amount 2
        "creditVal": 0.00,         # overdraft limit
        "balancemax": 1000.00,     # maximum balance
        "remainPowerOn": 0.00,     # reconnection balance limit

        # load limit (kW, x10000 on the wire) and its delay (seconds)
        "kwMax": 22.5,
        "sleepKw": 3,

        # 4 tariffs per rate set (yuan/kWh, x10000 on the wire)
        "set1Price1": 0.5133, "set1Price2": 0.5233,
        "set1Price3": 0.5333, "set1Price4": 0.5433,
        "set2Price1": 0.6133, "set2Price2": 0.6233,
        "set2Price3": 0.6333, "set2Price4": 0.6433,

        # ladder step values (kWh, x10000 on the wire)
        "set1Step1": 200, "set1Step2": 400, "set1Step3": 0,
        "set2Step1": 200, "set2Step2": 400, "set2Step3": 0,

        # ladder step prices (yuan/kWh, x10000 on the wire)
        "set1StepPrice1": 0.5133, "set1StepPrice2": 0.5533,
        "set1StepPrice3": 0.5933, "set1StepPrice4": 0.0,
        "set2StepPrice1": 0.6133, "set2StepPrice2": 0.6533,
        "set2StepPrice3": 0.6933, "set2StepPrice4": 0.0,
    }


# --------------------------------------------------------------------------
# make070104FF
# --------------------------------------------------------------------------

def make070104ff(meter_id, n_chl, tbuf):
    """Build the 070104FF (send prepaid meter parameters) payload.

    Args:
        meter_id: logical meter id (the Java SQL filter key; unused here).
        n_chl:    channel number (unused in the original body, kept for parity).
        tbuf:     bytearray that receives the payload.

    Returns:
        Payload length in bytes, or 0 when no user type is configured.
    """
    # Original Java returns 0 when the joined query is empty
    # ("meter user type is not configured"). Kept as a guard so the contract matches.
    config = mock_meter_config()
    if config is None:
        print("send prepaid: meter has no user type configured")
        return 0

    idx = 0

    # 1) Rate switch date -- 10 hex digits -> 5 bytes, little-endian
    long_val = obj2_long(config.get("priceChgDate"))
    buf = hex_str_to_bytes(f"{long_val:010d}")
    copy_buf(tbuf, idx, buf)
    idx += len(buf)

    # 2) Ladder switch date
    long_val = obj2_long(config.get("stepChgDate"))
    buf = hex_str_to_bytes(f"{long_val:010d}")
    copy_buf(tbuf, idx, buf)
    idx += len(buf)

    # 3) 2nd-set time-zone switch date
    long_val = obj2_long(config.get("timeAreaChgDate"))
    buf = hex_str_to_bytes(f"{long_val:010d}")
    copy_buf(tbuf, idx, buf)
    idx += len(buf)

    # 4) 2nd-set time-section switch date
    long_val = obj2_long(config.get("timeSecChgDate"))
    buf = hex_str_to_bytes(f"{long_val:010d}")
    copy_buf(tbuf, idx, buf)
    idx += len(buf)

    # 5) Number of time zones (capped at 2) -- single BCD byte
    int_val = min(obj2_int(config.get("qtyarea")), 2)
    tbuf[idx] = get_byte_bcd(int_val)
    idx += 1

    # 6) Number of timer tables (capped at 2)
    int_val = min(obj2_int(config.get("qtytimertable")), 2)
    tbuf[idx] = get_byte_bcd(int_val)
    idx += 1

    # 7) Number of timer segments (capped at 8)
    int_val = min(obj2_int(config.get("qtytimer")), 8)
    tbuf[idx] = get_byte_bcd(int_val)
    idx += 1

    # 8) Number of tariffs (capped at 4)
    int_val = min(obj2_int(config.get("qtyprice")), 4)
    tbuf[idx] = get_byte_bcd(int_val)
    idx += 1

    # 9) Number of ladder steps (capped at 3)
    int_val = min(obj2_int(config.get("qtystep")), 3)
    tbuf[idx] = get_byte_bcd(int_val)
    idx += 1

    # 10) Voltage transformer ratio -- 6 digits -> 3 bytes, little-endian
    dbl_val = obj2_double(config.get("pt"))
    buf = hex_str_to_bytes(f"{int(dbl_val):06d}")
    copy_buf(tbuf, idx, buf)
    idx += len(buf)

    # 11) Current transformer ratio
    dbl_val = obj2_double(config.get("ct"))
    buf = hex_str_to_bytes(f"{int(dbl_val):06d}")
    copy_buf(tbuf, idx, buf)
    idx += len(buf)

    # 12-16) Amount limits, in cents (x100) -- 8 digits -> 4 bytes each
    for field in ("warnlowbala1", "warnlowbala2",
                  "creditVal", "balancemax", "remainPowerOn"):
        dbl_val = obj2_double(config.get(field)) * 100
        buf = hex_str_to_bytes(f"{int(dbl_val):08d}")
        copy_buf(tbuf, idx, buf)
        idx += len(buf)

    # 17) Max load power limit (x10000) -- 6 digits -> 3 bytes
    dbl_val = obj2_double(config.get("kwMax")) * 10000
    buf = hex_str_to_bytes(f"{int(dbl_val):06d}")
    copy_buf(tbuf, idx, buf)
    idx += len(buf)

    # 18) Load power delay -- single raw byte
    int_val = obj2_int(config.get("sleepKw"))
    tbuf[idx] = int_val & 0xFF
    idx += 1

    # 19) Set-1 tariffs (x10000) -- 8 digits -> 4 bytes each
    for i in range(1, 5):
        dbl_val = obj2_double(config.get(f"set1Price{i}")) * 10000
        buf = hex_str_to_bytes(f"{int(dbl_val):08d}")
        copy_buf(tbuf, idx, buf)
        idx += len(buf)

    # 20) Set-2 tariffs
    for i in range(1, 5):
        dbl_val = obj2_double(config.get(f"set2Price{i}")) * 10000
        buf = hex_str_to_bytes(f"{int(dbl_val):08d}")
        copy_buf(tbuf, idx, buf)
        idx += len(buf)

    # 21) Set-1 ladder step values (x10000)
    for i in range(1, 4):
        dbl_val = obj2_double(config.get(f"set1Step{i}")) * 10000
        buf = hex_str_to_bytes(f"{int(dbl_val):08d}")
        copy_buf(tbuf, idx, buf)
        idx += len(buf)

    # 22) Set-1 ladder step prices (x10000)
    for i in range(1, 5):
        dbl_val = obj2_double(config.get(f"set1StepPrice{i}")) * 10000
        buf = hex_str_to_bytes(f"{int(dbl_val):08d}")
        copy_buf(tbuf, idx, buf)
        idx += len(buf)

    # 23) Set-2 ladder step values (x10000)
    for i in range(1, 4):
        dbl_val = obj2_double(config.get(f"set2Step{i}")) * 10000
        buf = hex_str_to_bytes(f"{int(dbl_val):08d}")
        copy_buf(tbuf, idx, buf)
        idx += len(buf)

    # 24) Set-2 ladder step prices (x10000)
    for i in range(1, 5):
        dbl_val = obj2_double(config.get(f"set2StepPrice{i}")) * 10000
        buf = hex_str_to_bytes(f"{int(dbl_val):08d}")
        copy_buf(tbuf, idx, buf)
        idx += len(buf)

    # LoBYTE33: add 0x33 to every produced byte (obfuscation)
    for i in range(idx):
        tbuf[i] = lo_byte_33(tbuf[i])

    return idx


# --------------------------------------------------------------------------
# DLT645 frame helpers (Command2007 / AcUtil ports)
# --------------------------------------------------------------------------

PRE_QTY = 4  # Command2007.PRE_QTY: number of 0xFE preamble bytes


def copy_buf_lo33(dst, src):
    for b in reversed(src):
        dst.append(lo_byte_33(b))


def check_sum(buf, length, beg_idx):
    total = 0
    for i in range(beg_idx, length):
        total += buf[i]
    return total & 0xFF


def make_general03_cmd(meter_num, data_index, data_buf, length, seqno):
    """
    Args:
        meter_num:  12-hex-digit meter number, e.g. "260305510012".
        data_index: 8-hex-digit data identifier, e.g. "070104FF".
        data_buf:   payload byte list (already LoBYTE33-encoded when it
                    comes from make070104ff).
        length:     number of payload bytes to include.
        seqno:      serial number as an 8-hex-digit string, or "" to omit.

    Returns:
        The complete frame as a list of ints (0..255).
    """
    frame = []

    # 1) Preamble: PRE_QTY x 0xFE
    frame.extend([0xFE] * PRE_QTY)

    # 2) Start byte
    frame.append(0x68)

    # 3) Meter address: 6 bytes, written in REVERSED order (copyBuf)
    frame.extend(reversed(hex_str_to_bytes(meter_num)))

    # 4) Start byte again
    frame.append(0x68)

    # 5) Control code: 0x03 = write data
    frame.append(0x03)

    # 6) Length L: payload length + fixed overhead (8 without seqno, 12 with)
    frame.append(length + (12 if seqno else 8))

    # 7) Data identifier: 4 bytes, reversed + LoBYTE33 each (copyBufLo33)
    copy_buf_lo33(frame, hex_str_to_bytes(data_index))

    # 8) Operator code
    frame.extend([0x77, 0x66, 0x55, 0x44])

    # 9) Payload data
    frame.extend(data_buf[:length])

    # 10) Serial number (optional): 4 bytes, reversed + LoBYTE33 each
    if seqno:
        sn = hex_str_to_bytes(seqno)
        frame.extend([lo_byte_33(sn[3]), lo_byte_33(sn[2]),
                      lo_byte_33(sn[1]), lo_byte_33(sn[0])])

    # 11) Checksum over bytes [PRE_QTY, end of frame)
    frame.append(check_sum(frame, len(frame), PRE_QTY))

    # 12) End byte
    frame.append(0x16)

    return frame


# --------------------------------------------------------------------------
# Self-test / demo
# --------------------------------------------------------------------------

def main():
    # --- 1) Build the 143-byte prepaid-parameter payload ------------------//
    tbuf = bytearray(143)  # fixed layout: 143 payload bytes
    length = make070104ff(meter_id="mock-meter-0001", n_chl=1, tbuf=tbuf)

    print(f"payload length : {length} bytes")
    print(f"payload hex    : {tbuf[:length].hex().upper()}")
    print(f"valid length   : {length == 143}")

    # --- 2) Wrap it in a DLT645 write frame -------------------------------//
    # Call-site equivalent (SendRequestMultPrice line 1333):
    #   dataIndex = task flag  -> "070104FF"
    #   cmd_seqNo              -> "" (empty for this case)
    meter_num = "260305510012"          # test meter number
    data_index = "070104FF"
    seqno = ""                          # empty, as in the Java call site

    frame = make_general03_cmd(meter_num, data_index, tbuf, length, seqno)
    frame_hex = "".join(f"{b:02X}" for b in frame)

    print("---")
    print(f"frame length   : {len(frame)} bytes")
    print(f"frame hex      : {frame_hex}")

    # Frame structure breakdown
    print("---")
    print(f"preamble       : {frame[:PRE_QTY]}")
    print(f"meter addr     : {['%02X' % b for b in frame[5:11]]}")
    print(f"L              : {frame[13]}  (payload {length} + 8)")
    print(f"data id (lo33) : {['%02X' % b for b in frame[14:18]]}")
    print(f"checksum       : {frame[-2]:02X}")
    print(f"end byte       : {frame[-1]:02X}")


if __name__ == "__main__":
    main()
