from django import forms
from .models import GlobalSettings
from django import forms
from .models import GlobalSettings
from zoneinfo import available_timezones


class GlobalSettingsForm(forms.ModelForm):
    smtp_password = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=True))

    TZ_CHOICES = sorted((tz, tz) for tz in available_timezones())
    time_zone = forms.ChoiceField(choices=TZ_CHOICES)

    class Meta:
        model = GlobalSettings
        fields = ["site_name", "logo", "favicon",
                  "smtp_host", "smtp_port", "smtp_use_tls", "smtp_user", "smtp_password",
                  "whatsapp_number", "twilio_account_sid", "twilio_auth_token", "twilio_from_number",
                  "currency_code","country_code",  "unit_rate_per_kwh", "service_charge_flat",
                  "listener_host", "listener_port",
                  "time_zone",  # ← NEW
                  ]
# core/forms.py
from django import forms
from .models import PaymentMethod


class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ["name", "code", "is_active", "sort_order"]
