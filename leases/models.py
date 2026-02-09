from .models_lease_photos import LeaseMedia
from .storage import OverwriteStorage
from django.core.files.base import ContentFile
import io
from PIL import Image
import os
from django.utils.text import slugify
from django.db.models.signals import post_save
from django.dispatch import receiver
from properties.models import Property, Unit
import json
import os
from django.db.models.signals import pre_delete
from .models_lease_photos import LeaseMedia
from invoices.services import security_deposit_balance

from decimal import Decimal

from django.db import models
from django.db.models import Sum
from tenants.models import Tenant
from properties.models import Unit
from django.urls import reverse
from datetime import timedelta
from django.utils import timezone
from decimal import Decimal
from datetime import date
from django.db import models
from decimal import Decimal, ROUND_HALF_UP
from invoices.services import security_deposit_totals
from django.template import Template, Context
from decimal import Decimal
from django.db.models import Sum, Case, When, F, DecimalField
from django.db.models.functions import Coalesce


def default_lease_terms():
    return """No Special term."""


def signed_agreement_upload_path(instance, filename):
    ext = filename.split(".")[-1]
    tenant_name = slugify(instance.tenant.name)
    tenant_cnic = instance.tenant.cnic  # Assuming you have this in Tenant model
    lease_id = instance.pk or "new"
    filename = f"{tenant_name}-{tenant_cnic}-{lease_id}-sign-agreement.{ext}"
    return os.path.join("signed_agreements", filename)


