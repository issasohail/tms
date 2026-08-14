from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from invoices.models import Invoice
from leases.models import Lease
from properties.models import Property, Unit
from tenants.models import Tenant


class InvoiceDeleteReturnFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="delete-filter-admin",
            email="delete-filter@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)
        property_obj = Property.objects.create(
            property_name="Delete Filter Property",
            owner_name="Test Owner",
            owner_cnic="61101-5555555-5",
            type="Residential",
            property_type="Building",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="D-1")
        tenant = Tenant.objects.create(
            first_name="Delete",
            last_name="Filter",
            cnic="61101-6666666-6",
        )
        lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=Decimal("10000.00"),
            status="active",
        )
        self.invoice = Invoice.objects.create(
            lease=lease,
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 5),
            amount=Decimal("10000.00"),
        )

    def test_delete_returns_to_filtered_invoice_list(self):
        return_to = reverse("invoices:invoice_list") + "?" + urlencode(
            {"property": "1", "status": "unpaid", "page": "2", "sort": "issue_date"}
        )
        delete_url = reverse("invoices:invoice_delete", args=[self.invoice.pk])

        response = self.client.post(delete_url + "?" + urlencode({"return_to": return_to}))

        self.assertRedirects(response, return_to, fetch_redirect_response=False)
        self.assertFalse(Invoice.objects.filter(pk=self.invoice.pk).exists())

    def test_external_return_url_is_rejected(self):
        delete_url = reverse("invoices:invoice_delete", args=[self.invoice.pk])

        response = self.client.post(
            delete_url + "?" + urlencode({"return_to": "https://example.com/steal"})
        )

        self.assertRedirects(
            response,
            reverse("invoices:invoice_list"),
            fetch_redirect_response=False,
        )
