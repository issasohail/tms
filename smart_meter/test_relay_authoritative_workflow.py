import json
import inspect
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase
from django.utils import timezone

from smart_meter import views
from smart_meter.management.commands.meter_listener import ClientHandler, DbCommandPoller
from smart_meter.models import LiveReading, Meter, MeterCommand
from smart_meter.services.command_lifecycle import queue_relay_command
from smart_meter.services.relay_status import (
    classify_relay_ack,
    reconcile_live_relay_command_state,
    sync_authoritative_relay_status,
)


class ExplicitRelayActionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.meter = Meter.objects.create(
            meter_number="260305510007", power_status="on"
        )

    def _request(self, view, cached_state):
        Meter.objects.filter(pk=self.meter.pk).update(power_status=cached_state)
        request = self.factory.post(
            "/smart-meter/relay/",
            data=json.dumps({"meter_id": self.meter.pk}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request.user = MagicMock()
        request.user.get_username.return_value = "relay-review"
        with patch.object(views, "DISABLE_CUTOFFS", False), patch.object(
            views, "_call_send", return_value={
                "ok": True, "status": "acknowledged", "command_id": 336, "error": ""
            }
        ) as sender, patch("time.sleep"), patch.object(
            views, "refresh_live"
        ), patch("smart_meter.views.MeterEvent.objects.create"):
            response = view(request, self.meter.pk)
        return response, sender.call_args.kwargs

    def test_turn_on_always_sends_close_regardless_of_cached_state(self):
        for cached_state in ("on", "off"):
            _response, kwargs = self._request(views.restore_meter, cached_state)
            self.assertEqual(kwargs["desired_state"], "on")
            self.assertIn("4E34", kwargs["frame"].hex().upper())
            self.meter.refresh_from_db()
            self.assertEqual(self.meter.power_status, cached_state)

    def test_turn_off_always_sends_open_regardless_of_cached_state(self):
        for cached_state in ("on", "off"):
            _response, kwargs = self._request(views.cutoff_meter, cached_state)
            self.assertEqual(kwargs["desired_state"], "off")
            self.assertIn("4D34", kwargs["frame"].hex().upper())
            self.meter.refresh_from_db()
            self.assertEqual(self.meter.power_status, cached_state)


class AuthoritativeRelayStatusTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number="260305510007", power_status="on")

    def test_valid_status_synchronizes_cached_meter_state(self):
        state = sync_authoritative_relay_status(self.meter, "0910")
        self.meter.refresh_from_db()
        self.assertEqual(state, "off")
        self.assertEqual(self.meter.power_status, "off")

    def test_invalid_or_missing_status_preserves_confirmed_state(self):
        for value in (None, "", "XYZ", "091"):
            self.assertIsNone(sync_authoritative_relay_status(self.meter, value))
            self.meter.refresh_from_db()
            self.assertEqual(self.meter.power_status, "on")

    def _process_telemetry(self, status_word):
        parsed = {
            "meter_number": self.meter.meter_number,
            "control_code": 0x91,
            "di": "028011FF",
            "data": {
                "status_word": status_word,
                "total_energy": "123.456",
                "voltage_a": "230.1",
                "current_a": "1.250",
            },
            "cs_style": "std",
        }
        handler = ClientHandler(MagicMock(), ("127.0.0.1", 23456))
        with patch(
            "smart_meter.management.commands.meter_listener.parse_frame",
            return_value=parsed,
        ), patch(
            "smart_meter.management.commands.meter_listener.verify_checksum",
            return_value=(True, "std"),
        ), patch(
            "smart_meter.management.commands.meter_listener._register_handler"
        ):
            handler.process_frame(b"\x68")

    def test_valid_fresh_telemetry_synchronizes_meter_state(self):
        self._process_telemetry("0910")
        self.meter.refresh_from_db()
        self.assertEqual(self.meter.power_status, "off")
        self.assertEqual(LiveReading.objects.get(meter=self.meter).status_word, "0910")

    def test_invalid_telemetry_does_not_overwrite_last_confirmed_word_or_state(self):
        LiveReading.objects.create(meter=self.meter, status_word="0000")
        self._process_telemetry("invalid")
        self.meter.refresh_from_db()
        live = LiveReading.objects.get(meter=self.meter)
        self.assertEqual(self.meter.power_status, "on")
        self.assertEqual(live.status_word, "0000")

    def test_acknowledgement_alone_does_not_verify_command(self):
        command = queue_relay_command(self.meter, "on", source="manual")
        command.status = "acknowledged"
        command.acknowledged_at = timezone.now()
        command.save(update_fields=["status", "acknowledged_at"])
        command.refresh_from_db()
        self.assertEqual(command.status, "acknowledged")
        self.assertIsNone(command.verified_at)

    def test_matching_readback_verifies_and_updates_meter(self):
        self.meter.power_status = "off"
        self.meter.save(update_fields=["power_status"])
        command = queue_relay_command(self.meter, "on", source="manual")
        command.status = "acknowledged"
        command.save(update_fields=["status"])

        sync_authoritative_relay_status(self.meter, "0000", command=command)

        command.refresh_from_db()
        self.meter.refresh_from_db()
        self.assertEqual(command.status, "verified")
        self.assertIsNotNone(command.verified_at)
        self.assertEqual(self.meter.power_status, "on")

    def test_mismatched_readback_does_not_verify(self):
        command = queue_relay_command(self.meter, "on", source="manual")
        command.status = "acknowledged"
        command.save(update_fields=["status"])

        sync_authoritative_relay_status(self.meter, "0100", command=command)

        command.refresh_from_db()
        self.assertEqual(command.status, "acknowledged")
        self.assertIsNone(command.verified_at)
        self.assertIn("does not match", command.error)

    def test_response_cannot_verify_different_meter_or_nonrelay_command(self):
        other = Meter.objects.create(meter_number="260305510008")
        command = queue_relay_command(other, "on", source="manual")
        with self.assertRaises(ValueError):
            sync_authoritative_relay_status(self.meter, "0000", command=command)

        unrelated = MeterCommand.objects.create(
            meter=self.meter,
            meter_number=self.meter.meter_number,
            frame_hex="68",
            command_type="read",
        )
        with self.assertRaises(ValueError):
            sync_authoritative_relay_status(self.meter, "0000", command=unrelated)

    def _live_command_state(self, command, status_word, *, reading_at=None, now=None):
        return reconcile_live_relay_command_state(
            self.meter,
            command,
            status_word,
            reading_at or timezone.now(),
            is_fresh=True,
            now=now,
        )

    def test_live_acknowledged_on_is_reconciled_by_fresh_matching_status(self):
        command = queue_relay_command(self.meter, "on", source="manual")
        MeterCommand.objects.filter(pk=command.pk).update(status="acknowledged")
        command.refresh_from_db()

        state = self._live_command_state(command, "0000")

        command.refresh_from_db()
        self.assertEqual(command.status, "verified")
        self.assertEqual(state["confirmed_state"], "on")
        self.assertEqual(state["status"], "verified")
        self.assertEqual(state["operation_label"], "")

    def test_live_acknowledged_off_is_reconciled_by_fresh_matching_status(self):
        command = queue_relay_command(self.meter, "off", source="manual")
        MeterCommand.objects.filter(pk=command.pk).update(status="acknowledged")
        command.refresh_from_db()

        state = self._live_command_state(command, "0910")

        command.refresh_from_db()
        self.assertEqual(command.status, "verified")
        self.assertEqual(state["confirmed_state"], "off")
        self.assertEqual(state["operation_label"], "")

    def test_live_acknowledged_mismatch_remains_unverified_and_not_working(self):
        command = queue_relay_command(self.meter, "on", source="manual")
        MeterCommand.objects.filter(pk=command.pk).update(status="acknowledged")
        command.refresh_from_db()

        state = self._live_command_state(command, "0910")

        command.refresh_from_db()
        self.assertEqual(command.status, "acknowledged")
        self.assertIsNone(command.verified_at)
        self.assertEqual(state["status"], "acknowledged")
        self.assertEqual(state["operation_label"], "")

    def test_live_pending_and_sent_commands_remain_working(self):
        for status, desired_state, label in (
            ("pending", "on", "Restoring…"),
            ("sent", "off", "Connecting…"),
        ):
            command = MeterCommand.objects.create(
                meter=self.meter,
                meter_number=self.meter.meter_number,
                frame_hex="68",
                command_type="relay",
                desired_state=desired_state,
                status=status,
                expires_at=timezone.now() + timedelta(minutes=5),
            )

            state = self._live_command_state(command, "0000")

            self.assertEqual(state["status"], status)
            self.assertEqual(state["operation_label"], label)
            self.assertEqual(state["indicator_class"], "is-working")

    def test_stale_historical_failure_and_expired_active_command_are_hidden(self):
        now = timezone.now()
        failed = MeterCommand.objects.create(
            meter=self.meter,
            meter_number=self.meter.meter_number,
            frame_hex="68",
            command_type="relay",
            desired_state="on",
            status="failed",
        )
        MeterCommand.objects.filter(pk=failed.pk).update(
            updated_at=now - timedelta(minutes=6)
        )
        failed.refresh_from_db()
        sent = MeterCommand.objects.create(
            meter=self.meter,
            meter_number=self.meter.meter_number,
            frame_hex="68",
            command_type="relay",
            desired_state="on",
            status="sent",
            expires_at=now - timedelta(minutes=6),
        )

        failed_state = self._live_command_state(failed, "0000", now=now)
        sent_state = self._live_command_state(sent, "0000", now=now)

        self.assertEqual(failed_state["status"], "")
        self.assertEqual(failed_state["indicator_label"], "")
        self.assertEqual(sent_state["status"], "")
        self.assertEqual(sent_state["indicator_label"], "")

    def test_initial_page_and_json_polling_share_reconciliation_helper(self):
        initial_source = inspect.getsource(views.live_custom)
        polling_source = inspect.getsource(views.live_custom_data)

        self.assertIn("reconcile_live_relay_command_state", initial_source)
        self.assertIn("reconcile_live_relay_command_state", polling_source)
        self.assertIn('relay_state["indicator_label"]', initial_source)
        self.assertIn('"relay_indicator_label": relay_state["indicator_label"]', polling_source)


class RelayAcknowledgementTests(TestCase):
    def test_9c_is_acknowledged_and_dc_is_failed(self):
        meter_number = "260305510007"
        self.assertEqual(
            classify_relay_ack(
                {"meter_number": meter_number, "control_code": 0x9C}, meter_number
            ),
            "acknowledged",
        )
        self.assertEqual(
            classify_relay_ack(
                {"meter_number": meter_number, "control_code": 0xDC}, meter_number
            ),
            "failed",
        )

    def test_ack_from_different_meter_is_rejected(self):
        self.assertIsNone(
            classify_relay_ack(
                {"meter_number": "260305510008", "control_code": 0x9C},
                "260305510007",
            )
        )

    def _run_ack(self, control_code):
        meter = Meter.objects.create(meter_number=f"260305510{control_code:03d}")
        command = queue_relay_command(meter, "on", source="manual")
        command.timeout = 0.001
        command.save(update_fields=["timeout"])
        handler = MagicMock()

        def send_ok(_frame, expire_at=None, transport_q=None):
            transport_q.put_nowait((True, ""))

        handler.enqueue_send.side_effect = send_ok

        def register_waiter(_meter_number, waiter, _di, expect_controls=None):
            if 0x9C in (expect_controls or ()) or 0xDC in (expect_controls or ()):
                waiter.put_nowait(b"ack")

        parsed_ack = {
            "meter_number": meter.meter_number,
            "control_code": control_code,
            "di": "",
            "data": None,
        }
        poller = DbCommandPoller()
        with patch(
            "smart_meter.management.commands.meter_listener.revalidate_command",
            return_value=SimpleNamespace(allowed=True, reason="test"),
        ), patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ), patch(
            "smart_meter.management.commands.meter_listener._push_waiter",
            side_effect=register_waiter,
        ), patch(
            "smart_meter.management.commands.meter_listener.parse_frame",
            return_value=parsed_ack,
        ):
            poller._process_command(command)
        command.refresh_from_db()
        return command

    def test_9c_alone_remains_acknowledged_not_verified(self):
        command = self._run_ack(0x9C)
        self.assertEqual(command.status, "acknowledged")
        self.assertIsNone(command.verified_at)
        self.assertIn("acknowledged but not verified", command.error)

    def test_dc_marks_command_failed(self):
        command = self._run_ack(0xDC)
        self.assertEqual(command.status, "failed")
        self.assertIn("0xDC", command.error)
