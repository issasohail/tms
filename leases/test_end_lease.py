from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from invoices.models import Invoice, InvoiceItem, ItemCategory, SecurityDepositTransaction
from invoices.services import security_deposit_totals
from payments.forms import PaymentDetailForm
from payments.models import Payment
from payments.services.payment_detail import rebuild_payment_detail
from leases.models import Lease, LeaseUnitOccupancy
from leases.utils.end_lease import (
    ZERO,
    _billable_days,
    _proration_window,
    build_end_lease_preview,
    end_lease,
    money,
    move_out_charge_defaults,
    rollback_end_lease,
    tenant_message,
)
from properties.models import Property, Unit
from tenants.models import Tenant


class EndLeaseCalculationTests(SimpleTestCase):
    def test_signed_amounts_derive_payment_and_refund_types(self):
        positive = PaymentDetailForm(
            data={
                "payment_type": "LEASE_REFUND",
                "lease_amount": "100.00",
                "security_amount": "0.00",
                "security_type": "PAYMENT",
            },
            payment_total=Decimal("100.00"),
        )
        self.assertTrue(positive.is_valid(), positive.errors)
        self.assertEqual(positive.cleaned_data["payment_type"], "LEASE")

        negative = PaymentDetailForm(
            data={
                "payment_type": "LEASE_REFUND",
                "lease_amount": "-100.00",
                "security_amount": "0.00",
                "security_type": "PAYMENT",
            },
            payment_total=Decimal("-100.00"),
        )
        self.assertTrue(negative.is_valid(), negative.errors)
        self.assertEqual(negative.cleaned_data["payment_type"], "LEASE_REFUND")
        self.assertEqual(negative.cleaned_data["lease_amount"], Decimal("-100.00"))

        security_refund = PaymentDetailForm(
            data={
                "lease_amount": "0.00",
                "security_amount": "-100.00",
            },
            payment_total=Decimal("-100.00"),
        )
        self.assertTrue(security_refund.is_valid(), security_refund.errors)
        self.assertEqual(security_refund.cleaned_data["payment_type"], "REFUND")
        self.assertEqual(security_refund.cleaned_data["security_type"], "REFUND")
        self.assertEqual(security_refund.cleaned_data["security_amount"], Decimal("100.00"))

    def test_refund_payment_type_is_named_security_refund(self):
        form = PaymentDetailForm()
        self.assertIn(("REFUND", "Security Refund"), form.fields["payment_type"].choices)

    def test_proration_uses_inclusive_end_date(self):
        lease = SimpleNamespace(start_date=date(2026, 1, 1))
        self.assertEqual(_proration_window(lease, date(2026, 7, 13)), (13, 31))

    def test_proration_respects_midmonth_lease_start(self):
        lease = SimpleNamespace(start_date=date(2026, 7, 10))
        self.assertEqual(_proration_window(lease, date(2026, 7, 13)), (4, 31))

    def test_proration_rounds_up_to_configured_billing_block(self):
        self.assertEqual(_billable_days(13, 31, 1), 13)
        self.assertEqual(_billable_days(13, 31, 5), 15)
        self.assertEqual(_billable_days(13, 31, 7), 14)
        self.assertEqual(_billable_days(13, 31, 10), 20)
        self.assertEqual(_billable_days(13, 31, 15), 15)
        self.assertEqual(_billable_days(31, 31, 15), 31)

    def test_invalid_money_is_rejected(self):
        with self.assertRaises(ValidationError):
            money("not-an-amount")

    def test_move_out_charge_defaults_follow_building_type(self):
        lease = SimpleNamespace(
            unit=SimpleNamespace(
                interest_type=SimpleNamespace(
                    inspection_incomplete_charge=Decimal("7500.00"),
                    key_card_not_returned_charge=Decimal("1250.00"),
                ),
                inspection_incomplete_charge=Decimal("5000.00"),
                key_card_not_returned_charge=Decimal("1000.00"),
            )
        )
        defaults = move_out_charge_defaults(lease)
        self.assertEqual(defaults["inspection_charge"], Decimal("7500.00"))
        self.assertEqual(defaults["key_charge"], Decimal("1250.00"))

    def test_refund_message_requests_account_details(self):
        tenant = SimpleNamespace(get_full_name=lambda: "Test Tenant")
        lease = SimpleNamespace(tenant=tenant)
        result = {
            "lease": lease,
            "end_date": date(2026, 7, 13),
            "amount_payable": Decimal("0.00"),
            "refund_due": Decimal("1250.00"),
            "gross_balance": Decimal("-250.00"),
            "security_applied": Decimal("0.00"),
        }
        message = tenant_message(result)
        self.assertIn("account/IBAN", message)
        self.assertIn("Rs. 1,250.00", message)


