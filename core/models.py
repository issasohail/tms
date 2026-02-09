from django.db import models


class GlobalSettings(models.Model):
    id = models.PositiveSmallIntegerField(
        primary_key=True, default=1, editable=False)

    # Branding
    site_name = models.CharField(
        max_length=100, default="Tenant Management System")
    logo = models.ImageField(upload_to="branding/", blank=True, null=True)
    favicon = models.ImageField(upload_to="branding/", blank=True, null=True)

    # Email (SMTP)
    smtp_host = models.CharField(max_length=200, blank=True)
    smtp_port = models.PositiveIntegerField(default=25)
    smtp_use_tls = models.BooleanField(default=True)
    smtp_user = models.CharField(max_length=200, blank=True)
    smtp_password = models.CharField(max_length=512, blank=True)   # ok for dev

    # WhatsApp / Twilio
    whatsapp_number = models.CharField(max_length=40, blank=True)
    twilio_account_sid = models.CharField(max_length=128, blank=True)
    twilio_auth_token = models.CharField(max_length=128, blank=True)
    twilio_from_number = models.CharField(max_length=40, blank=True)

    # Billing
    currency_code = models.CharField(max_length=8, default="PKR")
    country_code = models.CharField(max_length=4, default="+92")
    unit_rate_per_kwh = models.DecimalField(
        max_digits=10, decimal_places=4, default=0)
    service_charge_flat = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)

    time_zone = models.CharField(max_length=64, default="Asia/Karachi")  # NEW

    # Listener (meter socket)
    listener_host = models.CharField(max_length=100, default="127.0.0.1")
    listener_port = models.PositiveIntegerField(default=6000)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self): return "Global Settings"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
# core/models.py
from django.db import models


class GlobalSettings(models.Model):
    id = models.PositiveSmallIntegerField(
        primary_key=True, default=1, editable=False)

    # Branding
    site_name = models.CharField(
        max_length=100, default="Tenant Management System")
    logo = models.ImageField(upload_to="branding/", blank=True, null=True)
    favicon = models.ImageField(upload_to="branding/", blank=True, null=True)

    # Email (SMTP)
    smtp_host = models.CharField(max_length=200, blank=True)
    smtp_port = models.PositiveIntegerField(default=25)
    smtp_use_tls = models.BooleanField(default=True)
    smtp_user = models.CharField(max_length=200, blank=True)
    smtp_password = models.CharField(max_length=512, blank=True)   # ok for dev

    # WhatsApp / Twilio
    whatsapp_number = models.CharField(max_length=40, blank=True)
    twilio_account_sid = models.CharField(max_length=128, blank=True)
    twilio_auth_token = models.CharField(max_length=128, blank=True)
    twilio_from_number = models.CharField(max_length=40, blank=True)

    # Billing
    currency_code = models.CharField(max_length=8, default="PKR")
    country_code = models.CharField(max_length=4, default="+92")
    unit_rate_per_kwh = models.DecimalField(
        max_digits=10, decimal_places=4, default=0)
    service_charge_flat = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)

    time_zone = models.CharField(max_length=64, default="Asia/Karachi")  # NEW

    # Listener (meter socket)
    listener_host = models.CharField(max_length=100, default="127.0.0.1")
    listener_port = models.PositiveIntegerField(default=6000)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Global Settings"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class PaymentMethod(models.Model):
    """
    Dynamic payment methods for the entire system.
    Managed via UI/admin instead of hard-coded choices.
    """
    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text="Internal code, e.g. 'cash', 'easypaisa', 'bank_transfer'"
    )
    name = models.CharField(
        max_length=100,
        help_text="Display name, e.g. 'Cash', 'Easy Paisa', 'Bank Transfer'"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to hide this method from new payments, "
                  "while keeping old payments intact."
    )
    sort_order = models.PositiveIntegerField(
        default=10,
        help_text="Lower numbers show first in dropdowns."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name