class Lease(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("ended", "Ended"),
        ("terminated", "Terminated"),
    ]

    # Your model fields here
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="leases")
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="leases")
    agreement_date = models.DateField(null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2)
    society_maintenance = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, default=1200
    )
    water_charges = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        null=True,
        help_text="Monthly water charges (0 for none)",
    )
    internet_charges = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        null=True,
        help_text="Monthly internet charges (0 for none)",
    )
    agreement_charges = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1500.00"),
        help_text="One-time agreement charges (0 to skip)",
    )
    # total_payment = models.DecimalField(max_digits=10, decimal_places=2)
    security_deposit = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    terms = models.TextField(
        default=default_lease_terms,
        null=True,
        blank=True,
        help_text="Detailed terms and conditions of the lease agreement",
    )
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # leases/models.py - Add to Lease model
    witness1_name = models.CharField(max_length=100, null=True, blank=True)
    witness1_cnic = models.CharField(max_length=20, null=True, blank=True)
    witness2_name = models.CharField(max_length=100, null=True, blank=True)
    witness2_cnic = models.CharField(max_length=20, null=True, blank=True)
    electric_unit_rate = models.IntegerField(blank=True, null=True, default=50)
    electricity_meter_reading = models.CharField(max_length=20, null=True, blank=True)
    gas_meter_reading = models.CharField(max_length=20, null=True, blank=True)
    signed_agreement = models.FileField(
        upload_to="agreements/", blank=True, null=True, verbose_name="Signed Agreement"
    )
    security_deposit_paid = models.BooleanField(default=False)
    security_deposit_returned = models.BooleanField(default=False)
    security_deposit_return_date = models.DateField(null=True, blank=True)
    security_deposit_return_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    security_deposit_return_notes = models.TextField(null=True, blank=True)
    rent_increase_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=10.00,
        null=True,
        blank=True,
        verbose_name="Rent Increase Percentage (%)",
        help_text="Percentage increase applied when lease renews",
    )
    due_date = models.CharField(
        max_length=100, null=True, blank=True, default="5th of each month."
    )
    late_fee = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, default=500.00
    )

    # Security Deposit Installments
    security_installment_1_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    security_installment_1_date = models.DateField(null=True, blank=True)
    security_installment_2_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    security_installment_2_date = models.DateField(null=True, blank=True)
    condition_photos_signed = models.FileField(
        upload_to="leases/condition_photos/signed/",
        blank=True,
        null=True,
        help_text="Signed PDF acknowledging move-in photos (PCR)",
        verbose_name="Signed Condition Photos",
    )
    # Clause #6 specific fields
    min_lease_occupancy_months = models.PositiveIntegerField(
        default=6, null=True, blank=True
    )
    early_termination_penalty = models.DecimalField(
        max_digits=10, decimal_places=2, default=2000.00, null=True, blank=True
    )

    inventory_ceiling_fans = models.IntegerField(default=3, null=True, blank=True)
    inventory_exhaust_fans = models.IntegerField(default=2, null=True, blank=True)
    inventory_ceiling_lights = models.IntegerField(default=16, null=True, blank=True)
    inventory_stove = models.IntegerField(default=0, null=True, blank=True)
    inventory_wardrobes = models.IntegerField(default=2, null=True, blank=True)
    inventory_keys = models.IntegerField(default=2, null=True, blank=True)
    paint_condition = models.TextField(blank=True, null=True, default="New Paint.")

    # Key details
    keys_issued = models.PositiveIntegerField(default=2, null=True, blank=True)
    key_replacement_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, default=1000.00
    )
    generated_agreement = models.FileField(
        upload_to="leases/sign_agreements/",
        blank=True,
        null=True,
        help_text="Auto-generated lease agreement",
        verbose_name="Signed Agreement",
    )
    is_agreement_signed = models.BooleanField(
        default=False, help_text="Mark if agreement has been signed"
    )
    generated_agreement_docx = models.FileField(
        upload_to="leases/agreements/generated/",
        blank=True,
        null=True,
        help_text="Auto-generated lease agreement (Word)",
    )
    generated_agreement_pdf = models.FileField(
        upload_to="leases/agreements/generated/",
        blank=True,
        null=True,
        help_text="Auto-generated lease agreement (PDF)",
    )
    generated_date = models.DateTimeField(
        blank=True, null=True, help_text="When the agreement was generated"
    )
    signed_copy = models.FileField(
        upload_to="leases/agreements/signed/",
        blank=True,
        null=True,
        help_text="Scanned signed agreement with thumb impression",
    )

    def initialize_clauses(self):
        """
        For a newly created lease, copy all active DefaultClause rows
        into LeaseAgreementClause rows for this lease.
        """
        if self.clauses.exists():
            return  # already initialized

        default_clauses = DefaultClause.objects.filter(is_active=True).order_by(
            "clause_number"
        )

        for dc in default_clauses:
            LeaseAgreementClause.objects.create(
                lease=self,
                clause_number=dc.clause_number,
                template_text=dc.body,
            )
        default_clauses = [
            # Clause 1
            "That the rate of rent of the said Premises is hereby agreed at Rs. [MONTHLY_RENT]/- ( [MONTHLY_RENT_IN_WORDS]) Rupees Only per month.",
            # Clause 2
            "Tenant will also pay Rs. [SOCIETY_MAINTENANCE]/- society maintenance charges along with the rent to the first party. Tenant will pay a total of Rs. [TOTAL_MONTHLY]/monthly ([TOTAL_MONTHLY_IN_WORDS]) Rupees Only).",
            # Clause 3
            "That one month's advance rent amount Rs. [TOTAL_MONTHLY]/- ([TOTAL_MONTHLY_IN_WORDS]) Rupees Only has been paid by the Tenant and received by the Owner. Thereafter, rent will be payable monthly in advance on or before the [DUE_DATE]th of each month. In case of late payment, Rs. [LATE_FEE]/- will be charged per day as penalty after the [DUE_DATE]th of each month.",
            # Clause 4
            "That a further sum of Rs. [SECURITY_DEPOSIT]/- ([SECURITY_DEPOSIT_IN_WORDS]) Rupees Only will be paid by the tenant to the Owner as Security before taking possession. If paying in installments, first installment of Rs. [SECURITY_INSTALLMENT_1_AMOUNT]/- is due on [SECURITY_INSTALLMENT_1_DATE], and second installment of Rs. [SECURITY_INSTALLMENT_2_AMOUNT]/- is due on [SECURITY_INSTALLMENT_2_DATE]. The security is refundable at the time of vacation of said premises after deducting breakage, damages, and clearance of all utility bills (Electricity, Sui Gas, Society/Building Maintenance Charges, Telephone, etc.).",
            # Clause 5
            "That the period of tenancy is hereby agreed as [LEASE_DURATION_MONTHS] months, commencing from [START_DATE] to [END_DATE], with a rent increase of @ [RENT_INCREASE_PERCENT]% after [LEASE_DURATION_MONTHS] months. Renewal is possible with mutual consent of both parties. The Tenant shall vacate peacefully after the lease expires.",
            # Clause 6
            "That the Tenant is bound not to vacate the premises within [MIN_OCCUPANCY_PERIOD] months. If they choose to vacate earlier, they must pay Rs. [EARLY_TERMINATION_PENALTY]/- per month as penalty.",
            # Clause 7
            "That [KEYS_ISSUED] keys/keycards will be issued to the Tenant, to be returned upon vacating the premises. If lost, Rs. [KEY_REPLACEMENT_COST]/- per key/keycard will be deducted from the Security Deposit.",
            # Clause 8
            "That the Tenant shall maintain the premises in good condition, including all fittings and fixtures, and replace any broken items with equal quality. No alterations or wall drilling is allowed without written permission. Subletting is strictly prohibited.",
            # Clause 9
            "That in case the Owner sells the property, the Tenant shall have no objection and will cooperate in executing a fresh lease agreement with the new Owner for the remaining term.",
            # Clause 10
            "That the Tenant shall not demand any compensation for decoration or expenses upon vacating the premises. Any legal claims shall be deemed void.",
            # Clause 11
            "That the said premises will be used strictly for residential purposes only.",
            # Clause 12
            "That the Tenant is responsible to complete verification from the concerned Police Station.",
            # Clause 13
            "That the said premises has been handed over in working order, including [INVENTORY_CEILING_FANS] Ceiling Fans, [INVENTORY_LIGHTS] Ceiling Lights, [INVENTORY_EXHAUST_FANS] Exhaust Fans, [INVENTORY_WARDROBE] wardrobes, and [INVENTORY_STOVE] Stove(s). The Tenant shall return all in the same condition upon vacating.",
            # Clause 14
            "That the Tenant shall pay all utility bills timely and submit copies to the Owner upon request. Electricity bill will be paid at Rs. [ELECTRIC_UNIT_RATE]/- per unit to the Owner along with the rent.",
            # Clause 15
            "That a 2-month advance written notice is required from either party to vacate the premises. Failure to do so by the Tenant will result in forfeiture of the Security Deposit.",
            # Clause 16
            "That the Tenant will not rent or sublet the premises to any third party.",
            # Clause 17
            "That the Owner may visit the premises with reasonable advance notice.",
            # Clause 18
            "That during the lease period, any complaints from the society against the Tenant shall be the Tenant's responsibility.",
            # Clause 19
            "That the Electricity Meter reading is [ELECTRICITY_METER_READING] as on [METER_READING_DATE].",
            # Clause 20
            "That the Security Deposit will not be adjusted against rent under any circumstances.",
            # Clause 21
            "That smoking is not allowed in the building. Tenant is not allowed to use the common area for drying clothes.",
            # Clause 22
            "That the Tenant shall not use the terrace, hallway, or any other common areas beyond their own rented portion.",
            # Clause 23
            "That the Tenant is responsible for cleaning and maintaining common and exterior areas (doors, windows, hallways, walls, stairs, and ceilings) at least three times a week.",
            # Clause 24
            "That all legal rights regarding the said premises are reserved with the Owner.",
            # Clause 25
            "That both parties agree to abide by all terms and conditions stated in this agreement.",
            # Clause 26
            "That the Tenant shall not engage in any illegal or immoral activities on the premises.",
        ]

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "is_active", "start_date"]),
        ]

    # leases/models.py

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_file = None
        if not is_new:
            try:
                old_file = Lease.objects.get(pk=self.pk).signed_agreement
            except Lease.DoesNotExist:
                pass

        # Save initially to get self.pk
        super().save(*args, **kwargs)

        # If uploaded file is an image, convert it to PDF
        if self.signed_agreement and self.signed_agreement.name.lower().endswith(
            (".jpg", ".jpeg", ".png")
        ):
            image = Image.open(self.signed_agreement)
            image_rgb = image.convert("RGB")
            pdf_io = io.BytesIO()
            image_rgb.save(pdf_io, format="PDF")
            pdf_io.seek(0)

            # Create new filename
            tenant_name = slugify(self.tenant.name)
            tenant_cnic = self.tenant.cnic
            lease_id = self.pk
            new_filename = (
                f"{tenant_name}-{tenant_cnic}-{lease_id}-signed_agreement.pdf"
            )

            # Replace file
            self.signed_agreement.save(
                new_filename, ContentFile(pdf_io.read()), save=False
            )
            super().save(update_fields=["signed_agreement"])

        # Delete old file if replaced
        if old_file and old_file != self.signed_agreement:
            old_file.delete(save=False)

    # --- Template rendering helpers ---

    def get_template_context(self):
        """
        Build the context used in clause text.
        These keys are available in {{ }} inside DefaultClause.body
        and LeaseAgreementClause.template_text.
        """
        property_obj = getattr(self.unit, "property", None)

        monthly_rent = self.monthly_rent or 0
        maintenance = self.society_maintenance or 0
        water = self.water_charges or 0
        internet = self.internet_charges or 0

        total_monthly = monthly_rent + maintenance + water + internet
        security = self.security_deposit or 0

        return {
            # Objects
            "lease": self,
            "tenant": self.tenant,
            "unit": self.unit,
            "property": property_obj,
            # Basic values
            "monthly_rent": monthly_rent,
            "society_maintenance": maintenance,
            "water_charges": water,  # NEW
            "internet_charges": internet,  # NEW
            "total_monthly": total_monthly,
            "security_deposit": security,
            "rent_increase_percent": self.rent_increase_percent or 0,
            "start_date": self.start_date,
            "end_date": self.end_date,
            # Derived / helpers
            "lease_duration_months": self.get_lease_duration(),
            "new_rent_after_increase": self.new_rent_after_increase,
            # In words (assuming you have number_to_words)
            "monthly_rent_in_words": (
                number_to_words(int(monthly_rent)) if monthly_rent else ""
            ),
            "total_monthly_in_words": (
                number_to_words(int(total_monthly)) if total_monthly else ""
            ),
            "security_deposit_in_words": (
                number_to_words(int(security)) if security else ""
            ),
        }

    def render_text_with_context(self, raw_text: str) -> str:
        """
        Render any text block using the lease template context.
        """
        tpl = Template(raw_text)
        ctx = Context(self.get_template_context())
        return tpl.render(ctx)

    @property
    def total_rent_due(self):
        """Calculate total rent due from all invoices"""
        return self.invoices.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    # === Security Deposit computed properties ===

    @property
    def security_required(self):
        """Total agreed security deposit (from field)."""
        return self.security_deposit or Decimal("0.00")

    @property
    def security_paid_in(self):
        """How much deposit has actually been paid in (via ledger)."""
        return security_deposit_totals(self)["paid_in"]

    @property
    def security_refunded(self):
        """Total refunded back to tenant."""
        return security_deposit_totals(self)["refunded"]

    @property
    def security_damages(self):
        """Total consumed for damages/adjustments."""
        return security_deposit_totals(self)["damages"]

    @property
    def security_balance_to_collect(self):
        """
        How much deposit is still owed by tenant (positive = they owe).
        """
        return security_deposit_totals(self)["balance_to_collect"]

    @property
    def security_currently_held(self):
        """
        How much deposit you currently hold (paid - refunded - damages).
        """
        return security_deposit_totals(self)["currently_held"]

    @property
    def security_due(self):
        """
        Alias used in tables: 'Sec. Due' column.
        """
        return self.security_balance_to_collect

    @property
    def total_payments(self):
        """Calculate total payments made against this lease"""
        from payments.models import Payment

        return Payment.objects.filter(lease=self).aggregate(total=Sum("amount"))[
            "total"
        ] or Decimal("0.00")

    @property
    def get_balance(self):
        invoices_total = self.invoices.aggregate(
            t=Coalesce(Sum("amount"), Decimal("0.00"))
        )["t"]

        # IMPORTANT: only count the portion allocated to lease
        payments_total = self.payments.aggregate(
            t=Coalesce(
                Sum(
                    Case(
                        When(
                            allocation__isnull=False, then=F("allocation__lease_amount")
                        ),
                        default=F(
                            "amount"
                        ),  # legacy fallback when allocation row missing
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    )
                ),
                Decimal("0.00"),
            )
        )["t"]

        return invoices_total - payments_total

    def return_security_deposit(self, return_amount=None, notes=""):
        """
        Register a refund of security deposit and write to the ledger.
        """
        from invoices.models import SecurityDepositTransaction

        if self.security_deposit_returned:
            return False  # Already returned (simple guard)

        if return_amount is None:
            return_amount = self.security_deposit or Decimal("0.00")

        # Update legacy fields (for Excel header etc.)
        self.security_deposit_returned = True
        self.security_deposit_return_date = timezone.now().date()
        self.security_deposit_return_amount = return_amount
        self.security_deposit_return_notes = notes
        self.save()

        # NEW: write to security deposit ledger
        SecurityDepositTransaction.objects.create(
            lease=self,
            type="REFUND",
            amount=return_amount,
            notes=notes or "Security deposit refund",
        )

        # Here you would add actual payment/refund logic & notifications
        return True

    class Meta:
        ordering = ["-start_date"]

    def get_lease_duration(self):
        """Calculate lease duration in months"""
        delta = self.end_date - self.start_date
        return round(delta.days / 30)

    @property
    def new_rent_after_increase(self):
        if not self.rent_increase_percent:
            return Decimal(str(self.monthly_rent)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        new_value = Decimal(str(self.monthly_rent)) * (
            Decimal("1") + Decimal(str(self.rent_increase_percent)) / Decimal("100")
        )
        return new_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def __str__(self):
        return f"Lease #{self.id} - {self.tenant}"

    def generate_invoice(self):
        from invoices.models import Invoice

        return Invoice.objects.create(lease=self, amount=self.monthly_rent)

    def save(self, *args, **kwargs):
        # Only set end_date if not provided and start_date exists
        if not self.end_date and self.start_date:
            self.end_date = self.start_date + timedelta(days=365)
        super().save(*args, **kwargs)

    def get_renewal_url(self):
        return reverse("leases:lease_renew", args=[self.id])

    def get_print_url(self):
        return reverse("leases:print", args=[self.id])

    @property
    def property_info(self):
        """Access property through unit relationship"""
        if hasattr(self, "unit") and hasattr(self.unit, "property"):
            return self.unit.property
        return None

    @property
    def is_active(self):
        return self.status == "active"

    @property
    def lease_period(self):
        return f"{self.start_date} to {self.end_date}"

    @property
    def get_total_payment(self):
        """Returns sum of rent, maintenance, water, internet"""
        return (
            (self.monthly_rent or 0)
            + (self.society_maintenance or 0)
            + (self.water_charges or 0)
            + (self.internet_charges or 0)
        )

    @property
    def get_monthly_payment(self):
        """Same as get_total_payment for monthly view"""
        return self.get_total_payment

    @property
    def total_payment(self):
        return self.get_total_payment

    def is_ending_soon(self):
        if self.status != "active":
            return False
        return (self.end_date - timezone.now().date()).days <= 40


@receiver(pre_delete, sender=Lease)
def delete_lease_files(sender, instance, **kwargs):
    # Delete associated agreement file
    if instance.signed_agreement:
        file_path = instance.signed_agreement.path
        if os.path.exists(file_path):
            os.remove(file_path)


@receiver(post_save, sender=Lease)
def create_lease_clauses(sender, instance, created, **kwargs):
    if created:
        instance.initialize_clauses()


def days_remaining(self):
    if not self.end_date:
        return 999
    return (self.end_date - date.today()).days


def get_balance2(self):
    """
    Calculate the current balance for this lease
    (Total Payments - Total Invoices)
    """
    total_invoices = self.invoices.aggregate(total=models.Sum("amount"))[
        "total"
    ] or Decimal("0.00")

    total_payments = self.payments.aggregate(total=models.Sum("amount"))[
        "total"
    ] or Decimal("0.00")

    return total_payments - total_invoices

    # In your Lease model


@property
def total_payments(self):
    return self.payments.aggregate(
        total=Coalesce(
            Sum(
                Case(
                    When(allocation__isnull=False, then=F("allocation__lease_amount")),
                    default=F("amount"),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            ),
            Decimal("0.00"),
        )
    )["total"]


def renew_lease(
    self,
    new_end_date=None,
    rent_increase_percent=None,
    society_maintenance=None,
    notes="",
):
    """Renew the lease with custom parameters"""
    if self.status != "active":
        return None

    # Use instance value if not provided
    if rent_increase_percent is None:
        rent_increase_percent = float(self.rent_increase_percent)

    if society_maintenance is None:
        society_maintenance = self.society_maintenance

    # Calculate new rent
    new_rent = self.monthly_rent * (1 + rent_increase_percent / 100)

    # Calculate new end date (1 year from current end date by default)
    if new_end_date is None:
        new_end_date = self.end_date + timedelta(days=365)

    # Create the renewed lease
    renewed_lease = Lease.objects.create(
        tenant=self.tenant,
        unit=self.unit,
        start_date=self.end_date + timedelta(days=1),
        end_date=new_end_date,
        monthly_rent=new_rent,
        society_maintenance=self.society_maintenance,
        security_deposit=self.security_deposit,
        security_deposit_paid=True,  # Deposit carries over
        rent_increase_percent=rent_increase_percent,
        terms=self.terms,
        notes=f"Custom renewal of Lease #{self.id}. {notes}",
    )

    # Update notes on original lease
    self.notes = f"{self.notes}\n\nRenewed as Lease #{renewed_lease.id} on {timezone.now().date()}"
    self.save()

    # Here you would add code to send notifications
    # self.send_renewal_notification(renewed_lease)

    return renewed_lease


def auto_renew_if_needed(self):
    """Check if lease should auto-renew and process if needed"""
    if not self.is_active or not self.should_auto_renew():
        return False

    return self.renew_lease(notes="Automatically renewed by system")


def should_auto_renew(self):
    """Determine if lease should auto-renew"""
    # Add your business logic here - maybe a field in the model
    # For now, just checking if end date is near
    return (self.end_date - timezone.now().date()).days <= 30


def send_renewal_notification(self, renewed_lease):
    """Send notification about lease renewal"""
    # Implement your email/SMS notification logic here
    subject = f"Your lease has been renewed (#{renewed_lease.id})"
    message = f"""
        Your lease #{self.id} has been renewed as lease #{renewed_lease.id}.

        New terms:
        - Start Date: {renewed_lease.start_date}
        - End Date: {renewed_lease.end_date}
        - Monthly Rent: {renewed_lease.monthly_rent}

        Thank you for continuing with us!
        """

    # Example using Django's send_mail
    from django.core.mail import send_mail

    send_mail(
        subject,
        message,
        "noreply@yourdomain.com",
        [self.tenant.email],
        fail_silently=False,
    )


def get_renewal_rent(self):
    """Calculate what the rent would be if renewed now"""
    return self.monthly_rent * (1 + float(self.rent_increase_percent) / 100)


class LeaseAgreementClause(models.Model):
    lease = models.ForeignKey("Lease", on_delete=models.CASCADE, related_name="clauses")
    clause_number = models.PositiveIntegerField()
    template_text = models.TextField()
    is_customized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("lease", "clause_number")
        ordering = ["clause_number"]

    def __str__(self):
        return f"Lease #{self.lease_id} – Clause {self.clause_number}"


class LeaseTemplate(models.Model):
    name = models.CharField(max_length=100, unique=True)
    clauses = models.JSONField(default=list, help_text="JSON array of clause templates")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.is_default:
            LeaseTemplate.objects.filter(is_default=True).update(is_default=False)
        super().save(*args, **kwargs)


PLACEHOLDER_REGISTRY = {
    "TENANT_NAME": lambda lease: lease.tenant.get_full_name(),
    "TENANT_CNIC": lambda lease: lease.tenant.cnic,
    "TENANT_ADDRESS": lambda lease: lease.tenant.address,
    "OWNER_NAME": lambda lease: lease.unit.property.owner_name,
    "OWNER_CNIC": lambda lease: lease.unit.property.owner_cnic,
    "OWNER_ADDRESS": lambda lease: lease.unit.property.owner_address,
    "PROPERTY_NAME": lambda lease: lease.unit.property.property_name,
    "UNIT_NUMBER": lambda lease: lease.unit.unit_number,
    "UNIT_DETAILS": lambda lease: lease.unit.comments,
    "RENT_AMOUNT": lambda lease: lease.monthly_rent,
    "MONTHLY_RENT": lambda lease: lease.monthly_rent,
    "MAINTENANCE_CHARGES": lambda lease: lease.society_maintenance,
    "SOCIETY_MAINTENANCE": lambda lease: lease.society_maintenance,  # Already exists
    "FAMILY_MEMBERS": lambda lease: ", ".join(
        f"{m.full_name} ({m.relation})" if m.relation else m.full_name
        for m in lease.family_members.all()
    ),
    "SECURITY_DEPOSIT": lambda lease: lease.security_deposit,
    "START_DATE": lambda lease: lease.start_date.strftime("%d-%m-%Y"),
    "END_DATE": lambda lease: lease.end_date.strftime("%d-%m-%Y"),
    "RENT_INCREASE_PERCENT": lambda lease: lease.rent_increase_percent,
    "AGREEMENT_DATE": lambda lease: timezone.now().strftime("%d-%m-%Y"),
    # New placeholders for your template
    "SECURITY_DEPOSIT_HALF": lambda lease: lease.security_deposit / 2,
    "SECURITY_DEPOSIT_DUE_DATE": lambda lease: (
        lease.start_date + timedelta(days=30)
    ).strftime("%b %d, %Y"),
    "ELECTRICITY_METER_READING": lambda lease: lease.electricity_meter_reading
    or "Not Recorded",
    "WATER_METER_READING": lambda lease: lease.water_meter_reading or "Not Recorded",
    "TOTAL_MONTHLY": lambda lease: lease.monthly_rent
    + (lease.society_maintenance or 0),
    # 'TOTAL_MONTHLY': lambda lease: lease.society_maintenance + lease.monthly_rent,
    # For clause 5:
    "LEASE_START_DATE": lambda lease: lease.start_date.strftime("%d-%m-%Y"),
    "LEASE_END_DATE": lambda lease: lease.end_date.strftime("%d-%m-%Y"),
    "MONTHLY_RENT": lambda lease: lease.monthly_rent,
    "SOCIETY_MAINTENANCE": lambda lease: lease.society_maintenance or 0,
    "TOTAL_MONTHLY": lambda lease: lease.monthly_rent
    + (lease.society_maintenance or 0),
    "SECURITY_DEPOSIT": lambda lease: lease.security_deposit,
    "SECURITY_PAID": lambda lease: lease.security_deposit_paid or 0,
    "SECURITY_BALANCE": lambda lease: lease.security_deposit
    - (lease.security_deposit_paid or 0),
    "SECURITY_BALANCE_DUE_DATE": lambda lease: lease.security_deposit_due_date.strftime(
        "%b %d, %Y"
    ),
    "METER_READING_DATE": lambda lease: timezone.now().strftime("%b %d, %Y"),
    "MONTHLY_RENT_IN_WORDS": lambda lease: number_to_words(int(lease.monthly_rent)),
    "TOTAL_MONTHLY_IN_WORDS": lambda lease: number_to_words(
        int(lease.monthly_rent + (lease.society_maintenance or 0))
    ),
    "SECURITY_DEPOSIT_IN_WORDS": lambda lease: number_to_words(
        int(lease.security_deposit)
    ),
}

# leases/models.py (add to Lease model)


def update_from_template(self, template):
    """Update clauses from template while preserving customizations"""
    existing_clauses = {c.clause_number: c for c in self.clauses.all()}

    for i, clause_text in enumerate(template.clauses):
        clause_number = i + 1

        if clause_number in existing_clauses:
            clause = existing_clauses[clause_number]
            if not clause.is_customized:
                clause.template_text = clause_text
                clause.save()
        else:
            LeaseAgreementClause.objects.create(
                lease=self, clause_number=clause_number, template_text=clause_text
            )

    def generate_agreement_text(self) -> str:
        """
        Build the full agreement text for this lease by:
        - Adding a header + parties section
        - Rendering each LeaseAgreementClause with {{ }} variables
        - Adding a footer
        """
        lines = []

        # Header (static text; can later move to DB if you want)
        header = (
            "RENT AGREEMENT\n"
            f"This RENT AGREEMENT is made at Islamabad on this "
            f"{timezone.now().strftime('%d-%m-%Y')}\n"
        )
        lines.append(header)

        # Parties block using template variables
        parties_block = """
BETWEEN
{{ property.owner_name }} holding CNIC NO. {{ property.owner_cnic }}
{{ property.owner_address }} (Hereinafter called "Owner")

AND
{{ tenant.full_name }} holding CNIC NO. {{ tenant.cnic }}
{{ tenant.address }} (Hereinafter called "Tenant")
"""
        lines.append(self.render_text_with_context(parties_block))

        # Clauses
        for clause in self.clauses.order_by("clause_number"):
            rendered_clause = self.render_text_with_context(clause.template_text)
            lines.append(f"{clause.clause_number}. {rendered_clause}")

        # Footer (also templated)
        footer = """
Owner: _________________________
Tenant: _________________________

Generated at: {{ now_string }}
"""
        ctx = self.get_template_context()
        ctx["now_string"] = timezone.now().strftime("%d-%m-%Y %H:%M:%S")
        footer_tpl = Template(footer)
        footer_rendered = footer_tpl.render(Context(ctx))
        lines.append(footer_rendered)

        return "\n".join(lines)


# leases/models.py


# leases/models.py  (add near your Lease model)


class LeaseFamily(models.Model):
    lease = models.ForeignKey(
        "Lease", on_delete=models.CASCADE, related_name="family_members"
    )
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="lease_families"
    )
    relation = models.CharField(max_length=50, blank=True)
    whatsapp_opt_in = models.BooleanField(default=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("lease", "tenant")]
        ordering = ["tenant__first_name", "tenant__last_name"]

    def __str__(self):
        return f"{self.tenant} ({self.relation or 'family'})"


class DefaultClause(models.Model):
    """
    Global default clauses for new leases.
    Edit these in Admin or a custom UI.
    """

    clause_number = models.PositiveIntegerField()
    body = models.TextField(
        help_text="Use {{ }} variables like {{ monthly_rent }}, {{ tenant.full_name }}, etc."
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["clause_number"]
        unique_together = [("clause_number", "is_active")]

    def __str__(self):
        return f"Default Clause {self.clause_number}"


# leases/models.py
from django.conf import settings


class AgreementVersion(models.Model):
    AGREEMENT_TYPES = [
        ("NEW", "New Agreement"),
        ("RENEWAL", "Renewal"),
        ("AMENDMENT", "Amendment"),
    ]

    lease = models.ForeignKey(
        "leases.Lease",
        related_name="agreement_versions",
        on_delete=models.CASCADE,
    )

    version = models.PositiveIntegerField()
    type = models.CharField(max_length=20, choices=AGREEMENT_TYPES, default="NEW")

    agreement_date = models.DateField()
    term_start_date = models.DateField()
    term_end_date = models.DateField()

    is_current = models.BooleanField(default=True)

    signed_agreement = models.FileField(
        upload_to="signed_agreements/",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["lease", "version"],
                name="uniq_version_per_lease",
            ),
        ]

    def __str__(self):
        return f"Lease #{self.lease.pk} – Agreement v{self.version}"


class AgreementClause(models.Model):
    agreement_version = models.ForeignKey(
        AgreementVersion,
        related_name="clauses",
        on_delete=models.CASCADE,
    )
    clause_number = models.PositiveIntegerField()
    template_text = models.TextField()

    class Meta:
        ordering = ["clause_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["agreement_version", "clause_number"],
                name="uniq_clause_per_version",
            ),
        ]

    def __str__(self):
        return f"Clause {self.clause_number} (v{self.agreement_version.version})"


class DefaultLeaseClause(models.Model):
    clause_number = models.PositiveIntegerField(unique=True)
    template_text = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["clause_number"]

    def __str__(self):
        return f"Default Clause {self.clause_number}"
