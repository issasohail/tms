from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from smart_meter.dlt645 import verify_checksum
from smart_meter.management.commands.meter_listener import (
    DbCommandPoller,
    _decode_switch_action_from_hex,
    _deliver_if_match,
    _register_handler,
    _unregister_handler,
)
from smart_meter.models import LiveReading, Meter, MeterCommand, MeterTimingEvent
from smart_meter.protocol import build_switch_command
from smart_meter.services.command_lifecycle import queue_relay_command
from smart_meter.services.timing_schedule import enforce_meter_timing_schedule
from smart_meter.utils.commands import send_restore_command
from smart_meter.utils.db_send import send_via_db
from smart_meter.vendor.switch_OnOff import (
    RELAY_CLOSE_COMMAND,
    RELAY_OPEN_COMMAND,
    frame_command,
)


METER_NUMBER = "260305510009"
EXPECTED_OFF_FRAME = (
    "FEFEFEFE68090051050326681C1035333333333333334D348C8C566445CC8216"
)
EXPECTED_ON_FRAME = (
    "FEFEFEFE68090051050326681C1035333333333333334E348C8C566445CC8316"
)
METER_ACK = bytes.fromhex("68090051050326689C00F416")
STATUS_REPLY = bytes.fromhex("68090051050326689100E916")


class ImmediateMeterHandler:
    peer = "test:6000"

    def __init__(self, meter_number, *, reply_to_status=True):
        self.meter_number = meter_number
        self.reply_to_status = reply_to_status
        self.frames = []

    def enqueue_send(self, frame, expire_at=None, transport_q=None):
        frame_hex = frame.hex().upper()
        self.frames.append(frame_hex)
        if transport_q is not None:
            transport_q.put_nowait((True, ""))
        if "681C10" in frame_hex:
            _deliver_if_match(self.meter_number, "", 0x9C, METER_ACK)
        elif self.reply_to_status:
            _deliver_if_match(
                self.meter_number, "028011FF", 0x91, STATUS_REPLY
            )

    def close(self, reason="shutdown"):
        return None


class SwitchFrameTests(TestCase):
    def test_production_frames_have_correct_actions_checksums_and_exact_bytes(self):
        off = frame_command(METER_NUMBER, RELAY_OPEN_COMMAND)
        on = frame_command(METER_NUMBER, RELAY_CLOSE_COMMAND)

        self.assertEqual(off.hex().upper(), EXPECTED_OFF_FRAME)
        self.assertEqual(on.hex().upper(), EXPECTED_ON_FRAME)
        self.assertEqual(verify_checksum(off[4:], 0), (True, "incl_1st68"))
        self.assertEqual(verify_checksum(on[4:], 0), (True, "incl_1st68"))
        self.assertEqual(_decode_switch_action_from_hex(off.hex()), "OFF")
        self.assertEqual(_decode_switch_action_from_hex(on.hex()), "ON")

    def test_alternate_protocol_builder_uses_correct_actions(self):
        self.assertEqual(
            _decode_switch_action_from_hex(
                build_switch_command(METER_NUMBER, "off").hex()
            ),
            "OFF",
        )
        self.assertEqual(
            _decode_switch_action_from_hex(
                build_switch_command(METER_NUMBER, "on").hex()
            ),
            "ON",
        )

    @patch("smart_meter.utils.commands.send_via_listener")
    def test_restore_utility_uses_corrected_on_frame(self, send):
        send.return_value = {"ok": True}

        send_restore_command(METER_NUMBER)

        self.assertEqual(send.call_args.args[1].hex().upper(), EXPECTED_ON_FRAME)


class ManualSwitchQueueTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number=METER_NUMBER)

    def test_pending_manual_command_is_safely_reused(self):
        first = queue_relay_command(self.meter, "on", source="manual")

        second = queue_relay_command(self.meter, "on", source="manual")

        self.assertEqual(second.pk, first.pk)

    def test_acknowledged_manual_command_is_not_reused(self):
        first = queue_relay_command(self.meter, "on", source="manual")
        MeterCommand.objects.filter(pk=first.pk).update(status="acknowledged")

        second = queue_relay_command(self.meter, "on", source="manual")

        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual(second.frame_hex, EXPECTED_ON_FRAME)

    def test_opposite_manual_command_cancels_only_unfinished_opposite(self):
        unfinished = queue_relay_command(self.meter, "off", source="manual")
        completed = MeterCommand.objects.create(
            meter=self.meter,
            meter_number=self.meter.meter_number,
            frame_hex=EXPECTED_OFF_FRAME,
            command_type="relay",
            desired_state="off",
            source="manual",
            status="acknowledged",
        )

        queue_relay_command(self.meter, "on", source="manual")

        unfinished.refresh_from_db()
        completed.refresh_from_db()
        self.assertEqual(unfinished.status, "cancelled")
        self.assertEqual(completed.status, "acknowledged")

    @patch("smart_meter.services.command_lifecycle.queue_relay_command")
    def test_relay_acknowledgement_without_verification_is_not_ui_success(self, queue):
        command = MeterCommand.objects.create(
            meter=self.meter,
            meter_number=self.meter.meter_number,
            frame_hex=EXPECTED_ON_FRAME,
            command_type="relay",
            desired_state="on",
            source="manual",
            status="acknowledged",
        )
        queue.return_value = command

        result = send_via_db(
            meter_number=self.meter.meter_number,
            frame_hex=EXPECTED_ON_FRAME,
            command_type="relay",
            desired_state="on",
            source="manual",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "acknowledged")
        self.assertIn("not verified", result["error"])


class AutomaticSourceFrameTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number=METER_NUMBER)

    def test_payment_and_credit_control_restore_use_corrected_frame(self):
        payment = queue_relay_command(self.meter, "on", source="payment")
        credit = queue_relay_command(self.meter, "on", source="credit_control")

        self.assertEqual(payment.frame_hex, EXPECTED_ON_FRAME)
        self.assertEqual(credit.frame_hex, EXPECTED_ON_FRAME)

    def test_timing_schedule_on_uses_corrected_frame(self):
        monday = timezone.make_aware(datetime(2026, 8, 24, 9, 0))
        MeterTimingEvent.objects.create(
            meter=self.meter, weekday=0, event_time=time(8), command="on"
        )
        self.meter.power_status = "off"
        self.meter.save(update_fields=["power_status"])
        MeterCommand.objects.create(
            meter=self.meter,
            meter_number=self.meter.meter_number,
            frame_hex=EXPECTED_OFF_FRAME,
            command_type="relay",
            desired_state="off",
            source="schedule",
            status="verified",
        )

        with patch(
            "smart_meter.services.timing_schedule.timezone.now", return_value=monday
        ):
            command = enforce_meter_timing_schedule(self.meter)

        self.assertIsNotNone(command)
        self.assertEqual(command.desired_state, "on")
        self.assertEqual(command.source, "schedule")
        self.assertEqual(command.frame_hex, EXPECTED_ON_FRAME)


class RelayVerificationTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number=METER_NUMBER)
        self.poller = DbCommandPoller(interval=0)

    def command(self, desired="on"):
        return queue_relay_command(
            self.meter,
            desired,
            source="manual",
            expires_in=timedelta(minutes=5),
        )

    @patch("smart_meter.management.commands.meter_listener.parse_frame")
    def test_real_ack_and_matching_fresh_status_become_verified(self, parse):
        parse.side_effect = [
            {"meter_number": self.meter.meter_number, "control_code": 0x9C, "di": ""},
            {"meter_number": self.meter.meter_number, "control_code": 0x91, "di": "028011FF", "data": {"status_word": "0000"}},
        ]
        command = self.command("on")
        command.timeout = 0.05
        handler = ImmediateMeterHandler(self.meter.meter_number)

        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ):
            self.poller._process_command(command)

        command.refresh_from_db()
        self.meter.refresh_from_db()
        self.assertEqual(command.status, "verified")
        self.assertEqual(command.raw_ack_hex, METER_ACK.hex().upper())
        self.assertEqual(command.reply_hex, STATUS_REPLY.hex().upper())
        self.assertEqual(command.parsed_relay_state, "on")
        self.assertIsNotNone(command.acknowledged_at)
        self.assertIsNotNone(command.verified_at)
        self.assertEqual(self.meter.power_status, "on")

    def test_socket_send_without_meter_ack_is_not_acknowledged_or_verified(self):
        command = self.command("on")
        command.timeout = 0.01
        handler = SimpleNamespace()

        def transport_only(frame, expire_at=None, transport_q=None):
            transport_q.put_nowait((True, ""))

        handler.enqueue_send = transport_only
        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ):
            self.poller._process_command(command)

        command.refresh_from_db()
        self.assertIn(command.status, {"retry", "failed"})
        self.assertIsNone(command.acknowledged_at)
        self.assertIsNone(command.verified_at)
        self.assertEqual(command.raw_ack_hex, "")

    @patch("smart_meter.management.commands.meter_listener.parse_frame")
    def test_missing_relay_bit_remains_acknowledged_but_unverified(self, parse):
        parse.side_effect = [
            {"meter_number": self.meter.meter_number, "control_code": 0x9C, "di": ""},
            {"meter_number": self.meter.meter_number, "control_code": 0x91, "di": "028011FF", "data": {"status_word": ""}},
        ]
        command = self.command("on")
        command.timeout = 0.05
        handler = ImmediateMeterHandler(self.meter.meter_number)

        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ):
            self.poller._process_command(command)

        command.refresh_from_db()
        self.assertEqual(command.status, "acknowledged")
        self.assertIsNotNone(command.acknowledged_at)
        self.assertIsNone(command.verified_at)
        self.assertEqual(command.parsed_relay_state, "")
        self.assertIn("no relay state", command.error)

    @patch("smart_meter.management.commands.meter_listener.parse_frame")
    def test_status_timeout_does_not_reuse_stale_live_state(self, parse):
        parse.return_value = {"meter_number": self.meter.meter_number, "control_code": 0x9C, "di": ""}
        LiveReading.objects.create(
            meter=self.meter,
            status_word="0000",
            ts=timezone.now() - timedelta(hours=1),
        )
        command = self.command("on")
        command.timeout = 0.01
        handler = ImmediateMeterHandler(
            self.meter.meter_number, reply_to_status=False
        )

        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ):
            self.poller._process_command(command)

        command.refresh_from_db()
        self.assertEqual(command.status, "acknowledged")
        self.assertIsNone(command.verified_at)
        self.assertIn("verification timed out", command.error)

    @patch("smart_meter.management.commands.meter_listener.parse_frame")
    def test_waiting_online_command_dispatches_corrected_frame_after_reconnect(
        self, parse
    ):
        parse.side_effect = [
            {"meter_number": self.meter.meter_number, "control_code": 0x9C, "di": ""},
            {"meter_number": self.meter.meter_number, "control_code": 0x91, "di": "028011FF", "data": {"status_word": "0000"}},
        ]
        command = self.command("on")
        MeterCommand.objects.filter(pk=command.pk).update(status="waiting_online")
        handler = ImmediateMeterHandler(self.meter.meter_number)

        try:
            _register_handler(self.meter.meter_number, handler)
            command.refresh_from_db()
            self.assertEqual(command.status, "pending")
            self.poller._process_command(command)
        finally:
            _unregister_handler(self.meter.meter_number, handler)

        command.refresh_from_db()
        self.assertEqual(handler.frames[0], EXPECTED_ON_FRAME)
        self.assertEqual(command.status, "verified")
