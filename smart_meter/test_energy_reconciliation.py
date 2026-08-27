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

from invoices.models import Invoice, InvoiceItem, ItemCategory
from leases.models import Lease
from payments.models import Payment, PaymentDetail
from properties.models import Property, Unit
from smart_meter.models import (
    EnergyReconciliationAuditEvent,
    EnergySystem,
    EnergySystemMeterAssignment,
    InverterPeriodStatement,
    Meter,
    MeterCheckGroup,
    MeterCheckGroupMembership,
    MeterReading,
    UtilityBillCycle,
    UtilityBillPayment,
    UtilityConnection,
)
from smart_meter.services.reconciliation import (
    PV_RESIDUAL_LABEL,
    build_energy_reconciliation,
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

    def test_financials_use_invoice_items_explicit_payment_allocation_and_utility_payments(self):
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
        self.assertEqual(report["current_cycle_utility_cost"], Decimal("420"))
        self.assertEqual(report["operating_energy_margin"], Decimal("80"))
        self.assertEqual(report["cash_position"], Decimal("50"))
        self.assertEqual(report["tenant_outstanding"], Decimal("200"))

    def test_cash_position_is_withheld_when_utility_payment_is_not_recorded(self):
        self._bill()
        report = build_energy_reconciliation(self.system, self.start, self.end)
        self.assertIsNone(report["utility_amount_paid"])
        self.assertIsNone(report["cash_position"])

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

        seeded = EnergySystem.objects.filter(name__in=("Photon", "Tesla", "H9"))
        self.assertEqual(seeded.count(), 3)
        self.assertEqual(UtilityConnection.objects.filter(energy_system__in=seeded).count(), 3)
        self.assertEqual(
            EnergySystemMeterAssignment.objects.filter(energy_system__in=seeded, end_date__isnull=True).count(),
            5,
        )
        self.assertIsNotNone(groups["260305510019"].__class__.objects.get(pk=groups["260305510019"].pk).superseded_by_energy_system_id)
        self.assertIsNotNone(groups["260305510020"].__class__.objects.get(pk=groups["260305510020"].pk).superseded_by_energy_system_id)
