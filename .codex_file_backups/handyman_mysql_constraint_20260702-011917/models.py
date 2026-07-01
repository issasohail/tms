import os

from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg, Q
from django.urls import reverse
from django.utils import timezone

from core.upload_utils import compress_instance_file_field
from core.utils.text import smart_title


HANDYMAN_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "heic", "heif"]
HANDYMAN_FILE_EXTENSIONS = HANDYMAN_IMAGE_EXTENSIONS + ["pdf", "doc", "docx", "xls", "xlsx"]


def _safe_name(value, fallback="handyman"):
    value = str(value or fallback).strip()
    safe = "".join(ch if ch.isalnum() else "-" for ch in value)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:60] or fallback


def handyman_document_upload_to(instance, filename):
    ext = os.path.splitext(filename or "")[1].lower()
    name = _safe_name(getattr(instance, "full_name", "") or f"handyman-{instance.pk or 'new'}")
    stem = os.path.splitext(os.path.basename(filename or "file"))[0]
    return f"handyman/profiles/{name}/{timezone.localdate():%Y%m%d}-{_safe_name(stem, 'file')}{ext}"


def handyman_attachment_upload_to(instance, filename):
    ext = os.path.splitext(filename or "")[1].lower()
    assignment_id = instance.assignment_id or "new"
    stem = os.path.splitext(os.path.basename(filename or "file"))[0]
    return f"handyman/jobs/{assignment_id}/{timezone.localdate():%Y%m%d}-{_safe_name(stem, 'file')}{ext}"


class HandymanCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "Handyman categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        super().save(*args, **kwargs)


class HandymanProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handyman_profile",
    )
    full_name = models.CharField(max_length=140)
    phone = models.CharField(max_length=30, blank=True)
    whatsapp_number = models.CharField(max_length=30, blank=True)
    categories = models.ManyToManyField(HandymanCategory, blank=True, related_name="handymen")
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    is_preferred = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    photo = models.ImageField(
        upload_to=handyman_document_upload_to,
        blank=True,
        null=True,
        validators=[FileExtensionValidator(HANDYMAN_IMAGE_EXTENSIONS)],
        max_length=255,
    )
    id_card_front = models.ImageField(
        upload_to=handyman_document_upload_to,
        blank=True,
        null=True,
        validators=[FileExtensionValidator(HANDYMAN_IMAGE_EXTENSIONS)],
        max_length=255,
    )
    id_card_back = models.ImageField(
        upload_to=handyman_document_upload_to,
        blank=True,
        null=True,
        validators=[FileExtensionValidator(HANDYMAN_IMAGE_EXTENSIONS)],
        max_length=255,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_preferred", "full_name"]

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse("handyman:handyman_detail", args=[self.pk])

    @property
    def display_phone(self):
        return self.whatsapp_number or self.phone

    @property
    def average_rating(self):
        value = self.ratings.aggregate(avg=Avg("rating"))["avg"]
        return round(value or 0, 1)

    @property
    def rating_count(self):
        return self.ratings.count()

    @property
    def completed_jobs_count(self):
        return self.assignments.filter(status=MaintenanceHandymanAssignment.STATUS_COMPLETED).count()

    def save(self, *args, **kwargs):
        self.full_name = smart_title(self.full_name)
        for field_name in ("photo", "id_card_front", "id_card_back"):
            compress_instance_file_field(self, field_name)
        super().save(*args, **kwargs)


class MaintenanceHandymanAssignment(models.Model):
    STATUS_ASSIGNED = "assigned"
    STATUS_ACCEPTED = "accepted"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ASSIGNED, "Assigned"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    maintenance_request = models.ForeignKey(
        "maintenance.MaintenanceRequest",
        on_delete=models.CASCADE,
        related_name="handyman_assignments",
    )
    handyman = models.ForeignKey(HandymanProfile, on_delete=models.PROTECT, related_name="assignments")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handyman_assignments_created",
    )
    assigned_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ASSIGNED)
    tenant_notified_at = models.DateTimeField(null=True, blank=True)
    handyman_notified_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_current", "-assigned_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["maintenance_request"],
                condition=Q(is_current=True),
                name="one_current_handyman_assignment_per_request",
            )
        ]

    def __str__(self):
        return f"{self.maintenance_request_id} - {self.handyman}"


class HandymanJobAttachment(models.Model):
    TYPE_INVOICE = "invoice"
    TYPE_JOB_PHOTO = "job_photo"
    TYPE_CHOICES = [
        (TYPE_INVOICE, "Invoice"),
        (TYPE_JOB_PHOTO, "Job Photo"),
    ]

    assignment = models.ForeignKey(MaintenanceHandymanAssignment, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(
        upload_to=handyman_attachment_upload_to,
        validators=[FileExtensionValidator(HANDYMAN_FILE_EXTENSIONS)],
        max_length=255,
    )
    attachment_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    original_filename = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=40, blank=True)
    whatsapp_media_id = models.CharField(max_length=160, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return self.original_filename or os.path.basename(self.file.name or "file")

    @property
    def is_image(self):
        return os.path.splitext(self.file.name or "")[1].lower().lstrip(".") in HANDYMAN_IMAGE_EXTENSIONS

    def save(self, *args, **kwargs):
        if self.file and not self.original_filename:
            self.original_filename = os.path.basename(self.file.name or "")
        super().save(*args, **kwargs)


class HandymanRating(models.Model):
    handyman = models.ForeignKey(HandymanProfile, on_delete=models.CASCADE, related_name="ratings")
    maintenance_request = models.ForeignKey(
        "maintenance.MaintenanceRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handyman_ratings",
    )
    lease = models.ForeignKey("leases.Lease", on_delete=models.SET_NULL, null=True, blank=True, related_name="handyman_ratings")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.SET_NULL, null=True, blank=True, related_name="handyman_ratings")
    property = models.ForeignKey("properties.Property", on_delete=models.SET_NULL, null=True, blank=True, related_name="handyman_ratings")
    unit = models.ForeignKey("properties.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="handyman_ratings")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comments = models.TextField(blank=True)
    property_name_snapshot = models.CharField(max_length=160, blank=True)
    unit_name_snapshot = models.CharField(max_length=80, blank=True)
    tenant_name_snapshot = models.CharField(max_length=160, blank=True)
    tenant_phone_snapshot = models.CharField(max_length=40, blank=True)
    job_title_snapshot = models.CharField(max_length=180, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        unique_together = [("handyman", "maintenance_request", "tenant")]

    def __str__(self):
        return f"{self.handyman} - {self.rating}"

    def save(self, *args, **kwargs):
        job = self.maintenance_request
        if job:
            self.lease = self.lease or job.lease
            self.tenant = self.tenant or job.lease_tenant
            self.property = self.property or job.building
            self.unit = self.unit or job.unit
            self.job_title_snapshot = self.job_title_snapshot or job.title
        if self.property and not self.property_name_snapshot:
            self.property_name_snapshot = getattr(self.property, "property_name", "") or str(self.property)
        if self.unit and not self.unit_name_snapshot:
            self.unit_name_snapshot = getattr(self.unit, "unit_number", "") or str(self.unit)
        if self.tenant and not self.tenant_name_snapshot:
            self.tenant_name_snapshot = self.tenant.get_full_name()
            self.tenant_phone_snapshot = getattr(self.tenant, "phone", "") or ""
        super().save(*args, **kwargs)
