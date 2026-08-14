from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase, TestCase


class TenantWelcomeWhatsAppTests(TestCase):
    def test_empty_active_template_uses_readable_welcome_message(self):
        from core.models import GlobalSettings
        from leases.models import WhatsAppTemplate
        from leases.whatsapp import render_whatsapp_template

        settings_obj = GlobalSettings.get_solo()
        settings_obj.whatsapp_number = "+923001234567"
        settings_obj.late_fee_enabled = True
        settings_obj.late_fee_type = "fixed"
        settings_obj.late_fee_amount = 500
        settings_obj.late_fee_grace_days = 2
        settings_obj.late_fee_reminder_interval_days = 5
        settings_obj.save()

        WhatsAppTemplate.objects.update_or_create(
            template_type=WhatsAppTemplate.TEMPLATE_TENANT_WELCOME,
            defaults={
                "name": "Tenant Welcome",
                "body": "",
                "is_active": True,
            },
        )
        tenant = Mock()
        tenant.get_full_name.return_value = "Ali Tenant"
        property_obj = SimpleNamespace(
            property_name="Garden Heights",
            caretaker_phone="03007654321",
            owner_phone="",
            bank_account_details="",
        )
        unit = SimpleNamespace(
            property=property_obj,
            unit_number="A-12",
            bank_account_details="",
            use_property_bank_account=True,
            electric_meter_num="",
            gas_meter_num="",
        )
        lease = SimpleNamespace(
            tenant=tenant,
            unit=unit,
            start_date=None,
            end_date=None,
            due_date="5th of each month",
            monthly_rent=30000,
            society_maintenance=1000,
            water_charges=2000,
            internet_charges=500,
            security_deposit=60000,
            late_fee=500,
            get_balance=0,
        )

        template, message = render_whatsapp_template(
            WhatsAppTemplate.TEMPLATE_TENANT_WELCOME,
            lease,
        )

        self.assertIsNotNone(template)
        self.assertIn("Welcome, Ali Tenant", message)
        self.assertIn("Garden Heights - Unit A-12", message)
        self.assertIn("Total monthly payment: Rs. 33,500", message)
        self.assertIn("Security deposit: Rs. 60,000", message)
        self.assertIn("Owner payment account", message)
        self.assertIn("Bank account information has not been recorded", message)
        self.assertIn("Rs. 500 every 5 days after a 2-day grace period", message)
        self.assertIn("+92-300-123-4567", message)
        self.assertIn("within 24 hours", message)
        self.assertIn("may affect utility services", message)
        self.assertNotIn("[TENANT_NAME]", message)


class AuthorizedOccupantsPlaceholderTests(TestCase):
    def test_registry_contains_new_placeholders(self):
        from leases.utils.utils import PLACEHOLDER_REGISTRY

        self.assertIn("authorized_occupants_table", PLACEHOLDER_REGISTRY)
        self.assertIn("authorized_occupants_names", PLACEHOLDER_REGISTRY)
        self.assertIn("authorized_occupants_count", PLACEHOLDER_REGISTRY)
        self.assertIn("PARKING_CLAUSE", PLACEHOLDER_REGISTRY)
        self.assertIn("PARKING_ENABLED", PLACEHOLDER_REGISTRY)
        self.assertIn("PARKING_SPACE", PLACEHOLDER_REGISTRY)
        self.assertIn("PARKING_ASSIGNMENT_TERMS", PLACEHOLDER_REGISTRY)
        self.assertIn("PARKING_MONTHLY_RATE", PLACEHOLDER_REGISTRY)
        self.assertIn("UNAUTHORIZED_PARKING_PENALTY", PLACEHOLDER_REGISTRY)

    def test_table_formats_all_cnics_and_uses_four_column_layout(self):
        from leases.utils.utils import authorized_occupants_table

        tenant = Mock(cnic="6110112345671")
        tenant.get_full_name.return_value = "Primary Tenant"
        family_member = Mock(cnic="4210112345671")
        family_member.get_full_name.return_value = "Family Member"
        link = SimpleNamespace(
            family_member=family_member,
            relation="Spouse",
            relationship_type=None,
        )
        manager = Mock()
        manager.select_related.return_value.filter.return_value = [link]
        lease = Mock(tenant=tenant, family_members=manager)
        html = authorized_occupants_table(lease)
        self.assertIn("Primary Tenant", html)
        self.assertIn("61101-1234567-1", html)
        self.assertIn("42101-1234567-1", html)
        self.assertNotIn("6110112345671", html)
        self.assertNotIn("4210112345671", html)
        self.assertEqual(html.count('class="occupant-card"'), 4)
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


