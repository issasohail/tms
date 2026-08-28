"""Offline tests for the read-only meter diagnostic path."""

from decimal import Decimal
import queue
from unittest.mock import patch

from django.test import SimpleTestCase

from smart_meter.diagnostic import (
    DIAGNOSTIC_DI_ALLOWLIST,
    build_diagnostic_read_frame,
    decode_diagnostic_response,
    validate_diagnostic_request_frame,
)
from smart_meter.management.commands.meter_listener import (
    ClientHandler,
    _deliver_if_match,
    _push_waiter,
    process_diagnostic_request,
    start_diagnostic_server,
)


METER = "260305510020"
# Exact zero-value reply vectors for the requested meter address and DIs.
ZERO_FORWARD_ACTIVE_ENERGY_REPLY = bytes.fromhex(
    "682000510503266891083333343333333333A116"
)
ZERO_REVERSE_ACTIVE_ENERGY_REPLY = bytes.fromhex(
    "682000510503266891083333353333333333A216"
)
CAPTURED_BULK_REPLY = bytes.fromhex(
    "682000510503266891453244B335333333333333333377575456335745C5B4333333333333"
    "4A83B74C83B733333334333395BC95BC3343334398C67933333333333333333398C6793333"
    "3333333633D916"
)


class FakeSocket:
    def settimeout(self, _timeout):
        pass

    def setsockopt(self, *_args):
        pass

    def shutdown(self, _how):
        pass

    def close(self):
        pass


class DiagnosticFrameTests(SimpleTestCase):
    def test_allowlisted_request_is_complete_c11_and_first68_checksum(self):
        frame = build_diagnostic_read_frame(METER, "00010000")
        self.assertTrue(frame.startswith(b"\xFE" * 4))
        inner = frame[4:]
        self.assertEqual(inner[8], 0x11)
        self.assertEqual(inner[9], 4)
        validate_diagnostic_request_frame(frame, meter_number=METER, di="00010000")

    def test_meter_019_request_is_not_rejected_when_checksum_styles_coincide(self):
        meter = "260305510019"
        frame = build_diagnostic_read_frame(meter, "00010000")
        self.assertEqual(
            frame.hex().upper(),
            "FEFEFEFE68190051050326681104333334334A16",
        )
        validate_diagnostic_request_frame(frame, meter_number=meter, di="00010000")

    def test_captured_direction_flagged_currents_decode_as_bcd_magnitude(self):
        cases = (
            (
                METER,
                "02020100",
                "68200051050326689107333435354C48B42016",
                Decimal("11.519"),
            ),
            (
                "260305510021",
                "02020200",
                "6821005105032668910733353535598AB47116",
                Decimal("15.726"),
            ),
            (
                "260305510021",
                "02020300",
                "68210051050326689107333635354B85B45F16",
                Decimal("15.218"),
            ),
        )
        for meter, di, raw, expected in cases:
            with self.subTest(meter=meter, di=di):
                result = decode_diagnostic_response(
                    bytes.fromhex(raw), expected_meter=meter, expected_di=di
                )
                self.assertEqual(result["value"], expected)

    def test_non_allowlisted_and_write_identifiers_are_rejected(self):
        self.assertNotIn("070102FF", DIAGNOSTIC_DI_ALLOWLIST)
        for di in ("070102FF", "070104FF", "FFFFFFFF", ""):
            with self.subTest(di=di), self.assertRaises(ValueError):
                build_diagnostic_read_frame(METER, di)

    def test_zero_forward_energy_reply_uses_decimal_and_kwh(self):
        result = decode_diagnostic_response(
            ZERO_FORWARD_ACTIVE_ENERGY_REPLY,
            expected_meter=METER,
            expected_di="00010000",
        )
        self.assertEqual(result["status"], "supported")
        self.assertIsInstance(result["value"], Decimal)
        self.assertEqual(result["value"], Decimal("0.00"))
        self.assertEqual(result["unit"], "kWh")
        self.assertEqual(result["returned_di"], "00010000")
        self.assertTrue(result["checksum_ok"])

    def test_zero_reverse_energy_reply_uses_decimal_and_kwh(self):
        result = decode_diagnostic_response(
            ZERO_REVERSE_ACTIVE_ENERGY_REPLY,
            expected_meter=METER,
            expected_di="00020000",
        )
        self.assertEqual(result["status"], "supported")
        self.assertIsInstance(result["value"], Decimal)
        self.assertEqual(result["value"], Decimal("0.00"))
        self.assertEqual(result["unit"], "kWh")
        self.assertEqual(result["returned_di"], "00020000")
        self.assertTrue(result["checksum_ok"])

    def test_bulk_capture_decodes_every_numeric_value_as_decimal(self):
        result = decode_diagnostic_response(
            CAPTURED_BULK_REPLY,
            expected_meter=METER,
            expected_di="028011FF",
        )
        values = result["value"]
        for name, value in values.items():
            if name != "status_word":
                with self.subTest(name=name):
                    self.assertIsInstance(value, Decimal)
        self.assertEqual(values["voltage_a"], Decimal("244.4"))
        self.assertEqual(values["voltage_b"], Decimal("232.1"))
        self.assertEqual(values["voltage_c"], Decimal("240.0"))
        self.assertEqual(values["total_energy"], Decimal("4693.65"))
        self.assertEqual(values["status_word"], "0003")

    def test_response_address_di_checksum_length_and_bcd_are_enforced(self):
        with self.assertRaisesMessage(ValueError, "does not match"):
            decode_diagnostic_response(
                ZERO_FORWARD_ACTIVE_ENERGY_REPLY,
                expected_meter="260305510019",
                expected_di="00010000",
            )
        with self.assertRaisesMessage(ValueError, "does not match"):
            decode_diagnostic_response(
                ZERO_FORWARD_ACTIVE_ENERGY_REPLY,
                expected_meter=METER,
                expected_di="00020000",
            )
        bad_checksum = ZERO_FORWARD_ACTIVE_ENERGY_REPLY[:-2] + b"\x00\x16"
        with self.assertRaisesMessage(ValueError, "checksum"):
            decode_diagnostic_response(
                bad_checksum, expected_meter=METER, expected_di="00010000"
            )
        invalid_bcd = bytearray(ZERO_FORWARD_ACTIVE_ENERGY_REPLY)
        invalid_bcd[14] = 0xEE
        # Restore a valid first-68 checksum so BCD validation is the failing layer.
        invalid_bcd[-2] = sum(invalid_bcd[:-2]) & 0xFF
        with self.assertRaisesMessage(ValueError, "invalid BCD"):
            decode_diagnostic_response(
                bytes(invalid_bcd), expected_meter=METER, expected_di="00010000"
            )


