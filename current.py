def frame_construction(meter_number, identifier):
    """
    This function constructs a frame according to the DLT645 - 2007 protocol for reading current.

    :param meter_number: The meter number, e.g., '123456789012'.
    :param identifier: The current identifier, e.g., '02020100'.
    :return: A byte array representing the constructed frame.
    """
    # Preamble: 4 bytes of 0xFE
    preamble = bytes([0xFE] * 4)
    # Start the meter number with 0x68
    start_meter = bytes([0x68])
    # Convert the meter number to bytes
    meter_bytes = bytes.fromhex(meter_number)[::-1]
    # End the meter number with 0x68
    end_meter = bytes([0x68])
    # Control code for read, assuming 0x11 for this example
    control_code = bytes([0x11])
    # Data length, usually 4 bytes for identifier
    data_length = bytes([0x04])
    # Convert the identifier to bytes
    identifier_bytes = bytes.fromhex(identifier)[::-1]
    # Calculate the checksum
    all_data = start_meter + meter_bytes + end_meter + \
        control_code + data_length + identifier_bytes
    checksum = sum(all_data) % 256
    checksum_byte = bytes([checksum])
    # End code
    end_code = bytes([0x16])

    frame = preamble + start_meter + meter_bytes + end_meter + control_code + \
        data_length + identifier_bytes + checksum_byte + end_code
    return frame


# Example usage
meter_number = '250619510016'
identifier = '02020100'
constructed_frame = frame_construction(meter_number, identifier)
print(f"Constructed frame: {constructed_frame.hex()}")