class LeaseInventorySynchronizationTests(TestCase):
    def setUp(self):
        from datetime import date, timedelta

        from leases.models import Lease
        from leases.models_parking_inventory import InventoryItemDefinition
        from properties.models import Property, Unit
        from tenants.models import Tenant

        property_obj = Property.objects.create(
            property_name="Inventory Sync Property",
            owner_name="Test Owner",
            owner_cnic="61101-1111111-1",
            type="Residential",
            property_type="house",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="I-1")
        tenant = Tenant.objects.create(
            first_name="Inventory",
            last_name="Tenant",
            cnic="61101-2222222-2",
        )
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            monthly_rent=10000,
            inventory_ceiling_fans=4,
            inventory_exhaust_fans=3,
            inventory_ceiling_lights=11,
            inventory_stove=1,
            inventory_wardrobes=5,
            inventory_keys=6,
        )
        self.definitions = {}
        labels = {
            "ceiling_fan": "Ceiling Fan",
            "exhaust_fan": "Exhaust Fan",
            "ceiling_light": "Ceiling Light",
            "stove": "Stove",
            "wardrobe": "Wardrobe",
            "keys": "Keys",
        }
        for sort_order, (code, name) in enumerate(labels.items(), start=1):
            item, _ = InventoryItemDefinition.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "sort_order": sort_order,
                    "is_active": True,
                    "include_in_clause": True,
                },
            )
            self.definitions[code] = item

    def test_lease_fields_refresh_all_agreement_inventory_values(self):
        from leases.services.inventory_parking import (
            inventory_list_html,
            sync_lease_inventory_from_fields,
        )

        updated = sync_lease_inventory_from_fields(self.lease)
        quantities = dict(
            self.lease.inventory_items.values_list("item__code", "quantity")
        )

        # Migration 0084 seeds InventoryItemDefinition rows before any test
        # runs, so ensure_lease_inventory_snapshot already reconciles all six
        # legacy fields at Lease-creation time (see leases/services/
        # inventory_parking.py). This explicit call has nothing left to fix.
        self.assertEqual(updated, 0)
        self.assertEqual(
            quantities,
            {
                "ceiling_fan": 4,
                "exhaust_fan": 3,
                "ceiling_light": 11,
                "stove": 1,
                "wardrobe": 5,
                "keys": 6,
            },
        )
        self.assertIn(
            "<strong>11 Ceiling Light</strong>", inventory_list_html(self.lease)
        )

    def test_inventory_manager_value_refreshes_legacy_lease_field(self):
        from leases.services.inventory_parking import (
            sync_lease_field_from_inventory_item,
        )

        changed = sync_lease_field_from_inventory_item(
            self.lease,
            self.definitions["ceiling_light"],
            9,
        )

        self.assertTrue(changed)
        self.lease.refresh_from_db()
        self.assertEqual(self.lease.inventory_ceiling_lights, 9)

    def test_agreement_render_reconciles_existing_migration_snapshot(self):
        from leases.services.inventory_parking import (
            inventory_list_html,
            sync_lease_inventory_from_fields,
        )

        sync_lease_inventory_from_fields(self.lease)
        light = self.lease.inventory_items.get(item__code="ceiling_light")
        light.quantity = 16
        light.snapshot_source = "migration"
        light.save(update_fields=["quantity", "snapshot_source", "updated_at"])

        rendered = inventory_list_html(self.lease)

        light.refresh_from_db()
        self.assertEqual(light.quantity, 11)
        self.assertEqual(light.snapshot_source, "lease")
        self.assertIn("<strong>11 Ceiling Light</strong>", rendered)
        self.assertNotIn("<strong>16 Ceiling Light</strong>", rendered)

    def test_agreement_render_preserves_explicit_lease_inventory_override(self):
        from leases.services.inventory_parking import (
            inventory_list_html,
            sync_lease_inventory_from_fields,
        )

        sync_lease_inventory_from_fields(self.lease)
        light = self.lease.inventory_items.get(item__code="ceiling_light")
        light.quantity = 8
        light.snapshot_source = "lease"
        light.save(update_fields=["quantity", "snapshot_source", "updated_at"])

        rendered = inventory_list_html(self.lease)

        light.refresh_from_db()
        self.assertEqual(light.quantity, 8)
        self.assertIn("<strong>8 Ceiling Light</strong>", rendered)
        self.assertNotIn("<strong>11 Ceiling Light</strong>", rendered)


