import os
import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone


MAINTENANCE_FILE_EXTENSIONS = [
    "jpg", "jpeg", "png", "webp", "pdf", "mp4", "mov", "avi", "mkv"
]


def maintenance_media_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"maintenance/{instance.request_id or 'new'}/{uuid.uuid4().hex}{ext}"


class MaintenanceRequest(models.Model):
    CATEGORY_CHOICES = [
        ("general", "General"),
        ("electric", "Electric"),
        ("plumbing", "Plumbing"),
        ("paint", "Paint"),
        ("appliance", "Appliance"),
        ("cleaning", "Cleaning"),
        ("security", "Security"),
        ("other", "Other"),
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

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_requests",
    )
    building = models.ForeignKey(
        "properties.Property",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_requests",
    )
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
    title = models.CharField(max_length=160)
    description = models.TextField()
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default="general")
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
        return os.path.splitext(self.file.name or "")[1].lower() in {
            ".jpg", ".jpeg", ".png", ".webp"
        }


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