class EndLeasePostingTests(TestCase):
    def test_rollback_action_reactivates_lease_and_reopens_end_lease(self):
        today = date.today()
        user = get_user_model().objects.create_superuser(
            username="lease-rollback-view", email="rollback-view@example.com", password="test-password"
        )
        property_obj = Property.objects.create(
            property_name="Rollback View Property",
            owner_name="Owner",
            owner_cnic="6110112345678",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="RV-1")
        tenant = Tenant.objects.create(
            first_name="Rollback", last_name="Tenant", cnic="6110112345679"
        )
        lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=today - timedelta(days=60),
            end_date=today,
            monthly_rent=Decimal("10000.00"),
            status="ended",
        )
        self.client.force_login(user)
        ended_detail = self.client.get(reverse("leases:lease_detail", args=[lease.pk]))
        self.assertEqual(ended_detail.status_code, 200)
        self.assertContains(ended_detail, "Correct / Re-do Lease End")
        self.assertContains(
            ended_detail,
            f'value="{(today + timedelta(days=1)).isoformat()}"',
            html=False,
        )
        response = self.client.post(
            reverse("leases:lease_end_rollback_action", args=[lease.pk]),
            {"restored_end_date": (today + timedelta(days=180)).isoformat(), "notes": "Correct end date"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("open_end_lease=1", response["Location"])
        lease.refresh_from_db()
        self.assertEqual(lease.status, "active")

        detail = self.client.get(response["Location"])
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "End Lease")
        self.assertNotContains(detail, "Correct / Re-do Lease End")

    def test_future_electricity_is_projected_then_transferred_only_once(self):
        today = date.today()
        month_first = today.replace(day=1)
        future_date = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        property_obj = Property.objects.create(
            property_name="Electric Transfer Property",
            owner_name="Owner",
            owner_cnic="6110112345691",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(
            property=property_obj, unit_number="E-1", is_smart_meter=False
        )
        tenant = Tenant.objects.create(
            first_name="Electric",
            last_name="Tenant",
            cnic="6110112345692",
        )
        lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=month_first,
            end_date=today + timedelta(days=180),
            monthly_rent=ZERO,
            society_maintenance=ZERO,
            water_charges=ZERO,
            internet_charges=ZERO,
            status="active",
        )
        future_invoice = Invoice.objects.create(
            lease=lease,
            issue_date=future_date,
            due_date=future_date,
            description=f"Monthly charges {future_date:%b %Y}",
        )
        electric_category = ItemCategory.objects.create(name="Electricity")
        InvoiceItem.objects.bulk_create(
            [
                InvoiceItem(
                    invoice=future_invoice,
                    category=electric_category,
                    description=(
                        f"Electric bill; Billing Period={month_first:%Y-%m-%d} "
                        f"to {today:%Y-%m-%d}"
                    ),
                    amount=Decimal("4654.00"),
                )
            ]
        )
        Invoice.objects.filter(pk=future_invoice.pk).update(amount=Decimal("4654.00"))

        preview = build_end_lease_preview(
            lease,
            end_date=today,
            future_invoice_action="cancel",
            inspection_complete=True,
            keys_returned=True,
        )
        self.assertEqual(preview["electricity_transfer_on_confirm"], Decimal("4654.00"))
        self.assertFalse(
            preview["invoice"].items.filter(category__name__istartswith="Electric").exists()
        )
        self.assertEqual(preview["gross_balance"], Decimal("4654.00"))

        result = end_lease(
            lease,
            end_date=today,
            future_invoice_action="cancel",
            inspection_complete=True,
            keys_returned=True,
        )
        future_invoice.refresh_from_db()
        result["invoice"].refresh_from_db()
        self.assertEqual(future_invoice.status, "cancelled")
        electric_items = result["invoice"].items.filter(
            category__name__istartswith="Electric"
        )
        self.assertEqual(electric_items.count(), 1)
        self.assertEqual(electric_items.get().amount, Decimal("4654.00"))
        self.assertEqual(result["invoice"].amount, Decimal("4654.00"))

    def test_posts_proration_applies_security_and_transfers_refund_to_ledger(self):
        today = date.today()
        month_first = today.replace(day=1)
        days_in_month = monthrange(today.year, today.month)[1]
        property_obj = Property.objects.create(
            property_name="Test Property",
            owner_name="Owner",
            owner_cnic="6110112345671",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="A-1", is_smart_meter=False)
        tenant = Tenant.objects.create(
            first_name="Test",
            last_name="Tenant",
            cnic="6110112345672",
            phone="03001234567",
        )
        lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=month_first,
            end_date=today + timedelta(days=180),
            monthly_rent=Decimal(days_in_month * 1000),
            society_maintenance=ZERO,
            water_charges=ZERO,
            internet_charges=ZERO,
            security_deposit=Decimal("100000.00"),
            status="active",
        )
        LeaseUnitOccupancy.objects.create(
            lease=lease,
            unit=unit,
            move_in_date=month_first,
        )
        invoice = Invoice.objects.create(
            lease=lease,
            issue_date=month_first,
            due_date=month_first,
            description=f"Monthly charges {month_first:%b %Y}",
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            category=ItemCategory.objects.get_or_create(name="Rent")[0],
            description=f"Rent {month_first:%b %Y}",
            amount=Decimal(days_in_month * 1000),
        )
        future_issue_date = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        future_invoice = Invoice.objects.create(
            lease=lease,
            issue_date=future_issue_date,
            due_date=future_issue_date,
            description=f"Monthly charges {future_issue_date:%b %Y}",
            amount=Decimal("5000.00"),
        )
        SecurityDepositTransaction.objects.create(
            lease=lease,
            type="PAYMENT",
            amount=Decimal("100000.00"),
        )

        cancel_preview = build_end_lease_preview(
            lease,
            end_date=today,
            final_electric_amount="1000.00",
            other_amount="0",
            future_invoice_action="cancel",
        )
        keep_preview = build_end_lease_preview(
            lease,
            end_date=today,
            final_electric_amount="1000.00",
            other_amount="0",
            future_invoice_action="keep",
        )
        self.assertEqual(cancel_preview["future_invoices"], [future_invoice])
        self.assertEqual(
            keep_preview["gross_balance"] - cancel_preview["gross_balance"],
            Decimal("5000.00"),
        )

        result = end_lease(
            lease,
            end_date=today,
            final_electric_amount="1000.00",
            other_amount="0",
        )

        lease.refresh_from_db()
        future_invoice.refresh_from_db()
        self.assertEqual(lease.status, "ended")
        self.assertEqual(future_invoice.status, "cancelled")
        self.assertIn("Original amount: Rs. 5,000.00", future_invoice.notes)
        self.assertEqual(lease.end_date, today)
        expected_gross = Decimal(result["billable_days"] * 1000 + 1000 + 5000 + 1000)
        self.assertEqual(result["gross_balance"], expected_gross)
        self.assertEqual(result["amount_payable"], ZERO)
        self.assertEqual(result["refund_due"], Decimal("100000.00") - expected_gross)
        transfer = SecurityDepositTransaction.objects.get(
            lease=lease,
            type="REFUND",
            refund_status="PAID",
            deduction_reason="Transferred to lease ledger for tenant refund",
        )
        self.assertEqual(transfer.amount, ZERO)
        self.assertEqual(transfer.deduction_amount, result["refund_due"])
        self.assertEqual(transfer.payment.detail.lease_amount, result["refund_due"])
        self.assertEqual(security_deposit_totals(lease)["currently_held"], ZERO)
        self.assertEqual(
            LeaseUnitOccupancy.objects.get(lease=lease).move_out_date,
            today,
        )
        self.assertEqual(
            Lease.objects.get(pk=lease.pk).financial_summary["balance"],
            -result["refund_due"],
        )

        rollback = rollback_end_lease(
            lease,
            restored_end_date=today + timedelta(days=180),
            notes="Test rollback",
        )
        lease.refresh_from_db()
        future_invoice.refresh_from_db()
        result["invoice"].refresh_from_db()
        self.assertEqual(lease.status, "active")
        self.assertEqual(future_invoice.status, "sent")
        self.assertEqual(result["invoice"].status, "cancelled")
        self.assertEqual(rollback["restored_end_date"], today + timedelta(days=180))
        self.assertFalse(
            SecurityDepositTransaction.objects.filter(
                lease=lease, type="REFUND"
            ).exclude(refund_status="CANCELLED").exists()
        )


