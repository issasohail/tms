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
