from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from properties.models import Property, Unit
from smart_meter.models import (
    LiveReading,
    Meter,
    MeterCommand,
    MeterPrepaidPilot,
    MeterPrepaidRecharge,
    MeterReading,
)


class PrepaidLedgerUITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="prepaid-ledger-admin",
            password="pass",
            email="ledger@example.com",
        )
        self.client.force_login(self.user)
        property_obj = Property.objects.create(
            property_name="Ledger Property",
            owner_name="Owner",
            owner_cnic="1234512345673",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="L-1")
        self.meter = Meter.objects.create(
            meter_number="260305519991",
            unit=unit,
            billing_mode="prepaid_pilot",
            tariff_capability="single_rate",
            unit_rate=Decimal("50.0000"),
        )
        MeterPrepaidPilot.objects.create(meter=self.meter, status="active_test")
        LiveReading.objects.create(
            meter=self.meter,
            balance=Decimal("95.00"),
            total_energy=Decimal("10.100"),
            voltage_a=Decimal("230.0"),
            current_a=Decimal("1.250"),
        )
        MeterReading.objects.create(
            meter=self.meter,
            balance=Decimal("100.00"),
            total_energy=Decimal("10.000"),
            unit_rate=Decimal("50.0000"),
        )
        MeterReading.objects.create(
            meter=self.meter,
            balance=Decimal("95.00"),
            total_energy=Decimal("10.100"),
            unit_rate=Decimal("50.0000"),
        )

    def test_ledger_shows_balance_usage_rate_and_money_form(self):
        response = self.client.get(
            reverse("smart_meter:prepaid_meter_ledger", args=[self.meter.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Meter Balance Ledger")
        self.assertContains(response, "Estimated usage charge")
        self.assertContains(response, "Queue once")
        self.assertContains(response, "95.00")

    def test_prepaid_controls_show_operational_columns_and_ledger_link(self):
        response = self.client.get(reverse("smart_meter:prepaid_controls"))

        self.assertEqual(response.status_code, 200)
        for label in ("S/N", "Online", "Balance", "Unit rate", "Voltage", "Current"):
            self.assertContains(response, label)
        self.assertContains(
            response,
            reverse("smart_meter:prepaid_meter_ledger", args=[self.meter.pk]),
        )

    def test_ledger_topup_queues_one_guarded_money_command(self):
        response = self.client.post(
            reverse("smart_meter:prepaid_meter_ledger", args=[self.meter.pk]),
            {
                "operation": "recharge",
                "amount": "5.00",
                "reason": "UI ledger test",
                "confirm_meter_number": self.meter.meter_number,
            },
        )

        self.assertRedirects(
            response,
            reverse("smart_meter:prepaid_meter_ledger", args=[self.meter.pk]),
        )
        self.assertEqual(MeterPrepaidRecharge.objects.count(), 1)
        command = MeterCommand.objects.get(command_type="prepaid_recharge")
        self.assertEqual(command.max_attempts, 1)
        self.assertEqual(command.expect_di, "070102FF")

    def test_meter_and_live_lists_link_balance_to_ledger(self):
        ledger_url = reverse(
            "smart_meter:prepaid_meter_ledger", args=[self.meter.pk]
        )
        meter_response = self.client.get(reverse("smart_meter:meter_list"))
        live_response = self.client.get(reverse("smart_meter:smart_meter_live_custom"))

        self.assertContains(meter_response, ledger_url)
        self.assertContains(meter_response, "Prepaid")
        self.assertContains(live_response, ledger_url)
        self.assertContains(live_response, "Top up")
        self.assertContains(live_response, "Refund")

    def test_general_settings_redirect_to_combined_parameter_page(self):
        response = self.client.get(reverse("smart_meter:meter_settings"))
        self.assertRedirects(response, reverse("smart_meter:prepaid_params"))
        combined = self.client.get(reverse("smart_meter:prepaid_params"))
        self.assertContains(combined, "General settings")
        self.assertContains(combined, "Prepaid Parameter 1")
