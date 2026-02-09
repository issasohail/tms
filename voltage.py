import struct

def frame_voltage_request(meter_number, voltage_identifier):
    """
    This function constructs a request frame to read the voltage from an electric meter based on the DLT645 - 2007 protocol.
    :param meter_number: The meter number, which is a string representing the unique identifier of the meter.
    :param voltage_identifier: The identifier for the voltage parameter, which is a hexadecimal string.
    :return: A byte array representing the complete request frame.
    """
    # Preamble: Four bytes of 0xFE
    preamble = b'\xFE' * 4
    # Add 0x68 before and after the meter number
    meter_number_bytes = bytes.fromhex(meter_number)[::-1]
    meter_number_with_markers = b'\x68' + meter_number_bytes + b'\x68'
    # Control code for reading data
    control_code = b'\x11'
    # Data area length
    data_area_length = b'\x04'
    # Convert voltage identifier to bytes
    voltage_identifier_bytes = bytes.fromhex(voltage_identifier)[::-1]
    # Calculate the checksum
    data_to_check = meter_number_with_markers + control_code + data_area_length + voltage_identifier_bytes
    checksum = sum(data_to_check) & 0xFF
    checksum_byte = struct.pack('B', checksum)
    # End code
    end_code = b'\x16'
    # Combine all parts to form the complete frame
    frame = preamble + meter_number_with_markers + control_code + data_area_length + voltage_identifier_bytes + checksum_byte + end_code
    return frame


# Example usage
meter_number = '250619510016'
voltage_identifier = '02010100'
request_frame = frame_voltage_request(meter_number, voltage_identifier)
print(f"Request frame: {request_frame.hex()}")
