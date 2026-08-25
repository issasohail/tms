"""Offline-only DL/T645/prepaid frame regression tests.

These tests build/parse byte arrays and exercise transport guards with mocks. They do
not open sockets, start the listener, dispatch commands, or create MeterCommand rows.
"""

import inspect
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, SimpleTestCase

from smart_meter.dlt645 import (
    _add_33,
    _addr_from_meter_number,
    _amount_to_cents,
    build_charge_frame,
    build_frame,
    build_init_amount_frame,
    build_read_frame_for_di,
    build_refund_frame,
    build_topup_frame,
    calculate_outbound_checksum,
    parse_frame,
    verify_checksum,
)
from smart_meter.dlt645_money import build_amount_init_frame


METER = "260305510012"
CAPTURED_SECURITY_QUERY = bytes.fromhex(
    "681200510503266803083235B43A445566773716"
)
CAPTURED_SECURITY_RESPONSE = bytes.fromhex("6812005105032668C301355A16")
MANUFACTURER_RECHARGE = bytes.fromhex(
    "FEFEFEFE681200510503266803223235343A77665544435A33336B74453595B57345"
    "33333333453384383659333333338A16"
)
MANUFACTURER_REFUND = bytes.fromhex(
    "FEFEFEFE68120051050326680322323B343A776655441B3633336C74453595B57345"
    "33333333453384383659333333334516"
)

# Deliberately fake, non-production material for structure tests only.
TEST_OPERATOR = bytes.fromhex("11223344")
TEST_MAC1 = bytes.fromhex("01020304")
TEST_MAC2 = bytes.fromhex("A1A2A3A4")
TEST_PURCHASE_COUNT = bytes.fromhex("00000001")


def _plain_data(frame: bytes) -> bytes:
    while frame.startswith(b"\xFE"):
        frame = frame[1:]
    length = frame[9]
    return bytes((value - 0x33) & 0xFF for value in frame[10:10 + length])


def _data_field(frame: bytes) -> bytes:
    inner = frame[4:] if frame.startswith(b"\xFE" * 4) else frame
    return inner[10:10 + inner[9]]


class DLT645PrimitiveTests(SimpleTestCase):
    def test_meter_address_reversal(self):
        self.assertEqual(_addr_from_meter_number(METER), bytes.fromhex("120051050326"))

    def test_di_reversal_before_plus_33(self):
        expected = {
            "070102FF": "FF020107",
            "070103FF": "FF030107",
            "070108FF": "FF080107",
        }
        for di, wire_plain in expected.items():
            with self.subTest(di=di):
                self.assertEqual(bytes.fromhex(di)[::-1].hex().upper(), wire_plain)

    def test_plus_33_transformation(self):
        self.assertEqual(_add_33(bytes.fromhex("00FF020107")), bytes.fromhex("333235343A"))

    def test_explicit_checksum_modes(self):
        body = CAPTURED_SECURITY_QUERY[:-2]
        self.assertEqual(calculate_outbound_checksum(body, "std"), 0xD6)
        self.assertEqual(calculate_outbound_checksum(body, "incl_2nd68"), 0x3E)
        self.assertEqual(calculate_outbound_checksum(body, "incl_1st68"), 0x37)
        self.assertRaises(ValueError, calculate_outbound_checksum, body, "unknown")

    def test_known_production_capture_validates_only_as_incl_first_68(self):
        self.assertEqual(verify_checksum(CAPTURED_SECURITY_QUERY, 0), (True, "incl_1st68"))
        self.assertNotEqual(CAPTURED_SECURITY_QUERY[-2], calculate_outbound_checksum(
            CAPTURED_SECURITY_QUERY[:-2], "std"
        ))
        self.assertNotEqual(CAPTURED_SECURITY_QUERY[-2], calculate_outbound_checksum(
            CAPTURED_SECURITY_QUERY[:-2], "incl_2nd68"
        ))

    def test_known_security_response_regression(self):
        self.assertEqual(verify_checksum(CAPTURED_SECURITY_RESPONSE, 0), (True, "incl_1st68"))
        parsed = parse_frame(CAPTURED_SECURITY_RESPONSE)
        self.assertEqual(parsed["meter_number"], METER)
        self.assertEqual(parsed["control_code"], 0xC3)
        self.assertEqual((0x35 - 0x33) & 0xFF, 0x02)

    def test_known_normal_di_read_order_and_parser_behavior(self):
        request = build_read_frame_for_di(METER, "028011FF")
        self.assertEqual(_plain_data(request), bytes.fromhex("FF118002"))

        reply_data = _add_33(bytes.fromhex("FF11800200000000"))
        reply = build_frame(METER, 0x91, reply_data, checksum_mode="std")
        parsed = parse_frame(reply)
        self.assertEqual(parsed["di"], "028011FF")
        self.assertEqual(parsed["cs_style"], "std")
        self.assertEqual(parsed["data"]["balance"], 0.0)


