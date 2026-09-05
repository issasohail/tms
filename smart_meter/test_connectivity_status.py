import io
import json
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.utils import timezone
from redis.exceptions import RedisError

from properties.models import Property, Unit
from smart_meter import views
from smart_meter.models import LiveReading, Meter, MeterRawFrame
from smart_meter.services import meter_presence
from smart_meter.services.meter_presence import MeterPresence


class LiveConnectivityEndpointTests(TestCase):
    def setUp(self):
        property_obj = Property.objects.create(
            property_name="Connectivity Test",
            owner_name="Owner",
            owner_cnic="35202-1234567-1",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(
            property=property_obj,
            unit_number="1",
            is_smart_meter=True,
        )
        self.meter = Meter.objects.create(
            unit=unit,
            meter_number="CONNECTIVITY-1",
            name="Connectivity meter",
        )
        self.reading = LiveReading.objects.create(meter=self.meter, total_energy="1.000")

    def test_live_json_returns_new_status_fields(self):
        now = timezone.now()
        request = RequestFactory().get(
            "/smart-meter/live-custom/data/?chip=total&active=all",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request.user = AnonymousUser()
        presence = MeterPresence(
            available=True,
            connected=True,
            last_contact_at=now,
            source_ip="127.0.0.1",
            source_port=6000,
        )
        with patch(
            "smart_meter.status.get_meter_presences",
            return_value={self.meter.meter_number: presence},
        ):
            response = views.live_custom_data(request)

        row = json.loads(response.content)["rows"][0]
        self.assertEqual(row["connection_state"], "online")
        self.assertTrue(row["is_connected"])
        self.assertTrue(row["measurement_is_fresh"])
        self.assertIsNotNone(row["last_contact_at"])
        self.assertIsNotNone(row["last_measurement_at"])


class RedisFailureTests(TestCase):
    class FailingPipeline:
        def hgetall(self, _key):
            return self

        def execute(self):
            raise RedisError("simulated outage")

    class FailingClient:
        def pipeline(self, transaction=False):
            return RedisFailureTests.FailingPipeline()

    def test_presence_read_failure_is_reported_as_unavailable(self):
        meter_presence._retry_after_monotonic = 0.0
        with patch.object(
            meter_presence, "_get_redis_client", return_value=self.FailingClient()
        ):
            presence = meter_presence.get_meter_presence("REDIS-FAIL")
        self.assertFalse(presence.available)


class ConnectivityReportReadOnlyTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(
            meter_number="REPORT-1",
            name="Report meter",
        )
        self.no_reading_meter = Meter.objects.create(
            meter_number="REPORT-2",
            name="No reading meter",
        )
        self.reading = LiveReading.objects.create(meter=self.meter, total_energy="10.000")
        MeterRawFrame.objects.create(
            meter=self.meter,
            source_ip="127.0.0.1",
            source_port=6000,
            control_code=0x91,
            data_identifier="00000000",
            raw_frame_hex="68",
            decoded_data={"total_energy": "10.000"},
            trust_classification=MeterRawFrame.TRUST_AUTHORITATIVE,
        )

    def test_report_does_not_modify_database_or_presence(self):
        counts_before = (
            Meter.objects.count(),
            LiveReading.objects.count(),
            MeterRawFrame.objects.count(),
        )
        output = io.StringIO()
        presence = MeterPresence(
            available=True,
            connected=True,
            last_contact_at=timezone.now(),
        )
        with patch(
            "smart_meter.status.get_meter_presences",
            return_value={self.meter.meter_number: presence},
        ):
            call_command("meter_connectivity_report", hours=48, stdout=output)

        self.assertEqual(
            counts_before,
            (
                Meter.objects.count(),
                LiveReading.objects.count(),
                MeterRawFrame.objects.count(),
            ),
        )
        self.assertIn("REPORT-1", output.getvalue())
        self.assertIn("REPORT-2", output.getvalue())
        self.assertIn("online", output.getvalue())
