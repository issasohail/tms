from django.core.cache import cache
from django.db import models
from django.core.validators import MinValueValidator
from core.model_fields import NormalizedPhoneField
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
    whatsapp_number = NormalizedPhoneField(max_length=40, blank=True)
    twilio_account_sid = models.CharField(max_length=128, blank=True)
    twilio_auth_token = models.CharField(max_length=128, blank=True)
    twilio_from_number = models.CharField(max_length=40, blank=True)
    whatsapp_media_retention_days = models.PositiveIntegerField(
        default=90,
        help_text="Days to keep downloaded WhatsApp media files. Raw webhook logs are not deleted.",
    )
    whatsapp_pending_request_notifications_enabled = models.BooleanField(
        default=True,
        help_text="Send WhatsApp alerts to staff when tenant WhatsApp requests wait for approval.",
    )
    whatsapp_pending_request_staff_numbers = models.TextField(
        blank=True,
        help_text="Comma or line separated staff WhatsApp numbers for pending request alerts.",
    )

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
    late_fee_reminder_interval_days = models.PositiveIntegerField(
        default=5,
        help_text="Days between each late fee reminder after the grace period.",
    )
    late_fee_max_reminders = models.PositiveIntegerField(
        default=0,
        help_text="0 = unlimited. Reminders and reminder-based fees stop once this count is reached.",
    )
    late_fee_auto_send_reminders = models.BooleanField(
        default=False,
        help_text="If on, the scheduled late fee job sends WhatsApp reminders automatically.",
    )
    late_fee_auto_apply = models.BooleanField(
        default=True,
        help_text="If off, reminder-based late fees wait for approval before being added to invoices.",
    )
    billing_cap_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Optional invoice cap. Use 0 for no cap.",
    )
    default_lease_months = models.PositiveSmallIntegerField(
        default=11,
        validators=[MinValueValidator(1)],
        help_text="Default agreement term used for new leases and renewals.",
    )
    end_lease_proration_interval_days = models.PositiveSmallIntegerField(
        default=7,
        validators=[MinValueValidator(1)],
        help_text="Default billing-day block used when monthly charges are prorated at move-out.",
    )
    lease_file_share_valid_days = models.PositiveIntegerField(
        default=7,
        help_text="Default number of days public lease file share links remain valid.",
    )
    police_verification_document_category_code = models.CharField(
        max_length=50,
        default="police_verification",
        help_text="Lease document category code used to mark police verification as complete.",
    )
    police_verification_link_valid_hours = models.PositiveIntegerField(
        default=48,
        help_text="Hours a public police verification link remains valid.",
    )
    police_verification_whatsapp_command = models.CharField(
        max_length=80,
        default="Police Verification",
        help_text="WhatsApp command tenants can send to receive the police verification link.",
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
    whatsapp_ai_routing_enabled = models.BooleanField(default=False)
    whatsapp_ai_generated_responses_enabled = models.BooleanField(default=False)
    whatsapp_ai_multiple_tools_enabled = models.BooleanField(default=True)
    whatsapp_handover_enabled = models.BooleanField(default=False)
    whatsapp_ai_satisfaction_enabled = models.BooleanField(default=False)
    whatsapp_ai_temperature = models.DecimalField(max_digits=3, decimal_places=2, default=0.20)
    whatsapp_ai_max_tool_rounds = models.PositiveSmallIntegerField(default=3)
    whatsapp_ai_min_confidence = models.PositiveSmallIntegerField(default=65)
    whatsapp_ai_history_limit = models.PositiveSmallIntegerField(default=8)
    whatsapp_ai_fallback_to_rules = models.BooleanField(default=True)
    whatsapp_ai_max_reply_length = models.PositiveIntegerField(default=1200)
    whatsapp_ai_enable_urdu = models.BooleanField(default=True)
    whatsapp_ai_enable_roman_urdu = models.BooleanField(default=True)
    whatsapp_ai_mask_sensitive_fields = models.BooleanField(default=True)
    whatsapp_ai_store_logs = models.BooleanField(default=True)
    whatsapp_handover_reminder_interval_minutes = models.PositiveIntegerField(default=30)
    whatsapp_handover_escalation_timeout_minutes = models.PositiveIntegerField(default=60)
    whatsapp_handover_max_reminders = models.PositiveSmallIntegerField(default=3)
    whatsapp_handover_notify_multiple_staff = models.BooleanField(default=False)
    whatsapp_staff_reply_prefix = models.CharField(max_length=80, default="Management:")
    whatsapp_allow_manual_call_action = models.BooleanField(default=True)
    whatsapp_future_calling_enabled = models.BooleanField(default=False)
    whatsapp_allow_staff_reply_relay = models.BooleanField(default=True)
    whatsapp_allow_staff_media_relay = models.BooleanField(default=True)
    whatsapp_allow_handover_reassignment = models.BooleanField(default=True)
    whatsapp_return_to_ai_after_close = models.BooleanField(default=False)
    whatsapp_handover_ai_summary_enabled = models.BooleanField(default=True)
    whatsapp_default_support_staff = models.ForeignKey(
        "accounts.Account", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="default_whatsapp_support_settings",
    )
    whatsapp_accounts_staff = models.ForeignKey(
        "accounts.Account", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="accounts_whatsapp_support_settings",
    )
    whatsapp_maintenance_staff = models.ForeignKey(
        "accounts.Account", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="maintenance_whatsapp_support_settings",
    )
    whatsapp_leasing_staff = models.ForeignKey(
        "accounts.Account", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="leasing_whatsapp_support_settings",
    )
    whatsapp_escalation_staff = models.ForeignKey(
        "accounts.Account", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="escalation_whatsapp_support_settings",
    )

    # Handyman
    handyman_assignment_default_status = models.CharField(
        max_length=20,
        default="assigned",
        help_text="Status used when staff assigns a handyman and no status is selected.",
    )
    handyman_enable_whatsapp_profile_updates = models.BooleanField(
        default=True,
        help_text="Allow handymen to update profile photo and ID card images from WhatsApp.",
    )
    handyman_enable_whatsapp_job_uploads = models.BooleanField(
        default=True,
        help_text="Allow handymen to upload invoices and job photos from WhatsApp.",
    )
    handyman_enable_ratings = models.BooleanField(
        default=True,
        help_text="Show and accept tenant ratings for handyman jobs.",
    )
    handyman_require_id_documents = models.BooleanField(
        default=False,
        help_text="Require front and back ID card images before marking a handyman fully documented.",
    )
    handyman_profile_photo_command = models.CharField(
        max_length=40,
        default="PROFILE PHOTO",
        help_text="WhatsApp command for updating the handyman profile photo.",
    )
    handyman_id_front_command = models.CharField(
        max_length=40,
        default="ID FRONT",
        help_text="WhatsApp command for updating the front ID card image.",
    )
    handyman_id_back_command = models.CharField(
        max_length=40,
        default="ID BACK",
        help_text="WhatsApp command for updating the back ID card image.",
    )
    handyman_invoice_command = models.CharField(
        max_length=40,
        default="INVOICE",
        help_text="WhatsApp command for attaching an invoice to the active job.",
    )
    handyman_job_photo_command = models.CharField(
        max_length=40,
        default="PHOTO",
        help_text="WhatsApp command for attaching a job photo to the active job.",
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
