import os
from decimal import Decimal

from django.conf import settings
from django.db import models
from core.utils.text import normalize_title_fields


def renewal_file_upload_to(instance, filename):
    lease_id = instance.lease_id or "new"
    renewal_number = instance.renewal_number or "new"
    return os.path.join(
        "leases",
        "renewals",
        str(lease_id),
        f"renewal_{renewal_number}",
        filename,
    )


class LeaseRenewal(models.Model):
    lease = models.ForeignKey(
        "leases.Lease",
        on_delete=models.CASCADE,
        related_name="renewals",
    )
    renewal_number = models.PositiveIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    agreement_date = models.DateField(null=True, blank=True)
    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2)
    society_maintenance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    water_charges = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    internet_charges = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    agreement_charges = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    security_deposit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    rent_increase_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("10.00"),
    )
    witness1_name = models.CharField(max_length=100, null=True, blank=True)
    witness1_cnic = models.CharField(max_length=20, null=True, blank=True)
    witness2_name = models.CharField(max_length=100, null=True, blank=True)
    witness2_cnic = models.CharField(max_length=20, null=True, blank=True)
    terms = models.TextField(null=True, blank=True)
    is_original = models.BooleanField(default=False)
    generated_agreement_pdf = models.FileField(
        upload_to=renewal_file_upload_to,
        null=True,
        blank=True,
    )
    generated_agreement_docx = models.FileField(
        upload_to=renewal_file_upload_to,
        null=True,
        blank=True,
    )
    signed_copy = models.FileField(
        upload_to=renewal_file_upload_to,
        null=True,
        blank=True,
    )
    police_verification_status = models.CharField(
        max_length=20,
        choices=[
            ("not_started", "Not Started"),
            ("pending", "Pending"),
            ("verified", "Verified"),
            ("rejected", "Rejected"),
            ("follow_up", "Follow Up"),
        ],
        default="not_started",
    )
    police_verification_date = models.DateField(null=True, blank=True)
    police_verification_document = models.FileField(
        upload_to=renewal_file_upload_to,
        null=True,
        blank=True,
    )
    police_verification_remarks = models.TextField(blank=True, default="")
    police_verification_follow_up_date = models.DateField(null=True, blank=True)
    police_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="police_verified_lease_histories",
    )
    is_agreement_signed = models.BooleanField(default=False)
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lease_renewals_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lease_renewals_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["renewal_number"]
        unique_together = [("lease", "renewal_number")]

    def __str__(self):
        return f"Lease #{self.lease_id} Renewal #{self.renewal_number}"

    @property
    def history_label(self):
        return "Original Lease" if self.is_original else f"Renewal #{self.renewal_number}"

    @property
    def total_monthly_amount(self):
        return (
            (self.monthly_rent or Decimal("0.00"))
            + (self.society_maintenance or Decimal("0.00"))
            + (self.water_charges or Decimal("0.00"))
            + (self.internet_charges or Decimal("0.00"))
        )

    def save(self, *args, **kwargs):
        normalize_title_fields(self, ("witness1_name", "witness2_name"))
        super().save(*args, **kwargs)


class LeaseRenewalClause(models.Model):
    renewal = models.ForeignKey(
        LeaseRenewal,
        on_delete=models.CASCADE,
        related_name="clauses",
    )
    clause_number = models.PositiveIntegerField()
    template_text = models.TextField()
    is_customized = models.BooleanField(default=False)

    class Meta:
        ordering = ["clause_number"]
        unique_together = [("renewal", "clause_number")]

    def __str__(self):
        return f"Renewal #{self.renewal_id} Clause {self.clause_number}"
