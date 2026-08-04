from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from unittest.mock import call, patch

from invoices.models import Invoice, InvoiceItem, ItemCategory, RecurringCharge
from invoices.services import (
    _recurring_rules_for_lease,
    ensure_month_invoice,
    generate_monthly_billing_electric,
    invoice_due_date_from_lease,
    previous_month_start,
    run_monthly_billing_full,
    run_monthly_billing_preflight,
)
from leases.models import Lease
from properties.models import Property, Unit
from tenants.models import Tenant


class MonthlyBillingRegressionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            first_name="Test",
            last_name="Tenant",
            cnic="12345-1234567-1",
            phone="03000000000",
        )
        self.property = Property.objects.create(
            property_name="Test Plaza",
            owner_name="Owner",
            owner_cnic="12345-1234567-2",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="101",
            is_smart_meter=False,
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=Decimal("25000.00"),
            water_charges=Decimal("0.00"),
            status="active",
        )
        self.category = ItemCategory.objects.create(name="Rent")

    def test_previous_month_start_handles_year_boundary(self):
        self.assertEqual(previous_month_start(date(2026, 7, 1)), date(2026, 6, 1))
        self.assertEqual(previous_month_start(date(2026, 1, 1)), date(2025, 12, 1))

    def test_invoice_due_date_uses_lease_due_day(self):
        self.lease.due_date = "5th of each month."
        self.assertEqual(
            invoice_due_date_from_lease(self.lease, date(2026, 7, 1)),
            date(2026, 7, 5),
        )

    def test_invoice_due_date_clamps_to_month_end(self):
        self.lease.due_date = "31st of each month."
        self.assertEqual(
            invoice_due_date_from_lease(self.lease, date(2026, 2, 1)),
            date(2026, 2, 28),
        )

    def test_monthly_invoice_uses_lease_due_day(self):
        self.lease.due_date = "10th of each month."
        self.lease.save(update_fields=["due_date"])

        invoice = ensure_month_invoice(self.lease, date(2026, 7, 1))

        self.assertEqual(invoice.issue_date, date(2026, 7, 1))
        self.assertEqual(invoice.due_date, date(2026, 7, 10))

    def test_invoice_item_amount_rounds_up_to_nearest_10_on_save(self):
        invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 7, 1),
            due_date=date(2026, 7, 10),
        )

        item = InvoiceItem.objects.create(
            invoice=invoice,
            category=self.category,
            description="Rounded rent",
            amount=Decimal("25001.25"),
        )

        self.assertEqual(item.amount, Decimal("25010.00"))

    def test_invoice_total_uses_rounded_invoice_item_amounts(self):
        invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 7, 1),
            due_date=date(2026, 7, 10),
        )

        InvoiceItem.objects.create(
            invoice=invoice,
            category=self.category,
            description="Rent",
            amount=Decimal("25000.00"),
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            category=self.category,
            description="Electric",
            amount=Decimal("501.01"),
        )
        invoice.refresh_from_db()

        self.assertEqual(invoice.amount, Decimal("25510.00"))

    def test_recurring_rules_only_match_billing_month_window(self):
        future_rule = RecurringCharge.objects.create(
            lease=self.lease,
            category=self.category,
            amount=Decimal("25000.00"),
            start_date=date(2026, 8, 1),
            active=True,
        )
        july_rules = _recurring_rules_for_lease(self.lease, date(2026, 7, 1))
        self.assertNotIn(future_rule, list(july_rules))

        current_rule = RecurringCharge.objects.create(
            lease=self.lease,
            category=self.category,
            amount=Decimal("25000.00"),
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            active=True,
        )
        july_rules = _recurring_rules_for_lease(self.lease, date(2026, 7, 1))
        self.assertIn(current_rule, list(july_rules))

    def test_manual_electric_unit_is_not_marked_missing_meter(self):
        RecurringCharge.objects.create(
            lease=self.lease,
            category=self.category,
            amount=Decimal("25000.00"),
            start_date=date(2026, 1, 1),
            active=True,
        )

        run = run_monthly_billing_preflight(date(2026, 7, 1))
        item = run.items.get(lease=self.lease)

        self.assertTrue(item.manual_electric)
        self.assertFalse(item.electric_required)
        self.assertNotEqual(item.issue_code, item.ISSUE_METER_MISSING)

    def test_monthly_billing_electric_posts_previous_usage_to_current_invoice(self):
        self.unit.is_smart_meter = True
        self.unit.save(update_fields=["is_smart_meter"])
        RecurringCharge.objects.create(
            lease=self.lease,
            category=self.category,
            amount=Decimal("25000.00"),
            start_date=date(2026, 1, 1),
            active=True,
        )
        existing_invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 7, 1),
            due_date=date(2026, 7, 10),
            amount=Decimal("0.00"),
        )
        electric_category = ItemCategory.objects.create(name="Electric")
        run = run_monthly_billing_preflight(date(2026, 7, 1))
        item = run.items.get(lease=self.lease)
        item.status = item.STATUS_DRAFT
        item.electric_required = True
        item.electric_ready = True
        item.save()

        class FakeMeter:
            meter_number = "M-1"

        class FakeCtx:
            lease = self.lease
            meter = FakeMeter()
            period_start = date(2026, 6, 1)
            period_end = date(2026, 6, 30)
            beg_kwh = Decimal("10")
            end_kwh = Decimal("20")
            units = Decimal("10")
            unit_rate = Decimal("50")
            service_charges = Decimal("0")
            line_total = Decimal("500")
            billing_period_label = "2026-06-01 to 2026-06-30"
            description_text = "Meter#=M-1, Billing Period=2026-06-01 to 2026-06-30"

        from unittest.mock import patch

        with patch("smart_meter.models.MeterInstallation.objects") as mocked_installations, \
             patch("smart_meter.services.invoicing.compute_electric_bill", return_value=FakeCtx()), \
             patch("smart_meter.services.invoicing.upsert_invoice_with_electric_item") as mocked_upsert:
            mocked_installations.filter.return_value.filter.return_value.select_related.return_value = [
                SimpleNamespace(meter=FakeMeter())
            ]
            mocked_upsert.return_value = existing_invoice
            generate_monthly_billing_electric(run)

        mocked_upsert.assert_called()
        self.assertEqual(mocked_upsert.call_args.kwargs["posting_month"], date(2026, 7, 1))

    def test_full_monthly_billing_generates_pdfs_and_sends_ready_invoices(self):
        run = SimpleNamespace(billing_month=date(2026, 8, 1))
        created_by = SimpleNamespace(is_authenticated=True)

        with patch("invoices.services._run_log") as mocked_log, \
             patch("invoices.services.run_monthly_billing_preflight") as mocked_preflight, \
             patch("invoices.services.generate_monthly_billing_invoices") as mocked_invoices, \
             patch("invoices.services.generate_monthly_billing_electric") as mocked_electric, \
             patch("invoices.services.prepare_monthly_billing_ready") as mocked_ready, \
             patch("invoices.services.generate_monthly_billing_pdfs") as mocked_pdfs, \
             patch("invoices.services.send_monthly_billing_ready") as mocked_send:
            result = run_monthly_billing_full(run, created_by=created_by)

        self.assertIs(result, run)
        mocked_preflight.assert_called_once_with(
            run.billing_month, created_by=created_by, progress_callback=None
        )
        mocked_invoices.assert_called_once_with(run, progress_callback=None)
        mocked_electric.assert_called_once_with(run, progress_callback=None)
        mocked_ready.assert_called_once_with(run, progress_callback=None)
        mocked_pdfs.assert_called_once_with(run, progress_callback=None)
        mocked_send.assert_called_once_with(
            run, created_by=created_by, progress_callback=None
        )
        self.assertEqual(
            mocked_log.call_args_list,
            [call(run, "run billing started"), call(run, "run billing completed")],
        )