class ActiveClauseEditorDeletionTests(TestCase):
    def setUp(self):
        from datetime import date, timedelta

        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        from leases.models import Lease, LeaseRenewalClause
        from leases.services.lease_history import ensure_original_history
        from properties.models import Property, Unit
        from tenants.models import Tenant

        property_obj = Property.objects.create(
            property_name="Clause Deletion Property",
            owner_name="Test Owner",
            owner_cnic="61101-1111111-1",
            type="Residential",
            property_type="house",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="C-1")
        tenant = Tenant.objects.create(
            first_name="Clause",
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
        self.history = ensure_original_history(self.lease)
        self.user = get_user_model().objects.create_user(
            username="clause-delete-user",
            password="test-password",
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="leases",
                codename="change_lease",
            )
        )
        self.client.force_login(self.user)
        self.history.clauses.all().delete()
        LeaseRenewalClause.objects.bulk_create(
            [
                LeaseRenewalClause(
                    renewal=self.history,
                    clause_number=number,
                    template_text=f"Original clause {number}",
                )
                for number in range(1, 6)
            ]
        )

    def test_delete_clause_renumbers_contiguously_and_preserves_text_order(self):
        from django.urls import reverse

        clause_three = self.history.clauses.get(clause_number=3)
        response = self.client.post(
            reverse("leases:edit_clauses", args=[self.lease.pk]),
            {
                "action": "delete_clause",
                "clause_id": clause_three.pk,
                "history_id": self.history.pk,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        remaining = list(
            self.history.clauses.order_by("clause_number").values_list(
                "clause_number", "template_text"
            )
        )
        self.assertEqual(
            remaining,
            [
                (1, "Original clause 1"),
                (2, "Original clause 2"),
                (3, "Original clause 4"),
                (4, "Original clause 5"),
            ],
        )


class AgreementPartyAjaxTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        self.user = get_user_model().objects.create_user(
            username="party-editor", password="x"
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tenants", codename="add_tenant"
            ),
            Permission.objects.get(
                content_type__app_label="leases", codename="add_lease"
            ),
        )
        self.client.force_login(self.user)

    def test_quick_add_party_creates_tenant(self):
        from django.urls import reverse

        from tenants.models import Tenant

        response = self.client.post(
            reverse("leases:create_agreement_party_ajax"),
            {
                "prefix": "Mr.",
                "first_name": "Business",
                "relation": "S/O.",
                "last_name": "Partner",
                "cnic": "42101-1234567-1",
                "phone": "03001234567",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "Business Partner")
        self.assertEqual(payload["cnic_display"], "42101-1234567-1")
        self.assertTrue(
            Tenant.objects.filter(
                pk=payload["id"], cnic_digits="4210112345671"
            ).exists()
        )

    def test_quick_add_party_reuses_existing_cnic(self):
        from django.urls import reverse

        from tenants.models import Tenant

        existing = Tenant.objects.create(
            first_name="Existing", last_name="Person", cnic="42101-7654321-1"
        )
        response = self.client.post(
            reverse("leases:create_agreement_party_ajax"),
            {
                "first_name": "Other",
                "last_name": "Name",
                "cnic": "4210176543211",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], existing.pk)
        self.assertFalse(response.json()["created"])

    def test_quick_add_party_saves_photo_and_cnic_images(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        from tenants.models import Tenant

        image_bytes = (
            b"GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00"
            b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
            b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        )
        response = self.client.post(
            reverse("leases:create_agreement_party_ajax"),
            {
                "first_name": "Documented",
                "last_name": "Party",
                "cnic": "42101-1111111-1",
                "photo": SimpleUploadedFile(
                    "photo.gif", image_bytes, content_type="image/gif"
                ),
                "cnic_front": SimpleUploadedFile(
                    "front.gif", image_bytes, content_type="image/gif"
                ),
                "cnic_back": SimpleUploadedFile(
                    "back.gif", image_bytes, content_type="image/gif"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        tenant = Tenant.objects.get(pk=response.json()["id"])
        self.assertTrue(tenant.photo.name)
        self.assertTrue(tenant.cnic_front.name)
        self.assertTrue(tenant.cnic_back.name)

    def test_quick_add_party_uses_cnic_front_when_photo_is_missing(self):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        from PIL import Image

        from tenants.models import Tenant

        image = BytesIO()
        Image.new("RGB", (1300, 800), "white").save(image, format="JPEG")
        response = self.client.post(
            reverse("leases:create_agreement_party_ajax"),
            {
                "first_name": "Portrait",
                "last_name": "Fallback",
                "cnic": "42101-2222222-1",
                "cnic_front": SimpleUploadedFile(
                    "front.jpg", image.getvalue(), content_type="image/jpeg"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        tenant = Tenant.objects.get(pk=response.json()["id"])
        self.assertTrue(tenant.photo.name)
        self.assertTrue(tenant.cnic_front.name)


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
            first_name="Zulu",
            last_name="Witness",
            cnic="42101-2222222-2",
            phone="03002222222",
        )
        alpha = Tenant.objects.create(
            first_name="Alexanderthegreat",
            last_name="Witness",
            cnic="42101-1111111-1",
            phone="03001111111",
        )

        form = LeaseHistoryEditForm()
        field = form.fields["witness1_tenant"]
        ordered_names = [person.get_full_name() for person in field.queryset]
        label = field.label_from_instance(alpha)

        self.assertEqual(ordered_names, ["Alexanderthegreat Witness", "Zulu Witness"])
        self.assertTrue(label.startswith("Alexanderthegreat Wi - "))
        self.assertIn("42101-1111111-1", label)
        self.assertIn("+92-300-111-1111", label)


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
            agreement_charges=0,
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


class LeaseTermCalculationTests(SimpleTestCase):
    def test_first_day_start_uses_day_before_after_term(self):
        from datetime import date

        from leases.lease_term import calculate_lease_end_date

        self.assertEqual(
            calculate_lease_end_date(date(2026, 7, 1), 11),
            date(2027, 5, 31),
        )

    def test_mid_month_start_ends_on_previous_day(self):
        from datetime import date

        from leases.lease_term import calculate_lease_end_date

        self.assertEqual(
            calculate_lease_end_date(date(2026, 7, 10), 11),
            date(2027, 6, 9),
        )

    def test_month_end_is_clamped_before_subtracting_one_day(self):
        from datetime import date

        from leases.lease_term import calculate_lease_end_date

        self.assertEqual(
            calculate_lease_end_date(date(2027, 1, 31), 1),
            date(2027, 2, 27),
        )

    def test_leap_year_is_supported(self):
        from datetime import date

        from leases.lease_term import calculate_lease_end_date

        self.assertEqual(
            calculate_lease_end_date(date(2023, 3, 1), 11),
            date(2024, 1, 31),
        )


class ActiveAgreementAndMoveInBillingTests(TestCase):
    def setUp(self):
        from datetime import date

        from leases.models import Lease
        from properties.models import Property, Unit
        from tenants.models import Tenant

        property_obj = Property.objects.create(
            property_name="Agreement Source Property",
            owner_name="Test Owner",
            owner_cnic="61101-1111111-1",
            type="Residential",
            property_type="Building",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="A-1")
        tenant = Tenant.objects.create(
            first_name="Agreement",
            last_name="Tenant",
            cnic="61101-2222222-2",
        )
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            agreement_date=date(2026, 7, 20),
            start_date=date(2026, 8, 1),
            end_date=date(2027, 6, 30),
            lease_months=11,
            monthly_rent=12000,
            society_maintenance=800,
            water_charges=2000,
            internet_charges=500,
            agreement_charges=1500,
            security_deposit=18300,
        )

    def test_active_history_syncs_all_current_billing_controls(self):
        from leases.services.lease_history import (
            ensure_original_history,
            sync_history_to_master_lease,
        )

        history = ensure_original_history(self.lease)
        history.monthly_rent = 13000
        history.bill_water_charges = False
        history.bill_recurring_charges = False
        history.save(
            update_fields=[
                "monthly_rent",
                "bill_water_charges",
                "bill_recurring_charges",
            ]
        )

        sync_history_to_master_lease(history)
        self.lease.refresh_from_db()

        self.assertEqual(self.lease.monthly_rent, 13000)
        self.assertFalse(self.lease.bill_water_charges)
        self.assertFalse(self.lease.bill_recurring_charges)

    def test_current_history_editor_exposes_all_master_agreement_fields(self):
        from leases.forms_renewal import LeaseHistoryEditForm
        from leases.services.lease_history import ensure_original_history

        history = ensure_original_history(self.lease)
        form = LeaseHistoryEditForm(instance=history)

        self.assertIn("security_deposit", form.fields)
        self.assertIn("rent_increase_percent", form.fields)
        self.assertIn("terms", form.fields)

    def test_active_history_edit_saves_on_first_submission(self):
        from unittest.mock import patch

        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from leases.services.lease_history import ensure_original_history

        user = get_user_model().objects.create_superuser(
            username="history-editor",
            email="history-editor@example.com",
            password="test-password",
        )
        self.client.force_login(user)
        history = ensure_original_history(self.lease)

        with (
            patch("leases.views_renewal.is_active_history", return_value=True),
            patch("leases.views_renewal.update_billing_on_change") as update_billing,
        ):
            response = self.client.post(
                reverse(
                    "leases:lease_history_edit",
                    kwargs={"pk": self.lease.pk, "renewal_id": history.pk},
                ),
                {
                    "start_date": "2026-08-01",
                    "end_date": "2027-06-30",
                    "lease_months": "11",
                    "agreement_date": "2026-08-01",
                    "monthly_rent": "13500.00",
                    "society_maintenance": "800.00",
                    "water_charges": "2000.00",
                    "bill_water_charges": "on",
                    "bill_recurring_charges": "on",
                    "internet_charges": "500.00",
                    "agreement_charges": "1500.00",
                    "security_deposit": "18300.00",
                    "rent_increase_percent": "10.00",
                    "terms": history.terms or "",
                    "notes": history.notes or "",
                },
            )

        self.assertRedirects(
            response,
            reverse(
                "leases:lease_history_detail",
                kwargs={"pk": self.lease.pk, "renewal_id": history.pk},
            ),
        )
        history.refresh_from_db()
        self.lease.refresh_from_db()
        self.assertEqual(history.monthly_rent, 13500)
        self.assertEqual(self.lease.monthly_rent, 13500)
        update_billing.assert_called_once()

    def test_agreement_fee_is_created_once(self):
        from invoices.models import InvoiceItem
        from leases.utils.billing import ensure_agreement_fee_invoice

        first = ensure_agreement_fee_invoice(self.lease)
        second = ensure_agreement_fee_invoice(self.lease)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            InvoiceItem.objects.filter(
                invoice__lease=self.lease,
                category__name="Agreement Fee",
            ).count(),
            1,
        )

    def test_exact_move_in_proration_uses_partial_month_before_billing_start(self):
        from datetime import date
        from decimal import Decimal

        from leases.utils.billing import ensure_move_in_proration_invoice

        invoice = ensure_move_in_proration_invoice(
            self.lease,
            move_in_date=date(2026, 7, 20),
            mode="exact",
        )

        self.assertEqual(invoice.issue_date, date(2026, 7, 20))
        self.assertEqual(invoice.amount, Decimal("5922.58"))
        self.assertIn("2026-07-20 to 2026-07-31", invoice.description)


class LeaseFamilyNameEntryTests(TestCase):
    def setUp(self):
        from datetime import date, timedelta

        from django.contrib.auth import get_user_model

        from leases.models import Lease, LeaseRelationshipType
        from properties.models import Property, Unit
        from tenants.models import Tenant

        self.user = get_user_model().objects.create_user(
            username="family-name-user", password="x"
        )
        from django.contrib.auth.models import Permission

        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="leases",
                codename="add_leasefamilymember",
            )
        )
        self.client.force_login(self.user)
        property_obj = Property.objects.create(
            property_name="Family Name Property",
            owner_name="Owner",
            owner_cnic="61101-1111111-1",
            type="Residential",
            property_type="Building",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="FN-1")
        primary = Tenant.objects.create(
            first_name="Primary",
            last_name="Tenant",
            cnic="61101-2222222-2",
        )
        self.lease = Lease.objects.create(
            tenant=primary,
            unit=unit,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            monthly_rent=10000,
        )
        self.relationship = LeaseRelationshipType.objects.create(
            name="Brother", code="brother-test", is_active=True
        )

    def test_full_name_splits_into_first_and_remaining_last_name(self):
        from django.urls import reverse

        from tenants.models import Tenant

        response = self.client.post(
            reverse("leases:lease_family_create_and_add", args=[self.lease.pk]),
            {
                "full_name": "Asif Ali Hussain",
                "relation": self.relationship.pk,
                "cnic": "61101-3333333-3",
                "date_of_birth": "2000-02-25",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        family = Tenant.objects.get(cnic_digits="6110133333333")
        self.assertEqual(family.first_name, "Asif")
        self.assertEqual(family.last_name, "Ali Hussain")

    def test_single_word_full_name_is_rejected_instead_of_using_family_surname(self):
        from django.urls import reverse

        response = self.client.post(
            reverse("leases:lease_family_create_and_add", args=[self.lease.pk]),
            {
                "full_name": "Asif",
                "relation": self.relationship.pk,
                "cnic": "61101-4444444-4",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("at least two words", response.json()["error"])
