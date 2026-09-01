from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from smart_meter.models import Meter, MeterRawFrame


class MeterRawFrameHistoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="raw-frame-admin", password="pass", email="raw@example.com"
        )
        self.meter = Meter.objects.create(meter_number="260305510012")
        self.url = reverse("smart_meter:meter_raw_frame_history", args=[self.meter.pk])

    def test_login_is_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_history_displays_decoded_data_and_raw_frame_newest_first(self):
        MeterRawFrame.objects.create(
            meter=self.meter, received_at=timezone.now() - timedelta(minutes=1),
            source_ip="119.156.230.185", source_port=61330, control_code=0x91,
            data_identifier="028011FF", data_length=69, raw_frame_hex="OLDERFRAME",
            checksum_style="incl_1st68", decoded_data={"voltage_c": "233.1"},
            trust_classification=MeterRawFrame.TRUST_REPORTED_UNVERIFIED,
        )
        MeterRawFrame.objects.create(
            meter=self.meter, received_at=timezone.now(),
            source_ip="119.156.230.185", source_port=61331, control_code=0x91,
            data_identifier="028011FF", data_length=69, raw_frame_hex="NEWERFRAME",
            checksum_style="incl_1st68", decoded_data={"total_power": "0.7800"},
            trust_classification=MeterRawFrame.TRUST_REPORTED_UNVERIFIED,
        )

        self.client.force_login(self.user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Raw Readings: 260305510012")
        self.assertContains(response, "total_power: 0.7800")
        self.assertContains(response, "NEWERFRAME")
        self.assertLess(response.content.find(b"NEWERFRAME"), response.content.find(b"OLDERFRAME"))
