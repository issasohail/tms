import struct

# Define the frame data based on your example
frame = bytes.fromhex("FE FE FE FE 68 16 00 51 19 06 25 68 91 45 32 44 B3 35 33 33 33 33 33 33 33 33 83 57 33 33 33 33 3C 34 33 33 33 33 33 33 33 79 34 33 79 34 33 33 33 33 33 33 33 45 38 45 38 33 33 33 33 85 37 33 33 85 37 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 88 16")

# Extract the data between the 68 markers (before and after 68)
start_index = frame.find(b'\x68') + 1  # Start after the first 68
end_index = frame.find(b'\x68', start_index)  # Find the second 68

# Extract the bytes between the 68 markers
meter_data = frame[start_index:end_index]

# Reverse the bytes to get the correct meter number
meter_number = ''.join([f"{b:02X}" for b in reversed(meter_data)])  # Reverse and format as hex string
print(f"Meter Number: {meter_number}")  # This should print the correct meter number.

# Checksum (for simplicity, sum the bytes and check the result)
checksum = sum(frame) & 0xFF
print(f"Checksum: {checksum}")

# Now, let's extract some data based on positions in the frame
# Assuming that the data starts at byte 14 for voltage, current, and power (based on your previous data)

# Extract voltage (bytes 14 and 15)
voltage_raw = frame[14:16]
voltage = struct.unpack(">H", bytes(reversed(voltage_raw)))[0]  # Reverse and unpack
print(f"Voltage: {voltage} V")

# Extract current (bytes 16 and 17)
current_raw = frame[16:18]
current = struct.unpack(">H", bytes(reversed(current_raw)))[0]  # Reverse and unpack
print(f"Current: {current} A")

# Extract power (bytes 18 and 19)
power_raw = frame[18:20]
power = struct.unpack(">H", bytes(reversed(power_raw)))[0]  # Reverse and unpack
print(f"Power: {power} W")

# Continue extracting more fields similarly...
