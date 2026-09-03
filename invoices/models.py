from django.conf import settings
from django.dispatch import receiver
from django.core.cache import cache
from django.db.models.signals import post_save, post_delete
from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.db.models import Sum
from properties.models import Property
from decimal import Decimal, ROUND_CEILING
from core.utils.text import smart_title


def round_amount_up_to_nearest_10(amount):
    if amount is None:
        return amount
    amount = Decimal(amount)
    return ((amount / Decimal('10')).to_integral_value(rounding=ROUND_CEILING) * Decimal('10')).quantize(Decimal('0.01'))


class Invoice(models.Model):
    INVOICE_STATUS = (
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled'),
    )
    LIFECYCLE_STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('issued', 'Issued'),
        ('disputed', 'Disputed'),
        ('cancelled', 'Cancelled'),
        ('void', 'Void'),
        ('written_off', 'Written Off'),
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
    late_fee_hold_until = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Do not send reminders or apply reminder-based late fees through this date.",
    )
    late_fee_hold_reason = models.CharField(max_length=255, blank=True, default='')
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], blank=True, null=True, default=Decimal('0.00'))
    status = models.CharField(
        max_length=20, choices=INVOICE_STATUS, default='sent', blank=True)
    lifecycle_status = models.CharField(
        max_length=20, choices=LIFECYCLE_STATUS_CHOICES, default='issued', db_index=True
    )
    lifecycle_status_reason = models.CharField(max_length=255, blank=True, default='')
    lifecycle_status_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='invoice_lifecycle_updates',
    )
    lifecycle_status_updated_at = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date']
        permissions = [
            ('change_invoice_lifecycle_status', 'Can change invoice lifecycle status'),
            ('cancel_invoice', 'Can cancel invoice'),
            ('void_invoice', 'Can void invoice'),
            ('write_off_invoice', 'Can write off invoice'),
            ('view_invoice_status_history', 'Can view invoice status history'),
        ]

    def __str__(self):
        return f"Invoice #{self.invoice_number} - {self.lease.id}"

    @property
    def historical_unit(self):
        from invoices.historical_units import resolve_historical_invoice_unit

        return resolve_historical_invoice_unit(self)

    def late_fee_hold_is_active(self, today=None):
        today = today or timezone.localdate()
        return bool(self.late_fee_hold_until and self.late_fee_hold_until >= today)

    @property
    def total_amount(self):
        return sum(item.amount for item in self.items.all())

    @property
    def total(self):
        # fix bug: an InvoiceItem doesn't have .items
        return self.amount

    def accounting_allocation(self):
        """Return (allocated, outstanding, payment_status) using the project's
        existing oldest-invoice-first lease payment convention.

        PaymentDetail.lease_amount is used when a split payment exists; otherwise
        the full Payment.amount applies to the lease. This mirrors migration 0022.
        """
        cached = getattr(self, '_accounting_allocation_cache', None)
        if cached is not None:
            return cached

        from django.db.models import Case, DecimalField, F, Sum, When
        from django.db.models.functions import Coalesce
        from payments.models import Payment

        zero = Decimal('0.00')
        money_field = DecimalField(max_digits=12, decimal_places=2)
        available = (
            Payment.objects.filter(lease_id=self.lease_id)
            .aggregate(
                total=Coalesce(
                    Sum(
                        Case(
                            When(detail__isnull=False, then=F('detail__lease_amount')),
                            default=F('amount'),
                            output_field=money_field,
                        )
                    ),
                    zero,
                    output_field=money_field,
                )
            )['total']
            or zero
        )
        eligible = Invoice.objects.filter(lease_id=self.lease_id).exclude(
            lifecycle_status__in=('cancelled', 'void')
        ).exclude(status='cancelled').order_by('issue_date', 'id')

        allocated = zero
        remaining_after = zero
        eligible_rows = list(eligible.only('id', 'amount', 'due_date'))
        self_is_last_eligible = bool(eligible_rows and eligible_rows[-1].pk == self.pk)
        for invoice in eligible_rows:
            amount = invoice.amount or zero
            current = min(max(available, zero), amount)
            available -= current
            if invoice.pk == self.pk:
                allocated = current
                remaining_after = available
                break

        amount = self.amount or zero
        outstanding = max(amount - allocated, zero)
        if amount <= zero or allocated >= amount:
            payment_status = (
                'overpaid' if self_is_last_eligible and remaining_after > zero else 'paid'
            )
        elif allocated > zero:
            payment_status = 'partially_paid'
        elif self.due_date and self.due_date < timezone.localdate():
            payment_status = 'overdue'
        else:
            payment_status = 'unpaid'
        result = (allocated, outstanding, payment_status)
        self._accounting_allocation_cache = result
        return result

    @property
    def amount_paid(self):
        return self.accounting_allocation()[0]

    @property
    def outstanding_balance(self):
        return self.accounting_allocation()[1]

    @property
    def payment_status(self):
        return self.accounting_allocation()[2]

    @property
    def payment_status_display(self):
        return {
            'unpaid': 'Unpaid',
            'partially_paid': 'Partially Paid',
            'paid': 'Paid',
            'overpaid': 'Overpaid',
            'overdue': 'Overdue',
        }.get(self.payment_status, smart_title(self.payment_status))

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


