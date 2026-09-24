from datetime import date, datetime, timedelta
from decimal import Decimal
import importlib

import fitz
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from invoices.models import IescoBillReading, Invoice, InvoiceItem, ItemCategory
from leases.models import Lease
from payments.models import Payment, PaymentDetail
from properties.models import Property, Unit
from smart_meter.models import (
    EnergyReconciliationAuditEvent,
    EnergySystem,
    EnergySystemMeterAssignment,
    EnergySystemMeterLink,
    Inverter,
    InverterReading,
    InverterPeriodStatement,
    Meter,
    MeterCheckGroup,
    MeterCheckGroupMembership,
    MeterInstallation,
    MeterReading,
    UtilityBillCycle,
    UtilityBillPayment,
    UtilityConnection,
)
from smart_meter.services.reconciliation import (
    PV_RESIDUAL_LABEL,
    build_energy_reconciliation,
    build_check2_breakdown,
    calculate_check_group_period,
    confirm_bill,
    finalize_bill,
    reopen_record,
    tolerance_status,
)
from smart_meter.services.utility_bill_parser import parse_utility_bill
from smart_meter.forms_reconciliation import UtilityBillCycleForm
from tenants.models import Tenant


class EnergyReconciliationTests(TestCase):
    start = date(2026, 8, 1)
    end = date(2026, 9, 1)

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="energy-admin", password="test-pass", email="energy@example.com"
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Energy Fixture",
            owner_name="Owner",
            owner_cnic="1111111111111",
            type="apartment",
            property_type="apartment",
            total_units=2,
        )
        self.unit = Unit.objects.create(property=self.property, unit_number="E-1")
        self.output_meter = self._meter("FIX-OUTPUT", Meter.MEASUREMENT_POINT_INVERTER_OUTPUT)
        self.grid_meter = self._meter("FIX-GRID", Meter.MEASUREMENT_POINT_GRID_INTERFACE)
        self.billing_meter = Meter.objects.create(
            meter_number="FIX-BILLING", meter_role=Meter.METER_ROLE_BILLING, unit=self.unit
        )
        self.group = MeterCheckGroup.objects.create(name="Fixture output", check_meter=self.output_meter)
        MeterCheckGroupMembership.objects.create(
            group=self.group, billing_meter=self.billing_meter, start_date=self.start
        )
        self.system = EnergySystem.objects.create(
            name="Fixture",
            output_group=self.group,
            grid_interface_meter=self.grid_meter,
            output_meter_includes_grid_export=False,
        )
        EnergySystemMeterAssignment.objects.create(
            energy_system=self.system, role="output", meter=self.output_meter, start_date=self.start
        )
        EnergySystemMeterAssignment.objects.create(
            energy_system=self.system, role="grid_interface", meter=self.grid_meter, start_date=self.start
        )
        self.connection = UtilityConnection.objects.create(
            energy_system=self.system, consumer_id="1143000000", property_label="Fixture"
        )
        self._readings(self.output_meter, 1000, 1100)
        self._readings(self.billing_meter, 200, 280)
        self._readings(self.grid_meter, 500, 530)

    def _meter(self, number, point):
        return Meter.objects.create(
            meter_number=number,
            meter_role=Meter.METER_ROLE_CHECK,
            measurement_point=point,
        )

    def _at(self, day):
        return timezone.make_aware(datetime.combine(day, datetime.min.time()))

    def _readings(self, meter, opening, closing):
        MeterReading.objects.create(meter=meter, ts=self._at(self.start), total_energy=opening)
        MeterReading.objects.create(meter=meter, ts=self._at(self.end), total_energy=closing)

    def _bill(self, confirmed=True):
        return UtilityBillCycle.objects.create(
            utility_connection=self.connection,
            bill_month="AUG 2026",
            period_start=self.start,
            period_end=self.end,
            import_off_peak_kwh=20,
            import_peak_kwh=10,
            export_off_peak_kwh=7,
            export_peak_kwh=3,
            current_bill=Decimal("400.00"),
            total_fpa=Decimal("20.00"),
            grand_total=Decimal("450.00"),
            confirmed_at=timezone.now() if confirmed else None,
            attachment=SimpleUploadedFile("bill.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf"),
        )

    def test_measurement_point_constraint_is_database_enforced(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Meter.objects.create(
                meter_number="BAD-BILL-POINT",
                meter_role=Meter.METER_ROLE_BILLING,
                measurement_point=Meter.MEASUREMENT_POINT_GRID_INTERFACE,
            )

    def test_all_topology_branches_and_exact_bill_gate(self):
        self._bill()
        report = build_energy_reconciliation(self.system, self.start, self.end)
        self.assertEqual(report["building_consumption_kwh"], Decimal("100"))
        self.assertEqual(report["distribution_variance_kwh"], Decimal("20"))
        self.assertEqual(report["net_non_grid_contribution_kwh"], Decimal("80"))

        self.system.output_meter_includes_grid_export = True
        self.system.save(update_fields=["output_meter_includes_grid_export"])
        report = build_energy_reconciliation(self.system, self.start, self.end)
        self.assertEqual(report["building_consumption_kwh"], Decimal("90"))
        self.assertEqual(report["distribution_variance_kwh"], Decimal("10"))
        self.assertEqual(report["net_non_grid_contribution_kwh"], Decimal("70"))

        self.system.output_meter_includes_grid_export = None
        self.system.save(update_fields=["output_meter_includes_grid_export"])
        report = build_energy_reconciliation(self.system, self.start, self.end)
        self.assertEqual(report["raw_output_to_billing_difference_kwh"], Decimal("20"))
        self.assertIsNone(report["distribution_variance_kwh"])
        self.assertIsNone(report["net_non_grid_contribution_kwh"])

        other_end = date(2026, 8, 20)
        report = build_energy_reconciliation(self.system, self.start, other_end)
        self.assertIsNone(report["export_kwh"])

    def test_four_tolerance_tiers(self):
        self.assertEqual(tolerance_status(timedelta(minutes=15)), "green")
        self.assertEqual(tolerance_status(timedelta(minutes=60)), "acceptable")
        self.assertEqual(tolerance_status(timedelta(hours=24)), "warning")
        self.assertEqual(tolerance_status(timedelta(hours=24, seconds=1)), "invalid")
        self.assertEqual(tolerance_status(None), "invalid")

    def test_grid_register_deltas_use_forward_reverse_and_report_net(self):
        grid_readings = list(self.grid_meter.readings.order_by("ts", "id"))
        grid_readings[0].forward_active_energy_kwh = Decimal("500")
        grid_readings[0].reverse_active_energy_kwh = Decimal("10")
        grid_readings[0].save(update_fields=[
            "forward_active_energy_kwh", "reverse_active_energy_kwh"
        ])
        grid_readings[1].forward_active_energy_kwh = Decimal("530")
        grid_readings[1].reverse_active_energy_kwh = Decimal("15")
        grid_readings[1].save(update_fields=[
            "forward_active_energy_kwh", "reverse_active_energy_kwh"
        ])

        report = build_energy_reconciliation(self.system, self.start, self.end)

        self.assertEqual(report["grid_import_kwh"], Decimal("30"))
        self.assertEqual(report["grid_export_kwh"], Decimal("5"))
        self.assertEqual(report["net_grid_energy_kwh"], Decimal("25"))
        self.assertEqual(report["billing_total_kwh"], Decimal("80"))

    def test_iesco_invoice_reading_replaces_duplicate_grid_input(self):
        IescoBillReading.objects.create(
            reference_no="17140000000000",
            consumer_id=self.connection.consumer_id,
            bill_month="AUG 26",
            reading_date="08 AUG 26",
            current_bill="400.00",
            meter_readings=[
                {"meter_no": "I 01322400009141", "direction": "import", "period": "off_peak", "units": "60"},
                {"meter_no": "I 01322400009141", "direction": "import", "period": "peak", "units": "20"},
                {"meter_no": "E 01322400009141", "direction": "export", "period": "off_peak", "units": "10"},
            ],
        )

        report = build_energy_reconciliation(self.system, self.start, self.end)

        self.assertEqual(report["grid_source"], "IESCO invoice")
        self.assertEqual(report["grid_import_kwh"], Decimal("80"))
        self.assertEqual(report["grid_export_kwh"], Decimal("10"))
        self.assertEqual(report["iesco_bill"].meter_number_display, "01322400009141")

    def test_iesco_bills_match_issue_date_and_combine_units_and_cost(self):
        # Production seed migrations contain this real reference/month. Keep the
        # test fixture isolated so it can exercise its own two-bill period.
        IescoBillReading.objects.filter(reference_no="17146151548928").delete()
        for month, issue, reading, imported, exported, cost in (
            ("AUG 26", "09 AUG 26", "08 AUG 26", "837", "578", "19173"),
            ("SEP 26", "09 SEP 26", "08 SEP 26", "817", "533", "20461"),
        ):
            IescoBillReading.objects.create(
                reference_no="17146151548928",
                consumer_id=self.connection.consumer_id,
                bill_month=month,
                issue_date=issue,
                reading_date=reading,
                current_bill=cost,
                meter_readings=[
                    {"direction": "import", "period": "off_peak", "units": imported},
                    {"direction": "export", "period": "off_peak", "units": exported},
                ],
            )

        report = build_energy_reconciliation(
            self.system, date(2026, 8, 1), date(2026, 10, 1)
        )

        self.assertEqual(len(report["iesco_bills"]), 2)
        self.assertEqual(report["iesco_import_kwh"], Decimal("1654"))
        self.assertEqual(report["iesco_export_kwh"], Decimal("1111"))
        self.assertEqual(report["current_cycle_utility_cost"], Decimal("39634"))

        updated_period = build_energy_reconciliation(
            self.system, date(2026, 8, 8), date(2026, 9, 9)
        )
        self.assertEqual([bill.bill_month for bill in updated_period["iesco_bills"]], ["SEP 26"])
        self.assertEqual(updated_period["iesco_import_kwh"], Decimal("817"))

    def test_check2_uses_dashboard_units_times_meter_rate_not_invoice_amount(self):
        self.billing_meter.unit_rate = Decimal("54")
        self.billing_meter.save(update_fields=["unit_rate"])
        MeterReading.objects.create(
            meter=self.billing_meter,
            ts=timezone.make_aware(datetime(2026, 8, 31, 23, 59)),
            total_energy=Decimal("280"),
        )
        tenant = Tenant.objects.create(
            first_name="Check", last_name="Two", cnic="3333333333333"
        )
        lease = Lease.objects.create(
            tenant=tenant,
            unit=self.unit,
            start_date=self.start,
            end_date=date(2027, 7, 31),
            monthly_rent=1000,
        )
        category = ItemCategory.objects.create(name="Electric Bill")
        invoice = Invoice.objects.create(
            lease=lease,
            issue_date=date(2026, 8, 10),
            due_date=date(2026, 8, 20),
            amount=9999,
        )
        InvoiceItem.objects.create(
            invoice=invoice, category=category, description="Meter billing", amount=9999
        )

        result = build_check2_breakdown(self.system, self.start, self.end)

        self.assertEqual(result["billing_rows"][0]["kwh"], Decimal("80"))
        self.assertEqual(result["billing_rows"][0]["amount"], Decimal("4320"))
        self.assertEqual(result["billing_total_amount"], Decimal("4320"))
        self.assertEqual(
            result["billing_rows"][0]["display_name"],
            f"{self.property.property_name[:8]} - {self.unit.unit_number}",
        )

    def test_check2_includes_same_unit_replacement_meter_readings(self):
        replacement = Meter.objects.create(
            meter_number="FIX-OLD-BILLING",
            meter_role=Meter.METER_ROLE_BILLING,
            meter_type=Meter.METER_TYPE_ELECTRIC,
            unit=self.unit,
            is_active=False,
        )
        MeterReading.objects.create(
            meter=replacement, ts=self._at(self.start), total_energy=Decimal("10")
        )
        MeterReading.objects.create(
            meter=replacement,
            ts=timezone.make_aware(datetime(2026, 8, 31, 23, 59)),
            total_energy=Decimal("25"),
        )

        result = build_check2_breakdown(self.system, self.start, self.end)
        replacement_row = next(
            row for row in result["billing_rows"] if row["meter"] == replacement
        )

        self.assertTrue(replacement_row["is_replacement"])
        self.assertEqual(replacement_row["kwh"], Decimal("15"))
        self.assertEqual(result["billing_total_kwh"], Decimal("15"))

    def test_check2_same_unit_members_show_meter_names_not_replacement_labels(self):
        self.billing_meter.name = "Inverter Main"
        self.billing_meter.save(update_fields=["name"])
        second = Meter.objects.create(
            meter_number="FIX-BILLING-2",
            name="Inverter Secondary",
            meter_role=Meter.METER_ROLE_BILLING,
            meter_type=Meter.METER_TYPE_ELECTRIC,
            unit=self.unit,
        )
        MeterCheckGroupMembership.objects.create(
            group=self.group, billing_meter=second, start_date=self.start
        )
        MeterReading.objects.create(
            meter=second, ts=self._at(self.start), total_energy=Decimal("50")
        )
        MeterReading.objects.create(
            meter=second,
            ts=timezone.make_aware(datetime(2026, 8, 31, 23, 59)),
            total_energy=Decimal("70"),
        )

        result = build_check2_breakdown(self.system, self.start, self.end)
        rows = {row["meter"].pk: row for row in result["billing_rows"]}

        self.assertFalse(rows[self.billing_meter.pk]["is_replacement"])
        self.assertFalse(rows[second.pk]["is_replacement"])
        self.assertEqual(rows[self.billing_meter.pk]["display_name"], "Inverter Main")
        self.assertEqual(rows[second.pk]["display_name"], "Inverter Secondary")
        self.assertEqual(len(result["unit_groups"]), 1)

    def test_check2_hides_membership_without_period_readings(self):
        empty_unit = Unit.objects.create(
            property=self.property, unit_number="F35-FLAT# 02"
        )
        empty_meter = Meter.objects.create(
            meter_number="250619510015",
            meter_role=Meter.METER_ROLE_BILLING,
            meter_type=Meter.METER_TYPE_ELECTRIC,
            unit=empty_unit,
            is_active=False,
        )
        MeterCheckGroupMembership.objects.create(
            group=self.group,
            billing_meter=empty_meter,
            start_date=self.start,
            end_date=self.end,
            is_active=False,
        )

        result = build_check2_breakdown(self.system, self.start, self.end)

        self.assertNotIn(
            empty_meter.pk,
            [row["meter"].pk for row in result["billing_rows"]],
        )

    def test_scoreboard_month_and_year_filter_sets_calendar_month(self):
        response = self.client.get(
            reverse("smart_meter:energy_group_scoreboard", args=[self.group.pk]),
            {"range": "selected_month", "month": "8", "year": "2026"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["start_date"], date(2026, 8, 1))
        self.assertEqual(response.context["end_date"], date(2026, 8, 31))
        self.assertContains(response, 'name="month"')
        self.assertContains(response, 'name="year"')
        self.assertContains(response, "Audit − billing variance")
        self.assertNotContains(response, "Billing-meter memberships")
        self.assertContains(
            response,
            f"meter={self.output_meter.pk}&role=check&report_type=daily",
        )
        self.assertContains(
            response,
            f"unit={self.unit.pk}&meter={self.billing_meter.pk}"
            "&role=billing&report_type=daily"
            "&start=2026-08-01&end=2026-08-31",
        )
        self.assertContains(
            response,
            f"/smart-meter/energy-groups/scoreboard/{self.group.pk}/meter/"
            f"{self.billing_meter.pk}/?start=2026-08-01&end=2026-08-31",
        )

    def test_tenant_revenue_uses_dashboard_charge_not_manual_invoice_amount(self):
        self.billing_meter.unit_rate = Decimal("6.25")
        self.billing_meter.save(update_fields=["unit_rate"])
        MeterReading.objects.create(
            meter=self.billing_meter,
            ts=timezone.make_aware(datetime(2026, 8, 31, 23, 59)),
            total_energy=Decimal("280"),
        )
        tenant = Tenant.objects.create(
            first_name="Revenue", last_name="Check", cnic="4444444444444"
        )
        lease = Lease.objects.create(
            tenant=tenant,
            unit=self.unit,
            start_date=self.start,
            end_date=date(2027, 7, 31),
            monthly_rent=1000,
        )
        electric = ItemCategory.objects.create(name="Electric Bill")
        repair = ItemCategory.objects.create(name="Repair")
        invoice = Invoice.objects.create(
            lease=lease,
            issue_date=date(2026, 8, 10),
            due_date=date(2026, 8, 20),
            amount=11600,
        )
        InvoiceItem.objects.create(
            invoice=invoice, category=electric, description="Meter billing", amount=500
        )
        InvoiceItem.objects.create(
            invoice=invoice, category=repair, description="Electric Repair", amount=11100
        )

        report = build_energy_reconciliation(self.system, self.start, self.end)

        self.assertEqual(report["tenant_energy_revenue"], Decimal("500"))

    def test_scoreboard_meter_detail_returns_dashboard_table_fragment(self):
        IescoBillReading.objects.create(
            reference_no="17140000000001",
            consumer_id=self.connection.consumer_id,
            bill_month="AUG 26",
            issue_date="09 AUG 26",
            reading_date="08 AUG 26",
            current_bill="500",
            grand_total="500",
        )
        url = reverse(
            "smart_meter:energy_group_meter_detail",
            args=[self.group.pk, self.billing_meter.pk],
        )

        response = self.client.get(url, {
            "start": self.start.isoformat(),
            "end": (self.end - timedelta(days=1)).isoformat(),
        })

        self.assertEqual(response.status_code, 200)
        html = response.json()["html"]
        self.assertIn("Meter Detail:", html)
        self.assertIn("Period date", html)
        self.assertIn("Group total", html)
        self.assertIn("Audit", html)
        self.assertIn("Diff", html)
        self.assertIn("01 Aug 2026 to 08 Aug 2026", html)
        self.assertIn("09 Aug 2026 to 31 Aug 2026", html)
        self.assertIn("Open full dashboard", html)
        self.assertIn(f"meter={self.billing_meter.pk}", html)

        audit_response = self.client.get(
            reverse(
                "smart_meter:energy_group_meter_detail",
                args=[self.group.pk, self.output_meter.pk],
            ),
            {"start": self.start.isoformat(), "end": "2026-08-31"},
        )
        self.assertEqual(audit_response.status_code, 200)
        audit_html = audit_response.json()["html"]
        self.assertIn(self.output_meter.meter_number, audit_html)
        self.assertIn("role=check", audit_html)

    def test_scoreboard_inverter_detail_groups_saved_readings(self):
        IescoBillReading.objects.create(
            reference_no="17140000000002",
            consumer_id=self.connection.consumer_id,
            bill_month="AUG 26",
            issue_date="09 AUG 26",
            reading_date="08 AUG 26",
            current_bill="500",
            grand_total="500",
        )
        inverter = Inverter.objects.create(
            energy_system=self.system, name="Fixture Inverter"
        )
        InverterReading.objects.create(
            inverter=inverter,
            recorded_at=self._at(date(2026, 8, 1)),
            reading_kwh=Decimal("100"),
        )
        InverterReading.objects.create(
            inverter=inverter,
            recorded_at=self._at(date(2026, 8, 20)),
            reading_kwh=Decimal("150"),
        )
        response = self.client.get(
            reverse(
                "smart_meter:energy_group_inverter_detail",
                args=[self.group.pk, inverter.pk],
            ),
            {"start": "2026-08-01", "end": "2026-08-31"},
        )

        self.assertEqual(response.status_code, 200)
        html = response.json()["html"]
        self.assertIn("Meter Detail: Fixture Inverter", html)
        self.assertIn("01 Aug 2026 to 08 Aug 2026", html)
        self.assertIn("09 Aug 2026 to 31 Aug 2026", html)
        self.assertIn("150.000", html)

    def test_bulk_inverter_entry_uses_one_date_and_all_inverters(self):
        inverter_1 = Inverter.objects.create(energy_system=self.system, name="Inverter 1")
        inverter_2 = Inverter.objects.create(energy_system=self.system, name="Inverter 2")
        url = reverse("smart_meter:inverter_statement_add", args=[self.system.pk])

        page = self.client.get(url)
        self.assertContains(page, "Inverter 1")
        self.assertContains(page, "Inverter 2")
        self.assertContains(page, timezone.localdate().isoformat())

        response = self.client.post(url, {
            "reading_date": "2026-09-23",
            f"reading_{inverter_1.pk}": "1234.5",
            f"reading_{inverter_2.pk}": "2345.5",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(InverterReading.objects.count(), 2)
        self.assertEqual(
            set(InverterReading.objects.values_list("reading_kwh", flat=True)),
            {Decimal("1234.500"), Decimal("2345.500")},
        )

    def test_legacy_unit_dashboard_uses_meter_relationship_and_ts(self):
        self.unit.is_smart_meter = True
        self.unit.save(update_fields=["is_smart_meter"])

        response = self.client.get(
            reverse("smart_meter:meter_dashboard", args=[self.unit.pk])
        )

        self.assertRedirects(
            response,
            f"{reverse('smart_meter:energy_dashboard')}?unit={self.unit.pk}",
            fetch_redirect_response=False,
        )

    def test_register_decrease_is_a_discontinuity_not_zero_clamped(self):
        grid_readings = list(self.grid_meter.readings.order_by("ts", "id"))
        grid_readings[0].reverse_active_energy_kwh = Decimal("10")
        grid_readings[0].save(update_fields=["reverse_active_energy_kwh"])
        MeterReading.objects.create(
            meter=self.grid_meter,
            ts=self._at(date(2026, 8, 15)),
            total_energy=Decimal("515"),
            reverse_active_energy_kwh=Decimal("8"),
        )
        grid_readings[1].reverse_active_energy_kwh = Decimal("15")
        grid_readings[1].save(update_fields=["reverse_active_energy_kwh"])

        report = build_energy_reconciliation(self.system, self.start, self.end)

        self.assertIsNone(report["grid_export_kwh"])
        self.assertIsNone(report["net_grid_energy_kwh"])
        self.assertTrue(any("decreased" in reason for reason in report["withheld_reasons"]))

    def test_financials_use_dashboard_charge_payment_allocation_and_utility_payments(self):
        self.billing_meter.unit_rate = Decimal("6.25")
        self.billing_meter.save(update_fields=["unit_rate"])
        MeterReading.objects.create(
            meter=self.billing_meter,
            ts=timezone.make_aware(datetime(2026, 8, 31, 23, 59)),
            total_energy=Decimal("280"),
        )
        tenant = Tenant.objects.create(
            first_name="Energy", last_name="Tenant", cnic="2222222222222"
        )
        lease = Lease.objects.create(
            tenant=tenant,
            unit=self.unit,
            start_date=self.start,
            end_date=date(2027, 7, 31),
            monthly_rent=1000,
        )
        category = ItemCategory.objects.create(name="Electric")
        invoice = Invoice.objects.create(
            lease=lease,
            issue_date=date(2026, 8, 10),
            due_date=date(2026, 8, 20),
            amount=500,
        )
        InvoiceItem.objects.create(invoice=invoice, category=category, description="Electric bill", amount=500)
        tenant_payment = Payment.objects.create(
            lease=lease, payment_date=date(2026, 8, 15), amount=300
        )
        PaymentDetail.objects.create(
            payment=tenant_payment,
            lease_amount=300,
            electricity_amount=300,
            electricity_meter=self.billing_meter,
        )
        bill = self._bill()
        UtilityBillPayment.objects.create(
            bill_cycle=bill,
            amount=250,
            paid_at=timezone.now(),
            confirmed_at=timezone.now(),
        )
        report = build_energy_reconciliation(self.system, self.start, self.end)
        self.assertEqual(report["tenant_energy_revenue"], Decimal("500"))
        self.assertEqual(report["tenant_electricity_collections"], Decimal("300"))
        self.assertEqual(report["current_cycle_utility_cost"], Decimal("400"))
        self.assertEqual(report["operating_energy_margin"], Decimal("100"))
        self.assertEqual(report["cash_position"], Decimal("50"))
        self.assertEqual(report["utility_payable_credit"], Decimal("200"))
        self.assertEqual(report["tenant_outstanding"], Decimal("200"))

    def test_cash_position_is_withheld_when_utility_payment_is_not_recorded(self):
        self._bill()
        report = build_energy_reconciliation(self.system, self.start, self.end)
        self.assertIsNone(report["utility_amount_paid"])
        self.assertIsNone(report["cash_position"])
        self.assertEqual(report["utility_payable_credit"], Decimal("450"))

    def test_confirmed_exact_pv_statement_supplies_carefully_labelled_residual(self):
        self._bill()
        InverterPeriodStatement.objects.create(
            energy_system=self.system,
            period_start=self.start,
            period_end=self.end,
            pv_reading_start_kwh=1000,
            pv_reading_end_kwh=1090,
            confirmed_at=timezone.now(),
        )
        report = build_energy_reconciliation(self.system, self.start, self.end)
        self.assertEqual(report["pv_storage_residual_kwh"], Decimal("10"))
        self.assertEqual(report["pv_storage_residual_label"], PV_RESIDUAL_LABEL)

    def test_reassignment_closes_old_assignment_with_exclusive_end(self):
        replacement = self._meter("FIX-GRID-2", Meter.MEASUREMENT_POINT_GRID_INTERFACE)
        effective = date(2026, 8, 27)
        self.system.reassign_meter("grid_interface", replacement, self.user, effective)
        old = EnergySystemMeterAssignment.objects.get(
            energy_system=self.system, role="grid_interface", meter=self.grid_meter
        )
        self.assertEqual(old.end_date, effective)
        self.assertTrue(EnergySystemMeterAssignment.objects.filter(
            energy_system=self.system, role="grid_interface", meter=replacement, start_date=effective, end_date__isnull=True
        ).exists())

    def test_second_open_assignment_is_rejected_on_mysql_application_path(self):
        replacement = self._meter("FIX-GRID-3", Meter.MEASUREMENT_POINT_GRID_INTERFACE)
        with self.assertRaises(ValidationError):
            EnergySystemMeterAssignment.objects.create(
                energy_system=self.system,
                role="grid_interface",
                meter=replacement,
                start_date=date(2026, 8, 20),
            )

    def test_role_change_blocks_actively_assigned_energy_meter(self):
        with self.assertRaises(ValidationError):
            self.grid_meter.change_role(
                Meter.METER_ROLE_BILLING, effective_date=date(2026, 8, 27), user=self.user
            )

    def test_bill_workflow_validates_overlap_and_logs_confirm_finalize_reopen(self):
        bill = self._bill(confirmed=False)
        confirm_bill(bill, self.user)
        finalize_bill(bill, self.user)
        bill.refresh_from_db()
        self.assertEqual(bill.status, "final")
        self.assertIsNotNone(bill.finalized_at)
        overlapping = UtilityBillCycle.objects.create(
            utility_connection=self.connection,
            bill_month="OVERLAP",
            period_start=date(2026, 8, 15),
            period_end=date(2026, 9, 15),
            attachment="x.pdf",
        )
        with self.assertRaises(ValidationError):
            confirm_bill(overlapping, self.user)
        reopen_record(bill, self.user, "Correct register values")
        bill.refresh_from_db()
        self.assertEqual(bill.status, "draft")
        self.assertEqual(
            list(bill.audit_events.values_list("action", flat=True)),
            ["confirmed", "finalized", "reopened"],
        )

    def test_reopen_requires_reason(self):
        statement = InverterPeriodStatement.objects.create(
            energy_system=self.system,
            period_start=self.start,
            period_end=self.end,
            pv_reading_start_kwh=1,
            pv_reading_end_kwh=2,
            confirmed_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            reopen_record(statement, self.user, "")

    def test_invalid_duplicate_or_out_of_order_transitions_are_rejected(self):
        bill = self._bill(confirmed=False)
        with self.assertRaises(ValidationError):
            finalize_bill(bill, self.user)
        confirm_bill(bill, self.user)
        with self.assertRaises(ValidationError):
            confirm_bill(bill, self.user)
        finalize_bill(bill, self.user)
        with self.assertRaises(ValidationError):
            finalize_bill(bill, self.user)

        draft_statement = InverterPeriodStatement.objects.create(
            energy_system=self.system,
            period_start=self.start,
            period_end=self.end,
            pv_reading_start_kwh=1,
            pv_reading_end_kwh=2,
        )
        with self.assertRaises(ValidationError):
            reopen_record(draft_statement, self.user, "No transition occurred")

    def test_utility_bill_upload_rejects_spoofed_or_non_pdf_files(self):
        base = {
            "utility_connection": self.connection.pk,
            "bill_month": "AUG 2026",
            "period_start": self.start,
            "period_end": self.end,
        }
        wrong_extension = UtilityBillCycleForm(
            data=base,
            files={
                "attachment": SimpleUploadedFile(
                    "bill.txt", b"%PDF-1.4\n%%EOF", content_type="application/pdf"
                )
            },
        )
        self.assertFalse(wrong_extension.is_valid())
        self.assertIn("attachment", wrong_extension.errors)

        spoofed_pdf = UtilityBillCycleForm(
            data=base,
            files={
                "attachment": SimpleUploadedFile(
                    "bill.pdf", b"not a pdf", content_type="application/pdf"
                )
            },
        )
        self.assertFalse(spoofed_pdf.is_valid())
        self.assertIn("attachment", spoofed_pdf.errors)

    def test_legacy_characterization_values_match_service_and_view_after_refactor(self):
        result = calculate_check_group_period(self.group, self.start, self.end)
        self.assertEqual(result["check_kwh"], Decimal("100"))
        self.assertEqual(result["billing_kwh"], Decimal("80"))
        self.assertEqual(result["variance_kwh"], Decimal("20"))
        response = self.client.get(
            reverse("smart_meter:meter_check_group_detail", args=[self.group.pk]),
            {"start": self.start.isoformat(), "end": self.end.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["check_kwh"], result["check_kwh"])
        self.assertEqual(response.context["billing_kwh"], result["billing_kwh"])
        self.assertEqual(response.context["variance_kwh"], result["variance_kwh"])

    def test_linked_output_list_numbers_meters_and_hides_single_phase_reverse_badge(self):
        other_output = Meter.objects.create(
            meter_number="FIX-OUTPUT-2", meter_role=Meter.METER_ROLE_CHECK,
            reading_profile=Meter.READING_PROFILE_TOTAL_AND_PER_PHASE,
        )
        EnergySystemMeterLink.objects.create(
            energy_system=self.system, meter=self.grid_meter,
            side=EnergySystemMeterLink.SIDE_INPUT,
        )
        EnergySystemMeterLink.objects.create(
            energy_system=self.system, meter=self.output_meter,
            side=EnergySystemMeterLink.SIDE_OUTPUT,
        )
        EnergySystemMeterLink.objects.create(
            energy_system=self.system, meter=other_output,
            side=EnergySystemMeterLink.SIDE_OUTPUT,
        )
        response = self.client.get(reverse("smart_meter:energy_system_detail", args=[self.system.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("1. FIX-OUTPUT", html)
        self.assertIn("2. FIX-OUTPUT-2", html)
        self.assertNotIn("1. FIX-OUTPUT</strong> <span class=\"badge", html)
        self.assertIn("2. FIX-OUTPUT-2</strong> <span class=\"badge", html)

    def test_output_meter_transfer_auto_saves_without_changing_input_links(self):
        other_output = Meter.objects.create(
            meter_number="FIX-OUTPUT-AUTO-SAVE",
            meter_role=Meter.METER_ROLE_BILLING,
            unit=self.unit,
        )
        EnergySystemMeterLink.objects.create(
            energy_system=self.system,
            meter=self.grid_meter,
            side=EnergySystemMeterLink.SIDE_INPUT,
        )
        EnergySystemMeterLink.objects.create(
            energy_system=self.system,
            meter=self.output_meter,
            side=EnergySystemMeterLink.SIDE_OUTPUT,
        )
        edit_response = self.client.get(
            reverse("smart_meter:meter_check_group_edit", args=[self.group.pk])
        )
        self.assertContains(edit_response, 'data-output-meter-transfer')
        self.assertContains(edit_response, 'data-add-output')
        self.assertContains(edit_response, 'data-remove-output')

        url = reverse(
            "smart_meter:energy_system_output_meters_update",
            args=[self.system.pk],
        )
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {"output_meters": [other_output.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(self.system.meter_links.filter(
                side=EnergySystemMeterLink.SIDE_OUTPUT,
            ).values_list("meter_id", flat=True)),
            [other_output.pk],
        )
        self.assertTrue(self.system.meter_links.filter(
            side=EnergySystemMeterLink.SIDE_INPUT,
            meter=self.grid_meter,
        ).exists())

        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.system.meter_links.filter(
            side=EnergySystemMeterLink.SIDE_OUTPUT,
            meter=other_output,
        ).exists())

    def test_combined_energy_group_editor_saves_all_sections_and_adopts_membership(self):
        MeterInstallation.objects.create(
            meter=self.billing_meter,
            unit=self.unit,
            start_date=self.start,
        )
        url = reverse("smart_meter:meter_check_group_edit", args=[self.group.pk])
        self.assertRedirects(
            self.client.get(reverse("smart_meter:energy_system_edit", args=[self.system.pk])),
            url,
        )
        self.assertRedirects(
            self.client.get(reverse("smart_meter:energy_system_setup", args=[self.group.pk])),
            url,
        )
        page = self.client.get(url)
        self.assertContains(page, "Basic Details")
        self.assertContains(page, "Billing Coverage")
        self.assertContains(page, "Reconciliation Meters")
        self.assertContains(page, "IESCO Connection")

        response = self.client.post(url, {
            "name": "Combined Energy Group",
            "property": self.property.pk,
            "automatic_coverage": "on",
            "coverage_mode": MeterCheckGroup.COVERAGE_PROPERTY,
            "check_meter": self.output_meter.pk,
            "notes": "Combined settings",
            "is_active": "on",
            "reconciliation-input_meters": [self.grid_meter.pk],
            "reconciliation-output_meters": [self.billing_meter.pk],
            "reconciliation-output_meter_includes_grid_export": "false",
            "reconciliation-output_reverse_capability": "",
            "iesco-consumer_id": self.connection.consumer_id,
            "iesco-reference_no": "17140000000001",
            "iesco-dg_capacity_kw": "25.50",
            "iesco-property_label": "Combined property",
        })

        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.system.refresh_from_db()
        self.connection.refresh_from_db()
        membership = self.group.memberships.get(billing_meter=self.billing_meter)
        self.assertEqual(self.group.name, "Combined Energy Group")
        self.assertTrue(self.group.automatic_coverage)
        self.assertEqual(self.group.property, self.property)
        self.assertTrue(membership.assigned_automatically)
        self.assertEqual(self.system.name, self.group.name)
        self.assertEqual(
            set(self.system.meter_links.values_list("side", "meter_id")),
            {
                (EnergySystemMeterLink.SIDE_INPUT, self.grid_meter.pk),
                (EnergySystemMeterLink.SIDE_OUTPUT, self.billing_meter.pk),
            },
        )
        self.assertEqual(self.connection.reference_no, "17140000000001")
        self.assertEqual(self.connection.dg_capacity_kw, Decimal("25.50"))
        detail = self.client.get(
            reverse("smart_meter:meter_check_group_detail", args=[self.group.pk])
        )
        self.assertContains(detail, "Auto managed")

    def test_new_action_routes_are_post_only_and_audited(self):
        statement = InverterPeriodStatement.objects.create(
            energy_system=self.system,
            period_start=self.start,
            period_end=self.end,
            pv_reading_start_kwh=1,
            pv_reading_end_kwh=2,
        )
        url = reverse("smart_meter:inverter_statement_confirm", args=[statement.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        statement.refresh_from_db()
        self.assertIsNotNone(statement.confirmed_at)
        self.assertTrue(EnergyReconciliationAuditEvent.objects.filter(
            inverter_statement=statement, action="confirmed"
        ).exists())

    def test_every_new_route_has_the_documented_get_or_post_method(self):
        bill = self._bill(confirmed=False)
        statement = InverterPeriodStatement.objects.create(
            energy_system=self.system,
            period_start=self.start,
            period_end=self.end,
            pv_reading_start_kwh=1,
            pv_reading_end_kwh=2,
        )
        payment = UtilityBillPayment.objects.create(
            bill_cycle=bill, amount=10, paid_at=timezone.now()
        )
        get_routes = (
            ("energy_system_list", ()),
            ("energy_system_detail", (self.system.pk,)),
            ("inverter_statement_add", (self.system.pk,)),
            ("inverter_statement_edit", (statement.pk,)),
            ("utility_bill_upload", ()),
            ("utility_bill_detail", (bill.pk,)),
            ("utility_bill_edit", (bill.pk,)),
            ("utility_bill_payment_add", (bill.pk,)),
            ("utility_bill_payment_edit", (payment.pk,)),
        )
        for route_name, args in get_routes:
            with self.subTest(route=route_name):
                self.assertEqual(self.client.get(reverse(f"smart_meter:{route_name}", args=args)).status_code, 200)
        post_only_routes = (
            ("meter_reading_profile_update", (self.grid_meter.pk,)),
            ("energy_system_reassign_meter", (self.system.pk,)),
            ("energy_system_output_meters_update", (self.system.pk,)),
            ("inverter_statement_confirm", (statement.pk,)),
            ("inverter_statement_reopen", (statement.pk,)),
            ("utility_bill_confirm", (bill.pk,)),
            ("utility_bill_finalize", (bill.pk,)),
            ("utility_bill_reopen", (bill.pk,)),
            ("utility_bill_payment_confirm", (payment.pk,)),
        )
        for route_name, args in post_only_routes:
            with self.subTest(route=route_name):
                self.assertEqual(self.client.get(reverse(f"smart_meter:{route_name}", args=args)).status_code, 405)

    def test_reading_profile_update_requires_change_meter_permission(self):
        url = reverse("smart_meter:meter_reading_profile_update", args=[self.grid_meter.pk])
        limited_user = get_user_model().objects.create_user(
            username="energy-viewer", password="test-pass"
        )
        self.client.force_login(limited_user)
        response = self.client.post(
            url,
            {"reading_profile": Meter.READING_PROFILE_TOTAL_AND_PER_PHASE},
        )
        self.assertEqual(response.status_code, 403)
        self.grid_meter.refresh_from_db()
        self.assertEqual(self.grid_meter.reading_profile, Meter.READING_PROFILE_AUTO)

        self.client.force_login(self.user)
        response = self.client.post(
            url,
            {"reading_profile": Meter.READING_PROFILE_TOTAL_AND_PER_PHASE},
        )
        self.assertEqual(response.status_code, 302)
        self.grid_meter.refresh_from_db()
        self.assertEqual(
            self.grid_meter.reading_profile,
            Meter.READING_PROFILE_TOTAL_AND_PER_PHASE,
        )

    def test_every_reconciliation_endpoint_rejects_authenticated_user_without_permission(self):
        bill = self._bill(confirmed=False)
        statement = InverterPeriodStatement.objects.create(
            energy_system=self.system,
            period_start=self.start,
            period_end=self.end,
            pv_reading_start_kwh=1,
            pv_reading_end_kwh=2,
        )
        payment = UtilityBillPayment.objects.create(
            bill_cycle=bill, amount=10, paid_at=timezone.now()
        )
        limited_user = get_user_model().objects.create_user(
            username="reconciliation-no-perms", password="test-pass"
        )
        self.client.force_login(limited_user)
        routes = (
            ("get", "energy_system_list", ()),
            ("get", "energy_system_detail", (self.system.pk,)),
            ("post", "energy_system_reassign_meter", (self.system.pk,)),
            ("post", "energy_system_output_meters_update", (self.system.pk,)),
            ("get", "inverter_statement_add", (self.system.pk,)),
            ("get", "inverter_statement_edit", (statement.pk,)),
            ("post", "inverter_statement_confirm", (statement.pk,)),
            ("post", "inverter_statement_reopen", (statement.pk,)),
            ("get", "utility_bill_upload", ()),
            ("get", "utility_bill_detail", (bill.pk,)),
            ("get", "utility_bill_edit", (bill.pk,)),
            ("post", "utility_bill_confirm", (bill.pk,)),
            ("post", "utility_bill_finalize", (bill.pk,)),
            ("post", "utility_bill_reopen", (bill.pk,)),
            ("get", "utility_bill_payment_add", (bill.pk,)),
            ("get", "utility_bill_payment_edit", (payment.pk,)),
            ("post", "utility_bill_payment_confirm", (payment.pk,)),
        )
        for method, route_name, args in routes:
            with self.subTest(route=route_name):
                response = getattr(self.client, method)(
                    reverse(f"smart_meter:{route_name}", args=args)
                )
                self.assertEqual(response.status_code, 403)

    def test_linked_check_groups_hidden_by_default_and_available_by_toggle(self):
        archived_meter = self._meter("ARCHIVED-GRID", Meter.MEASUREMENT_POINT_GRID_INTERFACE)
        archived_group = MeterCheckGroup.objects.create(name="Linked input", check_meter=archived_meter)
        archived_group.superseded_by_energy_system = self.system
        archived_group.save(update_fields=["superseded_by_energy_system"])
        url = reverse("smart_meter:meter_check_group_list")
        self.assertNotContains(self.client.get(url), "Linked input")
        self.assertContains(self.client.get(url, {"show_linked": "1"}), "Linked input")

    def test_check_group_list_shows_iesco_reference_number_column(self):
        IescoBillReading.objects.create(
            reference_no="1714-TEST-REF",
            consumer_id=self.connection.consumer_id,
            bill_month="SEP 26",
        )

        response = self.client.get(reverse("smart_meter:meter_check_group_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IESCO Reference No.")
        self.assertContains(response, "1714-TEST-REF")
        self.assertContains(response, "reference_no=1714-TEST-REF")
        self.assertContains(response, 'colspan="2" class="text-center"')
        self.assertContains(response, '<span class="d-block">Active</span>', html=True)

    def test_pdf_parser_captures_current_bill_credit_and_ignores_mdi(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_text(
            (40, 60),
            "CONSUMER ID: 1143000000\nREFERENCE NO: REF-1\nBILL MONTH: AUG 2026\n"
            "READING DATE: 01-SEP-2026\nISSUE DATE: 02-SEP-2026\nDUE DATE: 15-SEP-2026\n"
            "IMPORT OFF PEAK 1000 1100 100 5\nIMPORT PEAK 200 220 20 6\n"
            "EXPORT OFF PEAK 500 530 30 7\nEXPORT PEAK 50 55 5 8\n"
            "TOTAL ELECTRICITY CHARGES: 1,000\nTAXES: 200\nCURRENT BILL: 1,200\n"
            "ARREARS: 0\nTOTAL FPA: 50\nGRAND TOTAL: 100 CR",
        )
        payload = document.tobytes()
        document.close()
        parsed = parse_utility_bill(SimpleUploadedFile("bill.pdf", payload, content_type="application/pdf"))
        self.assertEqual(parsed.data["current_bill"], Decimal("1200"))
        self.assertEqual(parsed.data["grand_total"], Decimal("-100"))
        self.assertEqual(parsed.data["import_off_peak_kwh"], Decimal("100"))

    def test_seed_data_migration_is_idempotent_and_uses_meter_numbers(self):
        numbers = (
            "260305510018", "260305510004", "260305510021",
            "260305510019", "260305510020",
        )
        groups = {}
        for number in numbers:
            meter = self._meter(number, Meter.MEASUREMENT_POINT_OTHER_AUDIT)
            groups[number] = MeterCheckGroup.objects.create(name=f"Group {number}", check_meter=meter)
        migration = importlib.import_module("smart_meter.migrations.0027_seed_energy_systems")
        from django.apps import apps

        migration.seed_energy_systems(apps, None)
        migration.seed_energy_systems(apps, None)

        seeded = EnergySystem.objects.filter(name__in=("Photon", "Tesla"))
        self.assertEqual(seeded.count(), 2)
        self.assertFalse(EnergySystem.objects.filter(name="H9").exists())
        self.assertEqual(UtilityConnection.objects.filter(energy_system__in=seeded).count(), 2)
        self.assertEqual(
            EnergySystemMeterAssignment.objects.filter(energy_system__in=seeded, end_date__isnull=True).count(),
            4,
        )
        meter_021 = Meter.objects.get(meter_number="260305510021")
        self.assertEqual(meter_021.measurement_point, Meter.MEASUREMENT_POINT_OTHER_AUDIT)
        self.assertIsNotNone(groups["260305510019"].__class__.objects.get(pk=groups["260305510019"].pk).superseded_by_energy_system_id)
        self.assertIsNotNone(groups["260305510020"].__class__.objects.get(pk=groups["260305510020"].pk).superseded_by_energy_system_id)
