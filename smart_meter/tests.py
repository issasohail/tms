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
from invoices.models import Invoice, InvoiceItem, ItemCategory
from properties.models import Property, Unit
from smart_meter.models import (
    LiveReading,
    Meter,
    MeterInstallation,
    MeterReading,
    MeterRoleHistory,
    UnknownMeter,
)
from smart_meter.services.invoicing import (
    ElectricBillContext,
    billing_contexts_for_period,
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


class ReadingListEnergyColumnTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="reading-energy-user",
            password="test-pass",
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_meterreading"),
            Permission.objects.get(
                content_type__app_label="accounts",
                codename="access_all_properties",
            ),
        )
        self.client.force_login(self.user)
        property_obj = Property.objects.create(
            property_name="Energy Column Property",
            owner_name="Owner",
            owner_cnic="1234512345688",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=property_obj, unit_number="1")

    def test_single_phase_has_plain_kwh_and_separate_pf_column(self):
        meter = Meter.objects.create(
            meter_number="ENERGY-SINGLE-1",
            unit=self.unit,
            reading_profile=Meter.READING_PROFILE_TOTAL_ONLY,
        )
        MeterReading.objects.create(
            meter=meter,
            total_energy=Decimal("123.456"),
            pf_total=Decimal("0.987"),
        )

        response = self.client.get(
            reverse("smart_meter:reading_list"),
            {"meter": meter.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<th class="col-energy">Energy (kWh)</th>', html=True)
        self.assertContains(response, '<span class="fw-bold">123.456</span>', html=True)
        self.assertNotContains(response, '<span class="phase-label">F</span>', html=True)
        self.assertContains(response, '<td class="col-pf">0.987</td>', html=True)
        self.assertNotContains(response, "col-pf-smpf")
        html = response.content.decode()
        self.assertIn("container reading-list-page", html)
        self.assertIn(".reading-list-page{ max-width:1340px; }", html)
        self.assertIn(".reading-list-page{ max-width:1520px; }", html)
        self.assertLess(
            html.index('<th class="col-meter">Meter #</th>'),
            html.index('<th class="col-energy">Energy (kWh)</th>'),
        )
        self.assertLess(
            html.index('<th class="col-energy">Energy (kWh)</th>'),
            html.index('<th class="ld-col-role">Role</th>'),
        )
        self.assertLess(
            html.index('<td class="col-meter">'),
            html.index('<td class="col-energy">'),
        )
        self.assertLess(
            html.index('<td class="col-energy">'),
            html.index('<td class="ld-col-role">'),
        )

    def test_three_phase_has_forward_reverse_net_and_separate_pf(self):
        meter = Meter.objects.create(
            meter_number="ENERGY-THREE-1",
            unit=self.unit,
            reading_profile=Meter.READING_PROFILE_TOTAL_AND_PER_PHASE,
        )
        MeterReading.objects.create(
            meter=meter,
            total_energy=Decimal("500.000"),
            forward_active_energy_kwh=Decimal("500.000"),
            reverse_active_energy_kwh=Decimal("0.500"),
            pf_total=Decimal("0.950"),
        )

        response = self.client.get(
            reverse("smart_meter:reading_list"),
            {"meter": meter.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="phase-label">F</span>', html=True)
        self.assertContains(response, '<span class="phase-label">R</span>', html=True)
        self.assertContains(response, '<span class="phase-label">Net</span>', html=True)
        self.assertContains(response, "500.000")
        self.assertContains(response, "0.500")
        self.assertNotContains(response, '<span class="phase-label">Net</span>', html=True)
        self.assertNotContains(response, "Total Power (W)")
        self.assertContains(response, '<td class="col-pf">0.950</td>', html=True)


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

    def test_monthly_rows_link_to_matching_daily_energy_views(self):
        readings = list(
            MeterReading.objects.filter(meter=self.billing_meter).order_by("ts", "id")
        )
        readings[0].forward_active_energy_kwh = Decimal("100.000")
        readings[0].reverse_active_energy_kwh = Decimal("5.000")
        readings[0].save(
            update_fields=["forward_active_energy_kwh", "reverse_active_energy_kwh"]
        )
        readings[1].forward_active_energy_kwh = Decimal("110.000")
        readings[1].reverse_active_energy_kwh = Decimal("7.000")
        readings[1].save(
            update_fields=["forward_active_energy_kwh", "reverse_active_energy_kwh"]
        )
        selected_day = timezone.localdate()
        month_start = selected_day.replace(day=1)
        month_end = selected_day.replace(
            day=calendar.monthrange(selected_day.year, selected_day.month)[1]
        )

        response = self.client.get(
            reverse("smart_meter:energy_dashboard"),
            {
                "start": month_start.isoformat(),
                "end": month_end.isoformat(),
                "report_type": "monthly",
            },
        )

        self.assertEqual(response.status_code, 200)
        row = response.context["rows_disp"][0]
        self.assertIn("report_type=daily", row["daily_both_url"])
        self.assertIn("energy_view=both", row["daily_both_url"])
        self.assertIn("energy_view=forward", row["daily_forward_url"])
        self.assertIn("energy_view=reverse", row["daily_reverse_url"])
        self.assertIn(f"meter={self.billing_meter.pk}", row["daily_forward_url"])
        self.assertIn(f"start={month_start.isoformat()}", row["daily_forward_url"])
        self.assertIn(f"end={month_end.isoformat()}", row["daily_forward_url"])
        self.assertContains(response, "Show daily forward readings")
        self.assertContains(response, "Show daily reverse readings")

        reverse_response = self.client.get(row["daily_reverse_url"])
        self.assertEqual(reverse_response.status_code, 200)
        self.assertEqual(reverse_response.context["report_type"], "daily")
        self.assertEqual(reverse_response.context["energy_view"], "reverse")
        self.assertEqual(reverse_response.context["current_meter"], str(self.billing_meter.pk))


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


class EnergyExportFormattingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="energy-export-user",
            password="test-pass",
            email="energy-export@example.com",
        )
        self.user.whatsapp_number = "+923001234567"
        self.user.save(update_fields=["whatsapp_number"])
        self.client.force_login(self.user)

        self.property = Property.objects.create(
            property_name="Export Property",
            owner_name="Owner",
            owner_cnic="1234512345600",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=self.property, unit_number="Room 4")
        self.meter = Meter.objects.create(
            meter_number="EXPORT-001",
            unit=self.unit,
            meter_role=Meter.METER_ROLE_BILLING,
        )
        first_tenant = Tenant.objects.create(
            first_name="First", last_name="Occupant", cnic="1234512345601"
        )
        second_tenant = Tenant.objects.create(
            first_name="Second", last_name="Occupant", cnic="1234512345602"
        )
        first_lease = Lease.objects.create(
            tenant=first_tenant,
            unit=self.unit,
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 20),
            monthly_rent=Decimal("10000.00"),
        )
        second_lease = Lease.objects.create(
            tenant=second_tenant,
            unit=self.unit,
            start_date=date(2026, 7, 21),
            end_date=date(2026, 12, 31),
            monthly_rent=Decimal("10000.00"),
        )
        LeaseUnitOccupancy.objects.create(
            lease=first_lease,
            unit=self.unit,
            move_in_date=date(2026, 7, 1),
            move_out_date=date(2026, 7, 20),
        )
        LeaseUnitOccupancy.objects.create(
            lease=second_lease,
            unit=self.unit,
            move_in_date=date(2026, 7, 21),
        )

    def _rows(self, count=2):
        rows = []
        for index in range(count):
            rows.append({
                "meter_number": self.meter.meter_number,
                "unit_number": self.unit.unit_number,
                "unit_id": self.unit.id,
                "property_name": self.property.property_name,
                "tenant_name": "",
                "period_label": date(2026, 7, 1).replace(day=(index % 28) + 1).strftime("%b %d, %Y"),
                "start_kwh": Decimal(index),
                "end_kwh": Decimal(index + 1),
                "usage": Decimal("1"),
                "unit_rate": Decimal("50"),
                "usage_amount": Decimal("50"),
                "service_charges": Decimal("0"),
                "total_amount": Decimal("50"),
            })
        return rows

    def _export_data(self, count=2):
        rows = self._rows(count)
        totals = {
            "total_kwh": Decimal(count),
            "usage_charges": Decimal(count * 50),
            "service_charges": Decimal("0"),
            "grand_total": Decimal(count * 50),
        }
        return "daily", rows, totals, date(2026, 7, 1), date(2026, 8, 30), "", str(self.meter.id)

    def test_export_header_lists_each_occupant_and_lease_period(self):
        from smart_meter.views_dashboard import _energy_export_header

        header = _energy_export_header(
            self._rows(), date(2026, 7, 1), date(2026, 8, 30), "+923001234567"
        )

        self.assertEqual(header["period"], "July 01, 2026 to August 30, 2026")
        self.assertEqual(header["units"], "Room 4")
        self.assertEqual(len(header["occupants"]), 2)
        self.assertIn("First Occupant (July 01, 2026 to July 20, 2026)", header["occupants"])
        self.assertIn("Second Occupant (July 21, 2026 to August 30, 2026)", header["occupants"])

    def test_excel_has_merged_report_header_and_numeric_totals_last(self):
        from io import BytesIO
        from openpyxl import load_workbook

        with patch("smart_meter.views_dashboard._export_rows", return_value=self._export_data()):
            response = self.client.get(reverse("smart_meter:energy_export_xlsx"))

        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(BytesIO(response.content), data_only=True).active
        self.assertIn("A1:H1", {str(cell_range) for cell_range in sheet.merged_cells.ranges})
        self.assertEqual(sheet["A2"].value, "Period: July 01, 2026 to August 30, 2026")
        self.assertEqual(sheet.cell(sheet.max_row, 1).value, "Grand Total")
        self.assertEqual(sheet.cell(sheet.max_row, 6).value, 2)
        self.assertEqual(sheet.cell(sheet.max_row, 8).value, 100)

    def test_pdf_repeats_header_uses_page_subtotals_and_last_page_grand_total(self):
        from io import BytesIO
        from pypdf import PdfReader

        with patch("smart_meter.views_dashboard._export_rows", return_value=self._export_data(40)):
            response = self.client.get(reverse("smart_meter:energy_export_pdf"))

        self.assertEqual(response.status_code, 200)
        reader = PdfReader(BytesIO(response.content))
        self.assertEqual(len(reader.pages), 2)
        first_page = reader.pages[0].extract_text()
        last_page = reader.pages[-1].extract_text()
        self.assertIn("Period: July 01, 2026 to August 30, 2026", first_page)
        self.assertIn("Occupant(s):", first_page)
        self.assertIn("Occupant(s):", last_page)
        self.assertIn("Page subtotal", first_page)
        self.assertNotIn("Grand total", first_page)
        self.assertIn("Grand total", last_page)


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

    def test_prepaid_pilot_remains_eligible_for_monthly_invoice_generation(self):
        self.meter.billing_mode = "prepaid_pilot"
        self.meter.save(update_fields=["billing_mode"])
        self.add_reading(datetime(2026, 7, 31, 23, 45), total="100.000")
        self.add_reading(datetime(2026, 8, 31, 23, 45), total="110.000")

        contexts = billing_contexts_for_period(
            self.period_start,
            self.period_end,
            meter_id=self.meter.pk,
        )

        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0].meter, self.meter)
        self.assertEqual(contexts[0].units, Decimal("10.000"))

    def test_final_timestamp_drives_dashboard_and_invoice(self):
        self.add_reading(datetime(2026, 7, 31, 23, 45), total="359.080")
        self.add_reading(datetime(2026, 8, 15, 12, 0), total="360.880")
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
        self.add_reading(datetime(2026, 8, 31, 23, 30), total="105.000")
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

    def test_installation_opening_reading_is_authoritative_on_replacement_day(self):
        installation = self.meter.installations.get()
        installation.start_date = date(2026, 8, 17)
        installation.start_reading = Decimal("159.230")
        installation.save()
        self.add_reading(datetime(2026, 8, 31, 23, 45), total="170.230")

        ctx = self.compute()

        self.assertEqual(ctx.beg_kwh, Decimal("159.230"))
        self.assertEqual(ctx.end_kwh, Decimal("170.230"))
        self.assertEqual(ctx.units, Decimal("11.000"))
        self.assertEqual(ctx.segments[0]["installation"].pk, installation.pk)

    def test_non_draft_existing_invoice_is_never_modified_by_regeneration(self):
        self.add_reading(datetime(2026, 7, 31, 23, 45), total="100.000")
        self.add_reading(datetime(2026, 8, 31, 23, 45), total="110.000")
        protected = Invoice.objects.create(
            lease=self.lease,
            invoice_number="202607211-002",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 10),
            status="sent",
            amount=Decimal("1720.00"),
        )
        item = InvoiceItem.objects.create(
            invoice=protected,
            category=self.category,
            description="Protected historical electricity line",
            amount=Decimal("1720.00"),
            is_recurring=False,
        )

        with self.assertRaisesMessage(ValueError, "will not modify a non-draft invoice"):
            upsert_invoice_with_electric_item(self.compute())

        protected.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(protected.amount, Decimal("1720.00"))
        self.assertEqual(item.description, "Protected historical electricity line")
        self.assertEqual(item.amount, Decimal("1720.00"))


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