class InvoiceStatusHistory(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='status_history')
    previous_status = models.CharField(max_length=20, blank=True, default='')
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='invoice_status_history_changes',
    )
    reason = models.CharField(max_length=255, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at', '-id')

    def __str__(self):
        return f"Invoice {self.invoice_id}: {self.previous_status} -> {self.new_status}"


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name='items')

    category = models.ForeignKey(
        'ItemCategory', on_delete=models.PROTECT)  # NEW (required)
    description = models.CharField(max_length=500, blank=True, null=True)
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

    def save(self, *args, **kwargs):
        self.amount = round_amount_up_to_nearest_10(self.amount)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "amount" not in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"amount"}
        return super().save(*args, **kwargs)

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

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        result = super().save(*args, **kwargs)
        cache.delete("invoices.active_item_categories")
        return result

    def delete(self, *args, **kwargs):
        cache.delete("invoices.active_item_categories")
        return super().delete(*args, **kwargs)

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
    payment_detail = models.OneToOneField(
        "payments.PaymentDetail",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="security_amt",
        db_column="allocation_id",
    )

    TYPE_CHOICES = [
        ('REQUIRED', 'Required (Agreed Deposit)'),
        ('PAYMENT', 'Payment In'),
        ('REFUND', 'Refund Out'),
        ('DAMAGE', 'Damage / Adjustment'),
        ('ADJUST', 'Manual Adjustment'),
    ]
    REFUND_STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("PAID", "Paid"),
        ("TRANSFERRED", "Transferred to Ledger"),
        ("CANCELLED", "Cancelled"),
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
    deduction_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    deduction_reason = models.TextField(blank=True, null=True)
    refund_payment_method = models.ForeignKey(
        'core.PaymentMethod',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='security_refunds',
    )
    refund_status = models.CharField(
        max_length=20,
        choices=REFUND_STATUS_CHOICES,
        blank=True,
        default="PAID",
    )
    refund_notes = models.TextField(blank=True, null=True)

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


class SecurityDepositLedgerTransfer(models.Model):
    lease = models.ForeignKey(
        'leases.Lease', on_delete=models.PROTECT, related_name='security_ledger_transfers'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_date = models.DateField(default=timezone.localdate)
    reason = models.CharField(max_length=255)
    reference = models.CharField(max_length=80, unique=True)
    ledger_credit_payment = models.OneToOneField(
        'payments.Payment', on_delete=models.PROTECT, related_name='security_ledger_transfer_credit'
    )
    security_movement = models.OneToOneField(
        SecurityDepositTransaction, on_delete=models.PROTECT, related_name='ledger_transfer_event'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='security_ledger_transfers_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='security_ledger_transfers_reversed',
    )
    reversal_reason = models.CharField(max_length=255, blank=True, default='')
    reversal_payment = models.OneToOneField(
        'payments.Payment', null=True, blank=True, on_delete=models.PROTECT,
        related_name='security_ledger_transfer_reversal',
    )

    class Meta:
        ordering = ('-transaction_date', '-id')
        permissions = [
            ('transfer_security_deposit_to_ledger', 'Can transfer refundable security deposit to ledger'),
            ('reverse_security_deposit_ledger_transfer', 'Can reverse security deposit ledger transfer'),
        ]

    @property
    def is_reversed(self):
        return self.reversed_at is not None

    def __str__(self):
        return f"{self.reference}: {self.amount}"


class MonthlyBillingRun(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PREFLIGHT = "preflight"
    STATUS_GENERATING = "generating"
    STATUS_READY = "ready"
    STATUS_PARTIAL = "partial"
    STATUS_SENT = "sent"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_ROLLED_BACK = "rolled_back"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PREFLIGHT, "Preflight"),
        (STATUS_GENERATING, "Generating"),
        (STATUS_READY, "Ready"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_SENT, "Sent"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_ROLLED_BACK, "Rolled Back"),
    ]

    billing_month = models.DateField(help_text="First day of the month being billed.")
    run_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    total_active_leases = models.PositiveIntegerField(default=0)
    recurring_created_count = models.PositiveIntegerField(default=0)
    missing_recurring_count = models.PositiveIntegerField(default=0)
    electric_ready_count = models.PositiveIntegerField(default=0)
    electric_pending_count = models.PositiveIntegerField(default=0)
    manual_electric_count = models.PositiveIntegerField(default=0)
    water_missing_count = models.PositiveIntegerField(default=0)
    ready_to_send_count = models.PositiveIntegerField(default=0)
    pdf_generating_count = models.PositiveIntegerField(default=0)
    sending_count = models.PositiveIntegerField(default=0)
    pending_attention_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    excluded_count = models.PositiveIntegerField(default=0)
    rolled_back_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="monthly_billing_runs",
    )
    created_by_label = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    audit_log = models.JSONField(default=list, blank=True)
    dry_run_summary = models.JSONField(default=dict, blank=True)
    created_invoice_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-billing_month", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["billing_month"],
                name="uniq_monthly_billing_run_month",
            ),
        ]
        indexes = [
            models.Index(fields=["billing_month", "status"]),
        ]

    def __str__(self):
        return f"Monthly billing {self.billing_month:%b %Y} ({self.get_status_display()})"


