from .models import Lease, LeaseFamily

from django.forms import inlineformset_factory
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
                self.fields['unit'].queryset = Unit.objects.filter(
                    property_id=int(pid)
                ).order_by('unit_number')
            except (TypeError, ValueError):
                pass
        # Editing existing lease: keep units for that lease's property
        elif self.instance.pk and self.instance.unit:
            self.fields['property'].initial = self.instance.unit.property
            self.fields['unit'].queryset = Unit.objects.filter(
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

from .models import Lease, DefaultClause, LeaseAgreementClause


class DefaultClauseForm(forms.ModelForm):
    class Meta:
        model = DefaultClause
        fields = ["clause_number", "body", "is_active"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
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
        model = LeaseFamily
        fields = ['tenant', 'relation', 'whatsapp_opt_in']
        widgets = {
            'tenant': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'relation': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'whatsapp_opt_in': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


LeaseFamilyFormSet = inlineformset_factory(
    Lease,
    LeaseFamily,
    form=LeaseFamilyForm,
    extra=0,            # existing links only; quick-add handles new rows
    can_delete=True
)
