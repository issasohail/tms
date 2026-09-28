from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from leases.models import Lease, LeaseUnitOccupancy
from properties.models import Property, Unit
from smart_meter.models import (
    LiveReading,
    Meter,
    MeterTariffAudit,
    MeterTariffConfiguration,
)
from smart_meter.services.tariff_configuration import configure_prices
from smart_meter.services.tariff_protocol import (
    MULTI_RATE_COUNT_OFFSET,
    MULTI_SET1_PRICE_OFFSETS,
    TariffProtocolError,
    encode_price,
)
from smart_meter.views import _attach_tariff_verification
from smart_meter.views_tariff import _latest_live_tariff_audit
from tenants.models import Tenant


def flat_multi_rate_payload(rate):
    payload = bytearray([0x33] * 143)
    for offset in MULTI_SET1_PRICE_OFFSETS:
        payload[offset : offset + 4] = encode_price(rate)
    payload[MULTI_RATE_COUNT_OFFSET] = 0x34
    return bytes(payload)


class LiveVacancyRefreshRegressionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="live-vacancy-admin",
            password="test-pass",
            email="live-vacancy@example.com",
        )
        self.client.force_login(self.user)
        property_obj = Property.objects.create(
            property_name="F56 Test",
            owner_name="Owner",
            owner_cnic="6110112345601",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="ROOM 12")
        tenant = Tenant.objects.create(
            first_name="Former",
            last_name="Tenant",
            cnic="6110112345602",
        )
        today = timezone.localdate()
        lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=today - timedelta(days=30),
            end_date=today,
            status="ended",
            monthly_rent=Decimal("10000.00"),
        )
        LeaseUnitOccupancy.objects.create(
            lease=lease,
            unit=unit,
            move_in_date=lease.start_date,
        )
        self.meter = Meter.objects.create(meter_number="241203519912", unit=unit)
        LiveReading.objects.create(meter=self.meter, total_energy=Decimal("1.000"))

    def test_ended_lease_is_vacant_in_page_and_live_json(self):
        page = self.client.get(
            reverse("smart_meter:smart_meter_live_custom"), {"active": "all"}
        )
        row = next(row for row in page.context["rows"] if row.meter_id == self.meter.pk)
        self.assertEqual(row.tenant_name, "Vacant")
        self.assertIsNone(row.tenant_id)

        payload = self.client.get(
            reverse("smart_meter:smart_meter_live_custom_data"), {"active": "all"}
        ).json()
        json_row = next(row for row in payload["rows"] if row["meter_id"] == self.meter.pk)
        self.assertEqual(json_row["tenant_name"], "Vacant")
        self.assertIsNone(json_row["tenant_id"])

    def test_live_template_applies_polled_tenant_and_has_working_money_links(self):
        source = (
            Path(settings.BASE_DIR)
            / "smart_meter/templates/smart_meter/live_custom.html"
        ).read_text(encoding="utf-8")
        self.assertIn("setRowTenant(tr, r.tenant_name, r.tenant_id);", source)
        self.assertIn("?action=recharge", source)
        self.assertIn("?action=refund", source)
        self.assertIn('id="rateUpdateProgressModal"', source)
        self.assertIn('id="rateUpdateTimer"', source)
        self.assertIn("Reading back and verifying the physical meter", source)
        modal_source = (
            Path(settings.BASE_DIR)
            / "smart_meter/templates/smart_meter/partials/prepaid_money_modal.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Current tenant:", modal_source)
        self.assertIn('name="confirm_amount"', modal_source)
        self.assertNotIn('name="confirm_meter_number"', modal_source)
        self.assertIn('id="prepaidMoneyTimer"', modal_source)
        self.assertIn("pollMoneyStatus", modal_source)
        self.assertIn("function textFrom(row, selectors)", modal_source)
        self.assertIn("document.addEventListener('submit'", modal_source)
        self.assertIn("trigger.dataset.location", modal_source)
        self.assertIn("td.col-balance", modal_source)

    def test_meter_list_has_the_same_guarded_rate_editor_and_progress_timer(self):
        source = (
            Path(settings.BASE_DIR)
            / "smart_meter/templates/smart_meter/meter_list.html"
        ).read_text(encoding="utf-8")
        self.assertIn("data-meter-rate-cell", source)
        self.assertIn("smart_meter:meter_unit_rate_update", source)
        self.assertIn('id="meterListRateProgressModal"', source)
        self.assertIn('id="meterListRateTimer"', source)
        self.assertIn("submission_key: rateSubmissionKey()", source)
        self.assertIn('data-unit="{{ m.display_location_name }}"', source)
        self.assertIn("data-tenant=\"{{ m.tenant_name|default:'Vacant' }}\"", source)
        self.assertIn('data-meter-id="{{ m.id }}"', source)


class PrepaidTariffConsistencyRegressionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="prepaid-rate-admin",
            password="test-pass",
            email="prepaid-rate@example.com",
        )
        self.client.force_login(self.user)
        self.meter = Meter.objects.create(
            meter_number="241203519913",
            billing_mode="prepaid_pilot",
            tariff_capability="multi_rate",
            unit_rate=Decimal("50.0000"),
        )

    @patch("smart_meter.services.tariff_configuration.send_via_db")
    @patch("smart_meter.services.tariff_configuration._send_read")
    def test_inline_prepaid_rate_runs_configuration_before_saving(self, read, send):
        read.return_value = (flat_multi_rate_payload("40.0000"), "AA", 10)

        response = self.client.post(
            reverse("smart_meter:meter_unit_rate_update", args=[self.meter.pk]),
            {"unit_rate": "40.0000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(response.json()["tariff_status"], "no_change")
        send.assert_not_called()
        self.meter.refresh_from_db()
        self.assertEqual(self.meter.unit_rate, Decimal("40.0000"))

    @patch("smart_meter.services.tariff_configuration._send_read")
    def test_inline_prepaid_rate_keeps_old_rate_when_verification_fails(self, read):
        read.side_effect = TariffProtocolError("meter not connected")

        response = self.client.post(
            reverse("smart_meter:meter_unit_rate_update", args=[self.meter.pk]),
            {"unit_rate": "40.0000"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["success"])
        self.meter.refresh_from_db()
        self.assertEqual(self.meter.unit_rate, Decimal("50.0000"))

    @patch("smart_meter.services.tariff_configuration.send_via_db")
    @patch("smart_meter.services.tariff_configuration._send_read")
    def test_verified_flat_tariff_synchronizes_tms_rate(self, read, send):
        live_payload = flat_multi_rate_payload("40.0000")
        read.return_value = (live_payload, "AA", 10)

        audit = configure_prices(
            meter=self.meter,
            user=self.user,
            mode="flat",
            prices=["40.0000"],
            active_rate_count=1,
        )

        self.assertEqual(audit.status, "no_change")
        send.assert_not_called()
        self.meter.refresh_from_db()
        self.assertEqual(self.meter.unit_rate, Decimal("40.0000"))

    def test_stale_verified_flat_rate_is_not_prepaid_ready(self):
        MeterTariffConfiguration.objects.create(
            meter=self.meter,
            mode="flat",
            active_rate_count=1,
            rate_1_price=Decimal("50.0000"),
            last_verified_at=timezone.now(),
            last_status="verified",
        )
        self.meter.unit_rate = Decimal("40.0000")
        self.meter.save(update_fields=["unit_rate"])

        attached = _attach_tariff_verification([self.meter])[0]

        self.assertFalse(attached.tariff_is_verified)

    def test_latest_verified_readback_replaces_older_read_for_display(self):
        old = MeterTariffAudit.objects.create(
            meter=self.meter,
            capability="multi_rate",
            configuration_type="read",
            status="read",
            values_after={"active_rate_count": 1, "prices": ["50.0000"] * 4},
        )
        newest = MeterTariffAudit.objects.create(
            meter=self.meter,
            capability="multi_rate",
            configuration_type="flat",
            status="verified",
            values_after={"active_rate_count": 1, "prices": ["40.0000"] * 4},
        )

        self.assertNotEqual(old.pk, newest.pk)
        self.assertEqual(_latest_live_tariff_audit(self.meter), newest)
