import inspect
import json
from unittest.mock import patch

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core import views as core_views
from dashboard import views as dashboard_views
from smart_meter import views as meter_views
from smart_meter import views_dashboard as meter_dashboard_views
from smart_meter.models import LiveReading, Meter
from smart_meter.services.meter_presence import MeterPresence
from smart_meter.status import online_threshold_minutes, resolve_meter_online_status


class MeterStatusThresholdTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.meter = Meter.objects.create(meter_number="THRESHOLD-TEST")
        self.reading = LiveReading.objects.create(meter=self.meter)
        LiveReading.objects.filter(pk=self.reading.pk).update(
            ts=timezone.now() - timezone.timedelta(minutes=5)
        )

    def status(self, query=""):
        with patch(
            "smart_meter.status.get_meter_presence",
            return_value=MeterPresence(available=False),
        ):
            response = meter_views.meter_status(
                self.factory.get(f"/smart-meter/status/{query}"), self.meter.pk
            )
        return json.loads(response.content)

    @override_settings(SMART_METER_ONLINE_THRESHOLD_MINUTES=4)
    def test_status_is_offline_outside_shared_threshold(self):
        self.assertFalse(self.status()["online"])

    @override_settings(SMART_METER_ONLINE_THRESHOLD_MINUTES=6)
    def test_status_is_online_inside_shared_threshold(self):
        self.assertTrue(self.status()["online"])

    @override_settings(SMART_METER_ONLINE_THRESHOLD_MINUTES=6)
    def test_query_string_cannot_override_shared_threshold(self):
        status = self.status("?minutes=1")

        self.assertTrue(status["online"])
        self.assertEqual(status["minutes_window"], 6)

    def test_json_status_contains_connectivity_fields(self):
        payload = self.status()
        for field in (
            "connection_state",
            "is_connected",
            "measurement_is_fresh",
            "last_contact_at",
            "last_measurement_at",
        ):
            self.assertIn(field, payload)


class MeterPresenceResolverTests(SimpleTestCase):
    def setUp(self):
        self.meter = type("MeterStub", (), {"meter_number": "STATUS-1"})()

    def reading_at(self, value):
        return type("ReadingStub", (), {"ts": value})()

    def test_fresh_measurement_and_active_connection_is_online(self):
        now = timezone.now()
        status = resolve_meter_online_status(
            self.meter,
            self.reading_at(now),
            MeterPresence(available=True, connected=True, last_contact_at=now),
        )
        self.assertEqual(status["connection_state"], "online")
        self.assertTrue(status["is_online"])
        self.assertTrue(status["measurement_is_fresh"])

    def test_active_connection_with_old_measurement_is_stale(self):
        now = timezone.now()
        status = resolve_meter_online_status(
            self.meter,
            self.reading_at(now - timezone.timedelta(minutes=11)),
            MeterPresence(available=True, connected=True, last_contact_at=now),
        )
        self.assertEqual(status["connection_state"], "stale")
        self.assertTrue(status["is_online"])
        self.assertFalse(status["measurement_is_fresh"])

    def test_missing_presence_is_offline(self):
        status = resolve_meter_online_status(
            self.meter,
            self.reading_at(timezone.now()),
            MeterPresence(available=True),
        )
        self.assertEqual(status["connection_state"], "offline")
        self.assertFalse(status["is_connected"])

    def test_expired_presence_is_offline(self):
        status = resolve_meter_online_status(
            self.meter,
            self.reading_at(timezone.now()),
            MeterPresence(
                available=True,
                connected=True,
                last_contact_at=timezone.now() - timezone.timedelta(minutes=21),
            ),
        )
        self.assertEqual(status["connection_state"], "offline")
        self.assertFalse(status["is_connected"])

    def test_redis_failure_uses_live_reading_compatibility_fallback(self):
        status = resolve_meter_online_status(
            self.meter,
            self.reading_at(timezone.now()),
            MeterPresence(available=False),
        )
        self.assertEqual(status["connection_state"], "online")
        self.assertTrue(status["is_online"])


class SharedThresholdWiringTests(SimpleTestCase):
    def test_default_threshold_is_ten_minutes(self):
        self.assertEqual(settings.SMART_METER_ONLINE_THRESHOLD_MINUTES, 10)
        self.assertEqual(online_threshold_minutes(), 10)

    def test_smart_meter_views_use_shared_threshold(self):
        for view in (
            meter_views.meter_status,
            meter_views.meter_list,
            meter_views.live_custom,
            meter_views.live_custom_data,
            meter_dashboard_views.energy_dashboard,
        ):
            self.assertIn("online_threshold_minutes", inspect.getsource(view))

    def test_main_dashboard_uses_shared_threshold(self):
        source = inspect.getsource(dashboard_views.dashboard)
        self.assertIn("meter_online_minutes = online_threshold_minutes()", source)
        self.assertIn("resolve_meter_online_statuses", source)
        self.assertNotIn("METER_ONLINE_MINUTES", source)

    def test_core_dashboard_uses_shared_threshold(self):
        source = inspect.getsource(core_views.dashboard)
        self.assertIn("meter_online_minutes = online_threshold_minutes()", source)
        self.assertIn("resolve_meter_online_statuses", source)
        self.assertNotIn("METER_ONLINE_MINUTES", source)
