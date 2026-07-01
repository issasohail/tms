from django import forms

from .models_late_fee import LeaseLateFeeSettings


class LeaseLateFeeSettingsForm(forms.ModelForm):
    class Meta:
        model = LeaseLateFeeSettings
        fields = [
            "override_enabled",
            "late_fee_enabled",
            "late_fee_type",
            "late_fee_amount",
            "late_fee_percent",
            "late_fee_grace_days",
            "reminder_interval_days",
            "late_fee_max_reminders",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({"class": "form-check-input"})
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.update({"class": "form-select form-select-sm"})
            else:
                field.widget.attrs.update({"class": "form-control form-control-sm"})

        self.fields["override_enabled"].label = "Use custom late fee rules for this lease"
        self.fields["late_fee_enabled"].label = "Enable late fees"
        self.fields["late_fee_grace_days"].label = "Grace days"
        self.fields["reminder_interval_days"].label = "Reminder interval days"
        self.fields["late_fee_max_reminders"].label = "Max reminders"
        self.fields["late_fee_max_reminders"].help_text = "Use 0 for unlimited reminders."
