import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from leases.models_parking_inventory import (
    InventoryItemDefinition,
    UnitInventoryItem,
)

from .models import Property, Unit


class UnitListInlineUpdateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="unit-list-user", password="test-password"
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_unit"),
            Permission.objects.get(codename="change_unit"),
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Test Property",
            owner_name="Owner",
            owner_cnic="35202-1234567-1",
            type="Building",
            property_type="apartment",
            total_units=2,
        )
        self.other_property = Property.objects.create(
            property_name="Other Property",
            owner_name="Owner",
            owner_cnic="35202-1234567-2",
            type="Building",
            property_type="apartment",
            total_units=1,
        )
        self.unit_one = Unit.objects.create(
            property=self.property, unit_number="1"
        )
        self.unit_two = Unit.objects.create(
            property=self.property, unit_number="2"
        )
        self.other_unit = Unit.objects.create(
            property=self.other_property, unit_number="1"
        )
        self.url = reverse("properties:unit_inline_update")

    def post_update(self, **payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_single_unit_charge_update_offers_property_bulk_update(self):
        response = self.post_update(
            id=self.unit_one.pk,
            field="internet_charges",
            value="1,500",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["offer_bulk"])
        self.assertEqual(response.json()["property_unit_count"], 2)
        self.unit_one.refresh_from_db()
        self.unit_two.refresh_from_db()
        self.assertEqual(self.unit_one.internet_charges, Decimal("1500.00"))
        self.assertEqual(self.unit_two.internet_charges, Decimal("0.00"))

    def test_bulk_update_is_limited_to_the_same_property(self):
        response = self.post_update(
            id=self.unit_one.pk,
            field="security_deposit_amount",
            value="50000",
            apply_to_property=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["affected_count"], 2)
        self.unit_one.refresh_from_db()
        self.unit_two.refresh_from_db()
        self.other_unit.refresh_from_db()
        self.assertEqual(
            self.unit_one.security_deposit_amount, Decimal("50000.00")
        )
        self.assertEqual(
            self.unit_two.security_deposit_amount, Decimal("50000.00")
        )
        self.assertEqual(
            self.other_unit.security_deposit_amount, Decimal("0.00")
        )

    def test_security_text_is_kept_and_can_be_bulk_updated(self):
        response = self.post_update(
            id=self.unit_one.pk,
            field="security_requires",
            value="Two Months",
            apply_to_property=True,
        )

        self.assertEqual(response.status_code, 200)
        self.unit_two.refresh_from_db()
        self.assertEqual(self.unit_two.security_requires, "Two Months")

    def test_negative_amount_is_rejected(self):
        response = self.post_update(
            id=self.unit_one.pk,
            field="monthly_rent",
            value="-1",
        )

        self.assertEqual(response.status_code, 400)
        self.unit_one.refresh_from_db()
        self.assertEqual(self.unit_one.monthly_rent, Decimal("25000.00"))

    def test_unit_list_shows_new_charge_columns_and_inventory(self):
        item = InventoryItemDefinition.objects.create(
            name="Ceiling Fan",
            code="test-ceiling-fan",
            default_quantity=0,
        )
        UnitInventoryItem.objects.create(
            unit=self.unit_one,
            item=item,
            quantity=3,
            condition="Working",
        )

        response = self.client.get(reverse("properties:unit_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Internet Charges")
        self.assertContains(response, "Security Text")
        self.assertContains(response, "Security Amount")
        self.assertContains(response, "3 Ceiling Fan")