class MonthlyBillingRunItem(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_READY = "ready_to_send"
    STATUS_PENDING = "pending_attention"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_EXCLUDED = "excluded"
    STATUS_ROLLED_BACK = "rolled_back"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_READY, "Ready to Send"),
        (STATUS_PENDING, "Pending Attention"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_SKIPPED, "Skipped"),
        (STATUS_EXCLUDED, "Excluded"),
        (STATUS_ROLLED_BACK, "Rolled Back"),
    ]

    ISSUE_INACTIVE_LEASE = "inactive_lease"
    ISSUE_MISSING_RECURRING = "missing_recurring_invoice_setup"
    ISSUE_RECURRING_FAILED = "recurring_invoice_generation_failed"
    ISSUE_DUPLICATE_INVOICE = "duplicate_invoice_exists"
    ISSUE_METER_MISSING = "latest_meter_reading_missing"
    ISSUE_METER_OFFLINE = "meter_offline"
    ISSUE_MANUAL_ELECTRIC = "manual_electric_billing"
    ISSUE_ELECTRIC_UNVERIFIED = "electric_billing_not_verified"
    ISSUE_WATER_MISSING = "water_charge_missing"
    ISSUE_PHONE_MISSING = "tenant_phone_missing"
    ISSUE_PDF_FAILED = "pdf_generation_failed"
    ISSUE_WHATSAPP_FAILED = "whatsapp_send_failed"
    ISSUE_UNUSUAL_TOTAL = "unusual_invoice_total"
    ISSUE_ZERO_TOTAL = "zero_invoice_total"

    ISSUE_CHOICES = [
        (ISSUE_INACTIVE_LEASE, "Inactive lease"),
        (ISSUE_MISSING_RECURRING, "Missing recurring invoice setup"),
        (ISSUE_RECURRING_FAILED, "Recurring invoice generation failed"),
        (ISSUE_DUPLICATE_INVOICE, "Duplicate invoice exists"),
        (ISSUE_METER_MISSING, "Latest meter reading missing"),
        (ISSUE_METER_OFFLINE, "Meter offline"),
        (ISSUE_MANUAL_ELECTRIC, "Manual electric billing"),
        (ISSUE_ELECTRIC_UNVERIFIED, "Electric billing not verified"),
        (ISSUE_WATER_MISSING, "Water charge missing"),
        (ISSUE_PHONE_MISSING, "Tenant phone missing"),
        (ISSUE_PDF_FAILED, "PDF generation failed"),
        (ISSUE_WHATSAPP_FAILED, "WhatsApp send failed"),
        (ISSUE_UNUSUAL_TOTAL, "Unusual invoice total"),
        (ISSUE_ZERO_TOTAL, "Zero invoice total"),
    ]

    billing_run = models.ForeignKey(
        MonthlyBillingRun,
        on_delete=models.CASCADE,
        related_name="items",
    )
    lease = models.ForeignKey("leases.Lease", on_delete=models.CASCADE, related_name="monthly_billing_items")
    tenant = models.ForeignKey("tenants.Tenant", null=True, blank=True, on_delete=models.SET_NULL)
    property = models.ForeignKey("properties.Property", null=True, blank=True, on_delete=models.SET_NULL)
    unit = models.ForeignKey("properties.Unit", null=True, blank=True, on_delete=models.SET_NULL)
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL, related_name="monthly_billing_items")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    issue_code = models.CharField(max_length=80, choices=ISSUE_CHOICES, blank=True)
    issue_message = models.TextField(blank=True)
    recurring_invoice_found = models.BooleanField(default=False)
    recurring_invoice_created = models.BooleanField(default=False)
    electric_required = models.BooleanField(default=False)
    electric_ready = models.BooleanField(default=False)
    manual_electric = models.BooleanField(default=False)
    electric_charge = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    electric_period_start = models.DateField(null=True, blank=True)
    electric_period_end = models.DateField(null=True, blank=True)
    latest_meter_reading_date = models.DateField(null=True, blank=True)
    water_required = models.BooleanField(default=False)
    water_charge = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    water_resolved = models.BooleanField(default=False)
    invoice_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    invoice_pdf = models.FileField(upload_to="invoices/monthly_billing_pdfs/", null=True, blank=True, max_length=255)
    whatsapp_message_id = models.CharField(max_length=160, blank=True)
    whatsapp_status = models.CharField(max_length=30, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_text = models.TextField(blank=True)
    excluded_reason = models.TextField(blank=True)
    excluded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="excluded_monthly_billing_items",
    )
    excluded_at = models.DateTimeField(null=True, blank=True)
    rolled_back_at = models.DateTimeField(null=True, blank=True)
    rollback_message = models.TextField(blank=True)
    created_invoice_ids = models.JSONField(default=list, blank=True)
    created_invoice_item_ids = models.JSONField(default=list, blank=True)
    dry_run_data = models.JSONField(default=dict, blank=True)
    log = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["billing_run", "property_id", "unit_id", "lease_id"]
        constraints = [
            models.UniqueConstraint(fields=["billing_run", "lease"], name="uniq_monthly_billing_run_lease"),
        ]
        indexes = [
            models.Index(fields=["billing_run", "status"]),
            models.Index(fields=["lease", "status"]),
        ]

    def __str__(self):
        return f"{self.billing_run_id} lease {self.lease_id} {self.get_status_display()}"