class PrepaidAmountAndStructureTests(SimpleTestCase):
    def test_manufacturer_binary_little_endian_amount_examples(self):
        expected = {
            Decimal("1.00"): "97333333",
            Decimal("10.00"): "1B363333",
            Decimal("50.00"): "BB463333",
            Decimal("100.00"): "435A3333",
        }
        for amount, encoded_hex in expected.items():
            with self.subTest(amount=amount):
                frame = build_charge_frame(METER, "recharge", "1", amount)
                self.assertEqual(_data_field(frame)[8:12].hex().upper(), encoded_hex)

    def test_manufacturer_recharge_vector_byte_for_byte(self):
        frame = build_charge_frame(
            METER, "recharge", "1240826202124138", Decimal("100.00")
        )
        self.assertEqual(frame, MANUFACTURER_RECHARGE)
        self.assertEqual(
            build_topup_frame(METER, Decimal("100.00"), "1240826202124138"),
            MANUFACTURER_RECHARGE,
        )

    def test_manufacturer_refund_vector_byte_for_byte(self):
        frame = build_charge_frame(
            METER, "refund", "1240826202124139", Decimal("10.00")
        )
        self.assertEqual(frame, MANUFACTURER_REFUND)
        self.assertEqual(
            build_refund_frame(METER, Decimal("10.00"), "1240826202124139"),
            MANUFACTURER_REFUND,
        )

    def test_charge_field_reversal_and_plus_33(self):
        data = _data_field(MANUFACTURER_RECHARGE)
        self.assertEqual(MANUFACTURER_RECHARGE[5:11], bytes.fromhex("120051050326"))
        self.assertEqual(data[0:4], _add_33(bytes.fromhex("070102FF")[::-1]))
        self.assertEqual(data[4:8], bytes.fromhex("77665544"))
        self.assertEqual(data[12:20], bytes.fromhex("6B74453595B57345"))
        self.assertEqual(data[20:24], bytes.fromhex("33333333"))
        self.assertEqual(data[24:30], _add_33(bytes.fromhex(METER)[::-1]))
        self.assertEqual(data[30:34], bytes.fromhex("33333333"))

    def test_short_order_number_is_left_zero_padded_then_reversed(self):
        data = _data_field(build_charge_frame(METER, "070102FF", "A1", "1.00"))
        self.assertEqual(data[12:20], bytes.fromhex("D433333333333333"))

    def test_charge_control_length_and_checksum_window(self):
        inner = MANUFACTURER_RECHARGE[4:]
        self.assertEqual(inner[8], 0x03)
        self.assertEqual(inner[9], 0x22)
        self.assertEqual(len(_data_field(MANUFACTURER_RECHARGE)), 0x22)
        self.assertEqual(verify_checksum(inner, 0), (True, "incl_1st68"))
        self.assertEqual(
            inner[-2], calculate_outbound_checksum(inner[:-2], "incl_1st68")
        )

    def test_charge_validation_rejects_malformed_meter_number(self):
        for meter in ("26030551001", "2603055100123", "26030551001Z", "2603 5510012"):
            with self.subTest(meter=meter), self.assertRaises(ValueError):
                build_charge_frame(meter, "recharge", "1", "1.00")

    def test_charge_validation_rejects_bad_order_number(self):
        for order in ("", "123G", "12408262021241380"):
            with self.subTest(order=order), self.assertRaises(ValueError):
                build_charge_frame(METER, "recharge", order, "1.00")

    def test_charge_validation_rejects_invalid_di(self):
        for operation in ("070103FF", "other"):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                build_charge_frame(METER, operation, "1", "1.00")

    def test_charge_amount_requires_safe_positive_integer_cents(self):
        self.assertEqual(_amount_to_cents(Decimal("100.00")), 10000)
        for amount in (Decimal("0"), Decimal("-1"), Decimal("1.001")):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                build_charge_frame(METER, "recharge", "1", amount)
        with self.assertRaises(TypeError):
            build_charge_frame(METER, "recharge", "1", 1.00)

    def test_init_security_arguments_remain_required(self):
        signature = inspect.signature(build_init_amount_frame)
        for name in ("operator", "mac1", "purchase_count", "mac2", "checksum_mode"):
            with self.subTest(argument=name):
                self.assertIs(signature.parameters[name].default, inspect.Parameter.empty)


class PrepaidTransportSafetyTests(SimpleTestCase):
    @patch("smart_meter.management.commands.meter_send.socket.create_connection")
    def test_meter_send_refuses_money_operations_before_opening_socket(self, create_connection):
        for operation in ("topup", "init", "refund"):
            with self.subTest(operation=operation), self.assertRaises(CommandError):
                call_command(
                    "meter_send",
                    host="127.0.0.1",
                    port=7000,
                    meter=METER,
                    op=operation,
                )
        create_connection.assert_not_called()

    @patch("smart_meter.views._call_send")
    @patch("smart_meter.views.messages.error")
    @patch("smart_meter.views.get_object_or_404")
    def test_display_balance_views_stop_before_dispatch(
        self, get_object, _message_error, call_send
    ):
        from smart_meter.views import reset_meter_display_balance, set_meter_display_balance

        get_object.return_value = type("MeterStub", (), {
            "meter_number": METER,
        })()
        factory = RequestFactory()

        reset_response = reset_meter_display_balance(
            factory.post("/reset", HTTP_REFERER="/meters"), meter_id=1
        )
        set_response = set_meter_display_balance(
            factory.post("/set", {"amount": "50.00"}, HTTP_REFERER="/meters"),
            meter_id=1,
        )

        self.assertEqual(reset_response.status_code, 302)
        self.assertEqual(set_response.status_code, 302)
        call_send.assert_not_called()

    def test_offline_init_structure_and_legacy_preamble_wrapper(self):
        frame = build_amount_init_frame(
            METER, 100.00,
            operator=TEST_OPERATOR, mac1=TEST_MAC1,
            purchase_count=TEST_PURCHASE_COUNT, mac2=TEST_MAC2,
            checksum_mode="incl_1st68",
        )
        self.assertTrue(frame.startswith(b"\xFE" * 4))
        expected = (
            bytes.fromhex("FF030107") + TEST_OPERATOR + bytes.fromhex("00010000")
            + TEST_MAC1 + TEST_PURCHASE_COUNT + TEST_MAC2
        )
        self.assertEqual(_plain_data(frame), expected)
        self.assertEqual(verify_checksum(frame[4:], 0), (True, "incl_1st68"))
