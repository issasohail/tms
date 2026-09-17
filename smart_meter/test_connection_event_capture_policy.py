from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from smart_meter.models import Meter, MeterSettings
from smart_meter.services.connection_event_capture import (
    clear_connection_event_capture_cache,
    effective_connection_event_capture_mode,
    should_capture_connection_event,
)


class ConnectionEventCapturePolicyTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number="CONNECTION-POLICY-1")
        MeterSettings.objects.update_or_create(
            pk=1,
            defaults={
                "connection_event_capture_mode": MeterSettings.CONNECTION_EVENT_CAPTURE_OFF,
            },
        )
        clear_connection_event_capture_cache()

    def tearDown(self):
        clear_connection_event_capture_cache()

    def test_global_off_disables_connection_event_history(self):
        self.assertFalse(should_capture_connection_event(self.meter.meter_number))

    def test_meter_can_temporarily_override_global_to_all(self):
        self.meter.connection_event_capture_mode = Meter.CONNECTION_EVENT_CAPTURE_ALL
        self.meter.connection_event_capture_until = timezone.now() + timedelta(hours=1)
        self.meter.save(update_fields=[
            "connection_event_capture_mode", "connection_event_capture_until",
        ])
        clear_connection_event_capture_cache()
        self.assertTrue(should_capture_connection_event(self.meter.meter_number))

    def test_expired_override_returns_to_global_off(self):
        self.meter.connection_event_capture_mode = Meter.CONNECTION_EVENT_CAPTURE_ALL
        self.meter.connection_event_capture_until = timezone.now() - timedelta(seconds=1)
        self.meter.save(update_fields=[
            "connection_event_capture_mode", "connection_event_capture_until",
        ])
        clear_connection_event_capture_cache()
        self.assertEqual(
            effective_connection_event_capture_mode(self.meter.meter_number),
            MeterSettings.CONNECTION_EVENT_CAPTURE_OFF,
        )
        self.assertFalse(should_capture_connection_event(self.meter.meter_number))

    def test_per_meter_off_overrides_global_all(self):
        MeterSettings.objects.filter(pk=1).update(
            connection_event_capture_mode=MeterSettings.CONNECTION_EVENT_CAPTURE_ALL,
        )
        self.meter.connection_event_capture_mode = Meter.CONNECTION_EVENT_CAPTURE_OFF
        self.meter.save(update_fields=["connection_event_capture_mode"])
        clear_connection_event_capture_cache()
        self.assertFalse(should_capture_connection_event(self.meter.meter_number))
