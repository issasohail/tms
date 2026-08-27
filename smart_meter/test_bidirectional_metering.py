import importlib
import queue
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from smart_meter.dlt645 import build_frame, parse_frame
from smart_meter.management.commands.meter_listener import (
    ClientHandler,
    _push_waiter,
)
from smart_meter.models import LiveReading, Meter, MeterReading
from smart_meter.utils.frames import build_read_register


METER_NUMBER = "260305510020"
CAPTURED_FORWARD = bytes.fromhex(
    "FEFEFEFE682000510503266891083333343333333333A116"
)
CAPTURED_REVERSE = bytes.fromhex(
    "FEFEFEFE682000510503266891083333353333333333A216"
)


def _add_33(data):
    return bytes(((byte + 0x33) & 0xFF) for byte in data)


def register_reply(di, plain_payload, *, meter_number=METER_NUMBER, preamble=False):
    data = _add_33(bytes.fromhex(di)[::-1] + plain_payload)
    return build_frame(
        meter_number,
        0x91,
        data,
        checksum_mode="std",
        include_preamble=preamble,
    )


class FakeSocket:
    def __init__(self, chunks=None):
        self.chunks = list(chunks or [b""])

    def settimeout(self, _timeout):
        pass

    def setsockopt(self, *_args):
        pass

    def recv(self, _size):
        return self.chunks.pop(0)

    def sendall(self, _frame):
        pass

    def shutdown(self, _how):
        pass

    def close(self):
        pass


class BidirectionalParserTests(SimpleTestCase):
    def test_captured_zero_forward_and_reverse_frames(self):
        forward = parse_frame(CAPTURED_FORWARD)
        reverse = parse_frame(CAPTURED_REVERSE)
        self.assertEqual(forward["meter_number"], METER_NUMBER)
        self.assertEqual(forward["di"], "00010000")
        self.assertEqual(
            forward["data"]["forward_active_energy_kwh"], Decimal("0.00")
        )
        self.assertEqual(reverse["meter_number"], METER_NUMBER)
        self.assertEqual(reverse["di"], "00020000")
        self.assertEqual(
            reverse["data"]["reverse_active_energy_kwh"], Decimal("0.00")
        )

    def test_non_zero_registers_decode_exact_decimal_without_float(self):
        # 12 34 56 78 BCD is transmitted least-significant byte first.
        forward = register_reply("00010000", bytes.fromhex("78563412"))
        reverse = register_reply("00020000", bytes.fromhex("21030000"), preamble=True)
        self.assertEqual(
            parse_frame(forward)["data"]["forward_active_energy_kwh"],
            Decimal("123456.78"),
        )
        self.assertEqual(
            parse_frame(reverse)["data"]["reverse_active_energy_kwh"],
            Decimal("3.21"),
        )

    def test_request_frames_match_captured_forward_and_reverse_requests(self):
        self.assertEqual(
            build_read_register(METER_NUMBER, "00010000").hex().upper(),
            "FEFEFEFE68200051050326681104333334335116",
        )
        self.assertEqual(
            build_read_register(METER_NUMBER, "00020000").hex().upper(),
            "FEFEFEFE68200051050326681104333335335216",
        )

    def test_non_bcd_and_bad_checksum_are_rejected(self):
        malformed = register_reply("00010000", bytes.fromhex("0A000000"))
        self.assertIsNone(parse_frame(malformed)["data"])
        bad_checksum = bytearray(CAPTURED_FORWARD)
        bad_checksum[-2] ^= 0x01
        self.assertIsNone(parse_frame(bytes(bad_checksum)))

    def test_direct_three_phase_registers_decode_to_decimal(self):
        cases = (
            ("02010100", bytes.fromhex("0123"), "voltage_a", Decimal("230.1")),
            ("02020200", bytes.fromhex("501200"), "current_b", Decimal("1.250")),
            ("02030300", bytes.fromhex("341200"), "power_c", Decimal("0.1234")),
        )
        for di, payload, field, expected in cases:
            with self.subTest(di=di):
                self.assertEqual(parse_frame(register_reply(di, payload))["data"][field], expected)


class BidirectionalPersistenceTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number=METER_NUMBER)
        self.handler = ClientHandler(FakeSocket(), ("127.0.0.1", 23456))

    def test_forward_is_authoritative_and_reverse_never_reduces_total_energy(self):
        self.handler.process_frame(register_reply("00010000", bytes.fromhex("00100000")))
        self.handler.process_frame(register_reply("00020000", bytes.fromhex("50000000")))
        live = LiveReading.objects.get(meter=self.meter)
        history = MeterReading.objects.get(meter=self.meter)
        self.assertEqual(live.forward_active_energy_kwh, Decimal("10.000"))
        self.assertEqual(live.reverse_active_energy_kwh, Decimal("0.500"))
        self.assertEqual(live.total_energy, Decimal("10.000"))
        self.assertEqual(history.total_energy, Decimal("10.000"))
        self.assertEqual(history.reverse_active_energy_kwh, Decimal("0.500"))

    def test_malformed_reply_does_not_overwrite_valid_reading(self):
        LiveReading.objects.create(
            meter=self.meter,
            total_energy=Decimal("42.000"),
            forward_active_energy_kwh=Decimal("42.000"),
        )
        malformed = register_reply("00010000", bytes.fromhex("0A000000"))
        self.handler.process_frame(malformed)
        live = LiveReading.objects.get(meter=self.meter)
        self.assertEqual(live.total_energy, Decimal("42.000"))
        self.assertEqual(live.forward_active_energy_kwh, Decimal("42.000"))

    def test_nonpersistent_validated_query_does_not_write(self):
        waiter = queue.Queue()
        _push_waiter(
            METER_NUMBER,
            waiter,
            "00010000",
            expect_controls={0x91},
            persist_reply=False,
        )
        frame = register_reply("00010000", bytes.fromhex("00100000"))
        self.handler.process_frame(frame)
        self.assertEqual(waiter.get_nowait(), frame)
        self.assertFalse(LiveReading.objects.filter(meter=self.meter).exists())

    @patch("smart_meter.management.commands.meter_listener.threading.Thread.start")
    @patch("smart_meter.management.commands.meter_listener.connection.close")
    def test_fragmented_and_concatenated_tcp_frames_are_both_processed(
        self, _close_connection, _start
    ):
        forward = register_reply("00010000", bytes.fromhex("00100000"), preamble=True)
        reverse = register_reply("00020000", bytes.fromhex("50000000"))
        split = 11
        handler = ClientHandler(
            FakeSocket([forward[:split], forward[split:] + reverse, b""]),
            ("127.0.0.1", 23456),
        )
        handler.run()
        live = LiveReading.objects.get(meter=self.meter)
        self.assertEqual(live.total_energy, Decimal("10.000"))
        self.assertEqual(live.reverse_active_energy_kwh, Decimal("0.500"))

    def test_single_phase_defaults_and_missing_phases_remain_null(self):
        self.assertEqual(self.meter.reading_profile, Meter.READING_PROFILE_AUTO)
        self.handler.process_frame(register_reply("02010100", bytes.fromhex("0123")))
        live = LiveReading.objects.get(meter=self.meter)
        self.assertEqual(live.voltage_a, Decimal("230.1"))
        self.assertIsNone(live.voltage_b)
        self.assertIsNone(live.voltage_c)
        history = MeterReading.objects.get(meter=self.meter)
        self.assertIsNone(history.power_b)

    def test_four_decimal_power_is_preserved_in_live_and_history(self):
        self.handler.process_frame(register_reply("02030300", bytes.fromhex("341200")))
        live = LiveReading.objects.get(meter=self.meter)
        history = MeterReading.objects.get(meter=self.meter)
        self.assertEqual(live.power_c, Decimal("0.1234"))
        self.assertEqual(history.power_c, Decimal("0.1234"))

    def test_three_named_meters_are_configured_by_number(self):
        other_numbers = ("260305510019", "260305510021")
        for number in other_numbers:
            Meter.objects.create(meter_number=number)
        migration = importlib.import_module(
            "smart_meter.migrations.0028_bidirectional_three_phase_metering"
        )
        migration.configure_three_phase_profiles(apps, None)
        profiles = dict(
            Meter.objects.filter(
                meter_number__in=(METER_NUMBER,) + other_numbers
            ).values_list("meter_number", "reading_profile")
        )
        self.assertEqual(set(profiles.values()), {Meter.READING_PROFILE_TOTAL_AND_PER_PHASE})


class EnergyRegisterCommandTests(TestCase):
    def setUp(self):
        Meter.objects.create(meter_number=METER_NUMBER)

    @patch("smart_meter.management.commands.query_energy_registers.send_via_db")
    def test_command_prints_raw_and_exact_values_without_persisting_by_default(self, send):
        send.side_effect = [
            {"ok": True, "reply": CAPTURED_FORWARD.hex().upper()},
            {"ok": True, "reply": CAPTURED_REVERSE.hex().upper()},
        ]
        output = StringIO()
        call_command("query_energy_registers", meter=METER_NUMBER, stdout=output)
        rendered = output.getvalue()
        self.assertIn("raw TX: FEFEFEFE", rendered)
        self.assertIn("raw RX: FEFEFEFE", rendered)
        self.assertIn("DI 00010000", rendered)
        self.assertIn("value: 0.00 kWh", rendered)
        self.assertIn("zero does not prove accumulation", rendered)
        self.assertTrue(all(call.kwargs["source"] == "energy_probe" for call in send.call_args_list))

    @patch("smart_meter.management.commands.query_energy_registers.send_via_db")
    def test_command_persist_flag_and_clean_timeout_failure(self, send):
        send.return_value = {"ok": False, "error": "timeout"}
        with self.assertRaisesMessage(CommandError, "--persist and --confirm-persist"):
            call_command(
                "query_energy_registers",
                meter=METER_NUMBER,
                persist=True,
                stderr=StringIO(),
            )
        send.assert_not_called()
