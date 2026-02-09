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
    PARAM_IDENTIFIER = bytes([0x07, 0x01, 0x04, 0xFF])
    
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
        
        addr_bytes = bytes([int(meter_address[i:i+2], 16) for i in range(10, -1, -2)])
        
        # Build data field
        data_field = self.PARAM_IDENTIFIER
        
        # Add parameter values in specified order
        param_order = [
            ('rate_switch_time', 5),          # Rate switch time
            ('step_switch_time', 5),          # Step switch time
            ('timezone_switch_time', 5),      # Timezone switch time
            ('schedule_switch_time', 5),      # Schedule switch time
            ('timezone_count', 1),            # Timezone count
            ('schedule_count', 1),            # Schedule count
            ('time_period_count', 1),         # Time period count
            ('rate_count', 1),                # Rate count
            ('step_count', 1),                # Step count
            ('voltage_ratio', 3),             # Voltage ratio
            ('current_ratio', 3),             # Current ratio
            ('alarm_amount_1', 4),            # Alarm amount 1
            ('alarm_amount_2', 4),            # Alarm amount 2
            ('overdraft_limit', 4),           # Overdraft limit
            ('area_amount_limit', 4),         # Area amount limit
            ('contract_amount_limit', 4),     # Contract amount limit
            ('max_load_power_limit', 3),      # Max load power limit
            ('load_power_delay', 1),          # Load power delay
            ('rate1_price_1', 4),             # Rate 1 price 1
            ('rate1_price_2', 4),             # Rate 1 price 2
            ('rate1_price_3', 4),             # Rate 1 price 3
            ('rate1_price_4', 4),             # Rate 1 price 4
            ('rate2_price_1', 4),             # Rate 2 price 1
            ('rate2_price_2', 4),             # Rate 2 price 2
            ('rate2_price_3', 4),             # Rate 2 price 3
            ('rate2_price_4', 4),             # Rate 2 price 4
            ('step1_value_1', 4),             # Step 1 value 1
            ('step1_value_2', 4),             # Step 1 value 2
            ('step1_value_3', 4),             # Step 1 value 3
            ('step1_price_1', 4),             # Step 1 price 1
            ('step1_price_2', 4),             # Step 1 price 2
            ('step1_price_3', 4),             # Step 1 price 3
            ('step1_price_4', 4),             # Step 1 price 4
            ('step2_value_1', 4),             # Step 2 value 1
            ('step2_value_2', 4),             # Step 2 value 2
            ('step2_value_3', 4),             # Step 2 value 3
            ('step2_price_1', 4),             # Step 2 price 1
            ('step2_price_2', 4),             # Step 2 price 2
            ('step2_price_3', 4),             # Step 2 price 3
            ('step2_price_4', 4),             # Step 2 price 4
        ]
        
        for param_name, length in param_order:
            if param_name in parameters:
                value = parameters[param_name]
                if isinstance(value, int):
                    data_field += self._bcd_to_bytes(value, length)
                else:
                    raise ValueError(f"Parameter {param_name} must be integer")
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
        checksum = self._calculate_checksum(frame[1:])  # Exclude start byte
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
        calculated_cs = self._calculate_checksum(frame[1:-2])
        received_cs = frame[-2]
        if calculated_cs != received_cs:
            raise ValueError(f"Checksum error: calculated {calculated_cs}, received {received_cs}")
        
        # Parse parameter data
        if data_field[:4] == self.PARAM_IDENTIFIER:
            param_data = data_field[4:]
            
            # Define parameter structure
            param_structure = [
                ('rate_switch_time', 5),
                ('step_switch_time', 5),
                ('timezone_switch_time', 5),
                ('schedule_switch_time', 5),
                ('timezone_count', 1),
                ('schedule_count', 1),
                ('time_period_count', 1),
                ('rate_count', 1),
                ('step_count', 1),
                ('voltage_ratio', 3),
                ('current_ratio', 3),
                ('alarm_amount_1', 4),
                ('alarm_amount_2', 4),
                ('overdraft_limit', 4),
                ('area_amount_limit', 4),
                ('contract_amount_limit', 4),
                ('max_load_power_limit', 3),
                ('load_power_delay', 1),
                ('rate1_price_1', 4),
                ('rate1_price_2', 4),
                ('rate1_price_3', 4),
                ('rate1_price_4', 4),
                ('rate2_price_1', 4),
                ('rate2_price_2', 4),
                ('rate2_price_3', 4),
                ('rate2_price_4', 4),
                ('step1_value_1', 4),
                ('step1_value_2', 4),
                ('step1_value_3', 4),
                ('step1_price_1', 4),
                ('step1_price_2', 4),
                ('step1_price_3', 4),
                ('step1_price_4', 4),
                ('step2_value_1', 4),
                ('step2_value_2', 4),
                ('step2_value_3', 4),
                ('step2_price_1', 4),
                ('step2_price_2', 4),
                ('step2_price_3', 4),
                ('step2_price_4', 4),
            ]
            
            parsed_params = {}
            offset = 0
            
            for param_name, length in param_structure:
                if offset + length <= len(param_data):
                    param_bytes = param_data[offset:offset+length]
                    parsed_params[param_name] = self._bytes_to_bcd(param_bytes)
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

