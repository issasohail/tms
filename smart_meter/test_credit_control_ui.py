from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from leases.models import Lease
from properties.models import Property, Unit
from smart_meter.models import LiveReading, Meter, MeterCreditAccount, MeterInstallation
from tenants.models import Tenant


class CreditControlUITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="credit-ui-admin", password="pass", email="credit-ui@example.com"
        )
        prop = Property.objects.create(property_name="Credit UI", owner_name="Owner", owner_cnic="1234512345673", type="apartment", property_type="apartment", total_units=1)
        unit = Unit.objects.create(property=prop, unit_number="UI-1")
        tenant = Tenant.objects.create(first_name="UI", last_name="Tenant", cnic="1234512345674")
        self.lease = Lease.objects.create(tenant=tenant, unit=unit, start_date=date(2026, 1, 1), end_date=date(2027, 1, 1), monthly_rent=Decimal("25000.00"), security_deposit=Decimal("100000.00"), electricity_security_deposit=Decimal("20000.00"), status="active")
        self.meter = Meter.objects.create(meter_number="250619519997", unit=unit, billing_mode="credit_controlled", unit_rate=Decimal("50.00"))
        self.installation = MeterInstallation.objects.create(meter=self.meter, unit=unit, lease=self.lease, start_date=date(2026, 1, 1), start_reading=Decimal("100.000"))
        LiveReading.objects.create(meter=self.meter, total_energy=Decimal("100.000"))
        self.url = reverse("smart_meter:credit_control", args=[self.meter.pk])

    def _settings_payload(self, **updates):
        data = {
            "action": "save",
            "credit_limit_source": "deposit_percent",
            "fixed_credit_limit": "25000.00",
            "deposit_percentage": "100.00",
            "lease_override_limit": "",
            "warning_threshold_percent": "75.00",
            "final_warning_threshold_percent": "90.00",
            "cutoff_threshold_percent": "100.00",
            "reconnect_threshold_percent": "80.00",
            "manual_only_cutoff": "on",
            "staff_approval_required": "on",
        }
        data.update(updates)
        return data

    def test_login_required_and_page_loads_for_authorized_staff(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Electricity Security Deposit")
        self.assertContains(response, "Help for Credit limit source")
        self.assertContains(response, "select Lease-specific manual override from this dropdown")
        self.assertContains(response, "Server-wide safety gate for automatic OFF commands")

    def test_meter_detail_shows_credit_control_tab_even_in_postpaid_mode(self):
        self.meter.billing_mode = "postpaid"
        self.meter.save(update_fields=["billing_mode"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("smart_meter:meter_detail", args=[self.meter.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Credit Control")
        self.assertContains(response, self.url)

    def test_lease_detail_shows_electricity_security_deposit(self):
        MeterCreditAccount.objects.create(
            meter=self.meter,
            installation=self.installation,
            lease=self.lease,
            is_enabled=True,
            credit_limit_source="fixed",
            fixed_credit_limit=Decimal("20000.00"),
            deposit_reference_amount=Decimal("20000.00"),
            effective_credit_limit=Decimal("20000.00"),
            current_exposure=Decimal("5000.00"),
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("leases:lease_detail", args=[self.lease.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Electricity Security")
        self.assertContains(response, "Smart Meter Credit Control")
        self.assertContains(response, "View / Manage")
        self.assertContains(response, reverse("smart_meter:credit_control", args=[self.meter.pk]))

    def test_valid_settings_save_uses_electricity_security(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, self._settings_payload())
        self.assertEqual(response.status_code, 302)
        account = MeterCreditAccount.objects.get(meter=self.meter)
        self.assertEqual(account.deposit_reference_amount, Decimal("20000.00"))
        self.assertEqual(account.effective_credit_limit, Decimal("20000.00"))
        self.assertFalse(account.automatic_cutoff)

    def test_combined_automatic_option_sets_cutoff_and_restore_together(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            self._settings_payload(automatic_cutoff_and_restore="on"),
        )
        self.assertEqual(response.status_code, 302)
        account = MeterCreditAccount.objects.get(meter=self.meter)
        self.assertTrue(account.automatic_cutoff)
        self.assertTrue(account.automatic_restore)
        self.assertFalse(account.manual_only_cutoff)

    def test_invalid_threshold_order_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            self._settings_payload(warning_threshold_percent="95.00", final_warning_threshold_percent="90.00"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Warning must not exceed final warning")
        self.assertFalse(MeterCreditAccount.objects.exists())

    @patch("smart_meter.views_credit_control.activate_credit_account")
    def test_activation_action_calls_service(self, activate):
        self.client.force_login(self.user)
        self.client.post(self.url, self._settings_payload())
        account = MeterCreditAccount.objects.get(meter=self.meter)
        activate.return_value = account
        response = self.client.post(self.url, {"action": "activate", "reason": "UI test"})
        self.assertEqual(response.status_code, 302)
        activate.assert_called_once()
        account.refresh_from_db()
        self.assertFalse(account.is_enabled)

    @patch("smart_meter.views_credit_control.evaluate_credit_account")
    def test_evaluate_now_calls_existing_service(self, evaluate):
        from smart_meter.services.credit_control import EvaluationResult

        self.client.force_login(self.user)
        self.client.post(self.url, self._settings_payload())
        account = MeterCreditAccount.objects.get(meter=self.meter)
        account.is_enabled = True
        account.save()
        evaluate.return_value = EvaluationResult(account.pk, True, "normal", Decimal("100.00"), Decimal("0.50"))
        response = self.client.post(self.url, {"action": "evaluate"})
        self.assertEqual(response.status_code, 302)
        evaluate.assert_called_once_with(account.pk, source="manual")