class DiagnosticListenerRoutingTests(SimpleTestCase):
    @patch(
        "smart_meter.management.commands.meter_listener.DiagnosticUnixServer",
        None,
    )
    def test_unsupported_platform_can_import_but_cannot_start_unix_socket(self):
        with self.assertRaisesMessage(RuntimeError, "not supported"):
            start_diagnostic_server()

    @patch("smart_meter.management.commands.meter_listener.perform_diagnostic_read")
    def test_ipc_rejects_caller_supplied_frames_before_dispatch(self, perform):
        with self.assertRaisesMessage(ValueError, "unsupported fields"):
            process_diagnostic_request(
                {"meter": METER, "di": "00010000", "frame": "relay-or-write"}
            )
        perform.assert_not_called()

    def test_waiter_matches_meter_and_di_and_marks_response_consumed(self):
        waiter = queue.Queue(maxsize=1)
        _push_waiter(METER, waiter, "00010000", {0x91}, consume=True)
        self.assertFalse(
            _deliver_if_match("260305510019", "00010000", 0x91, b"wrong meter")
        )
        self.assertFalse(_deliver_if_match(METER, "00020000", 0x91, b"wrong DI"))
        self.assertEqual(
            _deliver_if_match(METER, "00010000", 0x91, ZERO_FORWARD_ACTIVE_ENERGY_REPLY),
            2,
        )
        self.assertEqual(waiter.get_nowait(), ZERO_FORWARD_ACTIVE_ENERGY_REPLY)

    @patch("smart_meter.management.commands.meter_listener.Meter.objects.get")
    @patch("smart_meter.management.commands.meter_listener._register_handler")
    @patch(
        "smart_meter.management.commands.meter_listener._deliver_if_match",
        return_value=2,
    )
    def test_consumed_diagnostic_response_stops_before_persistence(
        self, _deliver, _register, meter_get
    ):
        handler = ClientHandler(FakeSocket(), ("127.0.0.1", 12345))
        handler._process_frame(ZERO_FORWARD_ACTIVE_ENERGY_REPLY)
        _register.assert_not_called()
        meter_get.assert_not_called()
