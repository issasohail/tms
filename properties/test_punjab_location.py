from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from properties.forms import PropertyForm
from properties.models import Property
from punjab_estamp.models import PunjabEStampDistrict, PunjabEStampTehsil


class PropertyPunjabLocationTests(TestCase):
    def setUp(self):
        self.rawalpindi = PunjabEStampDistrict.objects.create(
            name="Rawalpindi", portal_value="18", sort_order=1
        )
        self.lahore = PunjabEStampDistrict.objects.create(
            name="Lahore", portal_value="7", sort_order=2
        )
        self.rawalpindi_tehsil = PunjabEStampTehsil.objects.create(
            district=self.rawalpindi,
            name="Rawalpindi",
            portal_value="72",
            sort_order=1,
        )
        self.taxila = PunjabEStampTehsil.objects.create(
            district=self.rawalpindi,
            name="Taxila",
            portal_value="83",
            sort_order=2,
        )
        self.lahore_city = PunjabEStampTehsil.objects.create(
            district=self.lahore,
            name="Lahore City",
            portal_value="701",
        )

    def _form_data(self, **overrides):
        data = {
            "property_name": "Location Test",
            "owner_name": "Owner",
            "owner_cnic": "35202-1234567-1",
            "type": "house",
            "property_type": "house",
            "total_units": 1,
            "zila": self.rawalpindi.pk,
            "tehsil": self.rawalpindi_tehsil.pk,
            "welcome_bank_account_mode": Property.WELCOME_BANK_SELECTED,
        }
        data.update(overrides)
        return data

    def test_new_property_defaults_to_rawalpindi(self):
        form = PropertyForm()

        self.assertEqual(form.initial["zila"], self.rawalpindi.pk)
        self.assertEqual(form.initial["tehsil"], self.rawalpindi_tehsil.pk)

    def test_explicit_initial_district_takes_precedence_over_default(self):
        form = PropertyForm(initial={"zila": self.lahore.pk})

        self.assertEqual(form.initial["zila"], self.lahore.pk)
        self.assertEqual(
            list(form.fields["tehsil"].queryset.values_list("pk", flat=True)),
            [self.lahore_city.pk],
        )

    def test_bound_tehsil_queryset_is_filtered_by_district(self):
        form = PropertyForm(data=self._form_data())

        self.assertEqual(
            set(form.fields["tehsil"].queryset.values_list("pk", flat=True)),
            {self.rawalpindi_tehsil.pk, self.taxila.pk},
        )

    def test_tehsil_from_another_district_is_rejected(self):
        form = PropertyForm(
            data=self._form_data(tehsil=self.lahore_city.pk)
        )

        self.assertFalse(form.is_valid())
        self.assertIn("tehsil", form.errors)

    def test_matching_district_and_tehsil_are_valid(self):
        form = PropertyForm(data=self._form_data())

        self.assertTrue(form.is_valid(), form.errors)

    def test_tehsil_api_returns_only_selected_district_active_rows(self):
        PunjabEStampTehsil.objects.create(
            district=self.rawalpindi,
            name="Inactive",
            portal_value="999",
            active=False,
        )
        user = get_user_model().objects.create_superuser(
            username="property-location-user",
            email="property-location@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("properties:property_tehsils"),
            {"district": self.rawalpindi.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["name"] for item in response.json()["tehsils"]],
            ["Rawalpindi", "Taxila"],
        )

    def test_tehsil_api_requires_authentication(self):
        response = self.client.get(
            reverse("properties:property_tehsils"),
            {"district": self.rawalpindi.pk},
        )

        self.assertEqual(response.status_code, 302)

    def test_property_create_page_renders_linked_location_controls(self):
        user = get_user_model().objects.create_superuser(
            username="property-location-page-user",
            email="property-location-page@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("properties:property_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zila / District")
        self.assertContains(response, "Tehsil")
        self.assertContains(response, 'data-punjab-district="1"', html=False)
        self.assertContains(response, 'data-punjab-tehsil="1"', html=False)
        self.assertContains(response, reverse("properties:property_tehsils"))
