from .models import Lease, LeaseFamily, LeaseFamilyMember, LeaseRelationshipType

from django.forms import BaseInlineFormSet, inlineformset_factory
from .models import LeaseTemplate
from django import forms
from .models import Lease
from properties.models import Property, Unit
from django.utils import timezone
from tenants.models import Tenant
from django import forms
from django.utils import timezone
from datetime import timedelta


from django import forms
from .models import Lease
from properties.models import Property, Unit
from tenants.models import Tenant
from django.utils import timezone
from datetime import timedelta


class LeaseForm(forms.ModelForm):
    property = forms.ModelChoiceField(
        queryset=Property.objects.all(),
        required=True,
        label="Property",
        widget=forms.Select(attrs={
            'id': 'id_property',
            'class': 'form-control form-control-sm',
        })
    )

    unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        required=True,
        label="Unit",
        widget=forms.Select(attrs={
            'id': 'id_unit',
            'class': 'form-control form-control-sm'
        })
    )

    class Meta:
        model = Lease
        fields = '__all__'
        widgets = {
            'tenant': forms.Select(attrs={
                'class': 'form-control form-control-sm select2'  # add select2 CSS class
            }),

            # Date Fields
            'start_date': forms.DateInput(attrs={'class': 'form-control form-control-sm datepicker', 'autocomplete': 'off'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control form-control-sm datepicker', 'autocomplete': 'off'}),
            'agreement_date': forms.DateInput(attrs={'class': 'form-control form-control-sm datepicker', 'autocomplete': 'off'}),
            'security_deposit_return_date': forms.DateInput(attrs={'class': 'form-control form-control-sm datepicker', 'autocomplete': 'off'}),

            # Numbers and Money
            'monthly_rent': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'society_maintenance': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'water_charges': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'internet_charges': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'agreement_charges': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'security_deposit': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'security_deposit_return_amount': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'rent_increase_percent': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'late_fee': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'min_occupancy_period': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),

            # Other
            'terms': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'security_deposit_return_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'status': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'tenant': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'security_deposit_paid': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'security_deposit_returned': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'electricity_meter_reading': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),


            # Inventory
            'inventory_ceiling_fans': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'inventory_exhaust_fans': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'inventory_ceiling_lights': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'inventory_stove': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'inventory_wardrobes': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'inventory_keys': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'paint_condition': forms.Textarea(attrs={'class': 'form-control form-control-sm', 'rows': 2}),
            'key_replacement_cost': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'electric_unit_rate': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Start empty by default
        self.fields['unit'].queryset = Unit.objects.none()
        self.fields['tenant'].queryset = Tenant.objects.order_by(
            'first_name', 'last_name')

        # If user selected a property (POST or initial), filter units
        pid = self.data.get('property') or self.initial.get('property')
        if pid:
            try:
                self.fields['unit'].queryset = Unit.objects.select_related("property").filter(
                    property_id=int(pid)
                ).order_by('unit_number')
            except (TypeError, ValueError):
                pass
        # Editing existing lease: keep units for that lease's property
        elif self.instance.pk and self.instance.unit:
            self.fields['property'].initial = self.instance.unit.property
            self.fields['unit'].queryset = Unit.objects.select_related("property").filter(
                property=self.instance.unit.property
            )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError("End date cannot be before start date")
        return cleaned_data

    def clean_signed_agreement(self):
        file = self.cleaned_data.get('signed_agreement')
        if file:
            if file.size > 10 * 1024 * 1024:
                raise forms.ValidationError("File too large (max 10MB)")
            if not file.name.lower().endswith('.pdf'):
                raise forms.ValidationError("Only PDF files are allowed")
        return file


class CustomRenewForm(forms.Form):
    rent_increase_percent = forms.DecimalField(
        label="Rent Increase Percentage",
        max_digits=5,
        decimal_places=2,
        min_value=0,
        initial=10.00,
        help_text="Enter percentage increase (e.g., 10 for 10%)"
    )

    new_end_date = forms.DateField(
        label="New End Date",
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text="Leave blank for standard 1-year renewal"
    )

    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        help_text="Optional notes about this renewal"
    )

    def __init__(self, *args, **kwargs):
        self.lease = kwargs.pop('lease', None)
        super().__init__(*args, **kwargs)

        if self.lease:
            self.fields['rent_increase_percent'].initial = self.lease.rent_increase_percent

from django import forms
from django.forms import inlineformset_factory

from .models import AgreementPlaceholder, Lease, DefaultClause, LeaseAgreementClause, WhatsAppTemplate
from .models_renewal import LeaseRenewal


class DefaultClauseForm(forms.ModelForm):
    class Meta:
        model = DefaultClause
        fields = ["clause_number", "body", "is_active"]
        widgets = {
            "clause_number": forms.NumberInput(attrs={"class": "form-control form-control-sm"}),
            "body": forms.Textarea(attrs={"rows": 8, "class": "form-control clause-body-field"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class AgreementPlaceholderForm(forms.ModelForm):
    class Meta:
        model = AgreementPlaceholder
        fields = [
            "key",
            "label",
            "description",
            "category",
            "source_type",
            "resolver_key",
            "django_path",
            "default_value",
            "is_active",
            "sort_order",
        ]
        widgets = {
            "key": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "label": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "description": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 3}),
            "category": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "source_type": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "resolver_key": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "django_path": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "default_value": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "0"}),
        }

    def clean_key(self):
        key = (self.cleaned_data.get("key") or "").strip().upper()
        key = key.strip("[]")
        if " " in key:
            raise forms.ValidationError("Use underscores instead of spaces.")
        return key


