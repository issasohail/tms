from datetime import date
from decimal import Decimal

from django.test import TestCase

from invoices.models import Invoice, ItemCategory, RecurringCharge
from invoices.services import run_monthly_billing_for as run_service_monthly_billing
from invoices.views import run_monthly_billing_for as run_view_monthly_billing
from leases.models import Lease
from leases.utils.billing import apply_initial_billing, preview_initial_billing
from properties.models import Property, Unit
from tenants.models import Tenant


def _first_of_next_month(value):
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


class FutureLeaseBillingTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(
            property_name="Future Billing Property",
            owner_name="Test Owner",
            owner_cnic="61101-3333333-3",
            type="Residential",
            property_type="Building",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="Future-1",
        )
        self.tenant = Tenant.objects.create(
            first_name="Future",
            last_name="Tenant",
            cnic="61101-4444444-4",
        )
        self.billing_month = date.today().replace(day=1)
        self.future_start = _first_of_next_month(self.billing_month)
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            agreement_date=self.future_start,
            start_date=self.future_start,
            end_date=date(self.future_start.year + 1, self.future_start.month, 1),
            monthly_rent=Decimal("25000.00"),
            society_maintenance=Decimal("1000.00"),
            agreement_charges=Decimal("0.00"),
            security_deposit=Decimal("0.00"),
            status="active",
        )

    def test_initial_billing_does_not_create_future_monthly_invoices(self):
        plan = preview_initial_billing(self.lease)
        self.assertEqual(plan["invoices_to_create"], [])

        apply_initial_billing(self.lease)

        self.assertFalse(
            Invoice.objects.filter(
                lease=self.lease,
                description__icontains="monthly",
            ).exists()
        )
        self.assertTrue(
            RecurringCharge.objects.filter(lease=self.lease, active=True).exists()
        )

    def test_monthly_billing_skips_lease_that_has_not_started(self):
        category = ItemCategory.objects.create(name="Future Global Charge")
        RecurringCharge.objects.create(
            kind="FIXED",
            scope="GLOBAL",
            category=category,
            description="Future guard",
            amount=Decimal("500.00"),
            start_date=self.billing_month,
            active=True,
        )

        for runner in (run_service_monthly_billing, run_view_monthly_billing):
            with self.subTest(runner=runner.__module__):
                runner(self.billing_month)
                self.assertFalse(Invoice.objects.filter(lease=self.lease).exists())
