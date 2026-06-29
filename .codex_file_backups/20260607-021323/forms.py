from django import forms

from .models import MaintenanceCategory, MaintenanceRequest, MaintenanceRequestMedia
from core.utils.text import add_auto_titlecase_class


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={
            "class": "form-control form-control-sm",
            "multiple": True,
            "accept": "image/*,video/*,.pdf,.doc,.docx,.xls,.xlsx,.txt,.csv,.heic,.heif",
        }))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(d, initial) for d in data]
        return single_file_clean(data, initial)


class MaintenanceRequestForm(forms.ModelForm):
    files = MultipleFileField(
        required=False,
        help_text="Upload photos, videos, or PDFs.",
    )

    class Meta:
        model = MaintenanceRequest
        fields = [
            "building", "unit", "lease", "title", "description",
            "status", "priority", "category_ref", "reported_date",
            "resolved_date", "assigned_to", "cost", "admin_notes", "files",
        ]
        widgets = {
            "building": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "unit": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "lease": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "title": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "description": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 4}),
            "category_ref": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "priority": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "status": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "reported_date": forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
            "resolved_date": forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
            "assigned_to": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "cost": forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "0", "step": "0.01"}),
            "admin_notes": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        add_auto_titlecase_class(self.fields, {"title"})
        self.fields["description"].required = False
        self.fields["category_ref"].queryset = MaintenanceCategory.objects.filter(
            is_active=True
        ).order_by("sort_order", "name")
        self.fields["category_ref"].required = False
        self.fields["category_ref"].label = "Category"


class MaintenanceRequestMediaForm(forms.ModelForm):
    class Meta:
        model = MaintenanceRequestMedia
        fields = ["description", "is_active"]
        widgets = {
            "description": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
