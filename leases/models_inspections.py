import os
import uuid
import builtins
from decimal import Decimal

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from core.utils.text import smart_title


def inspection_public_token():
    return uuid.uuid4().hex + uuid.uuid4().hex


def inspection_photo_upload_to(instance, filename):
    ext = os.path.splitext(filename or "")[1].lower() or ".bin"
    inspection_id = instance.detail.inspection_id if instance.detail_id else "new"
    item_part = slugify(getattr(instance.detail, "item_name", "") or "item")[:40] or "item"
    token = uuid.uuid4().hex[:8]
    return f"leases/inspections/{inspection_id}/{item_part}-{token}{ext}"


class InspectionType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    display_order = models.PositiveIntegerField(default=50)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        super().save(*args, **kwargs)


class InspectionCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    display_order = models.PositiveIntegerField(default=50)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        super().save(*args, **kwargs)


class InspectionStatus(models.Model):
    name = models.CharField(max_length=100, unique=True)
    badge_color = models.CharField(
        max_length=20,
        default="secondary",
        help_text="Bootstrap color name, e.g. success, warning, danger, info, secondary.",
    )
    display_order = models.PositiveIntegerField(default=50)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "Inspection statuses"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        self.badge_color = (self.badge_color or "secondary").strip().lower()
        super().save(*args, **kwargs)


class InspectionItem(models.Model):
    category = models.ForeignKey(
        InspectionCategory, on_delete=models.PROTECT, related_name="items"
    )
    item_name = models.CharField(max_length=150)
    default_quantity = models.PositiveIntegerField(default=1)
    display_order = models.PositiveIntegerField(default=50)
    required = models.BooleanField(default=False)
    allow_photos = models.BooleanField(default=True)
    allow_damage_cost = models.BooleanField(default=True)
    allow_notes = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category__display_order", "display_order", "item_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["category", "item_name"],
                name="uniq_inspection_item_per_category",
            )
        ]

    def __str__(self):
        return f"{self.category} - {self.item_name}"

    def save(self, *args, **kwargs):
        self.item_name = smart_title(self.item_name)
        super().save(*args, **kwargs)


class InspectionTemplate(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    items = models.ManyToManyField(InspectionItem, related_name="templates", blank=True)
    item_order = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        super().save(*args, **kwargs)

    def ordered_items(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("items")
        if prefetched is not None:
            items = [
                item
                for item in prefetched
                if item.active and getattr(item.category, "active", False)
            ]
            items.sort(
                key=lambda item: (
                    item.category.display_order,
                    item.display_order,
                    item.item_name,
                )
            )
        else:
            items = list(
                self.items.filter(active=True, category__active=True)
                .select_related("category")
                .order_by("category__display_order", "display_order", "item_name")
            )

        order = [
            int(item_id)
            for item_id in (self.item_order or [])
            if str(item_id).isdigit()
        ]
        if not order:
            return items

        item_map = {item.pk: item for item in items}
        ordered = [item_map[item_id] for item_id in order if item_id in item_map]
        ordered_ids = set(order)
        return ordered + [item for item in items if item.pk not in ordered_ids]


class LeaseInspection(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_COMPLETED = "completed"
    STATUS_APPROVED = "approved"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    lease = models.ForeignKey(
        "leases.Lease", on_delete=models.CASCADE, related_name="inspections"
    )
    property = models.ForeignKey(
        "properties.Property", on_delete=models.PROTECT, related_name="lease_inspections"
    )
    unit = models.ForeignKey(
        "properties.Unit", on_delete=models.PROTECT, related_name="lease_inspections"
    )
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.PROTECT, related_name="lease_inspections"
    )
    inspection_type = models.ForeignKey(
        InspectionType, on_delete=models.PROTECT, related_name="inspections"
    )
    inspection_template = models.ForeignKey(
        InspectionTemplate,
        on_delete=models.PROTECT,
        related_name="inspections",
        null=True,
        blank=True,
    )
    inspection_date = models.DateField(default=timezone.localdate)
    inspector = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lease_inspections",
    )
    inspector_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    overall_condition = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    tenant_comments = models.TextField(blank=True)
    inspector_comments = models.TextField(blank=True)
    manager_comments = models.TextField(blank=True)
    tenant_signature = models.TextField(blank=True)
    tenant_signed_at = models.DateTimeField(null=True, blank=True)
    inspector_signature = models.TextField(blank=True)
    inspector_signed_at = models.DateTimeField(null=True, blank=True)
    manager_signature = models.TextField(blank=True)
    manager_signed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_lease_inspections",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    public_token = models.CharField(
        max_length=64, unique=True, default=inspection_public_token
    )
    public_expires_at = models.DateTimeField(null=True, blank=True)
    public_is_active = models.BooleanField(default=False)
    audit_log = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_lease_inspections",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-inspection_date", "-id"]
        indexes = [
            models.Index(fields=["lease", "status", "inspection_date"]),
            models.Index(fields=["public_token", "public_is_active"]),
        ]

    def __str__(self):
        return f"{self.inspection_type} - Lease #{self.lease_id} - {self.inspection_date}"

    @builtins.property
    def inspector_display(self):
        return self.inspector_name or str(self.inspector or "")

    @builtins.property
    def public_link_valid(self):
        if not self.public_is_active or self.status == self.STATUS_APPROVED:
            return False
        return not self.public_expires_at or self.public_expires_at >= timezone.now()

    @builtins.property
    def completion_percent(self):
        total = self.details.count()
        if not total:
            return 0
        completed = self.details.exclude(status_name="").count()
        return round((completed / total) * 100)

    def add_audit(self, action, user=None, extra=None):
        entry = {
            "at": timezone.now().isoformat(),
            "action": action,
            "user": str(user) if user and getattr(user, "is_authenticated", False) else "",
            "extra": extra or {},
        }
        self.audit_log = [*(self.audit_log or []), entry]
        self.save(update_fields=["audit_log", "updated_at"])

    def snapshot_template_items(self):
        if self.details.exists() or not self.inspection_template_id:
            return
        items = self.inspection_template.ordered_items()
        rows = []
        for idx, item in enumerate(items, start=1):
            rows.append(
                InspectionDetail(
                    inspection=self,
                    category=item.category.name,
                    item_name=item.item_name,
                    quantity=item.default_quantity,
                    display_order=idx,
                    required=item.required,
                    allow_photos=item.allow_photos,
                    allow_damage_cost=item.allow_damage_cost,
                    allow_notes=item.allow_notes,
                )
            )
        InspectionDetail.objects.bulk_create(rows)

    def get_absolute_url(self):
        return reverse("leases:inspection_detail", args=[self.pk])


