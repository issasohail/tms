import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from core.utils.text import smart_title


MAINTENANCE_FILE_EXTENSIONS = [
    "jpg", "jpeg", "png", "webp", "heic", "heif", "pdf", "mp4", "mov", "webm", "avi", "mkv",
    "doc", "docx", "xls", "xlsx", "txt", "csv",
]
MAINTENANCE_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAINTENANCE_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}


def _safe_name_part(value, fallback="item", max_len=42):
    value = str(value or fallback).strip()
    safe = "".join(ch if ch.isalnum() else "-" for ch in value)
    safe = "-".join(part for part in safe.split("-") if part)
    return (safe or fallback)[:max_len]


def _maintenance_media_base(instance):
    request_obj = instance.request
    property_name = getattr(getattr(request_obj, "building", None), "property_name", "") or "property"
    unit_name = getattr(getattr(request_obj, "unit", None), "unit_number", "") or "unit"
    date_value = getattr(request_obj, "reported_date", None) or timezone.localdate()
    date_part = date_value.strftime("%Y-%m-%d")
    serial = MaintenanceRequestMedia.objects.filter(request=request_obj).count() + 1
    return (
        f"{_safe_name_part(property_name)}_"
        f"{_safe_name_part(unit_name)}_"
        f"{date_part}_{serial:03d}"
    )


def maintenance_media_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    base = _maintenance_media_base(instance)
    folder = f"maintenance/{instance.request_id or 'new'}"
    path = f"{folder}/{base}{ext}"
    suffix = 1
    while default_storage.exists(path):
        path = f"{folder}/{base}-{suffix:02d}{ext}"
        suffix += 1
    return path


class MaintenanceRequest(models.Model):
    SOURCE_MANUAL = "manual"
    SOURCE_PUBLIC_LINK = "public_link"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_PUBLIC_LINK, "Public Link"),
    ]
    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("normal", "Normal"),
        ("high", "High"),
        ("urgent", "Urgent"),
    ]
    STATUS_CHOICES = [
        ("new", "New"),
        ("in_progress", "In Progress"),
        ("waiting", "Waiting"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_requests",
    )
    lease = models.ForeignKey(
        "leases.Lease",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_requests",
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_requests",
    )
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    category = models.CharField(max_length=80, blank=True, default="General")
    category_ref = models.ForeignKey(
        "maintenance.MaintenanceCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requests",
    )
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="normal")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new")
    reported_date = models.DateField(default=timezone.localdate)
    resolved_date = models.DateField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_maintenance_requests",
    )
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    admin_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_maintenance_requests",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_maintenance_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-reported_date", "-id"]

    def __str__(self):
        return f"#{self.pk} {self.title}"

    def get_absolute_url(self):
        return reverse("maintenance:request_detail", kwargs={"pk": self.pk})

    @property
    def building(self):
        return getattr(self.unit, "property", None)

    @property
    def current_lease(self):
        if self.lease_id:
            return self.lease
        if not self.unit_id:
            return None
        from leases.models import Lease

        request_date = self.reported_date or timezone.localdate()
        lease = (
            Lease.objects.select_related("tenant", "unit", "unit__property")
            .filter(unit_id=self.unit_id, start_date__lte=request_date, end_date__gte=request_date)
            .order_by("-start_date", "-id")
            .first()
        )
        if lease:
            return lease
        return (
            Lease.objects.select_related("tenant", "unit", "unit__property")
            .filter(unit_id=self.unit_id, status="active")
            .order_by("-start_date", "-id")
            .first()
        )

    @property
    def lease_tenant(self):
        if self.tenant_id:
            return self.tenant
        lease = self.current_lease
        return getattr(lease, "tenant", None)

    def save(self, *args, **kwargs):
        self.title = smart_title(self.title)
        if self.lease_id:
            self.unit = self.lease.unit
            self.tenant = self.lease.tenant
        if self.category_ref_id:
            self.category = self.category_ref.name
        if self.status == "completed" and not self.resolved_date:
            self.resolved_date = timezone.localdate()
        super().save(*args, **kwargs)


class MaintenanceCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "Maintenance categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        super().save(*args, **kwargs)


class MaintenanceRequestMedia(models.Model):
    request = models.ForeignKey(
        MaintenanceRequest,
        on_delete=models.CASCADE,
        related_name="media",
    )
    file = models.FileField(
        upload_to=maintenance_media_upload_to,
        validators=[FileExtensionValidator(MAINTENANCE_FILE_EXTENSIONS)],
        max_length=255,
    )
    description = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    uploaded_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)
    original_filename = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return self.original_filename or os.path.basename(self.file.name)

    @property
    def is_image(self):
        return os.path.splitext(self.file.name or "")[1].lower() in MAINTENANCE_IMAGE_EXTENSIONS

    @property
    def is_video(self):
        return os.path.splitext(self.file.name or "")[1].lower() in MAINTENANCE_VIDEO_EXTENSIONS

    @property
    def is_pdf(self):
        return os.path.splitext(self.file.name or "")[1].lower() == ".pdf"

    @property
    def display_filename(self):
        return os.path.basename(self.file.name or self.original_filename or "file")

    @property
    def file_size(self):
        if not self.file:
            return None
        try:
            return self.file.size
        except (FileNotFoundError, OSError, ValueError):
            return None

    def save(self, *args, **kwargs):
        if self.file and not self.original_filename:
            self.original_filename = os.path.basename(self.file.name or "")
        super().save(*args, **kwargs)


class MaintenanceRequestStatusLog(models.Model):
    request = models.ForeignKey(
        MaintenanceRequest,
        on_delete=models.CASCADE,
        related_name="status_logs",
    )
    old_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    changed_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-changed_at", "-id"]

    def __str__(self):
        return f"{self.request_id}: {self.old_status} -> {self.new_status}"
