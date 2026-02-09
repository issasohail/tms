"""
DLT645-2007 Protocol Implementation for Prepaid Parameter Setting
Function Code: 03
Identifier: 070104FF
"""

import struct
from typing import List, Dict, Any, Optional


class DLT645_2007_Prepaid:
    """DLT645-2007 Protocol Handler for Prepaid Parameter Setting"""

    # Frame constants
    FRAME_PREFIX = bytes([0xFE, 0xFE, 0xFE, 0xFE])
    FRAME_START = 0x68
    FRAME_END = 0x16

    # Function code
    FUNCTION_CODE = 0x03

    # Parameter identifier
    PARAM_IDENTIFIER = bytes([0xFF, 0x04, 0x01, 0x07])

    @staticmethod
    def _bcd_to_bytes(bcd_value: int, length: int) -> bytes:
        """Convert integer to BCD format bytes"""
        bcd_str = str(bcd_value).zfill(length * 2)
        return bytes([int(bcd_str[i:i+2], 16) for i in range(0, len(bcd_str), 2)])

    @staticmethod
    def _bytes_to_bcd(byte_data: bytes) -> int:
        """Convert BCD format bytes to integer"""
        bcd_str = ''.join([f"{b:02x}" for b in byte_data])
        return int(bcd_str)

    @staticmethod
    def _float_to_bcd(price: float, length: int) -> bytes:
        """
        Convert float price to BCD format with 4 decimal places
        Format: integer part (4 digits) + decimal part (4 digits)
        """
        # Multiply by 10000 to get integer representation with 4 decimal places
        int_price = int(round(price * 10000))
        bcd_str = str(int_price).zfill(length * 2)
        return bytes([int(bcd_str[i:i+2], 16) for i in range(0, len(bcd_str), 2)])

    @staticmethod
    def _bcd_to_float(byte_data: bytes) -> float:
        """Convert BCD format bytes to float with 4 decimal places"""
        bcd_str = ''.join([f"{b:02x}" for b in byte_data])
        int_value = int(bcd_str)
        return int_value / 10000.0

    @staticmethod
    def _calculate_checksum(data: bytes) -> int:
        """Calculate checksum for DLT645 frame"""
        return sum(data) & 0xFF

    @staticmethod
    def _add_33h(data: bytes) -> bytes:
        """Add 0x33 to each byte (DLT645 protocol requirement)"""
        return bytes([(b + 0x33) & 0xFF for b in data])

    @staticmethod
    def _sub_33h(data: bytes) -> bytes:
        """Subtract 0x33 from each byte (DLT645 protocol requirement)"""
        return bytes([(b - 0x33) & 0xFF for b in data])

    def build_frame(self, meter_address: str, parameters: Dict[str, Any]) -> bytes:
        """
        Build DLT645-2007 frame for prepaid parameter setting

        Args:
            meter_address: 12-digit meter address (BCD format)
            parameters: Dictionary containing parameter values

        Returns:
            bytes: Complete frame ready for transmission
        """
        # Convert meter address to bytes (reverse order, BCD format)
        if len(meter_address) != 12:
            raise ValueError("Meter address must be 12 digits")

        addr_bytes = bytes([int(meter_address[i:i+2], 16)
                           for i in range(10, -1, -2)])

        # Build data field
        data_field = self.PARAM_IDENTIFIER

        # Define parameter structure with special handling for price parameters
        param_structure = [
            ('rate_switch_time', 5, 'int'),          # Rate switch time
            ('step_switch_time', 5, 'int'),          # Step switch time
            ('timezone_switch_time', 5, 'int'),      # Timezone switch time
            ('schedule_switch_time', 5, 'int'),      # Schedule switch time
            ('timezone_count', 1, 'int'),            # Timezone count
            ('schedule_count', 1, 'int'),            # Schedule count
            ('time_period_count', 1, 'int'),         # Time period count
            ('rate_count', 1, 'int'),                # Rate count
            ('step_count', 1, 'int'),                # Step count
            ('voltage_ratio', 3, 'int'),             # Voltage ratio
            ('current_ratio', 3, 'int'),             # Current ratio
            ('alarm_amount_1', 4, 'int'),            # Alarm amount 1 (fen)
            ('alarm_amount_2', 4, 'int'),            # Alarm amount 2 (fen)
            ('overdraft_limit', 4, 'int'),           # Overdraft limit (fen)
            ('area_amount_limit', 4, 'int'),         # Area amount limit (fen)
            # Contract amount limit (fen)
            ('contract_amount_limit', 4, 'int'),
            ('max_load_power_limit', 3, 'int'),      # Max load power limit (W)
            ('load_power_delay', 1, 'int'),          # Load power delay (s)
            # First set rate 1 price (usd/kWh)
            ('rate1_price_1', 4, 'price'),
            # First set rate 2 price (usd/kWh)
            ('rate1_price_2', 4, 'price'),
            # First set rate 3 price (usd/kWh)
            ('rate1_price_3', 4, 'price'),
            # First set rate 4 price (usd/kWh)
            ('rate1_price_4', 4, 'price'),
            # Second set rate 1 price (usd/kWh)
            ('rate2_price_1', 4, 'price'),
            # Second set rate 2 price (usd/kWh)
            ('rate2_price_2', 4, 'price'),
            # Second set rate 3 price (usd/kWh)
            ('rate2_price_3', 4, 'price'),
            # Second set rate 4 price (usd/kWh)
            ('rate2_price_4', 4, 'price'),
            ('step1_value_1', 4, 'int'),             # First step value 1 (kWh)
            ('step1_value_2', 4, 'int'),             # First step value 2 (kWh)
            ('step1_value_3', 4, 'int'),             # First step value 3 (kWh)
            # First step price 1 (usd/kWh)
            ('step1_price_1', 4, 'price'),
            # First step price 2 (usd/kWh)
            ('step1_price_2', 4, 'price'),
            # First step price 3 (usd/kWh)
            ('step1_price_3', 4, 'price'),
            # First step price 4 (usd/kWh)
            ('step1_price_4', 4, 'price'),
            # Second step value 1 (kWh)
            ('step2_value_1', 4, 'int'),
            # Second step value 2 (kWh)
            ('step2_value_2', 4, 'int'),
            # Second step value 3 (kWh)
            ('step2_value_3', 4, 'int'),
            # Second step price 1 (usd/kWh)
            ('step2_price_1', 4, 'price'),
            # Second step price 2 (usd/kWh)
            ('step2_price_2', 4, 'price'),
            # Second step price 3 (usd/kWh)
            ('step2_price_3', 4, 'price'),
            # Second step price 4 (usd/kWh)
            ('step2_price_4', 4, 'price'),
        ]

        for param_name, length, param_type in param_structure:
            if param_name in parameters:
                value = parameters[param_name]
                if param_type == 'price':
                    # Handle price parameters (float with 4 decimal places)
                    if isinstance(value, (int, float)):
                        data_field += self._float_to_bcd(float(value), length)
                    else:
                        raise ValueError(
                            f"Price parameter {param_name} must be numeric")
                else:
                    # Handle integer parameters
                    if isinstance(value, int):
                        data_field += self._bcd_to_bytes(value, length)
                    else:
                        raise ValueError(
                            f"Parameter {param_name} must be integer")
            else:
                # Fill with zeros if parameter not provided
                data_field += bytes(length)

        # Build control code and data length
        control_code = 0x14  # Write command
        data_length = len(data_field)

        # Construct frame
        frame = bytes([self.FRAME_START])
        frame += addr_bytes
        frame += bytes([self.FRAME_START])
        frame += bytes([control_code])
        frame += bytes([data_length])
        frame += self._add_33h(data_field)

        # Calculate checksum
        checksum = self._calculate_checksum(frame[7:])  # Exclude start byte
        frame += bytes([checksum])
        frame += bytes([self.FRAME_END])

        # Add frame prefix
        complete_frame = self.FRAME_PREFIX + frame

        return complete_frame

    def parse_frame(self, frame: bytes) -> Dict[str, Any]:
        """
        Parse DLT645-2007 frame for prepaid parameter setting response

        Args:
            frame: Received frame bytes

        Returns:
            Dict: Parsed parameters and frame information
        """
        # Remove frame prefix if present
        if frame.startswith(self.FRAME_PREFIX):
            frame = frame[len(self.FRAME_PREFIX):]

        # Validate frame structure
        if (len(frame) < 12 or frame[0] != self.FRAME_START or
                frame[7] != self.FRAME_START or frame[-1] != self.FRAME_END):
            raise ValueError("Invalid frame structure")

        # Extract meter address (reverse order)
        addr_bytes = frame[1:7]
        meter_address = ''.join([f"{b:02x}" for b in reversed(addr_bytes)])

        # Extract control code and data length
        control_code = frame[8]
        data_length = frame[9]

        # Extract and process data field
        data_field = self._sub_33h(frame[10:10+data_length])

        # Verify checksum
        calculated_cs = self._calculate_checksum(frame[7:-2])
        received_cs = frame[-2]
        if calculated_cs != received_cs:
            raise ValueError(
                f"Checksum error: calculated {calculated_cs}, received {received_cs}")

        # Parse parameter data
        if data_field[:4] == self.PARAM_IDENTIFIER:
            param_data = data_field[4:]

            # Define parameter structure with type information
            param_structure = [
                ('rate_switch_time', 5, 'int'),
                ('step_switch_time', 5, 'int'),
                ('timezone_switch_time', 5, 'int'),
                ('schedule_switch_time', 5, 'int'),
                ('timezone_count', 1, 'int'),
                ('schedule_count', 1, 'int'),
                ('time_period_count', 1, 'int'),
                ('rate_count', 1, 'int'),
                ('step_count', 1, 'int'),
                ('voltage_ratio', 3, 'int'),
                ('current_ratio', 3, 'int'),
                ('alarm_amount_1', 4, 'int'),
                ('alarm_amount_2', 4, 'int'),
                ('overdraft_limit', 4, 'int'),
                ('area_amount_limit', 4, 'int'),
                ('contract_amount_limit', 4, 'int'),
                ('max_load_power_limit', 3, 'int'),
                ('load_power_delay', 1, 'int'),
                ('rate1_price_1', 4, 'price'),
                ('rate1_price_2', 4, 'price'),
                ('rate1_price_3', 4, 'price'),
                ('rate1_price_4', 4, 'price'),
                ('rate2_price_1', 4, 'price'),
                ('rate2_price_2', 4, 'price'),
                ('rate2_price_3', 4, 'price'),
                ('rate2_price_4', 4, 'price'),
                ('step1_value_1', 4, 'int'),
                ('step1_value_2', 4, 'int'),
                ('step1_value_3', 4, 'int'),
                ('step1_price_1', 4, 'price'),
                ('step1_price_2', 4, 'price'),
                ('step1_price_3', 4, 'price'),
                ('step1_price_4', 4, 'price'),
                ('step2_value_1', 4, 'int'),
                ('step2_value_2', 4, 'int'),
                ('step2_value_3', 4, 'int'),
                ('step2_price_1', 4, 'price'),
                ('step2_price_2', 4, 'price'),
                ('step2_price_3', 4, 'price'),
                ('step2_price_4', 4, 'price'),
            ]

            parsed_params = {}
            offset = 0

            for param_name, length, param_type in param_structure:
                if offset + length <= len(param_data):
                    param_bytes = param_data[offset:offset+length]
                    if param_type == 'price':
                        parsed_params[param_name] = self._bcd_to_float(
                            param_bytes)
                    else:
                        parsed_params[param_name] = self._bytes_to_bcd(
                            param_bytes)
                    offset += length

            return {
                'meter_address': meter_address,
                'control_code': control_code,
                'data_length': data_length,
                'parameters': parsed_params,
                'checksum_valid': True
            }

        else:
            raise ValueError("Invalid parameter identifier in response")


