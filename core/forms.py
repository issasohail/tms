from django import forms
from .models import GlobalSettings
from django import forms
from .models import GlobalSettings
from zoneinfo import available_timezones
from core.utils.text import add_auto_titlecase_class


class GlobalSettingsForm(forms.ModelForm):
    smtp_password = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=True))

    TZ_CHOICES = sorted((tz, tz) for tz in available_timezones())
    time_zone = forms.ChoiceField(choices=TZ_CHOICES)
    handyman_assignment_default_status = forms.ChoiceField()

    class Meta:
        model = GlobalSettings
        fields = ["site_name", "logo", "favicon",
                  "smtp_host", "smtp_port", "smtp_use_tls", "smtp_user", "smtp_password",
                  "whatsapp_number", "twilio_account_sid", "twilio_auth_token", "twilio_from_number",
                  "whatsapp_media_retention_days",
                  "currency_code","country_code",  "unit_rate_per_kwh", "service_charge_flat",
                  "late_fee_enabled", "late_fee_type", "late_fee_amount",
                  "late_fee_percent", "late_fee_grace_days",
                  "late_fee_reminder_interval_days", "late_fee_max_reminders",
                  "late_fee_auto_send_reminders", "late_fee_auto_apply",
                  "billing_cap_amount",
                  "lease_file_share_valid_days",
                  "police_verification_document_category_code",
                  "police_verification_link_valid_hours",
                  "police_verification_whatsapp_command",
                  "listener_host", "listener_port",
                  "time_zone",  # ← NEW
                  "whatsapp_ai_enabled", "whatsapp_ai_provider", "whatsapp_ai_model",
                  "whatsapp_ai_ocr_provider", "whatsapp_ai_use_celery",
                  "handyman_assignment_default_status",
                  "handyman_enable_whatsapp_profile_updates",
                  "handyman_enable_whatsapp_job_uploads",
                  "handyman_enable_ratings",
                  "handyman_require_id_documents",
                  "handyman_profile_photo_command",
                  "handyman_id_front_command",
                  "handyman_id_back_command",
                  "handyman_invoice_command",
                  "handyman_job_photo_command",
                  "enable_debug_toolbar",
                  ]
    FIELD_GROUPS = [
        ("Branding", "fas fa-building", ["site_name", "logo", "favicon"]),
        ("Billing Scale & Locale", "fas fa-coins", [
            "currency_code", "country_code", "time_zone", "unit_rate_per_kwh",
            "service_charge_flat", "billing_cap_amount", "lease_file_share_valid_days",
        ]),
        ("Police Verification", "fas fa-shield-alt", [
            "police_verification_document_category_code",
            "police_verification_link_valid_hours",
            "police_verification_whatsapp_command",
        ]),
        ("Late Fees", "fas fa-clock", [
            "late_fee_enabled", "late_fee_type", "late_fee_amount",
            "late_fee_percent", "late_fee_grace_days",
            "late_fee_reminder_interval_days", "late_fee_max_reminders",
            "late_fee_auto_send_reminders", "late_fee_auto_apply",
        ]),
        ("WhatsApp / Twilio", "fab fa-whatsapp", [
            "whatsapp_number", "twilio_account_sid", "twilio_auth_token",
            "twilio_from_number", "whatsapp_media_retention_days",
        ]),
        ("WhatsApp AI Assistant", "fas fa-robot", [
            "whatsapp_ai_enabled", "whatsapp_ai_provider", "whatsapp_ai_model",
            "whatsapp_ai_ocr_provider", "whatsapp_ai_use_celery",
        ]),
        ("Email / SMTP", "fas fa-envelope", [
            "smtp_host", "smtp_port", "smtp_use_tls", "smtp_user",
            "smtp_password",
        ]),
        ("Meter Listener", "fas fa-broadcast-tower", [
            "listener_host", "listener_port",
        ]),
        ("Handyman", "fas fa-tools", [
            "handyman_assignment_default_status",
            "handyman_enable_whatsapp_profile_updates",
            "handyman_enable_whatsapp_job_uploads",
            "handyman_enable_ratings",
            "handyman_require_id_documents",
            "handyman_profile_photo_command",
            "handyman_id_front_command",
            "handyman_id_back_command",
            "handyman_invoice_command",
            "handyman_job_photo_command",
        ]),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            from handyman.models import MaintenanceHandymanAssignment

            self.fields["handyman_assignment_default_status"].choices = MaintenanceHandymanAssignment.STATUS_CHOICES
        except Exception:
            self.fields["handyman_assignment_default_status"].choices = [
                ("assigned", "Assigned"),
                ("accepted", "Accepted"),
                ("in_progress", "In Progress"),
                ("completed", "Completed"),
                ("cancelled", "Cancelled"),
            ]
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({"class": "form-check-input"})
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.update({"class": "form-select form-select-sm"})
            elif isinstance(field.widget, forms.ClearableFileInput):
                field.widget.attrs.update({"class": "form-control form-control-sm"})
            else:
                field.widget.attrs.update({"class": "form-control form-control-sm"})

        self.fields["late_fee_reminder_interval_days"].label = "Reminder interval days"
        self.fields["late_fee_reminder_interval_days"].help_text = "Days between late fee reminders after the grace period."
        self.fields["late_fee_max_reminders"].label = "Max reminders"
        self.fields["late_fee_max_reminders"].help_text = "Use 0 for unlimited reminder-based fees."
        self.fields["late_fee_auto_send_reminders"].label = "Send reminders automatically"
        self.fields["late_fee_auto_send_reminders"].help_text = "On = the daily late fee job sends WhatsApp reminders. Off = reminders are sent manually from invoice detail."
        self.fields["late_fee_auto_apply"].label = "Apply fee automatically"
        self.fields["late_fee_auto_apply"].help_text = "Off = reminder is sent, but the late fee waits in the pending approval queue."
        self.fields["currency_code"].help_text = "Used as the currency label throughout TMS, for example PKR, USD, AED."
        self.fields["country_code"].help_text = "Used for WhatsApp phone normalization, for example +92."
        self.fields["lease_file_share_valid_days"].label = "Lease file share validity days"
        self.fields["police_verification_document_category_code"].label = "Police document category code"
        self.fields["police_verification_link_valid_hours"].label = "Police link validity hours"
        self.fields["police_verification_whatsapp_command"].label = "Police WhatsApp command"
        self.fields["whatsapp_media_retention_days"].label = "WhatsApp media retention days"
        self.fields["whatsapp_media_retention_days"].help_text = "Downloaded WhatsApp media files are kept for this many days. Raw webhook logs stay untouched."
        self.fields["whatsapp_ai_enabled"].label = "Enable WhatsApp AI assistant"
        self.fields["whatsapp_ai_provider"].label = "Assistant provider"
        self.fields["whatsapp_ai_model"].label = "OpenAI model"
        self.fields["whatsapp_ai_ocr_provider"].label = "Payment OCR provider"
        self.fields["whatsapp_ai_use_celery"].label = "Use Celery for WhatsApp AI"
        self.fields["handyman_assignment_default_status"].label = "Default assignment status"
        self.fields["handyman_enable_whatsapp_profile_updates"].label = "Allow WhatsApp profile updates"
        self.fields["handyman_enable_whatsapp_job_uploads"].label = "Allow WhatsApp job uploads"
        self.fields["handyman_enable_ratings"].label = "Enable handyman ratings"
        self.fields["handyman_require_id_documents"].label = "Require ID documents"
        self.fields["handyman_profile_photo_command"].label = "Profile photo command"
        self.fields["handyman_id_front_command"].label = "ID front command"
        self.fields["handyman_id_back_command"].label = "ID back command"
        self.fields["handyman_invoice_command"].label = "Invoice command"
        self.fields["handyman_job_photo_command"].label = "Job photo command"
        self.fields["enable_debug_toolbar"].label = "Enable Django Debug Toolbar (local development only)"
        self.fields["enable_debug_toolbar"].help_text = "Only works when DEBUG=True and host is local. It will not show in production."
        add_auto_titlecase_class(self.fields, {"site_name"})


# core/forms.py
from django import forms
from .models import PaymentMethod


class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ["name", "code", "is_active", "sort_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        add_auto_titlecase_class(self.fields, {"name"})


class BackupSettingsForm(forms.Form):
    backup_root = forms.CharField(
        label="Backup Root Folder",
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}),
    )
    retention_count = forms.IntegerField(
        min_value=1,
        max_value=200,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm"}),
    )
    mysqldump_path = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}),
    )
    mysql_path = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}),
    )
    include_db_in_full = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    include_media_in_full = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    include_code_in_full = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    compress_backups = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_db_backup = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_media_backup = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_code_backup = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_full_backup = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    fresh_reset_enabled = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class BackupRestoreForm(forms.Form):
    backup_id = forms.ChoiceField(
        choices=(),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    confirm_text = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control form-control-sm",
            "placeholder": "Type required confirmation text exactly",
        }),
    )

    def __init__(self, *args, backup_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["backup_id"].choices = backup_choices or [("", "----------")]


class BackupUploadForm(forms.Form):
    backup_type = forms.ChoiceField(
        choices=(("db", "Database"), ("media", "Media"), ("full", "Full")),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    backup_file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"class": "form-control form-control-sm"}),
    )

    def clean_backup_file(self):
        upload = self.cleaned_data["backup_file"]
        name = upload.name.lower()
        backup_type = self.cleaned_data.get("backup_type")
        if backup_type == "db" and not (name.endswith(".sql") or name.endswith(".sqlite3")):
            raise forms.ValidationError("Database restore upload must be .sql or .sqlite3.")
        if backup_type in {"media", "full"} and not name.endswith(".zip"):
            raise forms.ValidationError("Media and full restore uploads must be .zip files.")
        return upload


class SuggestionTicketForm(forms.Form):
    ticket_type = forms.ChoiceField(
        choices=(("SUGGESTION", "Suggestion"), ("ERROR", "Report Error")),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    screen_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Example: Cash Ledger, Settings, Payment Receipt",
        }),
    )
    title = forms.CharField(
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )
    priority = forms.ChoiceField(
        choices=(("LOW", "Low"), ("NORMAL", "Normal"), ("HIGH", "High"), ("URGENT", "Urgent")),
        initial="NORMAL",
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class SuggestionReplyForm(forms.Form):
    message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
