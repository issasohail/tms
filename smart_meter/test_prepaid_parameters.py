"""Offline regression vectors for the audited 070104FF Parameter 1 codec."""
from decimal import Decimal
from unittest import TestCase
from pathlib import Path

from smart_meter.dlt645 import _add_33, build_frame, parse_070104ff_prices
from smart_meter.services.prepaid_parameters import (
    build_parameter_frame, classify_write_response, decode_parameter_payload,
)

METER = "260305510012"
EXPECTED_FLAT_FRAME = "".join((
    "FEFEFEFE681200510503266803973237343A7766554433333333333333333333",
    "3333333333333333333333333333333333333333333363333333533333335333",
    "33CCCCCCCC333333333333333333338333333383333333833333338333333383",
    "3333338333333383333333833333333333333333333333333333333333333333",
    "3333333333333333333333333333333333333333333333333333333333333333",
    "33333333331916",
))


def flat_config():
    config = {
        "priceChgDate": 0, "stepChgDate": 0, "timeAreaChgDate": 0, "timeSecChgDate": 0,
        "qtyarea": 0, "qtytimertable": 0, "qtytimer": 0, "qtyprice": 0, "qtystep": 0,
        "pt": 0, "ct": 0, "warnlowbala1": Decimal("30.00"), "warnlowbala2": Decimal("20.00"),
        "creditVal": Decimal("20.00"), "balancemax": Decimal("999999.99"), "remainPowerOn": Decimal("0.00"),
        "kwMax": Decimal("0.0000"), "sleepKw": 0,
    }
    for rate in (1, 2):
        for slot in range(1, 5):
            config[f"set{rate}Price{slot}"] = Decimal("50.0000")
            config[f"set{rate}StepPrice{slot}"] = Decimal("0.0000")
        for slot in range(1, 4):
            config[f"set{rate}Step{slot}"] = Decimal("0.0000")
    return config


class Parameter1RegressionTests(TestCase):
    def test_flat_tariff_vector_is_exact(self):
        built = build_parameter_frame(METER, flat_config())
        frame, payload = built["frame"], built["payload"]
        self.assertEqual(len(payload), 143)
        self.assertEqual(len(frame), 167)
        self.assertEqual(frame[12], 0x03)
        self.assertEqual(frame[13], 0x97)
        self.assertEqual(frame[14:18].hex().upper(), "3237343A")
        self.assertEqual(frame[18:22].hex().upper(), "77665544")
        self.assertEqual(frame[-2], 0x19)
        self.assertEqual(frame[-1], 0x16)
        self.assertEqual(frame.hex().upper(), EXPECTED_FLAT_FRAME)
        for offset in range(55, 87, 4):
            self.assertEqual(payload[offset:offset + 4].hex().upper(), "33338333")
        # Every decoded (pre +0x33) payload byte is valid BCD, except sleepKw.
        for offset, byte in enumerate(payload):
            if offset == 54:
                continue
            plain = (byte - 0x33) & 0xFF
            self.assertLessEqual(plain >> 4, 9)
            self.assertLessEqual(plain & 0x0F, 9)

    def test_round_trip_and_single_money_scaling(self):
        config = flat_config()
        decoded = decode_parameter_payload(build_parameter_frame(METER, config)["payload"])
        self.assertEqual(decoded["warnlowbala1"], Decimal("30.00"))
        self.assertEqual(decoded["balancemax"], Decimal("999999.99"))
        self.assertEqual(decoded["set1Price1"], Decimal("50.0000"))

    def test_nonzero_tier_vector(self):
        config = flat_config()
        values = {
            "set1Step1": "100.0000", "set1Step2": "250.0000", "set1Step3": "500.0000",
            "set1StepPrice1": "10.0000", "set1StepPrice2": "20.0000", "set1StepPrice3": "30.0000", "set1StepPrice4": "40.0000",
            "set2Step1": "150.0000", "set2Step2": "300.0000", "set2Step3": "600.0000",
            "set2StepPrice1": "15.0000", "set2StepPrice2": "25.0000", "set2StepPrice3": "35.0000", "set2StepPrice4": "45.0000",
        }
        config.update({key: Decimal(value) for key, value in values.items()})
        payload = build_parameter_frame(METER, config)["payload"]
        expected_wire = ["33333334", "33338335", "33333338", "33334333", "33335333", "33336333", "33337333", "33338334", "33333336", "33333339", "33334833", "33335833", "33336833", "33337833"]
        offsets = list(range(87, 99, 4)) + list(range(99, 115, 4)) + list(range(115, 127, 4)) + list(range(127, 143, 4))
        self.assertEqual([payload[offset:offset + 4].hex().upper() for offset in offsets], expected_wire)
        decoded = decode_parameter_payload(payload)
        for key, value in values.items():
            self.assertEqual(decoded[key], Decimal(value))

    def test_write_response_classification(self):
        accepted = bytes.fromhex("681200510503266883009616")
        rejected = bytes.fromhex("6812005105032668C301355A16")
        self.assertEqual(classify_write_response(accepted)["state"], "accepted")
        rejected_result = classify_write_response(rejected)
        self.assertEqual(rejected_result["state"], "rejected")
        self.assertEqual(rejected_result["error_byte"], 0x02)

    def test_full_read_decoder_round_trip(self):
        payload = build_parameter_frame(METER, flat_config())["payload"]
        reply = build_frame(METER, 0x91, _add_33(bytes.fromhex("070104FF")[::-1]) + payload)
        decoded = parse_070104ff_prices(reply)
        self.assertEqual(decoded["meter_number"], METER)
        self.assertEqual(decoded["control_code"], 0x91)
        self.assertEqual(decoded["warnlowbala2"], Decimal("20.00"))
        self.assertEqual(decoded["set2Price4"], Decimal("50.0000"))

    def test_live_prepaid_route_has_no_legacy_builder_or_retry_loop(self):
        source = Path(__file__).with_name("views_prepaid.py").read_text()
        self.assertNotIn("DLT645_2007_Prepaid", source)
        self.assertIn("build_parameter_frame", source)
        self.assertIn("max_attempts=1", source)
