from decimal import Decimal
import logging

from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.urls import reverse
from django.conf import settings

from core.models import PaymentMethod

logger = logging.getLogger(__name__)


class Payment(models.Model):
    lease = models.ForeignKey(
        "leases.Lease",
        on_delete=models.CASCADE,
        related_name="payments",
    )
    payment_date = models.DateField(default=timezone.now)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.PROTECT,
        related_name="payments",
        null=True,
        blank=True,
    )
    description = models.CharField(max_length=200, blank=True, null=True, default="")
    reference_number = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    receipt_sent = models.BooleanField(default=False)
    receipt_sent_via = models.CharField(
        max_length=20,
        choices=[
            ("email", "Email"),
            ("whatsapp", "WhatsApp"),
            ("both", "Both"),
            ("none", "Not Sent"),
        ],
        default="none",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Payment of {self.amount} by {self.lease_id} on {self.payment_date}"

    def save(self, *args, **kwargs):
        self.full_clean()

        if self.pk:
            original = Payment.objects.get(pk=self.pk)
            changes = []
            for field in ["amount", "payment_method", "payment_date"]:
                if getattr(original, field) != getattr(self, field):
                    changes.append(
                        f"{field} changed from {getattr(original, field)} to {getattr(self, field)}"
                    )
            if changes:
                logger.info(f"Payment #{self.id} updated. Changes: {'; '.join(changes)}")

        super().save(*args, **kwargs)

        # NOTE: This part is suspicious because Payment has no "invoice" FK in this model.
        # If you truly have a reverse relation, keep it, otherwise remove it.
        if hasattr(self, "invoice") and self.invoice:
            total_paid = self.invoice.payments.aggregate(total=models.Sum("amount"))["total"] or 0
            if total_paid >= self.invoice.amount:
                self.invoice.status = "paid"
            elif total_paid > 0:
                self.invoice.status = "partially_paid"
            else:
                self.invoice.status = "unpaid"
            self.invoice.save()

    def get_absolute_url(self):
        return reverse("payments:payment_detail", args=[self.pk])

    def get_edit_url(self):
        return reverse("payments:payment_update", args=[self.pk])

    def get_delete_url(self):
        return reverse("payments:payment_delete", args=[self.pk])

    def get_receipt_url(self):
        return reverse("payments:send_receipt", args=[self.pk])

    @property
    def lease_effective_amount(self) -> Decimal:
        alloc = getattr(self, "allocation", None)
        if alloc:
            return alloc.lease_amount or Decimal("0.00")
        return self.amount or Decimal("0.00")

    @property
    def security_effective_amount(self) -> Decimal:
        alloc = getattr(self, "allocation", None)
        if alloc:
            return alloc.security_amount or Decimal("0.00")
        return Decimal("0.00")
    
class PaymentAllocation(models.Model):
    SECURITY_TYPES = [
        ("PAYMENT", "Payment"),
        ("REFUND", "Refund"),
        ("DAMAGE", "Damage"),
        ("ADJUST", "Adjust"),
    ]

    payment = models.OneToOneField(
        "payments.Payment",
        on_delete=models.CASCADE,
        related_name="allocation",
        null=True,
        blank=True,
    )

    lease_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    security_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    security_type = models.CharField(
        max_length=20,
        choices=SECURITY_TYPES,
        default="PAYMENT",
        blank=True,
        null=True,
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="allocation_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)
    last_reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def total_received(self):
        return (self.lease_amount or 0) + (self.security_amount or 0)

    def __str__(self):
        return f"Allocation #{self.pk} Payment #{self.payment_id}"


class AllocationAuditLog(models.Model):
    allocation = models.ForeignKey(
        PaymentAllocation,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    old_data = models.JSONField()
    new_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
