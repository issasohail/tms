import io
import json
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from redis.exceptions import RedisError

from properties.models import Property, Unit
from smart_meter import views
from smart_meter.models import LiveReading, Meter, MeterConnectionEvent, MeterRawFrame
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


class ListenerStartupPresenceResetTests(TestCase):
    class RecordingPipeline:
        def __init__(self):
            self.updates = []

        def hset(self, key, field, value):
            self.updates.append((key, field, value))
            return self

        def execute(self):
            return [1] * len(self.updates)

    class RecordingClient:
        def __init__(self):
            self.pipe = ListenerStartupPresenceResetTests.RecordingPipeline()

        def scan_iter(self, match):
            assert match == "smart_meter:presence:*"
            return iter(
                (
                    "smart_meter:presence:241203510003",
                    "smart_meter:presence:260305510012",
                )
            )

        def pipeline(self, transaction=False):
            assert transaction is False
            return self.pipe

    def test_listener_startup_marks_cached_connections_offline(self):
        meter_presence._retry_after_monotonic = 0.0
        client = self.RecordingClient()

        with patch.object(meter_presence, "_get_redis_client", return_value=client):
            reset = meter_presence.clear_all_meter_connections()

        self.assertTrue(reset)
        self.assertEqual(
            client.pipe.updates,
            [
                ("smart_meter:presence:241203510003", "connected", "0"),
                ("smart_meter:presence:260305510012", "connected", "0"),
            ],
        )


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

class RedisPresenceConfigurationTests(TestCase):
    @override_settings(
        SMART_METER_REDIS_URL="redis://127.0.0.1:6379/2",
        SMART_METER_REDIS_CONNECT_TIMEOUT=0.5,
        SMART_METER_REDIS_SOCKET_TIMEOUT=0.5,
    )
    def test_presence_client_uses_dedicated_url_and_configured_timeouts(self):
        meter_presence._get_redis_client.cache_clear()
        try:
            client = meter_presence._get_redis_client()
            kwargs = client.connection_pool.connection_kwargs
            self.assertEqual(kwargs["host"], "127.0.0.1")
            self.assertEqual(kwargs["port"], 6379)
            self.assertEqual(kwargs["db"], 2)
            self.assertEqual(kwargs["socket_connect_timeout"], 0.5)
            self.assertEqual(kwargs["socket_timeout"], 0.5)
        finally:
            meter_presence._get_redis_client.cache_clear()

    def test_socket_seen_at_is_parsed_separately_from_meter_contact(self):
        presence = meter_presence._as_presence({
            "connected": "1",
            "last_contact_at": "1700000000.0",
            "socket_seen_at": "1700000030.0",
        })
        self.assertTrue(presence.connected)
        self.assertNotEqual(presence.last_contact_at, presence.socket_seen_at)


class MeterConnectionEventTests(TestCase):
    def test_connection_event_can_capture_reconnect_diagnostics(self):
        event = MeterConnectionEvent.objects.create(
            meter_number="260305510019",
            event_type=MeterConnectionEvent.EVENT_RECONNECTED,
            source_ip="203.99.190.49",
            source_port=16906,
            previous_source_ip="203.99.190.49",
            previous_source_port=60238,
            connection_age_seconds="222.500",
        )
        self.assertEqual(event.event_type, "reconnected")
        self.assertEqual(event.previous_source_port, 60238)
