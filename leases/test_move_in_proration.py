from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from invoices.models import Invoice, ItemCategory
from leases.models import Lease, LeaseUnitOccupancy
from leases.utils.billing import (
    MOVE_IN_PRORATION_MARKER,
    reconcile_move_in_proration,
)
from properties.models import Property, Unit
from tenants.models import Tenant


class MoveInProrationReconciliationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            first_name="Proration",
            last_name="Tenant",
            cnic="61101-9000000-1",
            phone="03009000001",
        )
        self.property = Property.objects.create(
            property_name="Proration Property",
            owner_name="Test Owner",
            owner_cnic="61101-9000000-2",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="P-1",
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            agreement_date=date(2026, 9, 12),
            start_date=date(2026, 10, 1),
            end_date=date(2027, 8, 31),
            monthly_rent=Decimal("30000.00"),
            society_maintenance=Decimal("0.00"),
            water_charges=Decimal("0.00"),
            internet_charges=Decimal("0.00"),
            status="active",
        )
        self.occupancy = LeaseUnitOccupancy.objects.create(
            lease=self.lease,
            unit=self.unit,
            move_in_date=date(2026, 9, 12),
        )

    def marker_invoices(self):
        return Invoice.objects.filter(
            lease=self.lease,
            description__startswith=MOVE_IN_PRORATION_MARKER,
        )

    def reconcile(self, **overrides):
        values = {
            "enabled": True,
            "mode": "exact",
            "manual_days": None,
        }
        values.update(overrides)
        return reconcile_move_in_proration(self.lease, **values)

    def test_sep_1_start_and_sep_1_move_in_has_no_proration(self):
        self.lease.start_date = date(2026, 9, 1)
        self.lease.save(update_fields=["start_date", "updated_at"])
        self.occupancy.move_in_date = date(2026, 9, 1)
        self.occupancy.save(update_fields=["move_in_date", "updated_at"])

        result = self.reconcile()

        self.assertEqual(result["action"], "none")
        self.assertFalse(self.marker_invoices().exists())

    def test_sep_1_start_and_sep_12_move_in_has_no_proration(self):
        self.lease.start_date = date(2026, 9, 1)
        self.lease.save(update_fields=["start_date", "updated_at"])

        result = self.reconcile()

        self.assertEqual(result["action"], "none")
        self.assertFalse(self.marker_invoices().exists())

    def test_oct_1_start_and_sep_12_move_in_creates_exact_19_of_30(self):
        result = self.reconcile()

        invoice = result["invoice"]
        self.assertEqual(result["action"], "created")
        self.assertEqual(invoice.issue_date, date(2026, 9, 12))
        self.assertEqual(invoice.due_date, date(2026, 9, 12))
        self.assertIn("2026-09-12 to 2026-09-30", invoice.description)
        item = invoice.items.get()
        self.assertIn("19/30 days; exact", item.description)
        self.assertEqual(item.amount, Decimal("19000.00"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount, Decimal("19000.00"))

    def test_changing_start_to_sep_1_cancels_editable_stale_proration(self):
        invoice = self.reconcile()["invoice"]
        self.lease.start_date = date(2026, 9, 1)
        self.lease.save(update_fields=["start_date", "updated_at"])

        result = self.reconcile()

        invoice.refresh_from_db()
        self.assertEqual(result["action"], "cancelled")
        self.assertEqual(invoice.status, "cancelled")
        self.assertEqual(invoice.lifecycle_status, "cancelled")

    def test_changing_move_in_to_sep_1_reconciles_invoice(self):
        invoice = self.reconcile()["invoice"]
        self.occupancy.move_in_date = date(2026, 9, 1)
        self.occupancy.save(update_fields=["move_in_date", "updated_at"])

        result = self.reconcile()

        invoice.refresh_from_db()
        self.assertEqual(result["action"], "updated")
        self.assertEqual(invoice.issue_date, date(2026, 9, 1))
        self.assertEqual(invoice.amount, Decimal("30000.00"))
        self.assertIn("2026-09-01 to 2026-09-30", invoice.description)

    def test_recurring_charge_changes_update_editable_items_with_rounding(self):
        invoice = self.reconcile()["invoice"]
        self.lease.monthly_rent = Decimal("31001.00")
        self.lease.society_maintenance = Decimal("1201.00")
        self.lease.water_charges = Decimal("601.00")
        self.lease.internet_charges = Decimal("301.00")
        self.lease.save(
            update_fields=[
                "monthly_rent",
                "society_maintenance",
                "water_charges",
                "internet_charges",
                "updated_at",
            ]
        )

        result = self.reconcile()

        invoice.refresh_from_db()
        self.assertEqual(result["action"], "updated")
        amounts = {
            item.category.name: item.amount for item in invoice.items.select_related("category")
        }
        self.assertEqual(amounts["Rent"], Decimal("19640.00"))
        self.assertEqual(amounts["Society Maintenance"], Decimal("770.00"))
        self.assertEqual(amounts["Water Charges"], Decimal("390.00"))
        self.assertEqual(amounts["Internet"], Decimal("200.00"))
        self.assertEqual(invoice.amount, Decimal("21000.00"))

    def test_repeated_reconciliation_is_idempotent(self):
        first = self.reconcile()
        second = self.reconcile()

        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "unchanged")
        self.assertEqual(self.marker_invoices().count(), 1)
        self.assertEqual(first["invoice"].pk, second["invoice"].pk)

    def test_locked_invoice_is_unchanged_and_returns_warning(self):
        invoice = self.reconcile()["invoice"]
        invoice.status = "sent"
        invoice.lifecycle_status = "issued"
        invoice.save(update_fields=["status", "lifecycle_status", "updated_at"])
        original = {
            "description": invoice.description,
            "issue_date": invoice.issue_date,
            "amount": invoice.amount,
            "items": list(invoice.items.values_list("description", "amount")),
        }
        self.lease.start_date = date(2026, 9, 1)
        self.lease.save(update_fields=["start_date", "updated_at"])

        result = self.reconcile()

        invoice.refresh_from_db()
        self.assertEqual(result["action"], "warning")
        self.assertTrue(result["warning"])
        self.assertEqual(invoice.description, original["description"])
        self.assertEqual(invoice.issue_date, original["issue_date"])
        self.assertEqual(invoice.amount, original["amount"])
        self.assertEqual(
            list(invoice.items.values_list("description", "amount")),
            original["items"],
        )

    def test_waive_cancels_editable_invoice(self):
        invoice = self.reconcile()["invoice"]

        result = self.reconcile(enabled=False, mode="waive")

        invoice.refresh_from_db()
        self.assertEqual(result["action"], "cancelled")
        self.assertEqual(invoice.status, "cancelled")

    def test_category_setup_does_not_duplicate_aliases(self):
        self.reconcile()
        self.reconcile()

        self.assertEqual(ItemCategory.objects.filter(name="Rent").count(), 1)

    def test_audit_command_defaults_to_dry_run_and_apply_is_explicit(self):
        invoice = self.reconcile()["invoice"]
        self.lease.start_date = date(2026, 9, 1)
        self.lease.save(update_fields=["start_date", "updated_at"])
        dry_output = StringIO()

        call_command(
            "audit_move_in_proration",
            lease_id=self.lease.pk,
            stdout=dry_output,
        )

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "draft")
        self.assertIn("mode: DRY-RUN", dry_output.getvalue())
        self.assertIn("action=would_cancel", dry_output.getvalue())
        apply_output = StringIO()
        call_command(
            "audit_move_in_proration",
            lease_id=self.lease.pk,
            apply=True,
            stdout=apply_output,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "cancelled")
        self.assertIn("mode: APPLY", apply_output.getvalue())
        self.assertIn("action=cancelled", apply_output.getvalue())
