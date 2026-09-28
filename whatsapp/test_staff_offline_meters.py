from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from properties.models import Property, Unit
from smart_meter.models import LiveReading, Meter
from whatsapp.services.whatsapp_ai import WhatsAppAIAssistant


class StaffOfflineMeterReplyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="offline-admin",
            email="offline@example.com",
            password="test-pass",
        )
        self.property = Property.objects.create(
            property_name="H9",
            owner_name="Owner",
            owner_cnic="1111111111111",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="Flat 1",
        )
        self.meter = Meter.objects.create(
            meter_number="H9-OFFLINE-1",
            unit=self.unit,
            meter_role=Meter.METER_ROLE_BILLING,
        )
        live = LiveReading.objects.create(
            meter=self.meter,
            total_energy=Decimal("321.456"),
        )
        LiveReading.objects.filter(pk=live.pk).update(
            ts=timezone.now() - timedelta(hours=2)
        )

    @patch("whatsapp.services.whatsapp_ai.log_staff_action")
    @patch("smart_meter.status.get_meter_presences", return_value={})
    def test_staff_reply_lists_offline_meter_with_last_reading(
        self, _presence_mock, _log_mock
    ):
        assistant = WhatsAppAIAssistant.__new__(WhatsAppAIAssistant)
        reply = assistant._staff_offline_meters(
            SimpleNamespace(phone_number="+923001234567"),
            self.user,
        )

        self.assertIn("Offline Meters (1)", reply)
        self.assertIn("H9 / Flat 1", reply)
        self.assertIn("H9-OFFLINE-1", reply)
        self.assertIn("321.456 kWh", reply)
