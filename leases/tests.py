from unittest.mock import Mock

from django.test import TestCase


class AuthorizedOccupantsPlaceholderTests(TestCase):
    def test_registry_contains_new_placeholders(self):
        from leases.utils.utils import PLACEHOLDER_REGISTRY

        self.assertIn("authorized_occupants_table", PLACEHOLDER_REGISTRY)
        self.assertIn("authorized_occupants_names", PLACEHOLDER_REGISTRY)
        self.assertIn("authorized_occupants_count", PLACEHOLDER_REGISTRY)

    def test_table_includes_primary_tenant_and_three_column_layout(self):
        from leases.utils.utils import authorized_occupants_table

        tenant = Mock(cnic="61101-1234567-1")
        tenant.get_full_name.return_value = "Primary Tenant"
        manager = Mock()
        manager.select_related.return_value.filter.return_value.__iter__ = lambda self: (
            iter([])
        )
        lease = Mock(tenant=tenant, family_members=manager)
        html = authorized_occupants_table(lease)
        self.assertIn("Primary Tenant", html)
        self.assertIn("61101-1234567-1", html)
        self.assertEqual(html.count('class="occupant-card"'), 3)
        self.assertNotIn("N/A", html)

    def test_double_curly_placeholder_is_replaced(self):
        from leases.utils.utils import do_replace_placeholders

        tenant = Mock(cnic="1")
        tenant.get_full_name.return_value = "Tenant One"
        manager = Mock()
        manager.select_related.return_value.filter.return_value.__iter__ = lambda self: (
            iter([])
        )
        lease = Mock(tenant=tenant, family_members=manager)
        rendered = do_replace_placeholders("{{authorized_occupants_table}}", lease)
        self.assertIn("Tenant One", rendered)
        self.assertNotIn("{{authorized_occupants_table}}", rendered)


class AgreementPartyAjaxTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        self.user = get_user_model().objects.create_user(username="party-editor", password="x")
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="tenants", codename="add_tenant"),
            Permission.objects.get(content_type__app_label="leases", codename="add_lease"),
        )
        self.client.force_login(self.user)

    def test_quick_add_party_creates_tenant(self):
        from django.urls import reverse
        from tenants.models import Tenant
        response = self.client.post(reverse("leases:create_agreement_party_ajax"), {
            "prefix": "Mr.", "first_name": "Business", "relation": "S/O.",
            "last_name": "Partner", "cnic": "42101-1234567-1", "phone": "03001234567",
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "Business Partner")
        self.assertEqual(payload["cnic_display"], "42101-1234567-1")
        self.assertTrue(Tenant.objects.filter(pk=payload["id"], cnic_digits="4210112345671").exists())

    def test_quick_add_party_reuses_existing_cnic(self):
        from django.urls import reverse
        from tenants.models import Tenant
        existing = Tenant.objects.create(first_name="Existing", last_name="Person", cnic="42101-7654321-1")
        response = self.client.post(reverse("leases:create_agreement_party_ajax"), {
            "first_name": "Other", "last_name": "Name", "cnic": "4210176543211",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], existing.pk)
        self.assertFalse(response.json()["created"])


class LeaseHistoryWitnessSelectTests(TestCase):
    def test_both_witness_fields_use_quick_add_select2_class(self):
        from leases.forms_renewal import LeaseHistoryEditForm

        form = LeaseHistoryEditForm()

        for field_name in ("witness1_tenant", "witness2_tenant"):
            classes = form.fields[field_name].widget.attrs.get("class", "").split()
            self.assertIn("select2", classes)
            self.assertIn("witness-select", classes)

    def test_witnesses_are_ordered_and_show_limited_name_cnic_phone(self):
        from leases.forms_renewal import LeaseHistoryEditForm
        from tenants.models import Tenant

        Tenant.objects.create(
            first_name="Zulu", last_name="Witness",
            cnic="42101-2222222-2", phone="03002222222",
        )
        alpha = Tenant.objects.create(
            first_name="Alexanderthegreat", last_name="Witness",
            cnic="42101-1111111-1", phone="03001111111",
        )

        form = LeaseHistoryEditForm()
        field = form.fields["witness1_tenant"]
        ordered_names = [person.get_full_name() for person in field.queryset]
        label = field.label_from_instance(alpha)

        self.assertEqual(ordered_names, ["Alexanderthegreat Witness", "Zulu Witness"])
        self.assertTrue(label.startswith("Alexanderthegreat Wi - "))
        self.assertIn("42101-1111111-1", label)
        self.assertIn("0-300-111-1111", label)


class PublicPoliceVerificationVehicleTests(TestCase):
    def setUp(self):
        from datetime import date, timedelta

        from leases.models import Lease, LeaseVehicleType
        from leases.services.police_verification import create_police_verification_link
        from properties.models import Property, Unit
        from tenants.models import Tenant

        property_obj = Property.objects.create(
            property_name="Public Form Property",
            owner_name="Test Owner",
            owner_cnic="61101-1111111-1",
            type="Residential",
            property_type="Building",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="A-1")
        tenant = Tenant.objects.create(
            first_name="Public",
            last_name="Tenant",
            cnic="61101-2222222-2",
        )
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            monthly_rent=10000,
        )
        self.vehicle_type = LeaseVehicleType.objects.create(
            name="Test Car",
            code="test-car",
            is_active=True,
        )
        self.link, self.url = create_police_verification_link(None, self.lease)

    def test_form_shows_one_vehicle_first_and_no_registration_book(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="no_vehicle"')
        self.assertContains(response, 'id="addVehicle"')
        self.assertContains(response, " data-vehicle-entry>", count=3)
        self.assertContains(response, "vehicle-entry is-hidden", count=2)
        self.assertNotContains(response, "registration_book_photo")

    def test_no_vehicle_submission_updates_lease_and_ignores_vehicle_fields(self):
        from leases.models import PendingLeaseVehicleSubmission

        response = self.client.post(
            self.url,
            {
                "family-TOTAL_FORMS": "0",
                "vehicle-TOTAL_FORMS": "3",
                "no_vehicle": "1",
                "vehicle-0-vehicle_type": str(self.vehicle_type.pk),
                "vehicle-0-registration_number": "ABC-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.lease.refresh_from_db()
        self.assertIs(self.lease.has_vehicle, False)
        self.assertFalse(PendingLeaseVehicleSubmission.objects.exists())


class LeaseBillingChangeRegressionTests(TestCase):
    def setUp(self):
        from datetime import date, timedelta

        from leases.models import Lease
        from properties.models import Property, Unit
        from tenants.models import Tenant

        property_obj = Property.objects.create(
            property_name="Billing Regression Property",
            owner_name="Test Owner",
            owner_cnic="61101-3333333-3",
            type="Residential",
            property_type="Building",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="B-1")
        tenant = Tenant.objects.create(
            first_name="Billing",
            last_name="Tenant",
            cnic="61101-4444444-4",
        )
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=date.today() - timedelta(days=10),
            end_date=date.today() + timedelta(days=355),
            monthly_rent=10000,
            society_maintenance=1000,
        )

    def test_end_date_only_is_not_a_billing_change_or_backfill(self):
        from datetime import timedelta

        from invoices.models import Invoice
        from leases.utils.billing import (
            preview_billing_on_change,
            update_billing_on_change,
        )
        from leases.views import detect_lease_changes

        old_lease = type(self.lease).objects.get(pk=self.lease.pk)
        self.lease.end_date += timedelta(days=30)

        changes = detect_lease_changes(old_lease, self.lease)
        plan = preview_billing_on_change(self.lease, old_lease)
        update_billing_on_change(
            self.lease,
            old_lease,
            confirm_security_update=True,
        )

        self.assertTrue(changes["end_date_changed"])
        self.assertFalse(changes["billing_changed"])
        self.assertEqual(plan["backfill_months"], [])
        self.assertEqual(plan["recurring"], [])
        self.assertFalse(Invoice.objects.filter(lease=self.lease).exists())

    def test_existing_month_invoice_is_found_by_issue_month(self):
        from datetime import date, timedelta

        from invoices.models import Invoice
        from leases.utils.billing import update_billing_on_change

        month_start = date.today().replace(day=1)
        Invoice.objects.create(
            lease=self.lease,
            issue_date=month_start,
            due_date=month_start + timedelta(days=5),
            description=f"Invoice for {month_start:%B %Y}",
        )
        old_lease = type(self.lease).objects.get(pk=self.lease.pk)
        self.lease.monthly_rent = 12000

        update_billing_on_change(
            self.lease,
            old_lease,
            confirm_security_update=True,
            include_backfill=False,
            update_existing=False,
        )

        self.assertEqual(
            Invoice.objects.filter(
                lease=self.lease,
                issue_date__year=month_start.year,
                issue_date__month=month_start.month,
            ).count(),
            1,
        )
