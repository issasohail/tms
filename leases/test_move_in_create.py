from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from invoices.models import Invoice
from leases.forms import LeaseForm
from leases.models import Lease, LeaseUnitOccupancy
from leases.views import sync_lease_move_in_occupancy
from properties.models import Property, Unit
from tenants.models import Tenant


class LeaseMoveInCreateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="lease-move-in-user",
            email="lease-move-in@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.tenant = Tenant.objects.create(
            first_name="Move In",
            last_name="Tenant",
            cnic="61101-9100000-1",
            phone="03009100001",
        )
        self.property = Property.objects.create(
            property_name="Move In Property",
            owner_name="Test Owner",
            owner_cnic="61101-9100000-2",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="MI-1",
            monthly_rent=Decimal("30000.00"),
            society_maintenance=Decimal("0.00"),
            water_charges=Decimal("0.00"),
            internet_charges=Decimal("0.00"),
        )

    def create_data(self, **overrides):
        data = {
            "tenant": str(self.tenant.pk),
            "property": str(self.property.pk),
            "unit": str(self.unit.pk),
            "move_in_date": "2026-09-12",
            "start_date": "2026-09-12",
            "lease_months": "11",
            "monthly_rent": "30000.00",
            "society_maintenance": "0.00",
            "water_charges": "0.00",
            "internet_charges": "0.00",
            "agreement_charges": "0.00",
            "electricity_security_deposit": "0.00",
            "status": "active",
            "bill_recurring_charges": "on",
            "align_billing_to_month_start": "on",
            "create_move_in_proration": "on",
            "move_in_proration_mode": "exact",
            "family_members-TOTAL_FORMS": "0",
            "family_members-INITIAL_FORMS": "0",
            "family_members-MIN_NUM_FORMS": "0",
            "family_members-MAX_NUM_FORMS": "1000",
            "vehicles-TOTAL_FORMS": "0",
            "vehicles-INITIAL_FORMS": "0",
            "vehicles-MIN_NUM_FORMS": "0",
            "vehicles-MAX_NUM_FORMS": "1000",
            "qa-TOTAL": "0",
        }
        data.update(overrides)
        return data

    def test_fresh_create_form_has_empty_required_move_in_date(self):
        response = self.client.get(reverse("leases:lease_create"))

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIsNone(form["move_in_date"].value())
        self.assertTrue(form.fields["move_in_date"].required)

    def test_move_in_date_is_required_on_create(self):
        data = self.create_data()
        data.pop("move_in_date")

        response = self.client.post(reverse("leases:lease_create"), data)

        self.assertEqual(response.status_code, 200)
        self.assertIn("move_in_date", response.context["form"].errors)
        self.assertFalse(Lease.objects.exists())

    def test_restoration_guard_is_only_on_fresh_create(self):
        fresh = self.client.get(reverse("leases:lease_create"))
        self.assertContains(fresh, 'data-fresh-lease-create="1"')
        self.assertContains(fresh, 'autocomplete="off"')
        self.assertContains(fresh, "if (confirmField) confirmField.value = '0';")

        lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            agreement_date=date(2026, 9, 1),
            start_date=date(2026, 9, 1),
            end_date=date(2027, 7, 31),
            monthly_rent=Decimal("30000.00"),
            status="active",
        )
        LeaseUnitOccupancy.objects.create(
            lease=lease,
            unit=self.unit,
            move_in_date=date(2026, 9, 12),
        )
        editing = self.client.get(reverse("leases:lease_update", args=[lease.pk]))
        self.assertNotContains(editing, 'data-fresh-lease-create="1"')
        self.assertEqual(
            editing.context["form"]["move_in_date"].value(), date(2026, 9, 12)
        )

    def test_posted_move_in_survives_validation_and_preview_confirmation(self):
        invalid_data = self.create_data(monthly_rent="")
        invalid = self.client.post(reverse("leases:lease_create"), invalid_data)
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(invalid.context["form"]["move_in_date"].value(), "2026-09-12")
        self.assertNotContains(invalid, 'data-fresh-lease-create="1"')

        data = self.create_data()
        preview = self.client.post(reverse("leases:lease_create"), data)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.context["form"]["move_in_date"].value(), "2026-09-12")
        summary = preview.context["billing_plan"]["move_in_summary"]
        self.assertEqual(summary["physical_move_in_date"], date(2026, 9, 12))
        self.assertEqual(summary["regular_billing_start_date"], date(2026, 10, 1))
        self.assertTrue(summary["proration_will_create"])
        self.assertEqual(summary["proration_period_end"], date(2026, 9, 30))
        self.assertEqual(summary["proration_amount"], Decimal("19000.00"))
        self.assertContains(preview, "Physical Move-in Date")
        self.assertContains(preview, "Regular Billing Start Date")
        self.assertContains(preview, "Will be created")
        self.assertContains(preview, "2026-09-12 to 2026-09-30")

        data["confirm_billing"] = "1"
        confirmed = self.client.post(reverse("leases:lease_create"), data)
        self.assertEqual(confirmed.status_code, 302)
        lease = Lease.objects.get(tenant=self.tenant)
        self.assertEqual(lease.start_date, date(2026, 10, 1))
        self.assertEqual(
            lease.unit_occupancies.get(move_out_date__isnull=True).move_in_date,
            date(2026, 9, 12),
        )
        invoice = Invoice.objects.get(
            lease=lease, description__startswith="[MOVE_IN_PRORATION]"
        )
        self.assertEqual(invoice.amount, Decimal("19000.00"))

    def test_existing_lease_form_preserves_occupancy_move_in_date(self):
        lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            agreement_date=date(2026, 9, 1),
            start_date=date(2026, 9, 1),
            end_date=date(2027, 7, 31),
            monthly_rent=Decimal("30000.00"),
            status="active",
        )
        occupancy = LeaseUnitOccupancy.objects.create(
            lease=lease,
            unit=self.unit,
            move_in_date=date(2026, 9, 12),
        )

        form = LeaseForm(instance=lease)
        self.assertFalse(form.fields["move_in_date"].required)
        self.assertEqual(form["move_in_date"].value(), date(2026, 9, 12))
        sync_lease_move_in_occupancy(lease, form["move_in_date"].value())

        occupancy.refresh_from_db()
        self.assertEqual(occupancy.move_in_date, date(2026, 9, 12))
        self.assertEqual(lease.start_date, date(2026, 9, 1))
