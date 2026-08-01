from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from leases.models import Lease
from leases.models_parking_inventory import (
    InventoryItemDefinition,
    LeaseInventoryItem,
)
from leases.services.inventory_parking import effective_inventory
from properties.models import Property, Unit
from tenants.models import Tenant


class InventoryDefinitionManagementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="inventory-manager",
            email="inventory-manager@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)
        property_obj = Property.objects.create(
            property_name="Inventory Definition Property",
            owner_name="Test Owner",
            owner_cnic="61101-3333333-3",
            type="Residential",
            property_type="house",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=property_obj, unit_number="ID-1")
        tenant = Tenant.objects.create(
            first_name="Inventory",
            last_name="Definition",
            cnic="61101-4444444-4",
        )
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=self.unit,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            monthly_rent=10000,
        )
        self.fan = InventoryItemDefinition.objects.create(
            name="Ceiling Fan",
            code="ceiling_fan",
            default_quantity=3,
            sort_order=20,
        )
        LeaseInventoryItem.objects.create(
            lease=self.lease,
            item=self.fan,
            quantity=4,
            condition="Good",
            snapshot_source="lease",
        )

    def test_add_item_uses_order_and_does_not_rewrite_existing_lease_snapshot(self):
        response = self.client.post(
            reverse("leases:global_inventory_manage"),
            {
                "action": "add_definition",
                "name": "Air Conditioner",
                "sort_order": "10",
                "unit_label": "unit",
                "quantity": "1",
                "condition": "Working order",
                "include_in_clause": "on",
            },
        )

        self.assertRedirects(response, reverse("leases:global_inventory_manage"))
        air_conditioner = InventoryItemDefinition.objects.get(code="air_conditioner")
        self.assertEqual(air_conditioner.sort_order, 10)
        self.assertEqual(
            [row["item"].name for row in effective_inventory(unit=self.unit)],
            ["Air Conditioner", "Ceiling Fan"],
        )
        self.assertEqual(
            [row["item"].name for row in effective_inventory(lease=self.lease)],
            ["Ceiling Fan"],
        )
        lease_row = self.lease.inventory_items.get()
        self.assertEqual(lease_row.quantity, 4)
        self.assertEqual(lease_row.condition, "Good")

    def test_edit_name_and_order_preserves_code_and_lease_values(self):
        response = self.client.post(
            reverse("leases:global_inventory_manage"),
            {
                "action": "save_definition",
                "item_id": str(self.fan.pk),
                "name": "Premium Ceiling Fan",
                "sort_order": "5",
                "unit_label": "fan",
                "quantity": "6",
                "condition": "New",
                "include_in_clause": "on",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("leases:global_inventory_manage"))
        self.fan.refresh_from_db()
        lease_row = self.lease.inventory_items.select_related("item").get()
        self.assertEqual(self.fan.name, "Premium Ceiling Fan")
        self.assertEqual(self.fan.code, "ceiling_fan")
        self.assertEqual(self.fan.sort_order, 5)
        self.assertEqual(lease_row.item.name, "Premium Ceiling Fan")
        self.assertEqual(lease_row.quantity, 4)
        self.assertEqual(lease_row.condition, "Good")

    def test_unit_inventory_page_shows_serial_numbers_and_definition_link(self):
        response = self.client.get(
            reverse("leases:inventory_manage", args=["unit", self.unit.pk])
        )

        self.assertContains(response, "S.No.")
        self.assertContains(response, "Add / Edit Item List")
        self.assertContains(response, "Ceiling Fan")

        definition_response = self.client.get(
            reverse("leases:global_inventory_manage")
        )
        self.assertContains(definition_response, "Add Inventory Item")
        self.assertContains(definition_response, "Order")
        self.assertContains(definition_response, "existing lease inventory snapshots")
