from django.contrib import admin
from ..core.models import GlobalSettings


@admin.register(GlobalSettings)
class GlobalSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Branding", {"fields": ("site_name", "logo", "favicon")}),
        ("Email (SMTP)", {"fields": ("smtp_host", "smtp_port",
         "smtp_use_tls", "smtp_user", "smtp_password")}),
        ("WhatsApp / Twilio", {"fields": ("whatsapp_number",
         "twilio_account_sid", "twilio_auth_token", "twilio_from_number")}),
        ("Billing", {"fields": ("currency_code",
         "unit_rate_per_kwh", "service_charge_flat")}),
        ("Listener", {"fields": ("listener_host", "listener_port")}),
    )
