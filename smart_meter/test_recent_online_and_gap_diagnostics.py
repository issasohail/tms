from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from smart_meter.services.meter_presence import MeterPresence
from smart_meter.status import (
    build_reconnect_gap_diagnostics,
    resolve_meter_online_status,
)


@override_settings(SMART_METER_ONLINE_THRESHOLD_MINUTES=10)
class RecentOnlineStatusTests(SimpleTestCase):
    def setUp(self):
        self.meter = SimpleNamespace(meter_number="TEST-001")

    @patch("smart_meter.status.presence_ttl_seconds", return_value=180)
    def test_recent_measurement_remains_online_after_normal_socket_close(self, _ttl):
        now = timezone.now()
        reading = SimpleNamespace(ts=now - timedelta(minutes=3))
        presence = MeterPresence(
            available=True,
            connected=False,
            last_contact_at=reading.ts,
            socket_seen_at=now - timedelta(minutes=1),
            source_ip="203.99.190.49",
            source_port=50000,
        )

        status = resolve_meter_online_status(self.meter, reading, presence)

        self.assertTrue(status["measurement_is_fresh"])
        self.assertFalse(status["is_connected"])
        self.assertTrue(status["is_online"])
        self.assertEqual(status["connection_state"], "online")

    @patch("smart_meter.status.presence_ttl_seconds", return_value=180)
    def test_stale_measurement_with_live_socket_is_transport_stale(self, _ttl):
        now = timezone.now()
        reading = SimpleNamespace(ts=now - timedelta(minutes=30))
        presence = MeterPresence(
            available=True,
            connected=True,
            last_contact_at=now - timedelta(minutes=30),
            socket_seen_at=now - timedelta(seconds=20),
            source_ip="203.99.190.49",
            source_port=50001,
        )

        status = resolve_meter_online_status(self.meter, reading, presence)

        self.assertFalse(status["measurement_is_fresh"])
        self.assertTrue(status["is_connected"])
        self.assertTrue(status["is_online"])
        self.assertEqual(status["connection_state"], "stale")

    @patch("smart_meter.status.presence_ttl_seconds", return_value=180)
    def test_stale_measurement_and_no_socket_is_offline(self, _ttl):
        now = timezone.now()
        reading = SimpleNamespace(ts=now - timedelta(minutes=30))
        presence = MeterPresence(
            available=True,
            connected=False,
            last_contact_at=reading.ts,
            socket_seen_at=now - timedelta(minutes=20),
        )

        status = resolve_meter_online_status(self.meter, reading, presence)

        self.assertFalse(status["is_online"])
        self.assertFalse(status["is_connected"])
        self.assertEqual(status["connection_state"], "offline")


class ReconnectGapDiagnosticTests(SimpleTestCase):
    def event(self, when, event_type, reason=""):
        return SimpleNamespace(
            occurred_at=when,
            event_type=event_type,
            disconnect_reason=reason,
        )

    def test_current_and_completed_gaps_ignore_replaced_disconnects(self):
        now = timezone.now()
        events = [
            self.event(now - timedelta(hours=4), "disconnected", "eof"),
            self.event(now - timedelta(hours=3, minutes=30), "connected"),
            self.event(now - timedelta(hours=3), "disconnected", "replaced"),
            self.event(now - timedelta(hours=2, minutes=55), "reconnected"),
            self.event(now - timedelta(hours=2), "disconnected", "recv_error"),
        ]

        diagnostics = build_reconnect_gap_diagnostics(events, now=now)

        self.assertEqual(diagnostics["last_completed_gap"]["label"], "30m")
        self.assertEqual(diagnostics["longest_completed_gap"]["label"], "30m")
        self.assertEqual(diagnostics["current_gap"]["label"], "2h")
        self.assertEqual(diagnostics["current_gap"]["reason"], "recv_error")

    def test_no_disconnects_has_no_gap(self):
        now = timezone.now()
        diagnostics = build_reconnect_gap_diagnostics(
            [self.event(now - timedelta(minutes=5), "connected")],
            now=now,
        )

        self.assertIsNone(diagnostics["current_gap"])
        self.assertIsNone(diagnostics["last_completed_gap"])
        self.assertIsNone(diagnostics["longest_completed_gap"])
