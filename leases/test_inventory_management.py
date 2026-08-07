from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from leases.models import Lease
from leases.models_parking_inventory import (
    InventoryItemDefinition,
    LeaseInventoryItem,
    UnitInventoryItem,
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
        self.fan, _ = InventoryItemDefinition.objects.update_or_create(
            code="ceiling_fan",
            defaults={
                "name": "Ceiling Fan",
                "default_quantity": 3,
                "sort_order": 20,
            },
        )
        LeaseInventoryItem.objects.update_or_create(
            lease=self.lease,
            item=self.fan,
            defaults={
                "quantity": 4,
                "condition": "Good",
                "snapshot_source": "lease",
            },
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
        unit_names = [row["item"].name for row in effective_inventory(unit=self.unit)]
        self.assertIn("Air Conditioner", unit_names)
        self.assertIn("Ceiling Fan", unit_names)
        self.assertLess(
            unit_names.index("Air Conditioner"), unit_names.index("Ceiling Fan")
        )
        lease_item_names = {row["item"].name for row in effective_inventory(lease=self.lease)}
        self.assertIn("Ceiling Fan", lease_item_names)
        lease_row = self.lease.inventory_items.get(item=self.fan)
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
        lease_row = self.lease.inventory_items.select_related("item").get(item=self.fan)
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

    def test_lease_detail_shows_add_and_unit_import_controls(self):
        response = self.client.get(reverse("leases:lease_detail", args=[self.lease.pk]))

        self.assertContains(response, "Add Item")
        self.assertContains(response, "Import Unit Inventory")
        self.assertContains(response, "Add Missing Only")
        self.assertContains(response, "Replace with Unit Defaults")
        self.assertContains(response, "S.No.")
        self.assertContains(response, "Or enter a new item name")
        self.assertNotContains(response, "Inventory &amp; Condition")

    def test_lease_edit_inventory_panel_shows_controls_and_full_inventory(self):
        response = self.client.get(reverse("leases:lease_update", args=[self.lease.pk]))

        self.assertContains(response, "Add Item")
        self.assertContains(response, "Import Unit Inventory")
        self.assertContains(response, "Full Lease Inventory")
        self.assertContains(response, "Ceiling Fan")
        self.assertContains(response, "S.No.")
        self.assertContains(response, "Or enter a new item name")

    def test_add_item_from_lease_detail_creates_lease_override(self):
        refrigerator = InventoryItemDefinition.objects.create(
            name="Refrigerator",
            code="refrigerator",
            default_quantity=1,
            sort_order=30,
        )

        response = self.client.post(
            reverse("leases:inventory_manage", args=["lease", self.lease.pk]),
            {
                "action": "save",
                "return_to_detail": "1",
                "item_id": str(refrigerator.pk),
                "quantity": "2",
                "condition": "Good",
                "is_included": "on",
            },
        )

        self.assertRedirects(
            response, reverse("leases:lease_detail", args=[self.lease.pk])
        )
        row = LeaseInventoryItem.objects.get(
            lease=self.lease, item=refrigerator
        )
        self.assertEqual(row.quantity, 2)
        self.assertEqual(row.condition, "Good")
        self.assertEqual(row.snapshot_source, "lease")

    def test_inventory_action_can_return_to_edit_inventory_panel(self):
        refrigerator = InventoryItemDefinition.objects.create(
            name="Refrigerator",
            code="refrigerator",
            default_quantity=1,
            sort_order=30,
        )

        response = self.client.post(
            reverse("leases:inventory_manage", args=["lease", self.lease.pk]),
            {
                "action": "save",
                "return_to_edit": "1",
                "item_id": str(refrigerator.pk),
                "quantity": "1",
                "condition": "Working order",
                "is_included": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse("leases:lease_update", args=[self.lease.pk]) + "#pane-inventory",
            fetch_redirect_response=False,
        )

    def test_add_new_item_also_adds_it_to_unit_defaults(self):
        response = self.client.post(
            reverse("leases:inventory_manage", args=["lease", self.lease.pk]),
            {
                "action": "add_lease_item",
                "return_to_detail": "1",
                "new_item_name": "Microwave Oven",
                "quantity": "2",
                "condition": "New",
                "is_included": "on",
            },
        )

        self.assertRedirects(
            response, reverse("leases:lease_detail", args=[self.lease.pk])
        )
        item = InventoryItemDefinition.objects.get(code="microwave_oven")
        lease_row = LeaseInventoryItem.objects.get(lease=self.lease, item=item)
        unit_row = UnitInventoryItem.objects.get(unit=self.unit, item=item)
        self.assertEqual(lease_row.quantity, 2)
        self.assertEqual(lease_row.condition, "New")
        self.assertEqual(unit_row.quantity, 2)
        self.assertEqual(unit_row.condition, "New")

    def test_inline_update_changes_name_quantity_and_condition(self):
        response = self.client.post(
            reverse("leases:inventory_manage", args=["lease", self.lease.pk]),
            {
                "action": "save",
                "return_to_detail": "1",
                "item_id": str(self.fan.pk),
                "item_name": "Premium Ceiling Fan",
                "quantity": "7",
                "condition": "Serviced",
                "is_included": "1",
            },
        )

        self.assertRedirects(
            response, reverse("leases:lease_detail", args=[self.lease.pk])
        )
        self.fan.refresh_from_db()
        row = LeaseInventoryItem.objects.get(lease=self.lease, item=self.fan)
        self.assertEqual(self.fan.name, "Premium Ceiling Fan")
        self.assertEqual(row.quantity, 7)
        self.assertEqual(row.condition, "Serviced")

    def test_delete_removes_item_from_lease_but_preserves_unit_default(self):
        UnitInventoryItem.objects.create(
            unit=self.unit,
            item=self.fan,
            quantity=3,
            condition="Unit default",
        )

        response = self.client.post(
            reverse("leases:inventory_manage", args=["lease", self.lease.pk]),
            {
                "action": "delete",
                "return_to_detail": "1",
                "item_id": str(self.fan.pk),
            },
        )

        self.assertRedirects(
            response, reverse("leases:lease_detail", args=[self.lease.pk])
        )
        lease_row = LeaseInventoryItem.objects.get(lease=self.lease, item=self.fan)
        self.assertFalse(lease_row.is_included)
        self.assertEqual(lease_row.quantity, 0)
        self.assertTrue(UnitInventoryItem.objects.filter(unit=self.unit, item=self.fan).exists())
        detail_response = self.client.get(
            reverse("leases:lease_detail", args=[self.lease.pk])
        )
        self.assertNotIn(
            self.fan.pk,
            [row["item"].pk for row in detail_response.context["effective_inventory"]],
        )

    def test_import_missing_preserves_existing_lease_values(self):
        refrigerator = InventoryItemDefinition.objects.create(
            name="Refrigerator",
            code="refrigerator",
            default_quantity=1,
            sort_order=30,
        )
        UnitInventoryItem.objects.create(
            unit=self.unit,
            item=refrigerator,
            quantity=2,
            condition="New",
        )

        response = self.client.post(
            reverse("leases:inventory_manage", args=["lease", self.lease.pk]),
            {"action": "import_missing", "return_to_detail": "1"},
        )

        self.assertRedirects(
            response, reverse("leases:lease_detail", args=[self.lease.pk])
        )
        self.assertEqual(
            LeaseInventoryItem.objects.get(lease=self.lease, item=self.fan).quantity,
            4,
        )
        imported = LeaseInventoryItem.objects.get(
            lease=self.lease, item=refrigerator
        )
        self.assertEqual(imported.quantity, 2)
        self.assertEqual(imported.condition, "New")
        self.assertEqual(imported.snapshot_source, "unit")

    def test_replace_import_refreshes_existing_values_and_removes_inactive_rows(self):
        retired_item = InventoryItemDefinition.objects.create(
            name="Retired Item",
            code="retired_item",
            default_quantity=1,
            is_active=False,
            sort_order=40,
        )
        LeaseInventoryItem.objects.create(
            lease=self.lease,
            item=retired_item,
            quantity=9,
            condition="Lease only",
            snapshot_source="lease",
        )
        UnitInventoryItem.objects.create(
            unit=self.unit,
            item=self.fan,
            quantity=6,
            condition="Serviced",
        )

        response = self.client.post(
            reverse("leases:inventory_manage", args=["lease", self.lease.pk]),
            {"action": "import_replace", "return_to_detail": "1"},
        )

        self.assertRedirects(
            response, reverse("leases:lease_detail", args=[self.lease.pk])
        )
        refreshed = LeaseInventoryItem.objects.get(
            lease=self.lease, item=self.fan
        )
        self.assertEqual(refreshed.quantity, 6)
        self.assertEqual(refreshed.condition, "Serviced")
        self.assertEqual(refreshed.snapshot_source, "unit")
        self.assertFalse(
            LeaseInventoryItem.objects.filter(
                lease=self.lease, item=retired_item
            ).exists()
        )