class EnergyDashboardRegisterContinuityTests(TestCase):
    """Regression coverage for legacy-total -> direct F/R register cutovers."""

    def setUp(self):
        property_obj = Property.objects.create(
            property_name="Register Continuity",
            owner_name="Owner",
            owner_cnic="1234512345670",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=property_obj, unit_number="Audit")
        self.meter = Meter.objects.create(
            meter_number="BIDIR-CUTOVER-1",
            unit=self.unit,
            meter_role=Meter.METER_ROLE_CHECK,
            reverse_energy_capability=Meter.REVERSE_CAPABILITY_SUPPORTED,
            reading_profile=Meter.READING_PROFILE_TOTAL_AND_PER_PHASE,
        )
        self.tz = timezone.get_current_timezone()

    def add_reading(self, ts, *, total=None, forward=None, reverse=None):
        return MeterReading.objects.create(
            meter=self.meter,
            ts=timezone.make_aware(ts, self.tz),
            total_energy=Decimal(total) if total is not None else None,
            forward_active_energy_kwh=Decimal(forward) if forward is not None else None,
            reverse_active_energy_kwh=Decimal(reverse) if reverse is not None else None,
        )

    def test_cutover_period_is_withheld_and_next_period_resumes(self):
        # Pre-window and early Sep 1 rows use the historical combined total.
        self.add_reading(datetime(2026, 8, 31, 23, 45), total="7681.840", forward="7681.840")
        self.add_reading(datetime(2026, 9, 1, 0, 9), total="7681.990", forward="7681.990")
        # Direct F/R polling becomes authoritative during Sep 1.
        self.add_reading(
            datetime(2026, 9, 1, 16, 49),
            total="3078.900", forward="3078.900", reverse="4609.900",
        )
        self.add_reading(
            datetime(2026, 9, 1, 23, 57),
            total="3085.130", forward="3085.130", reverse="4609.920",
        )
        self.add_reading(
            datetime(2026, 9, 2, 0, 10),
            total="3085.270", forward="3085.270", reverse="4610.000",
        )
        self.add_reading(
            datetime(2026, 9, 2, 23, 55),
            total="3117.460", forward="3117.460", reverse="4628.550",
        )

        from smart_meter.views_dashboard import _per_meter_series

        _labels, datasets, rows, totals = _per_meter_series(
            Meter.objects.filter(pk=self.meter.pk),
            date(2026, 9, 1),
            date(2026, 9, 2),
            "daily",
        )

        self.assertEqual(len(rows), 2)
        sep1, sep2 = rows
        self.assertFalse(sep1["usage_valid"])
        self.assertEqual(sep1["display_start_kwh"], Decimal("7681.840"))
        self.assertIsNone(sep1["display_start_reverse_kwh"])
        self.assertEqual(
            sep1["display_end_reverse_kwh"],
            Decimal("4609.920"),
        )
        self.assertIsNone(sep1["display_usage"])
        self.assertIn("Register source changed", sep1["continuity_reason"])
        self.assertEqual(sep1["end_kwh"], Decimal("3085.130"))

        self.assertTrue(sep2["usage_valid"])
        self.assertEqual(sep2["start_kwh"], Decimal("3085.130"))
        self.assertEqual(sep2["end_kwh"], Decimal("3117.460"))
        self.assertEqual(sep2["usage"], Decimal("32.330"))
        self.assertEqual(totals["invalid_period_count"], 1)
        self.assertEqual(totals["total_kwh"], Decimal("32.330"))
        self.assertIsNone(datasets[0]["data"][0])
        self.assertEqual(datasets[0]["data"][1], 32.33)

    def test_bidirectional_legacy_rows_stay_legacy_until_paired_reverse_exists(self):
        from smart_meter.views_dashboard import _dashboard_register_values

        source, value, reverse = _dashboard_register_values(
            self.meter,
            {
                "total_energy": Decimal("7688.770"),
                "forward_active_energy_kwh": Decimal("7688.770"),
                "reverse_active_energy_kwh": None,
            },
        )
        self.assertEqual(source, "legacy_total")
        self.assertEqual(value, Decimal("7688.770"))
        self.assertIsNone(reverse)

        source, value, reverse = _dashboard_register_values(
            self.meter,
            {
                "total_energy": Decimal("3078.900"),
                "forward_active_energy_kwh": Decimal("3078.900"),
                "reverse_active_energy_kwh": Decimal("4609.900"),
            },
        )
        self.assertEqual(source, "forward_reverse")
        self.assertEqual(value, Decimal("3078.900"))
        self.assertEqual(reverse, Decimal("4609.900"))


class EnergyDashboardReadingDisplayTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="energy-display-user",
            password="test-pass",
            email="energy-display@example.com",
        )
        self.client.force_login(self.user)
        property_obj = Property.objects.create(
            property_name="Energy Display",
            owner_name="Owner",
            owner_cnic="1234512345680",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=property_obj, unit_number="1")

    def dashboard(self, meter):
        selected_day = timezone.localdate()
        return self.client.get(
            reverse("smart_meter:energy_dashboard"),
            {
                "meter": meter.pk,
                "start": selected_day.isoformat(),
                "end": selected_day.isoformat(),
                "report_type": "daily",
            },
        )

    def test_single_register_boundaries_are_plain_values(self):
        meter = Meter.objects.create(
            meter_number="DISPLAY-SINGLE-1",
            unit=self.unit,
        )
        now = timezone.now()
        MeterReading.objects.create(
            meter=meter,
            ts=now - timedelta(minutes=10),
            total_energy=Decimal("3080.000"),
        )
        MeterReading.objects.create(
            meter=meter,
            ts=now,
            total_energy=Decimal("3085.130"),
        )

        response = self.dashboard(meter)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        begin_cell = html.split(
            "<!-- Begin (small also shows End under it) -->", 1
        )[1].split("</td>", 1)[0]
        self.assertIn("3080.000", begin_cell)
        self.assertIn("3085.130", begin_cell)
        self.assertNotIn("F:", begin_cell)
        self.assertNotContains(response, "Legacy combined total")

    def test_paired_register_boundaries_show_forward_and_reverse(self):
        meter = Meter.objects.create(
            meter_number="DISPLAY-BIDIR-1",
            unit=self.unit,
            reverse_energy_capability=Meter.REVERSE_CAPABILITY_SUPPORTED,
        )
        now = timezone.now()
        MeterReading.objects.create(
            meter=meter,
            ts=now - timedelta(minutes=10),
            total_energy=Decimal("3080.000"),
            forward_active_energy_kwh=Decimal("3080.000"),
            reverse_active_energy_kwh=Decimal("4600.000"),
        )
        MeterReading.objects.create(
            meter=meter,
            ts=now,
            total_energy=Decimal("3085.130"),
            forward_active_energy_kwh=Decimal("3085.130"),
            reverse_active_energy_kwh=Decimal("4609.920"),
        )

        response = self.dashboard(meter)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "F: 3080.000")
        self.assertContains(response, "R: 4600.000")
        self.assertContains(response, "F: 3085.130")
        self.assertContains(response, "R: 4609.920")
        self.assertNotContains(response, "Forward / Reverse registers")


class MeterReadingCollapsedDaySummaryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="reading-summary-user",
            password="test-pass",
            email="reading-summary@example.com",
        )
        self.client.force_login(self.user)
        property_obj = Property.objects.create(
            property_name="Reading Summary",
            owner_name="Owner",
            owner_cnic="1234512345681",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=property_obj, unit_number="1")

    def test_latest_daily_reading_shows_forward_and_reverse(self):
        meter = Meter.objects.create(
            meter_number="SUMMARY-BIDIR-1",
            unit=self.unit,
            reading_profile=Meter.READING_PROFILE_TOTAL_AND_PER_PHASE,
        )
        selected_day = timezone.localdate()
        early = timezone.make_aware(datetime.combine(selected_day, datetime.min.time())).replace(hour=8)
        same_period_earlier = early.replace(hour=16, minute=10, second=0)
        late = early.replace(hour=16, minute=25, second=13)
        MeterReading.objects.create(
            meter=meter,
            ts=early,
            total_energy=Decimal("3400.000"),
            forward_active_energy_kwh=Decimal("3400.000"),
            reverse_active_energy_kwh=Decimal("5140.000"),
        )
        MeterReading.objects.create(
            meter=meter,
            ts=same_period_earlier,
            total_energy=Decimal("3405.000"),
            forward_active_energy_kwh=Decimal("3405.000"),
            reverse_active_energy_kwh=Decimal("5148.000"),
        )
        MeterReading.objects.create(
            meter=meter,
            ts=late,
            total_energy=Decimal("3407.080"),
            forward_active_energy_kwh=Decimal("3407.080"),
            reverse_active_energy_kwh=Decimal("5150.570"),
        )

        response = self.client.get(
            reverse("smart_meter:reading_list"),
            {"meter": meter.pk, "start": selected_day, "end": selected_day},
        )

        day = response.context["reading_groups"][0]
        self.assertEqual(day["last_ts"].time(), late.time())
        self.assertEqual(day["last_energy"], Decimal("3407.080"))
        self.assertEqual(day["last_reverse_energy"], Decimal("5150.570"))
        period_response = self.client.get(
            reverse("smart_meter:reading_day_periods"),
            {
                "meter": meter.pk,
                "start": selected_day,
                "end": selected_day,
                "day": selected_day,
                "day_serial": 1,
            },
        )
        latest_period = period_response.context["day"]["periods"][0]
        self.assertEqual(latest_period["last_ts"].time(), late.time())
        self.assertEqual(
            latest_period["last_reading"].display_forward_energy,
            Decimal("3407.080"),
        )
        self.assertEqual(
            latest_period["last_reading"].reverse_active_energy_kwh,
            Decimal("5150.570"),
        )
        html = response.content.decode()
        period_html = period_response.content.decode()
        day_summary = html.split(
            '<tr class="reading-day-heading reading-summary-heading"', 1
        )[1].split("</tr>", 1)[0]
        period_summary = period_html.split(
            '<tr class="reading-period-heading reading-summary-heading"', 1
        )[1].split("</tr>", 1)[0]
        for summary in (day_summary, period_summary):
            self.assertIn("16:25:13", summary)
            self.assertIn('class="phase-label">F</span>3407.080', summary)
            self.assertIn('class="phase-label">R</span>5150.570', summary)
            self.assertIn("SUMMARY-BIDIR-1", summary)
        period_headers = period_html.split(
            '<tr class="reading-period-heading reading-summary-heading"'
        )[1:]
        self.assertEqual(len(period_headers), 2)
        older_period_header = period_headers[1].split("</tr>", 1)[0]
        self.assertIn("08:00:00", older_period_header)
        self.assertIn('class="phase-label">F</span>3400.000', older_period_header)
        self.assertIn('class="phase-label">R</span>5140.000', older_period_header)

    def test_busy_day_does_not_hide_older_day_behind_reading_pagination(self):
        meter = Meter.objects.create(
            meter_number="SUMMARY-MULTI-DAY-1",
            unit=self.unit,
        )
        newest_day = timezone.localdate()
        newest_base = timezone.make_aware(
            datetime.combine(newest_day, datetime.min.time())
        ).replace(hour=12)
        older_timestamps = [newest_base - timedelta(days=offset) for offset in range(1, 11)]
        MeterReading.objects.bulk_create(
            [
                MeterReading(
                    meter=meter,
                    ts=newest_base + timedelta(seconds=index),
                    total_energy=Decimal(index),
                )
                for index in range(105)
            ]
            + [
                MeterReading(
                    meter=meter,
                    ts=older_ts,
                    total_energy=Decimal("1.000"),
                )
                for older_ts in older_timestamps
            ]
        )

        response = self.client.get(
            reverse("smart_meter:reading_list"),
            {
                "meter": meter.pk,
                "start": older_timestamps[-1].date(),
                "end": newest_day,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["reading_groups"]), 11)
        self.assertEqual(response.context["reading_groups"][0]["count"], 105)
        self.assertTrue(all(day["count"] == 1 for day in response.context["reading_groups"][1:]))
        self.assertEqual(response.context["displayed_reading_count"], 115)
        self.assertEqual(response.context["displayed_day_count"], 11)
        self.assertEqual(response.context["total_pages"], 1)
        html = response.content.decode()
        self.assertEqual(html.count('aria-label="Readings pages"'), 2)
        self.assertLess(
            html.index('aria-label="Readings pages"'),
            html.index('id="toggleAllReadings"'),
        )
        self.assertNotIn('<tr class="reading-period-heading', html)
        self.assertIn(reverse("smart_meter:reading_day_periods"), html)

    def test_latest_daily_reading_without_reverse_is_plain(self):
        meter = Meter.objects.create(
            meter_number="SUMMARY-SINGLE-1",
            unit=self.unit,
        )
        selected_day = timezone.localdate()
        ts = timezone.make_aware(datetime.combine(selected_day, datetime.min.time())).replace(
            hour=12,
            minute=5,
            second=9,
        )
        MeterReading.objects.create(
            meter=meter,
            ts=ts,
            total_energy=Decimal("125.750"),
        )

        response = self.client.get(
            reverse("smart_meter:reading_list"),
            {"meter": meter.pk, "start": selected_day, "end": selected_day},
        )

        summary_html = response.content.decode().split(
            '<tr class="reading-day-heading reading-summary-heading"', 1
        )[1].split("</tr>", 1)[0]
        summary_text = summary_html
        self.assertIn("125.750", summary_text)
        self.assertNotIn('class="phase-label">F</span>', summary_text)
        self.assertNotIn('class="phase-label">R</span>', summary_text)
        period_response = self.client.get(
            reverse("smart_meter:reading_day_periods"),
            {
                "meter": meter.pk,
                "start": selected_day,
                "end": selected_day,
                "day": selected_day,
                "day_serial": 1,
            },
        )
        period_summary = period_response.content.decode().split(
            '<tr class="reading-period-heading reading-summary-heading"', 1
        )[1].split("</tr>", 1)[0]
        self.assertIn("12:05:09", period_summary)
        self.assertIn("125.750", period_summary)
        self.assertNotIn('class="phase-label">F</span>', period_summary)
        self.assertNotIn('class="phase-label">R</span>', period_summary)

    def test_open_occupancy_does_not_outlive_ended_lease(self):
        tenant = Tenant.objects.create(
            first_name="Former",
            last_name="Occupant",
            cnic="1234512345689",
        )
        reading_day = timezone.localdate()
        lease = Lease.objects.create(
            tenant=tenant,
            unit=self.unit,
            start_date=reading_day - timedelta(days=60),
            end_date=reading_day - timedelta(days=1),
            status="ended",
            monthly_rent=Decimal("10000.00"),
        )
        LeaseUnitOccupancy.objects.create(
            lease=lease,
            unit=self.unit,
            move_in_date=lease.start_date,
        )
        meter = Meter.objects.create(
            meter_number="VACANT-AFTER-1",
            unit=self.unit,
        )
        reading = MeterReading.objects.create(
            meter=meter,
            ts=timezone.make_aware(datetime.combine(reading_day, datetime.min.time())).replace(hour=12),
            total_energy=Decimal("200.000"),
        )

        response = self.client.get(
            reverse("smart_meter:reading_list"),
            {"meter": meter.pk, "start": reading_day, "end": reading_day},
        )

        header_reading = response.context["reading_groups"][0]["last_reading"]
        self.assertEqual(header_reading.pk, reading.pk)
        self.assertEqual(header_reading.tenant_name, "Vacant")
        self.assertIsNone(header_reading.tenant_lease_id)
        self.assertContains(response, 'title="Vacant"')
        self.assertNotContains(response, "Former Occupant")


class UnknownMeterConversionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="unknown-convert-user",
            password="test-pass",
            email="unknown-convert@example.com",
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Unknown Conversion",
            owner_name="Owner",
            owner_cnic="1234512345682",
            type="apartment",
            property_type="apartment",
            total_units=2,
        )
        self.unit_one = Unit.objects.create(property=self.property, unit_number="1")
        self.unit_two = Unit.objects.create(property=self.property, unit_number="2")

    def payload(self, meter_number, unit):
        return {
            "unit": unit.pk,
            "meter_number": meter_number,
            "name": "Converted meter",
            "meter_type": Meter.METER_TYPE_ELECTRIC,
            "billing_mode": "postpaid",
            "meter_role": Meter.METER_ROLE_BILLING,
            "power_status": "on",
            "unit_rate": "50.0000",
            "service_charges": "250.00",
            "min_balance_alert": "100.00",
            "min_balance_cutoff": "0.00",
            "installed_at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
            "is_active": "on",
            "notes": "Approved from unknown meters",
        }

    def test_conversion_allows_another_active_meter_on_same_unit(self):
        old_meter = Meter.objects.create(meter_number="KNOWN-METER-A", unit=self.unit_one)
        old_installation = MeterInstallation.objects.create(
            meter=old_meter,
            unit=self.unit_one,
            start_date=timezone.localdate() - timedelta(days=30),
        )
        unknown = UnknownMeter.objects.create(meter_number="UNKNOWN-METER-B")

        response = self.client.post(
            reverse("smart_meter:unknown_meter_convert", args=[unknown.pk]),
            self.payload(unknown.meter_number, self.unit_one),
        )

        self.assertEqual(
            response.status_code,
            302,
            response.context["form"].errors.as_json() if response.context else "",
        )
        self.assertRedirects(response, reverse("smart_meter:unknown_meter_list"))
        new_meter = Meter.objects.get(meter_number=unknown.meter_number)
        self.assertEqual(
            self.unit_one.meter_installations.filter(
                is_active=True,
                end_date__isnull=True,
            ).count(),
            2,
        )
        old_installation.refresh_from_db()
        self.assertTrue(old_installation.is_active)
        self.assertIsNone(old_installation.end_date)
        self.assertTrue(
            new_meter.installations.filter(
                unit=self.unit_one,
                is_active=True,
                end_date__isnull=True,
            ).exists()
        )

    def test_conversion_rejects_meter_already_installed_elsewhere(self):
        meter = Meter.objects.create(
            meter_number="UNKNOWN-INSTALLED-B",
            unit=self.unit_two,
        )
        MeterInstallation.objects.create(
            meter=meter,
            unit=self.unit_two,
            start_date=timezone.localdate() - timedelta(days=10),
        )
        unknown = UnknownMeter.objects.create(meter_number=meter.meter_number)

        response = self.client.post(
            reverse("smart_meter:unknown_meter_convert", args=[unknown.pk]),
            self.payload(unknown.meter_number, self.unit_one),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"This physical meter already has an active installation in {self.unit_two}.",
            msg_prefix=response.context["form"].errors.as_json(),
        )
        self.assertEqual(meter.installations.filter(is_active=True).count(), 1)
        unknown.refresh_from_db()
        self.assertEqual(unknown.status, "new")
        self.assertEqual(response.context["form"].data["unit"], str(self.unit_one.pk))

    def test_failed_installation_rolls_back_meter_and_unknown_status(self):
        unknown = UnknownMeter.objects.create(meter_number="UNKNOWN-ROLLBACK-C")

        with patch(
            "smart_meter.views.MeterInstallation.objects.create",
            side_effect=ValidationError("Installation validation failed."),
        ):
            response = self.client.post(
                reverse("smart_meter:unknown_meter_convert", args=[unknown.pk]),
                self.payload(unknown.meter_number, self.unit_one),
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Unable to create meter installation: Installation validation failed.",
        )
        self.assertFalse(Meter.objects.filter(meter_number=unknown.meter_number).exists())
        self.assertFalse(MeterInstallation.objects.filter(unit=self.unit_one).exists())
        unknown.refresh_from_db()
        self.assertEqual(unknown.status, "new")