class EndLeaseRefundAndReviewTests(TestCase):
    def setUp(self):
        self.today = date.today()
        self.month_first = self.today.replace(day=1)
        self.user = get_user_model().objects.create_superuser(
            username="settlement-review-tester",
            email="settlement-review@example.com",
            password="test-password",
        )
        property_obj = Property.objects.create(
            property_name="Settlement Review Property",
            owner_name="Owner",
            owner_cnic="6110112345683",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=property_obj, unit_number="S-1")
        self.tenant = Tenant.objects.create(
            first_name="Settlement",
            last_name="Tenant",
            cnic="6110112345684",
        )
        self.client.force_login(self.user)

    def make_lease(self, *, security_deposit=ZERO):
        return Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=self.month_first,
            end_date=self.today + timedelta(days=180),
            monthly_rent=ZERO,
            society_maintenance=ZERO,
            water_charges=ZERO,
            internet_charges=ZERO,
            security_deposit=security_deposit,
            status="active",
        )

    def test_transfers_security_to_ledger_with_existing_lease_credit(self):
        lease = self.make_lease(security_deposit=Decimal("5000.00"))
        SecurityDepositTransaction.objects.create(
            lease=lease,
            type="PAYMENT",
            amount=Decimal("5000.00"),
        )
        credit_payment = Payment.objects.create(
            lease=lease,
            payment_date=self.today,
            amount=Decimal("2000.00"),
            description="Tenant overpayment",
        )
        rebuild_payment_detail(
            payment=credit_payment,
            lease_amount=Decimal("2000.00"),
            security_amount=ZERO,
            reason="Test lease credit",
        )

        result = end_lease(
            lease,
            end_date=self.today,
            inspection_complete=True,
            keys_returned=True,
        )

        self.assertEqual(result["security_refund"], Decimal("5000.00"))
        self.assertEqual(result["lease_credit"], Decimal("2000.00"))
        self.assertEqual(result["refund_due"], Decimal("7000.00"))
        transfers = SecurityDepositTransaction.objects.filter(
            lease=lease,
            type="REFUND",
            refund_status="PAID",
            deduction_reason="Transferred to lease ledger for tenant refund",
        )
        self.assertEqual(transfers.count(), 1)
        transfer = transfers.get()
        self.assertEqual(transfer.amount, ZERO)
        self.assertEqual(transfer.deduction_amount, result["security_refund"])
        self.assertEqual(transfer.payment.detail.lease_amount, result["security_refund"])
        self.assertEqual(security_deposit_totals(lease)["currently_held"], ZERO)
        self.assertEqual(
            Lease.objects.get(pk=lease.pk).financial_summary["balance"],
            -result["refund_due"],
        )
        Invoice.objects.create(
            lease=lease,
            issue_date=self.today + timedelta(days=1),
            due_date=self.today + timedelta(days=1),
            amount=Decimal("900.00"),
            status="cancelled",
            description="Cancelled future charge retained for audit",
        )

        ledger = self.client.get(
            reverse("leases:lease_ledger_by_pk", args=[lease.pk])
        )
        expected_query = (
            f"lease={lease.pk}&amp;payment_type=LEASE_REFUND&amp;amount=7000.00"
        )
        self.assertContains(ledger, expected_query)
        payment_form = self.client.get(
            reverse("payments:payment_create"),
            {
                "lease": lease.pk,
                "payment_type": "LEASE_REFUND",
                "amount": "7000.00",
            },
        )
        self.assertEqual(str(payment_form.context["form"]["amount"].value()), "-7000.00")
        detail_form = payment_form.context["payment_detail_form"]
        self.assertEqual(detail_form["payment_type"].value(), "LEASE_REFUND")
        self.assertEqual(str(detail_form["lease_amount"].value()), "-7000.00")
        self.assertEqual(str(detail_form["security_amount"].value()), "0.00")

        refund_payment = Payment.objects.create(
            lease=lease,
            payment_date=self.today,
            amount=Decimal("-7000.00"),
            description="Tenant settlement refund",
        )
        rebuild_payment_detail(
            payment=refund_payment,
            lease_amount=Decimal("-7000.00"),
            security_amount=ZERO,
            reason="Test ledger credit refund payout",
        )
        self.assertEqual(
            SecurityDepositTransaction.objects.filter(
                lease=lease, type="REFUND"
            ).count(),
            1,
        )
        self.assertEqual(
            Lease.objects.get(pk=lease.pk).financial_summary["balance"], ZERO
        )

    def test_prior_outstanding_invoice_is_in_settlement_review(self):
        lease = self.make_lease()
        prior_date = self.month_first - timedelta(days=1)
        prior_invoice = Invoice.objects.create(
            lease=lease,
            issue_date=prior_date,
            due_date=prior_date,
            amount=Decimal("3000.00"),
            status="overdue",
            description=f"Outstanding charges {prior_date:%b %Y}",
        )

        result = build_end_lease_preview(
            lease,
            end_date=self.today,
            inspection_complete=True,
            keys_returned=True,
        )

        self.assertIn(prior_invoice, result["review_invoices"])
        listed_total = sum(
            (invoice.amount or ZERO for invoice in result["review_invoices"]),
            ZERO,
        )
        self.assertEqual(listed_total, result["gross_balance"])

    def test_fully_covered_stale_status_invoices_are_not_reviewed(self):
        lease = self.make_lease()
        prior_date = self.month_first - timedelta(days=2)
        draft_invoice = Invoice.objects.create(
            lease=lease,
            issue_date=prior_date,
            due_date=prior_date,
            amount=Decimal("1250.00"),
            status="draft",
            description="Older draft charges",
        )
        overdue_invoice = Invoice.objects.create(
            lease=lease,
            issue_date=prior_date + timedelta(days=1),
            due_date=prior_date + timedelta(days=1),
            amount=Decimal("1750.00"),
            status="overdue",
            description="Older overdue charges",
        )
        allocated_payment = Payment.objects.create(
            lease=lease,
            payment_date=self.today,
            amount=Decimal("1500.00"),
            description="Part lease and part security",
        )
        rebuild_payment_detail(
            payment=allocated_payment,
            lease_amount=Decimal("1250.00"),
            security_amount=Decimal("250.00"),
            reason="Test lease allocation",
        )
        Payment.objects.create(
            lease=lease,
            payment_date=self.today,
            amount=Decimal("1750.00"),
            description="Legacy lease payment without detail",
        )

        result = build_end_lease_preview(
            lease,
            end_date=self.today,
            inspection_complete=True,
            keys_returned=True,
        )

        self.assertNotIn(draft_invoice, result["review_invoices"])
        self.assertNotIn(overdue_invoice, result["review_invoices"])
        self.assertEqual(result["gross_balance"], ZERO)
        self.assertEqual(result["amount_payable"], ZERO)

        from django.apps import apps
        from importlib import import_module

        migration = import_module(
            "invoices.migrations.0022_reconcile_fully_paid_invoice_statuses"
        )
        migration.reconcile_fully_paid_invoice_statuses(apps, None)
        draft_invoice.refresh_from_db()
        overdue_invoice.refresh_from_db()
        self.assertEqual(draft_invoice.status, "paid")
        self.assertEqual(overdue_invoice.status, "paid")

    def test_zero_prior_balance_hides_fully_covered_prior_rows(self):
        lease = self.make_lease()
        prior_date = self.month_first - timedelta(days=1)
        prior_invoice = Invoice.objects.create(
            lease=lease,
            issue_date=prior_date,
            due_date=prior_date,
            amount=Decimal("1000.00"),
            status="sent",
            description="Stale prior charge",
        )
        Payment.objects.create(
            lease=lease,
            payment_date=self.today,
            amount=Decimal("1000.00"),
            description="Prior charges paid",
        )

        result = build_end_lease_preview(
            lease,
            end_date=self.today,
            other_amount=Decimal("750.00"),
            inspection_complete=True,
            keys_returned=True,
        )

        prior_review_rows = [
            invoice
            for invoice in result["review_invoices"]
            if invoice.issue_date < result["billing_month_start"]
        ]
        self.assertEqual(prior_review_rows, [])
        self.assertEqual(result["gross_balance"], Decimal("750.00"))

    def test_cancelled_future_invoice_is_not_in_settlement_review(self):
        lease = self.make_lease()
        future_date = self.today + timedelta(days=1)
        cancelled_invoice = Invoice.objects.create(
            lease=lease,
            issue_date=future_date,
            due_date=future_date,
            amount=Decimal("2500.00"),
            status="cancelled",
            description="Previously cancelled future charge",
        )

        result = build_end_lease_preview(
            lease,
            end_date=self.today,
            inspection_complete=True,
            keys_returned=True,
        )

        self.assertNotIn(cancelled_invoice, result["review_invoices"])
        self.assertNotIn(cancelled_invoice, result["future_invoices"])

    def test_paid_prior_rows_do_not_change_review_financials_or_confirmation(self):
        lease = self.make_lease(security_deposit=Decimal("5000.00"))
        end_date = self.month_first
        prior_date = self.month_first - timedelta(days=1)
        prior_invoice = Invoice.objects.create(
            lease=lease,
            issue_date=prior_date,
            due_date=prior_date,
            amount=Decimal("3000.00"),
            status="overdue",
            description="Stale prior charges",
        )
        Payment.objects.create(
            lease=lease,
            payment_date=self.today,
            amount=Decimal("3000.00"),
            description="Prior charges paid",
        )
        future_date = (self.today.replace(day=28) + timedelta(days=4)).replace(day=1)
        future_invoice = Invoice.objects.create(
            lease=lease,
            issue_date=future_date,
            due_date=future_date,
            amount=Decimal("4000.00"),
            status="sent",
            description=f"Monthly charges {future_date:%b %Y}",
        )
        SecurityDepositTransaction.objects.create(
            lease=lease,
            type="PAYMENT",
            amount=Decimal("5000.00"),
        )

        preview = build_end_lease_preview(
            lease,
            end_date=end_date,
            other_amount=Decimal("1200.00"),
            future_invoice_action="cancel",
            inspection_complete=True,
            keys_returned=True,
        )

        self.assertNotIn(prior_invoice, preview["review_invoices"])
        self.assertIn(future_invoice, preview["review_invoices"])
        self.assertIn(preview["final_period_invoice"], preview["review_invoices"])
        self.assertIn(preview["invoice"], preview["review_invoices"])
        self.assertEqual(preview["gross_balance"], Decimal("1200.00"))
        self.assertEqual(preview["security_held"], Decimal("5000.00"))
        self.assertEqual(preview["security_applied"], Decimal("1200.00"))
        self.assertEqual(preview["security_refund"], Decimal("3800.00"))
        self.assertEqual(preview["amount_payable"], ZERO)

        result = end_lease(
            lease,
            end_date=end_date,
            other_amount=Decimal("1200.00"),
            future_invoice_action="cancel",
            inspection_complete=True,
            keys_returned=True,
        )

        lease.refresh_from_db()
        prior_invoice.refresh_from_db()
        future_invoice.refresh_from_db()
        result["invoice"].refresh_from_db()
        self.assertEqual(lease.status, "ended")
        self.assertEqual(prior_invoice.status, "overdue")
        self.assertEqual(future_invoice.status, "cancelled")
        self.assertEqual(result["invoice"].status, "sent")
        self.assertEqual(result["gross_balance"], Decimal("1200.00"))
        self.assertEqual(result["security_applied"], Decimal("1200.00"))
        self.assertEqual(result["security_refund"], Decimal("3800.00"))
        self.assertEqual(result["amount_payable"], ZERO)
        self.assertEqual(result["final_balance"], Decimal("-3800.00"))


class SecurityRefundLinkTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="refund-tester",
            email="refund@example.com",
            password="test-password",
        )
        property_obj = Property.objects.create(
            property_name="Refund Property",
            owner_name="Owner",
            owner_cnic="6110112345681",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="R-1")
        tenant = Tenant.objects.create(
            first_name="Refund",
            last_name="Tenant",
            cnic="6110112345682",
        )
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            monthly_rent=Decimal("10000.00"),
            society_maintenance=ZERO,
            security_deposit=Decimal("15300.00"),
        )
        SecurityDepositTransaction.objects.create(
            lease=self.lease,
            type="PAYMENT",
            amount=Decimal("15300.00"),
        )
        self.client.force_login(self.user)

    def test_security_refund_link_opens_prefilled_payment_form(self):
        ledger = self.client.get(
            reverse("leases:lease_security_list", args=[self.lease.pk])
        )
        expected_query = (
            f"lease={self.lease.pk}&amp;payment_type=REFUND&amp;amount=15300.00"
            "&amp;security_amount=15300.00&amp;security_type=REFUND"
        )
        self.assertContains(ledger, expected_query)

        payment_form = self.client.get(
            reverse("payments:payment_create"),
            {
                "lease": self.lease.pk,
                "payment_type": "REFUND",
                "amount": "15300.00",
                "security_amount": "15300.00",
                "security_type": "REFUND",
            },
        )
        self.assertEqual(payment_form.status_code, 200)
        self.assertEqual(str(payment_form.context["form"]["amount"].value()), "-15300.00")
        self.assertEqual(
            str(payment_form.context["form"]["lease"].value()), str(self.lease.pk)
        )
        detail_form = payment_form.context["payment_detail_form"]
        self.assertEqual(detail_form["payment_type"].value(), "REFUND")
        self.assertEqual(str(detail_form["lease_amount"].value()), "0.00")
        self.assertEqual(str(detail_form["security_amount"].value()), "-15300.00")
        self.assertEqual(detail_form["security_type"].value(), "REFUND")

    def test_posting_refund_payment_completes_existing_pending_refund(self):
        pending = SecurityDepositTransaction.objects.create(
            lease=self.lease,
            type="REFUND",
            amount=Decimal("15300.00"),
            refund_status="PENDING",
            notes="Pending security refund; awaiting tenant account details.",
        )
        payment = Payment.objects.create(
            lease=self.lease,
            payment_date=date.today(),
            amount=Decimal("-15300.00"),
            description="Security refund",
        )
        rebuild_payment_detail(
            payment=payment,
            lease_amount=ZERO,
            security_amount=Decimal("15300.00"),
            security_type="REFUND",
            user=self.user,
            reason="Refund paid",
        )
        pending.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(pending.refund_status, "PAID")
        self.assertEqual(payment.amount, Decimal("-15300.00"))
        self.assertEqual(pending.payment_id, payment.pk)
        self.assertEqual(
            SecurityDepositTransaction.objects.filter(
                lease=self.lease, type="REFUND"
            ).count(),
            1,
        )
        self.assertEqual(
            security_deposit_totals(self.lease)["currently_held"], ZERO
        )

    def test_ended_lease_can_transfer_legacy_pending_refund_to_ledger(self):
        self.lease.status = "ended"
        self.lease.end_date = date.today()
        self.lease.save(update_fields=["status", "end_date"])
        pending = SecurityDepositTransaction.objects.create(
            lease=self.lease,
            type="REFUND",
            amount=Decimal("15300.00"),
            refund_status="PENDING",
            notes="Pending tenant refund; awaiting tenant account details.",
        )

        response = self.client.post(
            reverse("leases:lease_transfer_pending_security", args=[self.lease.pk])
        )

        self.assertRedirects(
            response,
            reverse("leases:lease_ledger_by_pk", args=[self.lease.pk]),
        )
        pending.refresh_from_db()
        self.assertEqual(pending.refund_status, "CANCELLED")
        transfer = SecurityDepositTransaction.objects.get(
            lease=self.lease,
            refund_status="PAID",
            deduction_reason="Transferred to lease ledger for tenant refund",
        )
        self.assertEqual(transfer.deduction_amount, Decimal("15300.00"))
        self.assertEqual(transfer.payment.detail.lease_amount, Decimal("15300.00"))
        self.assertEqual(security_deposit_totals(self.lease)["currently_held"], ZERO)
        self.assertEqual(
            Lease.objects.get(pk=self.lease.pk).financial_summary["balance"],
            Decimal("-15300.00"),
        )
