"""Offline-only DL/T645/prepaid frame regression tests.

These tests build/parse byte arrays and exercise transport guards with mocks. They do
not open sockets, start the listener, dispatch commands, or create MeterCommand rows.
"""

import inspect
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, SimpleTestCase

from smart_meter.dlt645 import (
    _add_33,
    _addr_from_meter_number,
    _bcd_bytes_from_amount,
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

# Deliberately fake, non-production material for structure tests only.
TEST_OPERATOR = bytes.fromhex("11223344")
TEST_MAC1 = bytes.fromhex("01020304")
TEST_MAC2 = bytes.fromhex("A1A2A3A4")
TEST_ORDER = bytes.fromhex("0102030405060708")
TEST_SCHEDULE = bytes.fromhex("101112131415")
TEST_MAILING_ADDRESS = bytes.fromhex("202122232425")
TEST_PURCHASE_COUNT = bytes.fromhex("00000001")


def _plain_data(frame: bytes) -> bytes:
    while frame.startswith(b"\xFE"):
        frame = frame[1:]
    length = frame[9]
    return bytes((value - 0x33) & 0xFF for value in frame[10:10 + length])


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
    def test_amount_encoding_preserves_existing_project_representation(self):
        expected = {
            0.00: "00000000",
            1.00: "00000100",
            50.00: "00005000",
            100.00: "00010000",
        }
        for amount, plain_hex in expected.items():
            with self.subTest(amount=amount):
                plain = _bcd_bytes_from_amount(amount, 4, 2)
                self.assertEqual(plain.hex().upper(), plain_hex)
                self.assertEqual(_add_33(plain), bytes((b + 0x33) & 0xFF for b in plain))

    def test_security_sensitive_arguments_have_no_defaults(self):
        required = {
            build_topup_frame: ("operator", "mac1", "schedule_no", "mailing_addr", "mac2", "checksum_mode"),
            build_init_amount_frame: ("operator", "mac1", "purchase_count", "mac2", "checksum_mode"),
            build_refund_frame: ("operator", "mac1", "schedule_no", "mailing_addr", "mac2", "checksum_mode"),
        }
        for builder, names in required.items():
            signature = inspect.signature(builder)
            for name in names:
                with self.subTest(builder=builder.__name__, argument=name):
                    self.assertIs(signature.parameters[name].default, inspect.Parameter.empty)

    def test_offline_topup_structure(self):
        frame = build_topup_frame(
            METER, 50.00, TEST_ORDER,
            operator=TEST_OPERATOR, mac1=TEST_MAC1,
            schedule_no=TEST_SCHEDULE, mailing_addr=TEST_MAILING_ADDRESS,
            mac2=TEST_MAC2, checksum_mode="incl_1st68",
        )
        expected = (
            bytes.fromhex("FF020107") + TEST_OPERATOR + bytes.fromhex("00005000")
            + TEST_ORDER + TEST_MAC1 + TEST_SCHEDULE + TEST_MAILING_ADDRESS + TEST_MAC2
        )
        self.assertEqual(_plain_data(frame), expected)
        self.assertEqual(verify_checksum(frame, 0), (True, "incl_1st68"))


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

    def test_offline_refund_structure(self):
        frame = build_refund_frame(
            METER, 1.00, TEST_ORDER,
            operator=TEST_OPERATOR, mac1=TEST_MAC1,
            schedule_no=TEST_SCHEDULE, mailing_addr=TEST_MAILING_ADDRESS,
            mac2=TEST_MAC2, checksum_mode="incl_1st68",
        )
        expected = (
            bytes.fromhex("FF080107") + TEST_OPERATOR + bytes.fromhex("00000100")
            + TEST_ORDER + TEST_MAC1 + TEST_SCHEDULE + TEST_MAILING_ADDRESS + TEST_MAC2
        )
        self.assertEqual(_plain_data(frame), expected)
        self.assertEqual(verify_checksum(frame, 0), (True, "incl_1st68"))
