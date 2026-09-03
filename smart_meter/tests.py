import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from leases.models import Lease, LeaseUnitOccupancy
from invoices.models import InvoiceItem, ItemCategory
from properties.models import Property, Unit
from smart_meter.models import LiveReading, Meter, MeterInstallation, MeterReading, MeterRoleHistory
from smart_meter.services.invoicing import (
    ElectricBillContext,
    compute_electric_bill,
    upsert_invoice_with_electric_item,
)
from tenants.models import Tenant


class MeterFormUnitOrderingTests(TestCase):
    def setUp(self):
        alpha = Property.objects.create(
            property_name="Alpha Property",
            owner_name="Owner Alpha",
            owner_cnic="1234511111111",
            type="apartment",
            property_type="apartment",
            total_units=2,
        )
        beta = Property.objects.create(
            property_name="Beta Property",
            owner_name="Owner Beta",
            owner_cnic="1234522222222",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.alpha_1 = Unit.objects.create(property=alpha, unit_number="1")
        self.alpha_2 = Unit.objects.create(property=alpha, unit_number="2")
        self.beta_1 = Unit.objects.create(property=beta, unit_number="1")

    def test_add_and_convert_forms_order_units_by_property_then_unit(self):
        from smart_meter.forms import MeterForm, UnknownToMeterForm

        expected_ids = [self.alpha_1.pk, self.alpha_2.pk, self.beta_1.pk]
        for form_class in (MeterForm, UnknownToMeterForm):
            field = form_class().fields["unit"]
            self.assertEqual(list(field.queryset.values_list("pk", flat=True)), expected_ids)
            self.assertEqual(
                field.label_from_instance(self.alpha_1),
                "Alpha Property / Unit 1",
            )


class MeterRoleUpdateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="meter-admin", password="test-pass")
        self.user.user_permissions.add(
            Permission.objects.get(codename="change_meter"),
            Permission.objects.get(codename="view_meterreading"),
            Permission.objects.get(
                content_type__app_label="accounts",
                codename="access_all_properties",
            ),
        )
        property_obj = Property.objects.create(
            property_name="Role Property",
            owner_name="Owner",
            owner_cnic="1234599999999",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="Role Unit")
        self.meter = Meter.objects.create(meter_number="ROLE-UPDATE-1", unit=unit)
        MeterRoleHistory.objects.create(
            meter=self.meter,
            role=Meter.METER_ROLE_BILLING,
            start_date=date(2026, 1, 1),
        )
        self.client.force_login(self.user)

    def test_inline_role_update_returns_saved_role_and_label(self):
        response = self.client.post(
            reverse("smart_meter:meter_role_update", args=[self.meter.pk]),
            {"meter_role": Meter.METER_ROLE_CHECK},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], Meter.METER_ROLE_CHECK)
        self.assertEqual(response.json()["label"], "Audit")
        self.meter.refresh_from_db()
        self.assertEqual(self.meter.meter_role, Meter.METER_ROLE_CHECK)
        self.assertEqual(self.meter.role_history.filter(is_active=True, end_date__isnull=True).count(), 1)

    def test_reading_role_is_plain_text_until_clicked_and_dates_are_preserved(self):
        MeterReading.objects.create(
            meter=self.meter,
            ts=timezone.make_aware(datetime(2026, 8, 25, 12, 0)),
            total_energy=Decimal("100"),
        )

        response = self.client.get(
            reverse("smart_meter:reading_list"),
            {
                "meter": self.meter.pk,
                "start": "2026-08-24",
                "end": "2026-08-26",
                "role": "billing",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-role-display')
        self.assertContains(response, 'ld-role-select d-none')
        self.assertContains(response, 'value="2026-08-24"')
        self.assertContains(response, 'value="2026-08-26"')
        self.assertContains(response, 'if (rangeSel?.value) applyRange(rangeSel.value);')


class EnergyDashboardMeterRoleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="energy-role-user", password="test-pass"
        )
        self.user.user_permissions.add(Permission.objects.get(codename="view_meter"))
        self.client.force_login(self.user)
        property_obj = Property.objects.create(
            property_name="H9 Building",
            owner_name="Owner",
            owner_cnic="1234512345678",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="Invert 1")
        self.billing_meter = Meter.objects.create(
            meter_number="DASH-BILL-1", unit=unit, meter_role=Meter.METER_ROLE_BILLING
        )
        self.check_meter = Meter.objects.create(
            meter_number="DASH-CHECK-1", unit=unit, meter_role=Meter.METER_ROLE_CHECK
        )
        now = timezone.now()
        for meter in (self.billing_meter, self.check_meter):
            MeterReading.objects.create(
                meter=meter, ts=now - timedelta(minutes=30), total_energy=Decimal("100.000")
            )
            MeterReading.objects.create(
                meter=meter, ts=now, total_energy=Decimal("110.000")
            )

    def _dashboard(self, role=None):
        params = {
            "start": timezone.localdate().isoformat(),
            "end": timezone.localdate().isoformat(),
            "report_type": "daily",
        }
        if role:
            params["role"] = role
        return self.client.get(reverse("smart_meter:energy_dashboard"), params)

    def test_default_dashboard_shows_only_billing_role(self):
        response = self._dashboard()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_role"], Meter.METER_ROLE_BILLING)
        self.assertEqual({row["meter_role"] for row in response.context["rows"]}, {Meter.METER_ROLE_BILLING})

    def test_check_filter_uses_saved_meter_role(self):
        response = self._dashboard(Meter.METER_ROLE_CHECK)

        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["meter_number"] for row in response.context["rows"]}, {self.check_meter.meter_number})
        self.assertEqual({dataset["meterRole"] for dataset in response.context["datasets"]}, {Meter.METER_ROLE_CHECK})

    def test_all_roles_are_grouped_billing_then_check(self):
        response = self._dashboard("all")

        roles = [dataset["meterRole"] for dataset in response.context["datasets"]]
        self.assertEqual(roles, [Meter.METER_ROLE_BILLING, Meter.METER_ROLE_CHECK])

    def test_monthly_total_rounds_up_to_nearest_10(self):
        self.billing_meter.unit_rate = Decimal("47.11")
        self.billing_meter.service_charges = Decimal("250.00")
        self.billing_meter.save(update_fields=["unit_rate", "service_charges"])
        selected_day = timezone.localdate()

        response = self.client.get(
            reverse("smart_meter:energy_dashboard"),
            {
                "start": selected_day.replace(day=1).isoformat(),
                "end": selected_day.replace(
                    day=calendar.monthrange(selected_day.year, selected_day.month)[1]
                ).isoformat(),
                "report_type": "monthly",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rows"][0]["total_amount"], Decimal("730.00"))
        self.assertEqual(response.context["totals"]["grand_total"], Decimal("730.00"))


class EnergyDashboardBoundaryQueryTests(TestCase):
    def setUp(self):
        property_obj = Property.objects.create(
            property_name="Boundary Test",
            owner_name="Owner",
            owner_cnic="1234512345670",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="1")
        self.meter = Meter.objects.create(
            meter_number="BOUNDARY-1",
            unit=unit,
            meter_role=Meter.METER_ROLE_BILLING,
        )
        tz = timezone.get_current_timezone()
        self.start_dt = timezone.make_aware(
            datetime(2026, 8, 1, 0, 0, 0), tz
        )
        self.end_dt = timezone.make_aware(
            datetime(2026, 8, 3, 0, 0, 0), tz
        )
        points = [
            (datetime(2026, 8, 1, 0, 15), "100.000"),
            (datetime(2026, 8, 1, 12, 0), "105.000"),
            (datetime(2026, 8, 1, 23, 45), "110.000"),
            (datetime(2026, 8, 2, 0, 15), "120.000"),
            (datetime(2026, 8, 2, 23, 45), "130.000"),
        ]
        for ts, value in points:
            MeterReading.objects.create(
                meter=self.meter,
                ts=timezone.make_aware(ts, tz),
                total_energy=Decimal(value),
            )

    def _boundary_values(self, granularity):
        from smart_meter.views_dashboard import _boundary_readings_by_meter

        with CaptureQueriesContext(connection) as captured:
            grouped = _boundary_readings_by_meter(
                [self.meter.pk],
                self.start_dt,
                self.end_dt,
                timezone.get_current_timezone(),
                granularity,
            )
        return [
            row["total_energy"] for row in grouped[self.meter.pk]
        ], len(captured)

    def test_daily_fetches_only_first_and_last_reading_per_day(self):
        values, query_count = self._boundary_values("daily")

        self.assertEqual(
            values,
            [
                Decimal("100.000"),
                Decimal("110.000"),
                Decimal("120.000"),
                Decimal("130.000"),
            ],
        )
        self.assertEqual(query_count, 2)

    def test_monthly_fetches_only_first_and_last_reading_for_month(self):
        values, query_count = self._boundary_values("monthly")

        self.assertEqual(values, [Decimal("100.000"), Decimal("130.000")])
        self.assertEqual(query_count, 2)


class ElectricBillDescriptionTests(TestCase):
    def test_long_description_keeps_final_total_within_invoice_item_limit(self):
        ctx = ElectricBillContext(
            lease=None,
            meter=SimpleNamespace(meter_number="250619510016-LONG-METER-REFERENCE"),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            beg_kwh=Decimal("123456789.123"),
            end_kwh=Decimal("987654321.987"),
            units=Decimal("864197532.864"),
            unit_rate=Decimal("12345.67"),
            service_charges=Decimal("987654.32"),
        )

        description = ctx.description_text

        self.assertIn(f"total={ctx.line_total}.", description)
        self.assertLessEqual(len(description), 490)


class SmartMeterInvoiceGenerationRegressionTests(TestCase):
    period_start = date(2026, 8, 1)
    period_end = date(2026, 8, 31)

    def setUp(self):
        property_obj = Property.objects.create(
            property_name="Invoice Reading Test",
            owner_name="Owner",
            owner_cnic="1234512345699",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=property_obj, unit_number="1")
        tenant = Tenant.objects.create(
            first_name="Invoice",
            last_name="Tenant",
            cnic="1234512345698",
        )
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=self.unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=Decimal("25000.00"),
        )
        LeaseUnitOccupancy.objects.create(
            lease=self.lease,
            unit=self.unit,
            move_in_date=date(2026, 1, 1),
        )
        self.meter = Meter.objects.create(
            meter_number="INVOICE-BOUNDARY-1",
            unit=self.unit,
            meter_role=Meter.METER_ROLE_BILLING,
            billing_mode="postpaid",
            unit_rate=Decimal("50.0000"),
            service_charges=Decimal("250.00"),
        )
        MeterInstallation.objects.create(
            meter=self.meter,
            unit=self.unit,
            lease=self.lease,
            start_date=date(2026, 1, 1),
            start_reading=Decimal("0.000"),
        )
        self.category = ItemCategory.objects.create(pk=7, name="Electricity")
        self.tz = timezone.get_current_timezone()

    def add_reading(self, ts, *, total=None, forward=None):
        return MeterReading.objects.create(
            meter=self.meter,
            ts=timezone.make_aware(ts, self.tz),
            total_energy=Decimal(total) if total is not None else None,
            forward_active_energy_kwh=Decimal(forward) if forward is not None else None,
        )

    def compute(self):
        return compute_electric_bill(
            self.lease,
            self.meter,
            self.period_start,
            self.period_end,
        )

    def test_final_timestamp_not_mid_month_max_drives_dashboard_and_invoice(self):
        self.add_reading(datetime(2026, 7, 31, 23, 45), total="359.080")
        self.add_reading(datetime(2026, 8, 15, 12, 0), total="361.880")
        self.add_reading(datetime(2026, 8, 31, 23, 45), total="361.200")

        ctx = self.compute()
        from smart_meter.views_dashboard import _per_meter_series

        _labels, _datasets, dashboard_rows, _totals = _per_meter_series(
            Meter.objects.filter(pk=self.meter.pk),
            self.period_start,
            self.period_end,
            "monthly",
        )

        self.assertEqual(ctx.beg_kwh, Decimal("359.080"))
        self.assertEqual(ctx.end_kwh, Decimal("361.200"))
        self.assertEqual(ctx.units, Decimal("2.120"))
        self.assertEqual(ctx.usage_amount, Decimal("106.00"))
        self.assertEqual(ctx.raw_total, Decimal("356.00"))
        self.assertEqual(ctx.invoice_amount, Decimal("360.00"))
        self.assertEqual(dashboard_rows[0]["start_kwh"], ctx.beg_kwh)
        self.assertEqual(dashboard_rows[0]["end_kwh"], ctx.end_kwh)
        self.assertEqual(dashboard_rows[0]["usage"], ctx.units)
        self.assertEqual(dashboard_rows[0]["total_amount"], ctx.invoice_amount)

        invoice = upsert_invoice_with_electric_item(ctx)
        item = invoice.items.get(category=self.category)
        self.assertEqual(item.amount, Decimal("360.00"))
        self.assertIn("Beg Unit=359.080", item.description)
        self.assertIn("end unit=361.200", item.description)
        self.assertIn("unit consume=2.120", item.description)
        self.assertIn("total usage=106.00", item.description)
        self.assertIn("usage charge=106.00", item.description)
        self.assertIn("service charges=250.00", item.description)
        self.assertIn("raw subtotal=356.00", item.description)
        self.assertIn("rounded invoice total=360.00", item.description)

    def test_forward_active_energy_is_preferred_with_total_energy_fallback(self):
        self.add_reading(
            datetime(2026, 7, 31, 23, 45),
            total="900.000",
            forward="100.000",
        )
        self.add_reading(
            datetime(2026, 8, 31, 23, 45),
            total="110.000",
            forward=None,
        )

        ctx = self.compute()

        self.assertEqual(ctx.beg_kwh, Decimal("100.000"))
        self.assertEqual(ctx.end_kwh, Decimal("110.000"))
        self.assertEqual(ctx.units, Decimal("10.000"))

    def test_regeneration_updates_existing_electricity_item_without_duplicate(self):
        self.add_reading(datetime(2026, 7, 31, 23, 45), total="100.000")
        self.add_reading(datetime(2026, 8, 20, 12, 0), total="105.000")
        first_invoice = upsert_invoice_with_electric_item(self.compute())
        original_item = first_invoice.items.get(category=self.category)

        self.add_reading(datetime(2026, 8, 31, 23, 45), total="106.000")
        second_invoice = upsert_invoice_with_electric_item(self.compute())

        items = InvoiceItem.objects.filter(
            invoice=second_invoice,
            category=self.category,
            description__icontains=f"Meter#={self.meter.meter_number}",
        )
        self.assertEqual(second_invoice.pk, first_invoice.pk)
        self.assertEqual(items.count(), 1)
        self.assertEqual(items.get().pk, original_item.pk)
        self.assertIn("end unit=106.000", items.get().description)
        self.assertEqual(items.get().amount, Decimal("550.00"))

    def test_monotonically_increasing_readings_calculate_normally(self):
        self.add_reading(datetime(2026, 7, 31, 23, 45), total="200.000")
        self.add_reading(datetime(2026, 8, 10, 12, 0), total="204.000")
        self.add_reading(datetime(2026, 8, 31, 23, 45), total="210.000")

        ctx = self.compute()

        self.assertEqual(ctx.beg_kwh, Decimal("200.000"))
        self.assertEqual(ctx.end_kwh, Decimal("210.000"))
        self.assertEqual(ctx.units, Decimal("10.000"))
        self.assertEqual(ctx.usage_amount, Decimal("500.00"))
        self.assertEqual(ctx.raw_total, Decimal("750.00"))
        self.assertEqual(ctx.invoice_amount, Decimal("750.00"))


class HistoricalMeterOccupancyTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(
            property_name="Test Property",
            owner_name="Owner",
            owner_cnic="1234512345671",
            type="apartment",
            property_type="apartment",
            total_units=2,
        )
        self.unit_101 = Unit.objects.create(property=self.property, unit_number="101")
        self.unit_202 = Unit.objects.create(property=self.property, unit_number="202")

    def test_unit_allows_multiple_active_meter_installations(self):
        meter_a = Meter.objects.create(meter_number="MTR-1001")
        meter_b = Meter.objects.create(meter_number="MTR-1002")

        MeterInstallation.objects.create(
            meter=meter_a,
            unit=self.unit_101,
            start_date=date(2026, 5, 1),
            start_reading=Decimal("1000.000"),
        )
        MeterInstallation.objects.create(
            meter=meter_b,
            unit=self.unit_101,
            start_date=date(2026, 5, 1),
            start_reading=Decimal("20.000"),
        )

        active_count = self.unit_101.meter_installations.filter(is_active=True).count()
        self.assertEqual(active_count, 2)

    def test_meter_cannot_have_two_active_installations(self):
        meter = Meter.objects.create(meter_number="MTR-2001")
        MeterInstallation.objects.create(
            meter=meter,
            unit=self.unit_101,
            start_date=date(2026, 5, 1),
        )

        second_installation = MeterInstallation(
            meter=meter,
            unit=self.unit_202,
            start_date=date(2026, 5, 10),
        )

        with self.assertRaises(ValidationError):
            second_installation.full_clean()

    def test_lease_allows_only_one_active_occupancy(self):
        tenant = Tenant.objects.create(
            first_name="Test",
            last_name="Tenant",
            cnic="1234512345671",
        )
        lease = Lease.objects.create(
            tenant=tenant,
            unit=self.unit_101,
            start_date=date(2026, 5, 1),
            end_date=date(2027, 4, 30),
            monthly_rent=Decimal("25000.00"),
        )
        LeaseUnitOccupancy.objects.create(
            lease=lease,
            unit=self.unit_101,
            move_in_date=date(2026, 5, 1),
        )

        second_occupancy = LeaseUnitOccupancy(
            lease=lease,
            unit=self.unit_202,
            move_in_date=date(2026, 5, 13),
        )

        with self.assertRaises(ValidationError):
            second_occupancy.full_clean()


class MeterEditInstallationSyncTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="meter-move-admin",
            password="test-pass",
            email="meter-move@example.com",
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Meter Move Property",
            owner_name="Owner",
            owner_cnic="1234512345672",
            type="apartment",
            property_type="apartment",
            total_units=2,
        )
        self.old_unit = Unit.objects.create(
            property=self.property,
            unit_number="Old Unit",
        )
        self.new_unit = Unit.objects.create(
            property=self.property,
            unit_number="New Unit",
        )
        old_tenant = Tenant.objects.create(
            first_name="Old",
            last_name="Tenant",
            cnic="1234512345672",
        )
        new_tenant = Tenant.objects.create(
            first_name="New",
            last_name="Tenant",
            cnic="1234512345673",
        )
        self.old_lease = Lease.objects.create(
            tenant=old_tenant,
            unit=self.old_unit,
            start_date=date(2025, 1, 1),
            end_date=date(2027, 12, 31),
            monthly_rent=Decimal("25000.00"),
        )
        self.new_lease = Lease.objects.create(
            tenant=new_tenant,
            unit=self.new_unit,
            start_date=date(2025, 1, 1),
            end_date=date(2027, 12, 31),
            monthly_rent=Decimal("25000.00"),
        )
        self.meter = Meter.objects.create(
            meter_number="MOVE-METER-1",
            unit=self.old_unit,
        )
        self.old_installation = MeterInstallation.objects.create(
            meter=self.meter,
            unit=self.old_unit,
            lease=self.old_lease,
            start_date=date(2025, 1, 1),
            start_reading=Decimal("100.000"),
        )
        MeterReading.objects.create(
            meter=self.meter,
            total_energy=Decimal("150.000"),
        )

    def test_editing_meter_unit_closes_old_installation_and_opens_new_one(self):
        response = self.client.post(
            reverse("smart_meter:meter_edit", args=[self.meter.pk]),
            {
                "unit": self.new_unit.pk,
                "meter_number": self.meter.meter_number,
                "name": "",
                "meter_type": Meter.METER_TYPE_ELECTRIC,
                "billing_mode": "postpaid",
                "meter_role": Meter.METER_ROLE_BILLING,
                "power_status": "on",
                "unit_rate": "50.00",
                "service_charges": "250.00",
                "min_balance_alert": "100.00",
                "min_balance_cutoff": "0.00",
                "is_active": "on",
                "installed_at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
                "notes": "Moved for test",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.old_installation.refresh_from_db()
        self.assertFalse(self.old_installation.is_active)
        self.assertIsNotNone(self.old_installation.end_date)
        new_installation = MeterInstallation.objects.get(
            meter=self.meter,
            is_active=True,
            end_date__isnull=True,
        )
        self.assertEqual(new_installation.unit, self.new_unit)
        self.assertEqual(new_installation.lease, self.new_lease)
        self.assertEqual(new_installation.start_reading, Decimal("150.000"))


class InstantLiveReadingRegressionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="instant-reading-user", password="test-pass"
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="change_meter"),
            Permission.objects.get(
                content_type__app_label="accounts",
                codename="access_all_properties",
            ),
        )
        self.client.force_login(self.user)
        self.meter = Meter.objects.create(meter_number="INSTANT-READ-1")
        self.live = LiveReading.objects.create(
            meter=self.meter,
            total_energy=Decimal("100.000"),
            voltage_a=Decimal("230.0"),
        )

    @patch("smart_meter.views.request_instant_live_reading")
    def test_instant_read_uses_listener_and_returns_fresh_persisted_reading(self, request_read):
        def listener_request(meter_number, timeout=8.0):
            self.assertEqual(meter_number, self.meter.meter_number)
            LiveReading.objects.filter(pk=self.live.pk).update(
                ts=timezone.now() + timedelta(seconds=1),
                total_energy=Decimal("101.250"),
                voltage_a=Decimal("231.0"),
                current_a=Decimal("1.250"),
                total_power=Decimal("0.289"),
            )
            return {"ok": True}

        request_read.side_effect = listener_request
        response = self.client.post(
            reverse("smart_meter:smart_meter_instant_live_reading", args=[self.meter.pk])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total_energy"], "101.250")
        self.assertEqual(payload["voltage_a"], "231.0")
        request_read.assert_called_once()

    @patch(
        "smart_meter.views.request_instant_live_reading",
        return_value={"ok": False, "error": "Meter offline"},
    )
    def test_instant_read_reports_offline_without_queuing(self, request_read):
        response = self.client.post(
            reverse("smart_meter:smart_meter_instant_live_reading", args=[self.meter.pk])
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "Meter offline")
