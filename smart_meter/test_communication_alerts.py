from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from smart_meter.models import LiveReading, Meter, MeterCommunicationAlert, MeterConnectionEvent
from smart_meter.services.communication_alerts import evaluate_meter_communication_alerts


@override_settings(SMART_METER_COMMUNICATION_ALERT_MINUTES=30)
class MeterCommunicationAlertTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number="COMM-ALERT-001")

    def _live(self, ts):
        live = LiveReading.objects.create(meter=self.meter)
        LiveReading.objects.filter(pk=live.pk).update(ts=ts)
        live.refresh_from_db()
        return live

    def test_stale_reading_creates_one_open_alert_and_is_idempotent(self):
        now = timezone.now()
        self._live(now - timedelta(minutes=45))
        MeterConnectionEvent.objects.create(
            meter_number=self.meter.meter_number,
            event_type=MeterConnectionEvent.EVENT_DISCONNECTED,
            source_ip="203.99.190.49",
            source_port=51315,
            disconnect_reason="eof",
        )

        first = evaluate_meter_communication_alerts(now=now)
        second = evaluate_meter_communication_alerts(now=now + timedelta(minutes=5))

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["still_open"], 1)
        self.assertEqual(
            MeterCommunicationAlert.objects.filter(
                meter=self.meter, status=MeterCommunicationAlert.STATUS_OPEN
            ).count(),
            1,
        )
        alert = self.meter.communication_alerts.get()
        self.assertEqual(alert.disconnect_reason, "eof")
        self.assertEqual(alert.last_source_ip, "203.99.190.49")
        self.assertEqual(alert.last_source_port, 51315)

    def test_fresh_reading_resolves_existing_alert(self):
        now = timezone.now()
        old = now - timedelta(hours=2)
        live = self._live(old)
        evaluate_meter_communication_alerts(now=now - timedelta(hours=1))
        alert = self.meter.communication_alerts.get(status="open")

        fresh = now - timedelta(minutes=2)
        LiveReading.objects.filter(pk=live.pk).update(ts=fresh)
        result = evaluate_meter_communication_alerts(now=now)

        alert.refresh_from_db()
        self.assertEqual(result["resolved"], 1)
        self.assertEqual(alert.status, MeterCommunicationAlert.STATUS_RESOLVED)
        self.assertIsNotNone(alert.resolved_at)
        self.assertEqual(alert.restored_reading_at, fresh)
        self.assertGreater(alert.offline_duration_seconds, 0)

    def test_dry_run_does_not_write(self):
        now = timezone.now()
        self._live(now - timedelta(hours=1))
        result = evaluate_meter_communication_alerts(now=now, dry_run=True)
        self.assertEqual(result["created"], 1)
        self.assertFalse(MeterCommunicationAlert.objects.exists())