class InspectionDetail(models.Model):
    inspection = models.ForeignKey(
        LeaseInspection, on_delete=models.CASCADE, related_name="details"
    )
    category = models.CharField(max_length=100)
    item_name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    status_name = models.CharField(max_length=100, blank=True)
    status_badge_color = models.CharField(max_length=20, blank=True)
    remarks = models.TextField(blank=True)
    damage_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    display_order = models.PositiveIntegerField(default=50)
    required = models.BooleanField(default=False)
    allow_photos = models.BooleanField(default=True)
    allow_damage_cost = models.BooleanField(default=True)
    allow_notes = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "id"]
        indexes = [models.Index(fields=["inspection", "category", "display_order"])]

    def __str__(self):
        return f"{self.category} - {self.item_name}"


class InspectionPhoto(models.Model):
    detail = models.ForeignKey(
        InspectionDetail, on_delete=models.CASCADE, related_name="photos"
    )
    image = models.FileField(
        upload_to=inspection_photo_upload_to,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp", "mp4", "mov", "avi", "mkv"])],
    )
    caption = models.CharField(max_length=120, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inspection_photos_uploaded",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return self.caption or os.path.basename(self.image.name)


class InspectionMeterReading(models.Model):
    METER_TYPES = [
        ("electric", "Electric Meter"),
        ("water", "Water Meter"),
        ("gas", "Gas Meter"),
    ]
    inspection = models.ForeignKey(
        LeaseInspection, on_delete=models.CASCADE, related_name="meter_readings"
    )
    meter_type = models.CharField(max_length=20, choices=METER_TYPES)
    opening_reading = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    closing_reading = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    remarks = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["meter_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["inspection", "meter_type"],
                name="uniq_inspection_meter_type",
            )
        ]

    @property
    def consumption(self):
        return (self.closing_reading or Decimal("0.00")) - (self.opening_reading or Decimal("0.00"))


class InspectionKey(models.Model):
    inspection = models.ForeignKey(LeaseInspection, on_delete=models.CASCADE, related_name="keys")
    name = models.CharField(max_length=120)
    quantity_issued = models.PositiveIntegerField(default=0)
    quantity_returned = models.PositiveIntegerField(default=0)
    remarks = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        super().save(*args, **kwargs)


class InspectionAppliance(models.Model):
    inspection = models.ForeignKey(
        LeaseInspection, on_delete=models.CASCADE, related_name="appliances"
    )
    name = models.CharField(max_length=120)
    condition = models.CharField(max_length=120, blank=True)
    remarks = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        self.condition = smart_title(self.condition) if self.condition else ""
        super().save(*args, **kwargs)


class InspectionDamageCharge(models.Model):
    inspection = models.ForeignKey(
        LeaseInspection, on_delete=models.CASCADE, related_name="damage_charges"
    )
    detail = models.ForeignKey(
        InspectionDetail,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="damage_charges",
    )
    damage_description = models.TextField()
    repair_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    charge_tenant = models.BooleanField(default=True)
    generate_invoice = models.BooleanField(default=False)
    invoice = models.ForeignKey(
        "invoices.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inspection_damage_charges",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
