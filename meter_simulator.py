import socket
import time

HOST = '127.0.0.1'  # Use your server IP if remote
PORT = 6000

# Sample frame (fake, but structured for testing)
# Contains valid header, meter number, control code, length, data, checksum, end byte
def build_test_frame():
    meter_number = bytes.fromhex("250619510016")[::-1]  # Reversed BCD
    control_code = b'\x91'
    data = bytes([
        # Voltage (123.45)
        0x33 + 0x12, 0x33 + 0x34, 0x33 + 0x05, 0x33 + 0x00,
        # Padding (fake gap)
        0x33, 0x33, 0x33, 0x33,
        # Current (12.34)
        0x33 + 0x00, 0x33 + 0x12, 0x33 + 0x34, 0x33 + 0x00,
        # Padding
        0x33, 0x33, 0x33, 0x33,
        # Power (678.90)
        0x33 + 0x67, 0x33 + 0x89, 0x33 + 0x00, 0x33 + 0x00,
        # Padding
        0x33, 0x33, 0x33, 0x33,
        # Frequency (50.00)
        0x33 + 0x00, 0x33 + 0x50,
        # Power Factor (0.95)
        0x33 + 0x09, 0x33 + 0x50,
    ])
    data_len = len(data)
    header = b'\xFE' * 4 + b'\x68' + meter_number + b'\x68' + control_code + bytes([data_len])
    frame_wo_checksum = header + data
    checksum = sum(frame_wo_checksum[4:]) % 256
    frame = frame_wo_checksum + bytes([checksum]) + b'\x16'
    return frame


def send_test_frame():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        frame = build_test_frame()
        print(f"📤 Sending simulated frame: {frame.hex().upper()}")
        s.sendall(frame)
        time.sleep(1)


if __name__ == "__main__":
    while True:
        send_test_frame()
        time.sleep(5)
