# smart_meter/protocol.py

import struct
from datetime import datetime
import logging

# Use the same logger your views use so it lands in meter_control.log
logger = logging.getLogger("meter_control")

# === Identifiers for DLT645-2007 ===
IDENTIFIERS = {
    "voltage": "02010100",
    "current": "02020100",
    "power":   "02030100",
    "energy":  "00000000",  # total energy in kWh
}


def _reverse_and_hexify(hex_str):
    return bytes.fromhex(hex_str)[::-1]


def _checksum(data: bytes) -> bytes:
    return bytes([sum(data) % 256])


def _hx(b: bytes) -> str:
    """Upper-case hex for logs."""
    try:
        return b.hex().upper()
    except Exception:
        return str(b)


def build_read_frame(meter_number: str, identifier: str) -> bytes:
    """Constructs a read request frame according to DLT645-2007"""
    preamble = b'\xFE' * 4
    meter_id = _reverse_and_hexify(meter_number)
    identifier_bytes = _reverse_and_hexify(identifier)

    body = (
        b'\x68' + meter_id + b'\x68' +     # start + meter id + end
        b'\x11' +                          # control code: read
        b'\x04' +                          # data length
        identifier_bytes
    )

    frame = preamble + body + _checksum(body) + b'\x16'

    # DEBUG so it only shows if you set meter_control logger to DEBUG
    logger.debug(
        "PROTOCOL BUILD_READ meter=%s id=%s len=%s frame=%s",
        meter_number, identifier, len(frame), _hx(frame)
    )
    return frame


def build_voltage_frame(meter_number: str) -> bytes:
    return build_read_frame(meter_number, IDENTIFIERS["voltage"])


def build_current_frame(meter_number: str) -> bytes:
    return build_read_frame(meter_number, IDENTIFIERS["current"])


def build_power_frame(meter_number: str) -> bytes:
    return build_read_frame(meter_number, IDENTIFIERS["power"])


def build_energy_frame(meter_number: str) -> bytes:
    return build_read_frame(meter_number, IDENTIFIERS["energy"])


def build_switch_command(meter_number: str, action: str = "off") -> bytes:
    """
    Builds a breaker control command (DLT645-2007) to cut off or restore power.
    `action`: "on" or "off"
    """
    assert action in ["on", "off"], "action must be 'on' or 'off'"

    preamble = b'\xFE' * 4
    meter_id = _reverse_and_hexify(meter_number)
    meter_bytes = b'\x68' + meter_id + b'\x68'

    # Control code for write (matches your frames: ... 68 1C 10 ...)
    command_word = b'\x1C'
    length_byte = b'\x10'
    fixed_bytes = bytes([0x35, 0x33, 0x33, 0x33, 0x33, 0x33, 0x33, 0x33])

    # Control channel bytes (+0x33 encoded) based on action
    channel_bytes = {
        "off": bytes([0x1A + 0x33, 0x34]),  # OFF
        "on":  bytes([0x1C + 0x33, 0x34]),  # ON
    }[action]

    # Time validity — each byte + 0x33 (device typically ignores exact value)
    now = datetime.now()
    dt_bytes = [now.second, now.minute, now.hour,
                now.day, now.month, now.year % 100]
    time_validity = bytes([(b & 0xFF) + 0x33 for b in dt_bytes])

    payload = (
        meter_bytes +
        command_word + length_byte +
        fixed_bytes + channel_bytes + time_validity
    )
    checksum = _checksum(payload)
    frame = preamble + payload + checksum + b'\x16'

    # INFO: show in normal operation logs + blank line separator
    logger.info(
        "PROTOCOL BUILD_SWITCH meter=%s action=%s cmd=0x%s len=%s frame=%s",
        meter_number, action.upper(), _hx(command_word), len(frame), _hx(frame)
    )
    # visual separator between commands
    logger.info("=============================")

    return frame
