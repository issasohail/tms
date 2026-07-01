from django import forms

from .models import HandymanCategory, HandymanProfile, HandymanRating, MaintenanceHandymanAssignment


class HandymanProfileForm(forms.ModelForm):
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


class HandymanRatingForm(forms.ModelForm):
    class Meta:
        model = HandymanRating
        fields = ["rating", "comments"]
        widgets = {
            "rating": forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": 1, "max": 5}),
            "comments": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 4}),
        }