# Example usage
if __name__ == "__main__":
    # Create protocol handler instance
    protocol = DLT645_2007_Prepaid()
    # Example parameters for framing
# Complete example parameters for prepaid parameter setting
    example_params = {
        # Basic timing parameters (5 bytes each, BCD format)
        'rate_switch_time': 12345,           # Rate switch time (e.g., YYMMDDHHMM)
        'step_switch_time': 67890,           # Step switch time
        'timezone_switch_time': 11223,       # Timezone switch time
        'schedule_switch_time': 44556,       # Schedule switch time

        # Configuration limits (1 byte each)
        'timezone_count': 2,                 # Timezone count (<=2)
        'schedule_count': 2,                 # Schedule count (<=2)
        'time_period_count': 8,              # Time period count (<=8)
        'rate_count': 4,                     # Rate count (<=4)
        'step_count': 3,                     # Step count (<=3)

        # Measurement ratios (3 bytes each)
        'voltage_ratio': 100,                # Voltage ratio (e.g., 100:1)
        'current_ratio': 50,                 # Current ratio (e.g., 50:1)

        # Amount limits (4 bytes each, in fen/分)
        'alarm_amount_1': 10000,             # Alarm amount 1 (100.00 yuan)
        'alarm_amount_2': 5000,              # Alarm amount 2 (50.00 yuan)
        'overdraft_limit': 20000,            # Overdraft limit (200.00 yuan)
        'area_amount_limit': 50000,          # Area amount limit (500.00 yuan)
        'contract_amount_limit': 100000,     # Contract amount limit (1000.00 yuan)

        # Power limits (3 bytes for power, 1 byte for delay)
        'max_load_power_limit': 60000,       # Max load power limit (60kW)
        'load_power_delay': 30,              # Load power delay (30 seconds)

        # First set of rate prices (4 bytes each, in fen/分 per kWh)
        'rate1_price_1': 58,                 # Rate 1 price 1 (0.58 yuan/kWh)
        'rate1_price_2': 85,                 # Rate 1 price 2 (0.85 yuan/kWh)
        'rate1_price_3': 120,                # Rate 1 price 3 (1.20 yuan/kWh)
        'rate1_price_4': 180,                # Rate 1 price 4 (1.80 yuan/kWh)

        # Second set of rate prices (4 bytes each, in fen/分 per kWh)
        'rate2_price_1': 62,                 # Rate 2 price 1 (0.62 yuan/kWh)
        'rate2_price_2': 90,                 # Rate 2 price 2 (0.90 yuan/kWh)
        'rate2_price_3': 130,                # Rate 2 price 3 (1.30 yuan/kWh)
        'rate2_price_4': 200,                # Rate 2 price 4 (2.00 yuan/kWh)

        # First set of step values (4 bytes each, in kWh)
        'step1_value_1': 20000,              # Step 1 value 1 (200 kWh)
        'step1_value_2': 40000,              # Step 1 value 2 (400 kWh)
        'step1_value_3': 60000,              # Step 1 value 3 (600 kWh)

        # First set of step prices (4 bytes each, in fen/分 per kWh)
        'step1_price_1': 50,                 # Step 1 price 1 (0.50 yuan/kWh)
        'step1_price_2': 80,                 # Step 1 price 2 (0.80 yuan/kWh)
        'step1_price_3': 150,                # Step 1 price 3 (1.50 yuan/kWh)
        'step1_price_4': 250,                # Step 1 price 4 (2.50 yuan/kWh)

        # Second set of step values (4 bytes each, in kWh)
        'step2_value_1': 25000,              # Step 2 value 1 (250 kWh)
        'step2_value_2': 50000,              # Step 2 value 2 (500 kWh)
        'step2_value_3': 75000,              # Step 2 value 3 (750 kWh)

        # Second set of step prices (4 bytes each, in fen/分 per kWh)
        'step2_price_1': 55,                 # Step 2 price 1 (0.55 yuan/kWh)
        'step2_price_2': 85,                 # Step 2 price 2 (0.85 yuan/kWh)
        'step2_price_3': 160,                # Step 2 price 3 (1.60 yuan/kWh)
        'step2_price_4': 280,                # Step 2 price 4 (2.80 yuan/kWh)
    }
    # Build frame
    try:
        frame = protocol.build_frame("250619510017", example_params)
        print(f"Built frame: {frame.hex()}")
    except Exception as e:
        print(f"Frame building error: {e}")
    
    # Example parsing (would typically come from meter response)
    try:
        # This would normally be a response from the meter
        # For demonstration, we'll parse the frame we just built
        parsed = protocol.parse_frame(frame)
        print(f"Parsed data: {parsed}")
    except Exception as e:
        print(f"Frame parsing error: {e}")