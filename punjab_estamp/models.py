from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class PunjabEStampDistrict(models.Model):
    name = models.CharField(max_length=100, unique=True)
    portal_value = models.CharField(max_length=40, unique=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=50)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Punjab e-Stamp district"
        verbose_name_plural = "Punjab e-Stamp districts"

    def __str__(self):
        return self.name


class PunjabEStampTehsil(models.Model):
    district = models.ForeignKey(
        PunjabEStampDistrict,
        on_delete=models.PROTECT,
        related_name="tehsils",
    )
    name = models.CharField(max_length=100)
    portal_value = models.CharField(max_length=40, db_index=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=50)

    class Meta:
        ordering = ["district__sort_order", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["district", "name"],
                name="uniq_punjab_estamp_tehsil_name",
            ),
            models.UniqueConstraint(
                fields=["district", "portal_value"],
                name="uniq_punjab_estamp_tehsil_portal",
            ),
        ]
        verbose_name = "Punjab e-Stamp tehsil"
        verbose_name_plural = "Punjab e-Stamp tehsils"

    def __str__(self):
        return f"{self.name} ({self.district.name})"


class PunjabEStampRelation(models.Model):
    name = models.CharField(max_length=40, unique=True)
    portal_value = models.CharField(max_length=40, unique=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=50)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Punjab e-Stamp relation"
        verbose_name_plural = "Punjab e-Stamp relations"

    def __str__(self):
        return self.name


class PunjabEStampPurpose(models.Model):
    name = models.CharField(max_length=255, unique=True)
    portal_value = models.CharField(max_length=40, unique=True)
    denomination = models.PositiveIntegerField(default=100)
    default_continuation_sheets = models.PositiveSmallIntegerField(default=1)
    active = models.BooleanField(default=True)
    is_default_for_lease = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=50)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Punjab e-Stamp purpose"
        verbose_name_plural = "Punjab e-Stamp purposes"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default_for_lease:
            type(self).objects.filter(is_default_for_lease=True).exclude(pk=self.pk).update(
                is_default_for_lease=False
            )


class LeaseEStampWorkflow(models.Model):
    STATUS_READY = "ready"
    STATUS_CHALLAN_GENERATED = "challan_generated"
    STATUS_PAID = "paid"
    STATUS_STAMP_ISSUED = "stamp_issued"
    STATUS_UPLOADED = "uploaded"
    STATUS_INVALID = "invalid"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_READY, "Ready"),
        (STATUS_CHALLAN_GENERATED, "Challan Generated"),
        (STATUS_PAID, "Paid"),
        (STATUS_STAMP_ISSUED, "Stamp Issued"),
        (STATUS_UPLOADED, "Uploaded to TMS"),
        (STATUS_INVALID, "Invalid"),
        (STATUS_FAILED, "Failed"),
    )

    lease = models.ForeignKey(
        "leases.Lease",
        on_delete=models.CASCADE,
        related_name="punjab_estamp_workflows",
    )
    lease_history = models.OneToOneField(
        "leases.LeaseRenewal",
        on_delete=models.CASCADE,
        related_name="punjab_estamp_workflow",
    )
    purpose = models.ForeignKey(
        PunjabEStampPurpose,
        on_delete=models.PROTECT,
        related_name="workflows",
        null=True,
        blank=True,
    )
    lease_document = models.OneToOneField(
        "leases.LeaseDocument",
        on_delete=models.SET_NULL,
        related_name="punjab_estamp_workflow",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_READY,
        db_index=True,
    )

    applicant_snapshot = models.JSONField(default=dict, blank=True)
    property_snapshot = models.JSONField(default=dict, blank=True)
    portal_snapshot = models.JSONField(default=dict, blank=True)

    challan_number = models.CharField(max_length=100, blank=True, db_index=True)
    psid = models.CharField(max_length=100, blank=True, db_index=True)
    stamp_number = models.CharField(max_length=100, blank=True, db_index=True)
    challan_history = models.JSONField(default=list, blank=True)
    replacement_count = models.PositiveSmallIntegerField(default=0)

    last_portal_error = models.TextField(blank=True)
    challan_generated_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    stamp_issued_at = models.DateTimeField(null=True, blank=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_punjab_estamp_workflows",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="updated_punjab_estamp_workflows",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(fields=["lease", "status"], name="punjab_es_lease_status_idx"),
        ]
        verbose_name = "Lease Punjab e-Stamp workflow"
        verbose_name_plural = "Lease Punjab e-Stamp workflows"

    def __str__(self):
        return f"Lease {self.lease_id} / history {self.lease_history_id}: {self.get_status_display()}"

    def clean(self):
        super().clean()
        if (
            self.lease_id
            and self.lease_history_id
            and self.lease_history.lease_id != self.lease_id
        ):
            raise ValidationError(
                {"lease_history": "The selected agreement history does not belong to this lease."}
            )
        if self.lease_document_id:
            if self.lease_document.lease_id != self.lease_id:
                raise ValidationError(
                    {"lease_document": "The e-Stamp document does not belong to this lease."}
                )
            if (
                self.lease_document.lease_history_id
                and self.lease_document.lease_history_id != self.lease_history_id
            ):
                raise ValidationError(
                    {"lease_document": "The e-Stamp document belongs to another agreement history."}
                )

