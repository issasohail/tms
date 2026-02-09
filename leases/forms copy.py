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
        fields = '__all__'  # This includes all fields from your model
        widgets = {
            'start_date': forms.DateInput(attrs={
                'class': 'form-control form-control-sm datepicker',
                'autocomplete': 'off'
            }),
            'end_date': forms.DateInput(attrs={
                'class': 'form-control form-control-sm datepicker',
                'autocomplete': 'off'
            }),
            'security_deposit_return_date': forms.DateInput(attrs={
                'class': 'form-control form-control-sm datepicker',
                'autocomplete': 'off'
            }),
            'monthly_rent': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm'
            }),
            'society_maintenance': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm'
            }),
            'security_deposit': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm'
            }),
            'security_deposit_return_amount': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm'
            }),
            'rent_increase_percent': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm'
            }),
            'terms': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 5
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3
            }),
            'security_deposit_return_notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3
            }),
            'status': forms.Select(attrs={
                'class': 'form-select form-select-sm'
            }),
            'tenant': forms.Select(attrs={
                'class': 'form-select form-select-sm'
            }),
            'security_deposit_paid': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'security_deposit_returned': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.unit:
            # Set initial property value
            self.fields['property'].initial = self.instance.unit.property

            # Filter units based on property
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
            if file.size > 10 * 1024 * 1024:  # 10MB limit
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
