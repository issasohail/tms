from django.dispatch import receiver
from django.db.models.signals import post_save, post_delete
from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.db.models import Sum
from properties.models import Property
from decimal import Decimal


class Invoice(models.Model):
    INVOICE_STATUS = (
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled'),
    )

    # Replace direct import with string reference: 'leases.Lease'
    lease = models.ForeignKey(
        'leases.Lease',  # String reference instead of direct import
        on_delete=models.CASCADE,
        related_name='invoices'
    )
    invoice_number = models.CharField(max_length=20, unique=True, blank=True)
    issue_date = models.DateField()
    due_date = models.DateField()
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], blank=True, null=True, default=Decimal('0.00'))
    status = models.CharField(
        max_length=20, choices=INVOICE_STATUS, default='sent', blank=True)
    description = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date']

    def __str__(self):
        return f"Invoice #{self.invoice_number} - {self.lease.id}"

    @property
    def total_amount(self):
        return sum(item.amount for item in self.items.all())

    @property
    def total(self):
        # fix bug: an InvoiceItem doesn't have .items
        return self.amount

    def _generate_invoice_number(self):
        # Example for Sept 2, 2025 → 202509245-001  (245th day of 2025)
        prefix = timezone.localdate().strftime("%Y%m%j")  # yyyymmddd (day-of-year)
        last = (
            Invoice.objects
            .filter(invoice_number__startswith=f"{prefix}-")
            .order_by('-invoice_number')
            .first()
        )
        last_seq = 0
        if last:
            try:
                last_seq = int(last.invoice_number.split('-', 1)[1])
            except Exception:
                last_seq = 0
        return f"{prefix}-{last_seq + 1:03d}"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            # retry once if a race causes unique collision
            for _ in range(2):
                self.invoice_number = self._generate_invoice_number()
                try:
                    return super().save(*args, **kwargs)
                except Exception as e:
                    # If unique collision, loop and try next number
                    if 'unique' in str(e).lower():
                        continue
                    raise
            # final attempt
        return super().save(*args, **kwargs)


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name='items')

    category = models.ForeignKey(
        'ItemCategory', on_delete=models.PROTECT)  # NEW (required)
    description = models.CharField(max_length=200, blank=True, null=True)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=False,
        blank=False,
        default=Decimal('0.00'),
        validators=[MinValueValidator(0)]
    )

    is_recurring = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.description} - {self.amount}"

    @property
    def total(self):
        return self.amount


class ItemCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

# models.py

# invoices/models.py


class RecurringCharge(models.Model):
    KIND = [
        ('FIXED', 'Fixed amount'),
        ('WATER_SPLIT', 'Water split (per property)'),
    ]
    SCOPE = [
        ('LEASE', 'One lease'),
        ('PROPERTY', 'All active leases in a property'),
        ('GLOBAL', 'All active leases'),
    ]

    kind = models.CharField(max_length=20, choices=KIND, default='FIXED')
    scope = models.CharField(max_length=20, choices=SCOPE, default='LEASE')

    lease = models.ForeignKey(
        'leases.Lease', null=True, blank=True, on_delete=models.CASCADE)
    property = models.ForeignKey(
        Property, null=True, blank=True, on_delete=models.CASCADE)

    category = models.ForeignKey(ItemCategory, on_delete=models.PROTECT)
    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(
        # used by FIXED
        max_digits=10, decimal_places=2, default=Decimal('0.00'))
    day_of_month = models.PositiveSmallIntegerField(default=1)

    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    # add as item on the main monthly invoice
    combine_with_rent = models.BooleanField(default=True)
    last_applied = models.DateField(null=True, blank=True)  # idempotency aid

    class Meta:
        indexes = [
            models.Index(fields=['active', 'scope', 'kind', 'start_date']),
        ]


def _recalc_invoice_amount(invoice: Invoice):
    total = invoice.items.aggregate(total=Sum('amount'))['total'] or 0
    # store as field for reporting/filters; user can't edit in form
    Invoice.objects.filter(pk=invoice.pk).update(amount=total)


@receiver(post_save, sender=InvoiceItem)
def on_item_save(sender, instance, **kwargs):
    _recalc_invoice_amount(instance.invoice)


@receiver(post_delete, sender=InvoiceItem)
def on_item_delete(sender, instance, **kwargs):
    _recalc_invoice_amount(instance.invoice)

# invoices/models.py


class WaterBill(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE)
    period = models.DateField(
        help_text="Use first day of month, e.g. 2025-09-01")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=200, blank=True)
    posted = models.BooleanField(default=False)

    class Meta:
        unique_together = [('property', 'period')]  # prevent double posting
# invoices/models.py
from decimal import Decimal
from django.utils import timezone

# ...existing models: Invoice, InvoiceItem, RecurringCharge, WaterBill...


class SecurityDepositTransaction(models.Model):
    allocation = models.OneToOneField(
        "payments.PaymentAllocation",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="security_amt"
    )

    TYPE_CHOICES = [
        ('REQUIRED', 'Required (Agreed Deposit)'),
        ('PAYMENT', 'Payment In'),
        ('REFUND', 'Refund Out'),
        ('DAMAGE', 'Damage / Adjustment'),
        ('ADJUST', 'Manual Adjustment'),
    ]

    lease = models.ForeignKey(
        'leases.Lease',
        on_delete=models.CASCADE,
        related_name='security_transactions'
    )
    date = models.DateField(default=timezone.now)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00')
    )
    notes = models.TextField(blank=True, null=True)

    # optional links (for traceability)
    payment = models.ForeignKey(
        'payments.Payment',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='security_deposit_movements'
    )
    invoice_item = models.ForeignKey(
        'invoices.InvoiceItem',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='security_deposit_movements'
    )

    class Meta:
        ordering = ['date', 'id']

    def __str__(self):
        return f"{self.lease_id} {self.type} {self.amount} on {self.date}"
