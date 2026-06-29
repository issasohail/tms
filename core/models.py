from django.core.cache import cache
from django.db import models
from core.utils.text import smart_title


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
    late_fee_enabled = models.BooleanField(default=False)
    late_fee_type = models.CharField(
        max_length=10,
        choices=(("fixed", "Fixed amount"), ("percent", "Percentage")),
        default="fixed",
    )
    late_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    late_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    late_fee_grace_days = models.PositiveIntegerField(default=0)
    billing_cap_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Optional invoice cap. Use 0 for no cap.",
    )
    lease_file_share_valid_days = models.PositiveIntegerField(
        default=7,
        help_text="Default number of days public lease file share links remain valid.",
    )

    time_zone = models.CharField(max_length=64, default="Asia/Karachi")  # NEW

    # Listener (meter socket)
    listener_host = models.CharField(max_length=100, default="127.0.0.1")
    listener_port = models.PositiveIntegerField(default=6000)

    # Development tools
    enable_debug_toolbar = models.BooleanField(default=False)

    # WhatsApp AI assistant
    whatsapp_ai_enabled = models.BooleanField(
        default=True,
        help_text="Enable automated tenant assistant replies for inbound WhatsApp messages.",
    )
    whatsapp_ai_provider = models.CharField(
        max_length=30,
        choices=(("rules", "Rules only"), ("openai", "OpenAI")),
        default="openai",
        help_text="Rules only uses local matching. OpenAI enables AI OCR/understanding when OPENAI_API_KEY is configured.",
    )
    whatsapp_ai_model = models.CharField(
        max_length=80,
        default="gpt-4o-mini",
        help_text="OpenAI model used for WhatsApp AI tasks. Keep API keys in .env, not here.",
    )
    whatsapp_ai_ocr_provider = models.CharField(
        max_length=30,
        choices=(("basic", "Basic"), ("openai", "OpenAI Vision")),
        default="openai",
        help_text="Basic stores media for admin review. OpenAI Vision extracts receipt text when OPENAI_API_KEY is configured.",
    )
    whatsapp_ai_use_celery = models.BooleanField(
        default=False,
        help_text="Queue WhatsApp AI work through Celery when a worker is running; otherwise TMS uses a local background thread.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Global Settings"

    @classmethod
    def get_solo(cls):
        obj = cache.get("core.global_settings")
        if obj is not None:
            return obj
        obj, _ = cls.objects.get_or_create(pk=1)
        cache.set("core.global_settings", obj, 60)
        return obj

    def save(self, *args, **kwargs):
        self.site_name = smart_title(self.site_name)
        result = super().save(*args, **kwargs)
        cache.delete("core.global_settings")
        return result

    def delete(self, *args, **kwargs):
        cache.delete("core.global_settings")
        return super().delete(*args, **kwargs)


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

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        super().save(*args, **kwargs)
