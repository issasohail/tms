import json
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from expenses.models import Expense
from invoices.models import Invoice, InvoiceItem, ItemCategory, RecurringCharge
from leases.models import Lease
from properties.models import Property, Unit
from tenants.models import Tenant


class CategoryManagementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="category-admin",
            email="category-admin@example.com",
            password="test-pass",
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Category Test Property",
            owner_name="Owner",
            owner_cnic="12345-1234567-1",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="A-1",
        )
        self.tenant = Tenant.objects.create(
            first_name="Category",
            last_name="Tenant",
            cnic="12345-1234567-2",
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=Decimal("25000.00"),
        )
        self.source = ItemCategory.objects.create(name="Old Charge")
        self.target = ItemCategory.objects.create(name="Desired Charge")
        self.invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 10),
        )
        self.item = InvoiceItem.objects.create(
            invoice=self.invoice,
            category=self.source,
            description="Category test item",
            amount=Decimal("100.00"),
        )

    def test_item_count_links_to_category_invoice_details(self):
        list_response = self.client.get(reverse("invoices:category_list"))
        detail_url = reverse("invoices:category_detail", args=[self.source.pk])
        self.assertContains(list_response, detail_url)

        detail_response = self.client.get(detail_url)
        self.assertContains(detail_response, self.tenant.get_full_name())
        self.assertContains(detail_response, "Category Test Property - A-1")
        self.assertContains(detail_response, "Category test item")
        self.assertContains(
            detail_response,
            reverse("invoices:invoice_detail", args=[self.invoice.pk]),
        )

    def test_inline_update_accepts_name_and_status(self):
        response = self.client.post(
            reverse("invoices:category_inline_update", args=[self.source.pk]),
            data=json.dumps({"name": "renamed charge", "is_active": False}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "Renamed Charge")
        self.assertFalse(self.source.is_active)

    def test_merge_reassigns_dependencies_and_deactivates_source(self):
        recurring = RecurringCharge.objects.create(
            category=self.source,
            amount=Decimal("200.00"),
            start_date=date(2026, 1, 1),
        )
        expense = Expense.objects.create(
            property=self.property,
            unit=self.unit,
            category=self.source,
            amount=Decimal("300.00"),
            date=date(2026, 8, 2),
        )

        response = self.client.post(
            reverse("invoices:category_merge"),
            {
                "category_ids": [str(self.source.pk), str(self.target.pk)],
                "target_category": str(self.target.pk),
            },
        )

        self.assertRedirects(response, reverse("invoices:category_list"))
        self.source.refresh_from_db()
        self.item.refresh_from_db()
        recurring.refresh_from_db()
        expense.refresh_from_db()
        self.assertFalse(self.source.is_active)
        self.assertEqual(self.item.category, self.target)
        self.assertEqual(recurring.category, self.target)
        self.assertEqual(expense.category, self.target)
