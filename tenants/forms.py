from django import forms
from .models import Tenant
from leases.models import Lease
from properties.models import Property, Unit
from django.apps import apps


from django import forms
from .models import Tenant
from django.utils.text import slugify
import os
import re
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import F, Value
from django.db.models.functions import Replace
from .models import Tenant

CNIC_DIGITS = re.compile(r'\D+')

# tenants/forms.py

INTL_MIN_DIGITS = 7
INTL_MAX_DIGITS = 15
LOCAL_PATTERNS = (
    r'03\d{9}',      # e.g. 03XXXXXXXXX
    r'\+92\d{10}',   # e.g. +92XXXXXXXXXX

)


def _is_valid_international(number: str) -> bool:
    """
    Accept anything that STARTS WITH '+' as international,
    as long as it contains a reasonable count of digits.
    Spaces, dashes, brackets allowed.
    """
    if not number.startswith('+'):
        return False
    digits = re.sub(r'\D', '', number)
    return INTL_MIN_DIGITS <= len(digits) <= INTL_MAX_DIGITS


def _is_valid_local(number: str) -> bool:
    return any(re.fullmatch(p, number) for p in LOCAL_PATTERNS)


def normalize_cnic(value: str) -> str:
    return CNIC_DIGITS.sub('', value or '')


# 03XXXXXXXXX or +92XXXXXXXXXX
PK_PHONE_RE = re.compile(r'^(0\d{10}|\+92\d{10})$')


class TenantForm(forms.ModelForm):
    class Meta:
        model = Tenant
        fields = '__all__'
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
            'address': forms.Textarea(attrs={'rows': 2}),
            "phone":  forms.TextInput(attrs={"type": "tel", "maxlength": "20", "placeholder": "+447911123456 or 03123456789"}),
            "phone2": forms.TextInput(attrs={"type": "tel", "maxlength": "20"}),
            "phone3": forms.TextInput(attrs={"type": "tel", "maxlength": "20"}),
            "emergency_contact_phone": forms.TextInput(attrs={"type": "tel", "maxlength": "20"}),
        }

    # Optional: server-side validation that still preserves leading zero
    def _clean_pk_phone(self, value, field_label):
        value = (value or '').strip()
        if not value:
            return value  # allow blank for optional fields

        # Accept any international number that starts with '+'
        # and also accept your local formats (03XXXXXXXXX, +92XXXXXXXXXX)
        if _is_valid_international(value) or _is_valid_local(value):
            return value

        raise forms.ValidationError(
            f"{field_label}: enter 03XXXXXXXXX or anything starting with +."
        )

    def clean_phone(self):
        return self._clean_pk_phone(self.cleaned_data.get("phone"), "Phone")

    def clean_phone2(self):
        return self._clean_pk_phone(self.cleaned_data.get("phone2"), "Phone 2")

    def clean_phone3(self):
        return self._clean_pk_phone(self.cleaned_data.get("phone3"), "Phone 3")

    def clean_emergency_contact_phone(self):
        return self._clean_pk_phone(self.cleaned_data.get("emergency_contact_phone"), "Emergency phone")

    def clean_cnic(self):
        cnic = (self.cleaned_data.get('cnic') or '').strip()
        digits = normalize_cnic(cnic)

        if digits and len(digits) != 13:
            raise ValidationError(
                "CNIC must contain exactly 13 digits (format xxxxx-xxxxxxx-x).")

        if digits:
            # DB-side normalization: remove hyphens/spaces so formats compare equal
            qs = (Tenant.objects
                  .annotate(cnic_digits_db=Replace(Replace(F('cnic'), Value('-'), Value('')), Value(' '), Value('')))
                  .exclude(pk=self.instance.pk)
                  .filter(cnic_digits_db=digits))
            if qs.exists():
                raise ValidationError(
                    "A tenant with this CNIC already exists.")

        return cnic

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make fields smaller by adding form-control-sm class
        for field_name, field in self.fields.items():
            if 'photo' in field_name or 'cnic' in field_name:
                field.widget.attrs.update({'class': 'form-control-file'})
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': 'form-check-input'})
            else:
                field.widget.attrs.update(
                    {'class': 'form-control form-control-sm'})

        # Set smaller labels for some fields
        self.fields['emergency_contact_name'].label = "Emergency Name"
        self.fields['emergency_contact_phone'].label = "Emergency Phone"
        self.fields['emergency_contact_relation'].label = "Relation"
        self.fields['number_of_family_member'].label = "Family Members"


class LeaseForm(forms.ModelForm):
    class Meta:
        model = Lease
        fields = '__all__'
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'terms': forms.Textarea(attrs={'rows': 5}),
        }
