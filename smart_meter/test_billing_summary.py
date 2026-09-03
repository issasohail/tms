from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from invoices.models import Invoice, InvoiceItem, ItemCategory
from leases.models import Lease, LeaseUnitOccupancy
from properties.models import Property, Unit
from smart_meter.models import Meter, MeterInstallation
from smart_meter.views_dashboard import build_billing_summary_context
from tenants.models import Tenant


class BillingSummaryTenantSelectionTests(TestCase):
    report_month = "2026-09"

    def setUp(self):
        self.factory = RequestFactory()
        self.property = Property.objects.create(
            property_name="Billing Summary Property",
            owner_name="Owner",
            owner_cnic="1234512345600",
            type="apartment",
            property_type="apartment",
            total_units=10,
        )
        self.category = ItemCategory.objects.create(name="Billing Summary Rent")
        self._tenant_number = 0
        self._meter_number = 0

    def _tenant(self, first_name):
        self._tenant_number += 1
        return Tenant.objects.create(
            first_name=first_name,
            last_name="Tenant",
            cnic=f"123451234{self._tenant_number:04d}",
        )

    def _unit(self, number):
        unit = Unit.objects.create(property=self.property, unit_number=number)
        self._meter_number += 1
        meter = Meter.objects.create(
            meter_number=f"BILL-SUM-{self._meter_number}", unit=unit
        )
        MeterInstallation.objects.create(
            meter=meter,
            unit=unit,
            start_date=date(2026, 1, 1),
        )
        return unit

    def _lease(
        self,
        unit,
        tenant,
        *,
        status="active",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    ):
        return Lease.objects.create(
            tenant=tenant,
            unit=unit,
            status=status,
            start_date=start_date,
            end_date=end_date,
            monthly_rent=Decimal("1000.00"),
        )

    def _invoice(self, lease, amount="1000.00"):
        invoice = Invoice.objects.create(
            lease=lease,
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 10),
            amount=Decimal(amount),
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            category=self.category,
            description="Monthly charge",
            amount=Decimal(amount),
        )
        return invoice

    def _context(self, **params):
        query = {"month": self.report_month, **params}
        return build_billing_summary_context(
            self.factory.get("/smart-meter/reports/billing-summary/", query)
        )

    def _unit_rows(self, context, unit):
        return [
            row
            for group in context["groups"]
            for row in group["rows"]
            if row["unit"].pk == unit.pk
        ]

    def test_ended_lease_with_stale_occupancy_and_old_balance_is_vacant(self):
        unit = self._unit("Room 10")
        lease = self._lease(
            unit,
            self._tenant("Old"),
            status="ended",
            end_date=date(2026, 9, 30),
        )
        LeaseUnitOccupancy.objects.create(
            lease=lease,
            unit=unit,
            move_in_date=date(2026, 1, 1),
        )
        old_invoice = Invoice.objects.create(
            lease=lease,
            issue_date=date(2026, 5, 1),
            due_date=date(2026, 5, 10),
            amount=Decimal("500.00"),
        )
        InvoiceItem.objects.create(
            invoice=old_invoice,
            category=self.category,
            amount=Decimal("500.00"),
        )

        rows = self._unit_rows(self._context(), unit)

        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["lease"])
        self.assertEqual(rows[0]["tenant_name"], "Vacant")
        self.assertEqual(rows[0]["cells"][-2]["value"], Decimal("0.00"))

    def test_nonzero_month_invoice_shows_ended_lease(self):
        unit = self._unit("Room 01")
        lease = self._lease(
            unit,
            self._tenant("Invoiced"),
            status="ended",
            end_date=date(2026, 5, 31),
        )
        self._invoice(lease)

        rows = self._unit_rows(self._context(), unit)

        self.assertEqual([row["lease"].pk for row in rows], [lease.pk])
        self.assertEqual(rows[0]["tenant_name"], "Invoiced Tenant")

    def test_active_lease_in_previous_month_window_is_shown(self):
        unit = self._unit("Room 02")
        lease = self._lease(
            unit,
            self._tenant("Active"),
            status="active",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 8, 15),
        )

        rows = self._unit_rows(self._context(), unit)

        self.assertEqual([row["lease"].pk for row in rows], [lease.pk])

    def test_each_invoiced_lease_is_shown_during_turnover(self):
        unit = self._unit("Room 03")
        first = self._lease(
            unit,
            self._tenant("First"),
            status="ended",
            end_date=date(2026, 9, 10),
        )
        second = self._lease(
            unit,
            self._tenant("Second"),
            status="active",
            start_date=date(2026, 9, 11),
        )
        self._invoice(first, "400.00")
        self._invoice(second, "600.00")

        rows = self._unit_rows(self._context(), unit)

        self.assertEqual({row["lease"].pk for row in rows}, {first.pk, second.pk})

    def test_active_filter_keeps_invoiced_ended_lease_and_its_drilldown(self):
        unit = self._unit("Room 04")
        ended = self._lease(
            unit,
            self._tenant("Ended"),
            status="ended",
            end_date=date(2026, 5, 31),
        )
        invoice = self._invoice(ended)

        rows = self._unit_rows(self._context(active_only="1"), unit)

        self.assertEqual([row["lease"].pk for row in rows], [ended.pk])

        user = get_user_model().objects.create_superuser(
            username="billing-summary-filter-user",
            password="test-pass",
            email="billing-summary-filter@example.com",
        )
        self.client.force_login(user)
        response = self.client.get(
            reverse("smart_meter:billing_summary_items"),
            {
                "month": self.report_month,
                "scope": "unit",
                "unit_id": unit.pk,
                "category": "*all*",
                "active_only": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(str(invoice.pk), response.json()["html"])
        self.assertIn("Ended Tenant", response.json()["html"])

    def test_individual_balance_links_to_lease_ledger(self):
        user = get_user_model().objects.create_superuser(
            username="billing-summary-user",
            password="test-pass",
            email="billing-summary@example.com",
        )
        unit = self._unit("Room 05")
        lease = self._lease(unit, self._tenant("Ledger"))
        self._invoice(lease)
        self.client.force_login(user)

        response = self.client.get(
            reverse("smart_meter:billing_summary"), {"month": self.report_month}
        )

        self.assertEqual(response.status_code, 200)
        ledger_url = reverse("leases:lease_ledger_by_pk", args=[lease.pk])
        self.assertContains(
            response,
            f'href="{ledger_url}" title="Open Ledger Tenant ledger"',
        )
