import inspect
import json

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core import views as core_views
from dashboard import views as dashboard_views
from smart_meter import views as meter_views
from smart_meter import views_dashboard as meter_dashboard_views
from smart_meter.models import LiveReading, Meter
from smart_meter.status import online_threshold_minutes


class MeterStatusThresholdTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.meter = Meter.objects.create(meter_number="THRESHOLD-TEST")
        self.reading = LiveReading.objects.create(meter=self.meter)
        LiveReading.objects.filter(pk=self.reading.pk).update(
            ts=timezone.now() - timezone.timedelta(minutes=5)
        )

    def status(self, query=""):
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
        self.assertNotIn("METER_ONLINE_MINUTES", source)

    def test_core_dashboard_uses_shared_threshold(self):
        source = inspect.getsource(core_views.dashboard)
        self.assertIn("meter_online_minutes = online_threshold_minutes()", source)
        self.assertNotIn("METER_ONLINE_MINUTES", source)