class InvoiceLateFeeReminder(models.Model):
    SOURCE_AUTO = "auto"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = (
        (SOURCE_AUTO, "Automatic"),
        (SOURCE_MANUAL, "Manual"),
    )

    STATUS_SENT = "sent"
    STATUS_FEE_PENDING = "fee_pending"
    STATUS_FEE_APPLIED = "fee_applied"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_SENT, "Reminder sent"),
        (STATUS_FEE_PENDING, "Fee pending approval"),
        (STATUS_FEE_APPLIED, "Fee applied"),
        (STATUS_FAILED, "Failed"),
    )

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="late_fee_reminders",
    )
    reminder_number = models.PositiveIntegerField()
    sent_via = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SENT)
    whatsapp_message = models.ForeignKey(
        "whatsapp.WhatsAppMessageLog",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="late_fee_reminders",
    )
    late_fee_item = models.ForeignKey(
        InvoiceItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    error_text = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "reminder_number"],
                name="uniq_invoice_late_fee_reminder_number",
            ),
        ]
        indexes = [
            models.Index(fields=["invoice", "reminder_number"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Invoice #{self.invoice_id} reminder #{self.reminder_number} ({self.status})"


class BillingProgressJob(models.Model):
    ACTION_RUN_BILLING = "run_billing"
    ACTION_PREFLIGHT = "preflight"
    ACTION_RECURRING = "generate_recurring"
    ACTION_ELECTRIC = "generate_electric"
    ACTION_READY = "prepare_ready"
    ACTION_PDFS = "generate_pdfs"
    ACTION_SEND = "send_ready"
    ACTION_RETRY = "retry_failed"
    ACTION_ROLLBACK = "rollback_run"

    ACTION_CHOICES = [
        (ACTION_RUN_BILLING, "Run Billing"),
        (ACTION_PREFLIGHT, "Preflight"),
        (ACTION_RECURRING, "Generate Recurring"),
        (ACTION_ELECTRIC, "Generate Electric"),
        (ACTION_READY, "Prepare Ready"),
        (ACTION_PDFS, "Generate PDFs"),
        (ACTION_SEND, "Send WhatsApp"),
        (ACTION_RETRY, "Retry Failed"),
        (ACTION_ROLLBACK, "Rollback Run"),
    ]

    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    billing_run = models.ForeignKey(
        MonthlyBillingRun,
        on_delete=models.CASCADE,
        related_name="progress_jobs",
    )
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    rq_job_id = models.CharField(max_length=120, blank=True)
    current_step = models.CharField(max_length=120, blank=True)
    current_tenant = models.CharField(max_length=160, blank=True)
    current_property = models.CharField(max_length=160, blank=True)
    current_unit = models.CharField(max_length=80, blank=True)
    current_index = models.PositiveIntegerField(default=0)
    total_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    elapsed_seconds = models.PositiveIntegerField(default=0)
    average_seconds = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    estimated_remaining_seconds = models.PositiveIntegerField(default=0)
    message = models.TextField(blank=True)
    error_text = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_progress_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["billing_run", "status"]),
            models.Index(fields=["rq_job_id"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()} for run {self.billing_run_id}: {self.get_status_display()}"
