
def frame_command(meter_number, byCmd):
    # Preamble: 4 bytes of 0xFE
    preamble = bytes([0xFE] * 4)
    # Add 0x68 before and after the meter ID
    meter_bytes = bytes([0x68]) + \
        bytes.fromhex(meter_number)[::-1] + bytes([0x68])
    # Command word
    command_word = bytes([0x1C])
    # Length byte (you can adjust this according to actual requirements, here we assume a fixed length)
    length_byte = bytes([0x10])
    # Fixed bytes
    fixed_bytes = bytes([0x35, 0x33, 0x33, 0x33, 0x33, 0x33, 0x33, 0x33])
    # control_word + Channel bytes
    channel_bytes = bytes([byCmd + 0x33, 0x34])
    # Time validity, each byte plus 0x33
    time_validity = bytes(
        [x + 0x33 for x in [0x59, 0x59, 0x23, 0x31, 0x12, 0x99]])
    # Calculate the checksum
    data_to_check = meter_bytes + command_word + length_byte + \
        fixed_bytes + channel_bytes + time_validity
    checksum = bytes([sum(data_to_check) & 0xFF])
    # Combine all parts into a complete frame
    frame = preamble + meter_bytes + command_word + length_byte + \
        fixed_bytes + channel_bytes + time_validity + checksum + bytes([0x16])
    return frame


# Example usage
meter_number = '250619510017'
byCmd = 0x1C
# request_frame = frame_command(meter_number, byCmd)
# print(f"ON switch_onOff.py Request frame: {request_frame.hex()}")

byCmd = 0x1A
# request_frame = frame_command(meter_number, byCmd)
# print(f"OFFON switch_onOff.py Request frame: {request_frame.hex()}")