# Example usage with 4-decimal place prices
if __name__ == "__main__":
    # Create protocol handler instance
    protocol = DLT645_2007_Prepaid()

    # Complete example parameters with 4-decimal place prices
    example_params = {
        # Basic timing parameters (5 bytes each, BCD format)
        'rate_switch_time': 0,       # Rate switch time: 2023-12-31 23:59
        'step_switch_time': 0,       # Step switch time: 2024-01-01 00:00
        'timezone_switch_time': 0,            # Timezone switch time
        'schedule_switch_time': 0,            # Schedule switch time

        # Configuration limits (1 byte each)
        'timezone_count': 2,                  # Timezone count (<=2)
        'schedule_count': 2,                  # Schedule count (<=2)
        'time_period_count': 8,               # Time period count (<=8)
        'rate_count': 4,                      # Rate count (<=4)
        'step_count': 3,                      # Step count (<=3)

        # Measurement ratios (3 bytes each)
        'voltage_ratio': 100,                 # Voltage ratio (e.g., 100:1)
        'current_ratio': 50,                  # Current ratio (e.g., 50:1)

        # Amount limits (4 bytes each, in fen/分)
        'alarm_amount_1': 10000,              # Alarm amount 1 (100.00 usd)
        'alarm_amount_2': 5000,               # Alarm amount 2 (50.00 usd)
        'overdraft_limit': 20000,             # Overdraft limit (200.00 usd)
        'area_amount_limit': 50000,           # Area amount limit (500.00 usd)
        # Contract amount limit (1000.00 usd)
        'contract_amount_limit': 100000,

        # Power limits (3 bytes for power, 1 byte for delay)
        'max_load_power_limit': 60000,        # Max load power limit (60kW)
        'load_power_delay': 30,               # Load power delay (30 seconds)

        # First set of rate prices (4 bytes each, 4 decimal places in usd/kWh)
        'rate1_price_1': 0.5868,              # Rate 1 price 1 (0.5868 usd/kWh)
        'rate1_price_2': 0.8562,              # Rate 1 price 2 (0.8562 usd/kWh)
        'rate1_price_3': 1.2035,              # Rate 1 price 3 (1.2035 usd/kWh)
        'rate1_price_4': 1.8049,              # Rate 1 price 4 (1.8049 usd/kWh)

        # Second set of rate prices (4 bytes each, 4 decimal places in usd/kWh)
        'rate2_price_1': 0.6234,              # Rate 2 price 1 (0.6234 usd/kWh)
        'rate2_price_2': 0.9012,              # Rate 2 price 2 (0.9012 usd/kWh)
        'rate2_price_3': 1.3056,              # Rate 2 price 3 (1.3056 usd/kWh)
        'rate2_price_4': 2.0078,              # Rate 2 price 4 (2.0078 usd/kWh)

        # First set of step values (4 bytes each, in kWh)
        'step1_value_1': 20000,               # Step 1 value 1 (200 kWh)
        'step1_value_2': 40000,               # Step 1 value 2 (400 kWh)
        'step1_value_3': 60000,               # Step 1 value 3 (600 kWh)

        # First set of step prices (4 bytes each, 4 decimal places in usd/kWh)
        'step1_price_1': 0.5023,              # Step 1 price 1 (0.5023 usd/kWh)
        'step1_price_2': 0.8034,              # Step 1 price 2 (0.8034 usd/kWh)
        'step1_price_3': 1.5067,              # Step 1 price 3 (1.5067 usd/kWh)
        'step1_price_4': 2.5098,              # Step 1 price 4 (2.5098 usd/kWh)

        # Second set of step values (4 bytes each, in kWh)
        'step2_value_1': 25000,               # Step 2 value 1 (250 kWh)
        'step2_value_2': 50000,               # Step 2 value 2 (500 kWh)
        'step2_value_3': 75000,               # Step 2 value 3 (750 kWh)

        # Second set of step prices (4 bytes each, 4 decimal places in usd/kWh)
        'step2_price_1': 0.5521,              # Step 2 price 1 (0.5521 usd/kWh)
        'step2_price_2': 0.8532,              # Step 2 price 2 (0.8532 usd/kWh)
        'step2_price_3': 1.6074,              # Step 2 price 3 (1.6074 usd/kWh)
        'step2_price_4': 2.8095,              # Step 2 price 4 (2.8095 usd/kWh)
    }

    # Build frame
    try:
        frame = protocol.build_frame("250619510007", example_params)
        print(f"Built frame: {frame.hex()}")
        print(f"Frame length: {len(frame)} bytes")
    except Exception as e:
        print(f"Frame building error: {e}")

    # Example parsing (would typically come from meter response)
    try:
        # Simulate parsing the frame we just built
        parsed = protocol.parse_frame(frame)
        print(f"\nParsed meter address: {parsed['meter_address']}")
        print(f"Control code: 0x{parsed['control_code']:02x}")
        print(f"Data length: {parsed['data_length']} bytes")

        # Display price parameters with 4 decimal places
        print("\nPrice parameters:")
        for param_name, value in parsed['parameters'].items():
            if 'price' in param_name:
                print(f"  {param_name}: {value:.4f} usd/kWh")
            elif 'amount' in param_name:
                print(f"  {param_name}: {value} fen ({value/100:.2f} usd)")
            else:
                print(f"  {param_name}: {value}")

    except Exception as e:
        print(f"Frame parsing error: {e}")
