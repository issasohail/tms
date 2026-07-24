from datetime import date
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.forms import modelform_factory
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import Account
from core.utils.identity import (
    format_cnic,
    format_phone,
    normalize_cnic,
    normalize_phone,
    validate_cnic,
)
from tenants.models import Tenant
from tenants.services.registration_workflow import family_member_can_have_blank_cnic
from tenants.views import _create_new_registration_shell, tenant_search


class IdentityUtilityTests(TestCase):
    def test_cnic_normalization_and_formatting(self):
        cases = {
            "7150412345671": "7150412345671",
            "71504-1234567-1": "7150412345671",
            " 71504.1234567/1 ": "7150412345671",
            "CNIC 71504 / 1234567 - 1": "7150412345671",
            "": "",
        }
        for supplied, normalized in cases.items():
            with self.subTest(supplied=supplied):
                self.assertEqual(normalize_cnic(supplied), normalized)
        self.assertEqual(format_cnic("7150412345671"), "71504-1234567-1")
        self.assertEqual(format_cnic("71504-1234567-1"), "71504-1234567-1")
        self.assertEqual(format_cnic("123"), "123")

    def test_cnic_validation_rejects_short_and_long_values(self):
        validate_cnic("")
        with self.assertRaisesMessage(ValidationError, "exactly 13 digits"):
            validate_cnic("123456789012")
        with self.assertRaisesMessage(ValidationError, "exactly 13 digits"):
            validate_cnic("12345678901234")

    def test_phone_normalization(self):
        cases = {
            "0300-1234567": "+923001234567",
            "0 300 123 4567": "+923001234567",
            "+92 300 1234567": "+923001234567",
            "0092-300-1234567": "00923001234567",
            "(813) 403-8538": "8134038538",
            "+1 (813) 403.8538": "+18134038538",
            "12+34+56": "123456",
            "": "",
        }
        for supplied, expected in cases.items():
            with self.subTest(supplied=supplied):
                self.assertEqual(normalize_phone(supplied), expected)

    def test_phone_normalization_uses_configured_default_country_code(self):
        configured_settings = SimpleNamespace(country_code="+1")
        with patch(
            "core.models.GlobalSettings.get_solo",
            return_value=configured_settings,
        ):
            self.assertEqual(normalize_phone("0813-403-8538"), "+18134038538")

    def test_phone_right_based_display_formatting(self):
        cases = {
            "03001234567": "+92-300-123-4567",
            "3001234567": "300-123-4567",
            "923001234567": "92-300-123-4567",
            "+923001234567": "+92-300-123-4567",
            "18134038538": "1-813-403-8538",
            "+18134038538": "+1-813-403-8538",
            "441234567890": "44-123-456-7890",
            "+441234567890": "+44-123-456-7890",
            "1234567": "123-4567",
            "0012345": "001-2345",
            "+123456789012345678": "+12345678-901-234-5678",
            "": "",
        }
        for supplied, expected in cases.items():
            with self.subTest(supplied=supplied):
                self.assertEqual(format_phone(supplied), expected)

    def test_phone_display_replaces_local_zero_with_country_setting(self):
        self.assertEqual(
            format_phone("0332-512-6929", country_code="+92"),
            "+92-332-512-6929",
        )
        self.assertEqual(
            format_phone("+92-332-512-6929", country_code="+92"),
            "+92-332-512-6929",
        )
        self.assertEqual(
            format_phone("923325126929", country_code="+92"),
            "+92-332-512-6929",
        )


class IdentityIntegrationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            first_name="Ali",
            last_name="Khan",
            cnic="71504-1234567-1",
            phone="0300-1234567",
        )

    def test_model_fields_normalize_before_save(self):
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.cnic, "7150412345671")
        self.assertEqual(self.tenant.cnic_digits, "7150412345671")
        self.assertEqual(self.tenant.phone, "+923001234567")

    def test_model_form_normalizes_before_save(self):
        form_class = modelform_factory(
            Tenant, fields=("first_name", "last_name", "cnic", "phone")
        )
        form = form_class(
            data={
                "first_name": "Sara",
                "last_name": "Ahmed",
                "cnic": "35202 / 7654321 - 0",
                "phone": "+92 (311) 765-4321",
            }
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        tenant = form.save()
        self.assertEqual(tenant.cnic, "3520276543210")
        self.assertEqual(tenant.phone, "+923117654321")

    def test_model_form_and_direct_save_reject_invalid_cnic(self):
        form_class = modelform_factory(Tenant, fields=("first_name", "last_name", "cnic"))
        form = form_class(data={"first_name": "Bad", "last_name": "CNIC", "cnic": "123"})
        self.assertFalse(form.is_valid())
        self.assertIn("exactly 13 digits", str(form.errors["cnic"]))
        with self.assertRaises(ValidationError):
            Tenant.objects.create(first_name="Bad", last_name="CNIC", cnic="123")

    def test_unchanged_malformed_legacy_cnic_does_not_block_other_updates(self):
        Tenant.objects.filter(pk=self.tenant.pk).update(cnic="NEW5a685c9d79")
        legacy_tenant = Tenant.objects.get(pk=self.tenant.pk)
        legacy_tenant.phone = "0311-2223344"
        legacy_tenant.save()
        legacy_tenant.refresh_from_db()
        self.assertEqual(legacy_tenant.cnic, "NEW5a685c9d79")
        self.assertEqual(legacy_tenant.phone, "+923112223344")

    def test_quick_add_lead_is_saved_with_a_real_blank_cnic(self):
        form = SimpleNamespace(
            cleaned_data={
                "name": "Lead Person",
                "phone": "0300-5556677",
                "email": "",
                "notes": "",
                "interested_in": [],
            }
        )
        lead = _create_new_registration_shell(form)
        self.assertEqual(lead.cnic, "")
        self.assertEqual(lead.phone, "+923005556677")
        self.assertIn("quick tenant registration", lead.notes)

    def test_cleanup_converts_old_quick_add_placeholder_to_blank(self):
        Tenant.objects.filter(pk=self.tenant.pk).update(
            cnic="NEW5a685c9d79", cnic_digits="5685979"
        )
        output = StringIO()
        call_command("normalize_identity_data", apply=True, stdout=output)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.cnic, "")
        self.assertIsNone(self.tenant.cnic_digits)
        self.assertIn("legacy blank-CNIC placeholder(s)", output.getvalue())

    def test_phone_only_cleanup_internationalizes_phone_without_changing_cnic(self):
        Tenant.objects.filter(pk=self.tenant.pk).update(
            cnic="NEW5a685c9d79",
            cnic_digits="5685979",
            phone="0300-1234567",
        )
        call_command("normalize_identity_data", apply=True, phones_only=True)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.phone, "+923001234567")
        self.assertEqual(self.tenant.cnic, "NEW5a685c9d79")
        self.assertEqual(self.tenant.cnic_digits, "5685979")

    def test_minor_family_member_can_have_blank_cnic(self):
        today = date.today()
        minor = SimpleNamespace(
            role="family_member",
            cnic="",
            date_of_birth=date(today.year - 18, today.month, today.day),
        )
        self.assertTrue(family_member_can_have_blank_cnic(minor))
        minor.role = "proposer"
        self.assertFalse(family_member_can_have_blank_cnic(minor))

    def test_template_filters_and_tel_href(self):
        rendered = Template(
            '<a href="tel:{{ phone|normalize_phone }}">{{ phone|format_phone }}</a> '
            "{{ cnic|format_cnic }}"
        ).render(
            Context({"phone": "+92 (300) 123-4567", "cnic": "7150412345671"})
        )
        self.assertIn('href="tel:+923001234567"', rendered)
        self.assertIn(">+92-300-123-4567</a>", rendered)
        self.assertIn("71504-1234567-1", rendered)

    def test_formatted_cnic_and_phone_searches(self):
        factory = RequestFactory()
        for query in ("71504-1234567-1", "7150412345671", "0300-1234567"):
            with self.subTest(query=query):
                response = tenant_search(factory.get("/tenants/payments/tenant-search/", {"q": query}))
                self.assertContains(response, self.tenant.get_full_name())

    def test_ajax_update_returns_normalized_and_display_values(self):
        user = Account.objects.create_superuser(
            username="identity-admin", email="identity@example.com", password="test"
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("tenants:tenant_inline_update", args=[self.tenant.pk]),
            {"field": "phone", "value": "+92 (333) 123-4567"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["value"], "+923331234567")
        self.assertEqual(payload["display"], "+92-333-123-4567")
        self.assertEqual(payload["display_value"], payload["display"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.phone, "+923331234567")
