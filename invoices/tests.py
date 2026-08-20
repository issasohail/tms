from datetime import date, timedelta
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
        self.category, _ = ItemCategory.objects.get_or_create(name="Rent")

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


class MonthlyBillingWhatsAppIdempotencyTests(TestCase):
    """Phase 1: one billing event sends only one WhatsApp message per invoice."""

    def setUp(self):
        from invoices.models import MonthlyBillingRun, MonthlyBillingRunItem

        self.tenant = Tenant.objects.create(
            first_name="Test",
            last_name="Tenant",
            cnic="12345-1234567-9",
            phone="03001112222",
        )
        self.property = Property.objects.create(
            property_name="Test Plaza",
            owner_name="Owner",
            owner_cnic="12345-1234567-8",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=self.property, unit_number="U-1")
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=10000,
        )
        self.invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 7, 1),
            due_date=date(2026, 7, 5),
        )
        self.run = MonthlyBillingRun.objects.create(billing_month=date(2026, 7, 1))
        self.item = MonthlyBillingRunItem.objects.create(
            billing_run=self.run,
            lease=self.lease,
            tenant=self.tenant,
            property=self.property,
            unit=self.unit,
            invoice=self.invoice,
            status=MonthlyBillingRunItem.STATUS_READY,
        )

    def _mock_service(self, mocked_service_cls, ok=True):
        mocked_service = mocked_service_cls.return_value
        mocked_service.send_invoice.return_value = {"ok": ok, "log_id": 999}
        return mocked_service

    def test_one_invoice_sends_one_whatsapp_message(self):
        from invoices.services import send_monthly_billing_ready

        with patch("whatsapp.services.whatsapp.WhatsAppService") as mocked_cls, \
             patch("invoices.services._monthly_invoice_pdf_bytes", return_value=b"%PDF-1.4"):
            self._mock_service(mocked_cls)
            send_monthly_billing_ready(self.run)
            self.assertEqual(mocked_cls.return_value.send_invoice.call_count, 1)

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "sent")

    def test_multiple_invoices_send_one_message_each(self):
        from invoices.models import MonthlyBillingRunItem

        tenant2 = Tenant.objects.create(
            first_name="Second",
            last_name="Tenant",
            cnic="12345-1234567-7",
            phone="03003334444",
        )
        unit2 = Unit.objects.create(property=self.property, unit_number="U-2")
        lease2 = Lease.objects.create(
            tenant=tenant2,
            unit=unit2,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=12000,
        )
        invoice2 = Invoice.objects.create(
            lease=lease2, issue_date=date(2026, 7, 1), due_date=date(2026, 7, 5)
        )
        MonthlyBillingRunItem.objects.create(
            billing_run=self.run,
            lease=lease2,
            tenant=tenant2,
            property=self.property,
            unit=unit2,
            invoice=invoice2,
            status=MonthlyBillingRunItem.STATUS_READY,
        )

        from invoices.services import send_monthly_billing_ready

        with patch("whatsapp.services.whatsapp.WhatsAppService") as mocked_cls, \
             patch("invoices.services._monthly_invoice_pdf_bytes", return_value=b"%PDF-1.4"):
            self._mock_service(mocked_cls)
            send_monthly_billing_ready(self.run)
            self.assertEqual(mocked_cls.return_value.send_invoice.call_count, 2)

    def test_duplicate_call_does_not_send_twice(self):
        """Simulates a duplicate POST / double-click triggering the same send twice."""
        from invoices.services import send_monthly_billing_ready

        with patch("whatsapp.services.whatsapp.WhatsAppService") as mocked_cls, \
             patch("invoices.services._monthly_invoice_pdf_bytes", return_value=b"%PDF-1.4"):
            self._mock_service(mocked_cls)
            send_monthly_billing_ready(self.run)
            send_monthly_billing_ready(self.run)
            self.assertEqual(mocked_cls.return_value.send_invoice.call_count, 1)

    def test_meta_status_update_does_not_trigger_new_send(self):
        """A delivery-status webhook updating WhatsAppMessageLog.status must never
        cause a subsequent billing run to re-send."""
        from whatsapp.models import WhatsAppMessageLog
        from invoices.services import send_monthly_billing_ready

        log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_OUTBOUND,
            invoice=self.invoice,
            phone_number=self.tenant.phone,
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_PDF,
            status=WhatsAppMessageLog.STATUS_SENT,
        )
        # Simulate a webhook moving it through delivered -> read.
        log.status = WhatsAppMessageLog.STATUS_DELIVERED
        log.save()
        log.status = WhatsAppMessageLog.STATUS_READ
        log.save()

        with patch("whatsapp.services.whatsapp.WhatsAppService") as mocked_cls, \
             patch("invoices.services._monthly_invoice_pdf_bytes", return_value=b"%PDF-1.4"):
            self._mock_service(mocked_cls)
            send_monthly_billing_ready(self.run)
            mocked_cls.return_value.send_invoice.assert_not_called()

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "sent")

    def test_failed_pre_acceptance_send_can_retry(self):
        from invoices.services import send_monthly_billing_ready
        from invoices.models import MonthlyBillingRunItem

        self.item.status = MonthlyBillingRunItem.STATUS_FAILED
        self.item.save()

        with patch("whatsapp.services.whatsapp.WhatsAppService") as mocked_cls, \
             patch("invoices.services._monthly_invoice_pdf_bytes", return_value=b"%PDF-1.4"):
            self._mock_service(mocked_cls)
            send_monthly_billing_ready(self.run, retry_failed=True)
            self.assertEqual(mocked_cls.return_value.send_invoice.call_count, 1)

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "sent")

    def test_manual_resend_still_works(self):
        from invoices.services import send_monthly_billing_item
        from invoices.models import MonthlyBillingRunItem

        self.item.status = MonthlyBillingRunItem.STATUS_FAILED
        self.item.save()

        with patch("whatsapp.services.whatsapp.WhatsAppService") as mocked_cls, \
             patch("invoices.services._monthly_invoice_pdf_bytes", return_value=b"%PDF-1.4"):
            self._mock_service(mocked_cls)
            send_monthly_billing_item(self.item)
            self.assertEqual(mocked_cls.return_value.send_invoice.call_count, 1)

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "sent")


class InvoiceLifecycleAndAccountingStatusTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from properties.models import Property, Unit
        from tenants.models import Tenant

        tenant = Tenant.objects.create(
            first_name="Status", last_name="Tenant", cnic="35202-1234567-1", phone="03001234567"
        )
        prop = Property.objects.create(
            property_name="Status Property", owner_name="Owner", owner_cnic="35202-7654321-1",
            type="Residential", property_type="apartment", total_units=1,
        )
        unit = Unit.objects.create(property=prop, unit_number="S-01")
        self.lease = Lease.objects.create(
            tenant=tenant, unit=unit, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            monthly_rent=Decimal("10000.00"), status="active",
        )
        self.user = get_user_model().objects.create_superuser(
            username="invoice-status-admin", email="status@example.com", password="test"
        )
        self.client.force_login(self.user)

    def test_lifecycle_change_is_audited_without_forging_payment_status(self):
        from django.urls import reverse
        from invoices.models import InvoiceStatusHistory

        invoice = Invoice.objects.create(
            lease=self.lease, issue_date=date(2026, 7, 1), due_date=date(2026, 7, 5),
            amount=Decimal("10000.00"), status="unpaid",
        )
        response = self.client.post(
            reverse("invoices:invoice_lifecycle_status", args=[invoice.pk]),
            {"lifecycle_status": "disputed", "reason": "Tenant queried the billed amount."},
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.lifecycle_status, "disputed")
        self.assertEqual(invoice.status, "unpaid")
        self.assertEqual(invoice.payment_status, "overdue")
        history = InvoiceStatusHistory.objects.get(invoice=invoice)
        self.assertEqual(history.previous_status, "issued")
        self.assertEqual(history.new_status, "disputed")
        self.assertEqual(history.ip_address, "127.0.0.1")

    def test_only_last_invoice_can_be_overpaid(self):
        from payments.models import Payment
        from payments.services.payment_detail import rebuild_payment_detail

        first = Invoice.objects.create(
            lease=self.lease, issue_date=date(2026, 7, 1), due_date=date(2026, 7, 5),
            amount=Decimal("1000.00"), status="sent",
        )
        second = Invoice.objects.create(
            lease=self.lease, issue_date=date(2026, 8, 1), due_date=date(2026, 8, 5),
            amount=Decimal("1000.00"), status="sent",
        )
        payment = Payment.objects.create(
            lease=self.lease, payment_date=date(2026, 8, 2), amount=Decimal("2500.00")
        )
        rebuild_payment_detail(
            payment=payment, lease_amount=Decimal("2500.00"), security_amount=Decimal("0.00"),
            reason="Invoice accounting status test",
        )
        self.assertEqual(first.payment_status, "paid")
        self.assertEqual(second.payment_status, "overpaid")

    def test_user_without_lifecycle_permission_cannot_change_status(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from invoices.models import InvoiceStatusHistory

        invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            amount=Decimal("1000.00"),
            status="sent",
        )
        ordinary_user = get_user_model().objects.create_user(
            username="invoice-status-ordinary",
            email="ordinary@example.com",
            password="test",
        )
        self.client.force_login(ordinary_user)

        response = self.client.post(
            reverse("invoices:invoice_lifecycle_status", args=[invoice.pk]),
            {"lifecycle_status": "disputed", "reason": "Should not be allowed."},
        )

        self.assertEqual(response.status_code, 403)
        invoice.refresh_from_db()
        self.assertEqual(invoice.lifecycle_status, "issued")
        self.assertFalse(InvoiceStatusHistory.objects.filter(invoice=invoice).exists())

    def test_paid_is_not_a_valid_lifecycle_status(self):
        from django.urls import reverse
        from invoices.models import InvoiceStatusHistory

        invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            amount=Decimal("1000.00"),
            status="sent",
        )

        response = self.client.post(
            reverse("invoices:invoice_lifecycle_status", args=[invoice.pk]),
            {"lifecycle_status": "paid", "reason": "Attempt to forge payment state."},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.lifecycle_status, "issued")
        self.assertFalse(InvoiceStatusHistory.objects.filter(invoice=invoice).exists())

    def test_reason_is_required_for_cancelled_status(self):
        from django.urls import reverse
        from invoices.models import InvoiceStatusHistory

        invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            amount=Decimal("1000.00"),
            status="sent",
        )

        response = self.client.post(
            reverse("invoices:invoice_lifecycle_status", args=[invoice.pk]),
            {"lifecycle_status": "cancelled", "reason": ""},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.lifecycle_status, "issued")
        self.assertFalse(InvoiceStatusHistory.objects.filter(invoice=invoice).exists())

    def test_cancelled_lifecycle_is_audited_without_mutating_legacy_status(self):
        from django.urls import reverse
        from invoices.models import InvoiceStatusHistory

        invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            amount=Decimal("1000.00"),
            status="sent",
        )

        response = self.client.post(
            reverse("invoices:invoice_lifecycle_status", args=[invoice.pk]),
            {"lifecycle_status": "cancelled", "reason": "Duplicate invoice."},
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.lifecycle_status, "cancelled")
        self.assertEqual(invoice.lifecycle_status_reason, "Duplicate invoice.")
        self.assertEqual(invoice.status, "sent")
        history = InvoiceStatusHistory.objects.get(invoice=invoice)
        self.assertEqual(history.previous_status, "issued")
        self.assertEqual(history.new_status, "cancelled")
        self.assertEqual(history.reason, "Duplicate invoice.")

    def test_same_lifecycle_status_and_reason_does_not_duplicate_history(self):
        from django.urls import reverse
        from invoices.models import InvoiceStatusHistory

        invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            amount=Decimal("1000.00"),
            status="sent",
        )
        url = reverse("invoices:invoice_lifecycle_status", args=[invoice.pk])
        payload = {"lifecycle_status": "disputed", "reason": "Tenant queried invoice."}

        self.client.post(url, payload)
        self.client.post(url, payload)

        self.assertEqual(
            InvoiceStatusHistory.objects.filter(invoice=invoice).count(),
            1,
        )

    def test_invoice_list_filters_by_lifecycle_status(self):
        from django.test import RequestFactory
        from invoices.views import InvoiceListView

        disputed = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            amount=Decimal("1000.00"),
            status="sent",
            lifecycle_status="disputed",
        )
        issued = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 2),
            due_date=date(2026, 8, 31),
            amount=Decimal("1000.00"),
            status="sent",
            lifecycle_status="issued",
        )

        view = InvoiceListView()
        view.request = RequestFactory().get("/", {"status": "disputed"})
        view.request.user = self.user
        ids = set(view.get_queryset().values_list("id", flat=True))

        self.assertIn(disputed.pk, ids)
        self.assertNotIn(issued.pk, ids)

    def test_invoice_list_paid_filter_uses_accounting_status_not_lifecycle(self):
        from django.test import RequestFactory
        from invoices.views import InvoiceListView

        paid = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            amount=Decimal("1000.00"),
            status="paid",
            lifecycle_status="issued",
        )
        unpaid = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 2),
            due_date=date(2026, 8, 31),
            amount=Decimal("1000.00"),
            status="sent",
            lifecycle_status="issued",
        )

        view = InvoiceListView()
        view.request = RequestFactory().get("/", {"status": "paid"})
        view.request.user = self.user
        ids = set(view.get_queryset().values_list("id", flat=True))

        self.assertIn(paid.pk, ids)
        self.assertNotIn(unpaid.pk, ids)

    def test_invoice_list_overdue_filter_includes_past_due_non_cancelled_invoice(self):
        from django.test import RequestFactory
        from django.utils import timezone
        from invoices.views import InvoiceListView

        today = timezone.localdate()
        overdue = Invoice.objects.create(
            lease=self.lease,
            issue_date=today - timedelta(days=5),
            due_date=today - timedelta(days=1),
            amount=Decimal("1000.00"),
            status="sent",
            lifecycle_status="issued",
        )
        not_overdue = Invoice.objects.create(
            lease=self.lease,
            issue_date=today,
            due_date=today + timedelta(days=1),
            amount=Decimal("1000.00"),
            status="sent",
            lifecycle_status="issued",
        )

        view = InvoiceListView()
        view.request = RequestFactory().get("/", {"status": "overdue"})
        view.request.user = self.user
        ids = set(view.get_queryset().values_list("id", flat=True))

        self.assertIn(overdue.pk, ids)
        self.assertNotIn(not_overdue.pk, ids)