class WhatsAppTemplateForm(forms.ModelForm):
    class Meta:
        model = WhatsAppTemplate
        fields = ["template_type", "name", "body", "is_active"]
        widgets = {
            "template_type": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "body": forms.Textarea(attrs={"rows": 10, "class": "form-control whatsapp-template-body"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


LeaseClauseFormSet = inlineformset_factory(
    Lease,
    LeaseAgreementClause,
    fields=["clause_number", "template_text", "is_customized"],
    extra=0,
    can_delete=False,
    widgets={
        "template_text": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    },
)


class LeaseRenewalHistoryForm(forms.ModelForm):
    class Meta:
        model = LeaseRenewal
        fields = [
            "renewal_number",
            "start_date",
            "end_date",
            "agreement_date",
            "monthly_rent",
            "society_maintenance",
            "water_charges",
            "internet_charges",
            "rent_increase_percent",
            "is_agreement_signed",
            "notes",
        ]
        widgets = {
            "renewal_number": forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "1"}),
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control form-control-sm"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control form-control-sm"}),
            "agreement_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control form-control-sm"}),
            "monthly_rent": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "society_maintenance": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "water_charges": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "internet_charges": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "rent_increase_percent": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "is_agreement_signed": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 2}),
        }


class LeaseRenewalHistoryFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        seen_numbers = set()
        periods = []

        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue

            number = form.cleaned_data.get("renewal_number")
            start_date = form.cleaned_data.get("start_date")
            end_date = form.cleaned_data.get("end_date")

            if number in seen_numbers:
                form.add_error("renewal_number", "Renewal number must be unique for this lease.")
            if number:
                seen_numbers.add(number)

            if start_date and end_date and end_date <= start_date:
                form.add_error("end_date", "End date must be after start date.")

            if start_date and end_date:
                for other_start, other_end in periods:
                    if start_date <= other_end and end_date >= other_start:
                        form.add_error("start_date", "This renewal period overlaps another renewal row.")
                        break
                periods.append((start_date, end_date))

    def save_new(self, form, commit=True):
        obj = form.save(commit=False)
        obj.lease = self.instance
        if not obj.renewal_number:
            used = [
                f.cleaned_data.get("renewal_number")
                for f in self.forms
                if hasattr(f, "cleaned_data")
                and f.cleaned_data
                and not f.cleaned_data.get("DELETE")
                and f.cleaned_data.get("renewal_number")
            ]
            obj.renewal_number = max(used or [0]) + 1
        if commit:
            obj.save()
            form.save_m2m()
        return obj


LeaseRenewalInlineFormSet = inlineformset_factory(
    Lease,
    LeaseRenewal,
    form=LeaseRenewalHistoryForm,
    formset=LeaseRenewalHistoryFormSet,
    extra=1,
    can_delete=True,
)

class RenewLeaseForm(forms.Form):
    """Form for standard lease renewal with default percentage"""
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        help_text="Optional notes about this renewal"
    )

    def __init__(self, *args, **kwargs):
        self.lease = kwargs.pop('lease', None)
        super().__init__(*args, **kwargs)


class LeaseTemplateForm(forms.ModelForm):
    class Meta:
        model = LeaseTemplate
        fields = '__all__'
        widgets = {
            'content': forms.Textarea(attrs={'rows': 20}),
        }


# leases/forms.py


# leases/forms.py (add these)


class LeaseFamilyForm(forms.ModelForm):
    class Meta:
        model = LeaseFamilyMember
        fields = ['family_member', 'relationship_type', 'lives_with_tenant', 'notes']
        widgets = {
            'family_member': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'relationship_type': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'lives_with_tenant': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["relationship_type"].queryset = LeaseRelationshipType.objects.filter(is_active=True).order_by("sort_order", "name")


LeaseFamilyFormSet = inlineformset_factory(
    Lease,
    LeaseFamilyMember,
    form=LeaseFamilyForm,
    extra=0,            # existing links only; quick-add handles new rows
    can_delete=True
)
