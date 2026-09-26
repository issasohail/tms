from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from properties.forms import PropertyForm
from properties.models import Property
from punjab_estamp.models import (
    PunjabEStampDistrict,
    PunjabEStampRelation,
    PunjabEStampTehsil,
)
from tenants.models import Tenant


class PunjabTenantIdentityTests(TestCase):
    def setUp(self):
        self.relation, _ = PunjabEStampRelation.objects.get_or_create(
            portal_value="33", defaults={"name": "S/O", "sort_order": 1}
        )
        self.district, _ = PunjabEStampDistrict.objects.get_or_create(
            portal_value="18", defaults={"name": "Rawalpindi", "sort_order": 1}
        )
        self.tehsil, _ = PunjabEStampTehsil.objects.get_or_create(
            district=self.district,
            portal_value="72",
            defaults={"name": "Rawalpindi", "sort_order": 1},
        )
        self.tenant = Tenant.objects.create(
            first_name="Ali",
            last_name="Ahmed",
            relation=self.relation,
            cnic="3520212345671",
            phone="03001234567",
            permanent_address="Rawalpindi",
        )

    def property_form_data(self):
        return {
            "property_name": "Linked Property",
            "owner_tenant": self.tenant.pk,
            "owner_name": "",
            "owner_father_name": "",
            "relation": "",
            "owner_cnic": "",
            "owner_phone": "",
            "owner_address": "",
            "caretaker_tenant": "",
            "type": "Residential",
            "property_type": "house",
            "total_units": 1,
            "zila": self.district.pk,
            "tehsil": self.tehsil.pk,
            "welcome_bank_account_mode": Property.WELCOME_BANK_SELECTED,
        }

    def test_linked_owner_populates_legacy_identity_fields(self):
        form = PropertyForm(data=self.property_form_data())
        self.assertTrue(form.is_valid(), form.errors)
        property_obj = form.save()
        self.assertEqual(property_obj.owner_tenant, self.tenant)
        self.assertEqual(property_obj.owner_name, "Ali")
        self.assertEqual(property_obj.owner_father_name, "Ahmed")
        self.assertEqual(property_obj.relation, "S/O")
        self.assertEqual(property_obj.owner_cnic, "3520212345671")

    def test_owner_requires_link_or_legacy_identity(self):
        data = self.property_form_data()
        data["owner_tenant"] = ""
        form = PropertyForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("owner_name", form.errors)
        self.assertIn("owner_cnic", form.errors)

    def test_identity_endpoint_requires_login_and_returns_link_data(self):
        url = reverse("properties:property_tenant_identity", args=[self.tenant.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        user = get_user_model().objects.create_user(
            username="phase4-user",
            password="test-password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["relation"], "S/O")
        self.assertEqual(response.json()["cnic"], "3520212345671")


class TenantRelationCompatibilityTests(TestCase):
    def test_agreement_name_uses_relation_fk_then_legacy_fallback(self):
        from tenants.services.registration_workflow import resolve_punjab_relation

        relation, _ = PunjabEStampRelation.objects.get_or_create(
            portal_value="34", defaults={"name": "D/O", "sort_order": 1}
        )
        self.assertEqual(resolve_punjab_relation("D/O."), relation)
        tenant = Tenant.objects.create(
            first_name="Ayesha",
            last_name="Khan",
            relation=relation,
            relation_legacy="D/O.",
        )
        self.assertEqual(tenant.get_full_name_agreement(), "Ayesha D/O Khan")

        tenant.relation = None
        tenant.relation_legacy = "On Behalf"
        self.assertEqual(
            tenant.get_full_name_agreement(), "Ayesha On Behalf Khan"
        )
