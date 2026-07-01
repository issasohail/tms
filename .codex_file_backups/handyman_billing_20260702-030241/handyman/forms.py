from django import forms

from .models import HandymanCategory, HandymanProfile, HandymanRating, MaintenanceHandymanAssignment


class HandymanProfileForm(forms.ModelForm):
    new_category = forms.CharField(
        required=False,
        label="Add missing category",
        widget=forms.TextInput(attrs={
            "class": "form-control form-control-sm",
            "placeholder": "Type a new category if it is not in the list",
        }),
    )

    class Meta:
        model = HandymanProfile
        fields = [
            "full_name",
            "phone",
            "whatsapp_number",
            "categories",
            "is_preferred",
            "is_active",
            "address",
            "notes",
            "photo",
            "id_card_front",
            "id_card_back",
        ]
        widgets = {
            "full_name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "phone": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "whatsapp_number": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "categories": forms.SelectMultiple(attrs={"class": "form-select form-select-sm js-select2", "data-placeholder": "Select categories"}),
            "is_preferred": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "address": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "notes": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 4}),
            "photo": forms.FileInput(attrs={"class": "form-control form-control-sm", "accept": "image/*"}),
            "id_card_front": forms.FileInput(attrs={"class": "form-control form-control-sm", "accept": "image/*"}),
            "id_card_back": forms.FileInput(attrs={"class": "form-control form-control-sm", "accept": "image/*"}),
        }

    def save(self, commit=True):
        handyman = super().save(commit=commit)
        category_name = (self.cleaned_data.get("new_category") or "").strip()
        if commit and category_name:
            category, _created = HandymanCategory.objects.get_or_create(
                name=category_name,
                defaults={"is_active": True, "sort_order": 50},
            )
            handyman.categories.add(category)
        return handyman


class HandymanCategoryForm(forms.ModelForm):
    class Meta:
        model = HandymanCategory
        fields = ["name", "is_active", "sort_order"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-control form-control-sm"}),
        }


class MaintenanceHandymanAssignmentForm(forms.ModelForm):
    class Meta:
        model = MaintenanceHandymanAssignment
        fields = ["handyman", "status", "notes"]
        widgets = {
            "handyman": forms.Select(attrs={"class": "form-select form-select-sm js-select2"}),
            "status": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "notes": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.initial.get("status"):
            from core.models import GlobalSettings

            configured_status = GlobalSettings.get_solo().handyman_assignment_default_status
            allowed_statuses = {value for value, _label in MaintenanceHandymanAssignment.STATUS_CHOICES}
            if configured_status in allowed_statuses:
                self.fields["status"].initial = configured_status


class HandymanRatingForm(forms.ModelForm):
    class Meta:
        model = HandymanRating
        fields = ["rating", "comments"]
        widgets = {
            "rating": forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": 1, "max": 5}),
            "comments": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 4}),
        }
