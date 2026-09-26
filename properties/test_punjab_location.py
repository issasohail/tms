from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from properties.forms import PropertyForm
from properties.models import Property
from punjab_estamp.models import PunjabEStampDistrict, PunjabEStampTehsil
from punjab_estamp.services.sync import PunjabEStampSyncError


class PropertyPunjabLocationTests(TestCase):
    def setUp(self):
        self.rawalpindi, _ = PunjabEStampDistrict.objects.get_or_create(
            portal_value="18",
            defaults={
                "name": "Rawalpindi",
                "active": True,
                "sort_order": 0,
            },
        )
        self.lahore, _ = PunjabEStampDistrict.objects.get_or_create(
            portal_value="7",
            defaults={
                "name": "Lahore",
                "active": True,
                "sort_order": 2,
            },
        )
        self.rawalpindi_tehsil, _ = PunjabEStampTehsil.objects.get_or_create(
            district=self.rawalpindi,
            portal_value="72",
            defaults={
                "name": "Rawalpindi",
                "active": True,
                "sort_order": 1,
            },
        )
        self.taxila, _ = PunjabEStampTehsil.objects.get_or_create(
            district=self.rawalpindi,
            portal_value="83",
            defaults={
                "name": "Taxila",
                "active": True,
                "sort_order": 2,
            },
        )
        self.lahore_city, _ = PunjabEStampTehsil.objects.get_or_create(
            district=self.lahore,
            portal_value="701",
            defaults={
                "name": "Lahore City",
                "active": True,
            },
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

        expected_ids = set(
            PunjabEStampTehsil.objects.filter(
                district=self.rawalpindi, active=True
            ).values_list("pk", flat=True)
        )
        self.assertEqual(
            set(form.fields["tehsil"].queryset.values_list("pk", flat=True)),
            expected_ids,
        )

    def test_tehsil_from_another_district_is_rejected(self):
        form = PropertyForm(data=self._form_data(tehsil=self.lahore_city.pk))

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
        expected_names = list(
            PunjabEStampTehsil.objects.filter(
                district=self.rawalpindi, active=True
            ).values_list("name", flat=True)
        )
        self.assertEqual(
            [item["name"] for item in response.json()["tehsils"]],
            expected_names,
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

    @patch("properties.views.sync_district_tehsils")
    def test_tehsil_api_syncs_when_selected_district_has_no_cache(self, sync_mock):
        lahore = self.lahore
        self.lahore_city.delete()

        def populate(district):
            PunjabEStampTehsil.objects.create(
                district=district, name="Lahore City", portal_value="701", sort_order=1
            )
            return {"found": 1}

        sync_mock.side_effect = populate
        user = get_user_model().objects.create_superuser(
            username="property-location-sync-user",
            email="property-location-sync@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("properties:property_tehsils"),
            {"district": lahore.pk},
        )

        self.assertEqual(response.status_code, 200)
        sync_mock.assert_called_once_with(lahore)
        self.assertTrue(response.json()["sync_attempted"])
        self.assertEqual(
            [row["name"] for row in response.json()["tehsils"]], ["Lahore City"]
        )

    @patch("properties.views.sync_district_tehsils")
    def test_tehsil_api_sync_failure_falls_back_to_cached_rows(self, sync_mock):
        self.rawalpindi.last_synced_at = timezone.now() - timedelta(days=31)
        self.rawalpindi.save(update_fields=["last_synced_at"])
        sync_mock.side_effect = PunjabEStampSyncError("offline")
        user = get_user_model().objects.create_superuser(
            username="property-location-cache-user",
            email="property-location-cache@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("properties:property_tehsils"),
            {"district": self.rawalpindi.pk},
        )

        self.assertEqual(response.status_code, 200)
        expected_names = list(
            PunjabEStampTehsil.objects.filter(
                district=self.rawalpindi, active=True
            ).values_list("name", flat=True)
        )
        self.assertEqual(
            [item["name"] for item in response.json()["tehsils"]],
            expected_names,
        )
        self.assertIn("cached", response.json()["warning"].lower())

    @patch("properties.views.sync_district_tehsils")
    def test_tehsil_api_sync_failure_without_cache_returns_friendly_warning(
        self, sync_mock
    ):
        self.lahore_city.delete()
        sync_mock.side_effect = PunjabEStampSyncError("offline")
        user = get_user_model().objects.create_superuser(
            username="property-location-empty-cache-user",
            email="property-location-empty-cache@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("properties:property_tehsils"),
            {"district": self.lahore.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tehsils"], [])
        self.assertIn("vpn", response.json()["warning"].lower())
        self.assertTrue(response.json()["sync_attempted"])

    @patch("properties.views.sync_district_tehsils")
    def test_seeded_rawalpindi_cache_does_not_force_network_sync(self, sync_mock):
        self.assertIsNone(self.rawalpindi.last_synced_at)
        user = get_user_model().objects.create_superuser(
            username="property-location-seed-user",
            email="property-location-seed@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("properties:property_tehsils"),
            {"district": self.rawalpindi.pk},
        )

        self.assertEqual(response.status_code, 200)
        sync_mock.assert_not_called()
        self.assertFalse(response.json()["sync_attempted"])
