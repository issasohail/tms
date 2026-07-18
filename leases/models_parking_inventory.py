import builtins
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ParkingPolicy(models.Model):
    property = models.OneToOneField(
        "properties.Property", null=True, blank=True, on_delete=models.CASCADE,
        related_name="parking_policy",
    )
    unit = models.OneToOneField(
        "properties.Unit", null=True, blank=True, on_delete=models.CASCADE,
        related_name="parking_policy",
    )
    lease = models.OneToOneField(
        "leases.Lease", null=True, blank=True, on_delete=models.CASCADE,
        related_name="parking_policy",
    )
    enabled = models.BooleanField(default=False)
    monthly_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("2000.00")
    )
    unauthorized_parking_penalty = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("5000.00")
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Parking policies"

    def clean(self):
        targets = sum(bool(value) for value in (self.property_id, self.unit_id, self.lease_id))
        if targets != 1:
            raise ValidationError("A parking policy must target exactly one property, unit, or lease.")

    @builtins.property
    def scope_label(self):
        if self.lease_id:
            return f"Lease #{self.lease_id}"
        if self.unit_id:
            return str(self.unit)
        return str(self.property)

    def __str__(self):
        return f"Parking policy: {self.scope_label}"


class ParkingSpace(models.Model):
    property = models.ForeignKey(
        "properties.Property", on_delete=models.CASCADE, related_name="parking_spaces"
    )
    label = models.CharField(max_length=30)
    vehicle_type = models.ForeignKey(
        "leases.LeaseVehicleType", null=True, blank=True,
        on_delete=models.PROTECT, related_name="parking_spaces",
    )
    monthly_rate_override = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["property__property_name", "label"]
        constraints = [
            models.UniqueConstraint(fields=["property", "label"], name="uniq_parking_space_label")
        ]

    def __str__(self):
        return f"{self.property.property_name} {self.label}"


class LeaseParkingAllocation(models.Model):
    lease = models.ForeignKey(
        "leases.Lease", on_delete=models.CASCADE, related_name="parking_allocations"
    )
    parking_space = models.ForeignKey(
        ParkingSpace, on_delete=models.PROTECT, related_name="allocations"
    )
    vehicle = models.ForeignKey(
        "leases.LeaseVehicle", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="parking_allocations",
    )
    agreed_monthly_rate = models.DecimalField(max_digits=10, decimal_places=2)
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    active_space_key = models.PositiveIntegerField(null=True, blank=True, unique=True, editable=False)
    recurring_charge = models.OneToOneField(
        "invoices.RecurringCharge", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="parking_allocation",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["parking_space__label"]

    def clean(self):
        if self.lease_id and self.parking_space_id:
            if self.lease.unit.property_id != self.parking_space.property_id:
                raise ValidationError("The parking space must belong to the lease property.")
        if self.vehicle_id and self.vehicle.lease_id != self.lease_id:
            raise ValidationError("The selected vehicle must belong to this lease.")

    def save(self, *args, **kwargs):
        self.active_space_key = self.parking_space_id if self.is_active and not self.end_date else None
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Lease #{self.lease_id} - {self.parking_space.label}"


class InventoryItemDefinition(models.Model):
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=100, unique=True)
    unit_label = models.CharField(max_length=30, default="item")
    default_quantity = models.PositiveIntegerField(default=0)
    default_condition = models.CharField(max_length=120, blank=True, default="Working order")
    include_in_clause = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=50)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class BaseInventoryItem(models.Model):
    item = models.ForeignKey(InventoryItemDefinition, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=0)
    condition = models.CharField(max_length=120, blank=True)
    is_included = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PropertyInventoryItem(BaseInventoryItem):
    property = models.ForeignKey(
        "properties.Property", on_delete=models.CASCADE, related_name="inventory_items"
    )

    class Meta:
        ordering = ["item__sort_order", "item__name"]
        constraints = [
            models.UniqueConstraint(fields=["property", "item"], name="uniq_property_inventory_item")
        ]


class UnitInventoryItem(BaseInventoryItem):
    unit = models.ForeignKey(
        "properties.Unit", on_delete=models.CASCADE, related_name="inventory_items"
    )

    class Meta:
        ordering = ["item__sort_order", "item__name"]
        constraints = [
            models.UniqueConstraint(fields=["unit", "item"], name="uniq_unit_inventory_item")
        ]


class LeaseInventoryItem(BaseInventoryItem):
    lease = models.ForeignKey(
        "leases.Lease", on_delete=models.CASCADE, related_name="inventory_items"
    )
    snapshot_source = models.CharField(max_length=20, blank=True, default="unit")

    class Meta:
        ordering = ["item__sort_order", "item__name"]
        constraints = [
            models.UniqueConstraint(fields=["lease", "item"], name="uniq_lease_inventory_item")
        ]
