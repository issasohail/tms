# payments/forms.py
from django import forms
from django.apps import apps
from .models import Payment
from properties.models import Property, Unit
from tenants.models import Tenant
from django.db.models import Q
from leases.models import Lease
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class PaymentForm(forms.ModelForm):
    lease = forms.ModelChoiceField(
        queryset=Lease.objects.none(),  # Start with empty queryset
        widget=forms.Select(attrs={'class': 'select-lease'}),
        label="Lease"
    )
    send_receipt = forms.BooleanField(
        required=False,
        initial=True,
        label='Send receipt'
    )
    include_inactive = forms.BooleanField(
        required=False,
        initial=False,
        label='Include inactive leases'
    )
    property = forms.ModelChoiceField(
        queryset=Property.objects.all(),
        required=False,
        label='Property'
    )
    unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        required=False,
        label='Unit'
    )
    tenant_search = forms.CharField(
        required=False,
        label='Search Tenant',
        widget=forms.TextInput(attrs={
            'placeholder': 'Type to filter tenants...',
            'class': 'tenant-search'
        })
    )
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        localize=False,   # avoid locale surprises
        widget=forms.NumberInput(attrs={
            "id": "id_amount",
            "step": "0.01",            # allow any 2dp
            "inputmode": "decimal",    # better mobile keypad
            "pattern": r"^\d+([.]\d{0,2})?$",  # xx | xx. | xx.x | xx.xx
            "lang": "en",              # force '.' as decimal sep in some browsers
            "autocomplete": "off",
        })
    )

    def __init__(self, *args, **kwargs):
        lease = kwargs.pop('lease', None)
        super().__init__(*args, **kwargs)

        # Initialize with all active leases
        self.fields['lease'].queryset = Lease.objects.filter(status='active').select_related(
            'tenant', 'unit', 'unit__property'
        ).order_by('tenant__first_name')

        if lease:
            self.lease = lease
            # Set initial values
            self.fields['lease'].initial = lease
            self.fields['amount'].initial = lease.get_balance

            # Limit lease choices to just this lease
            self.fields['lease'].queryset = Lease.objects.filter(pk=lease.pk)

            # Make the field read-only
            self.fields['lease'].widget.attrs['readonly'] = True
            self.fields['lease'].widget.attrs['onfocus'] = "this.blur()"

            # Set property and unit based on lease
            self.fields['property'].initial = lease.unit.property
            self.fields['unit'].initial = lease.unit
            self.fields['unit'].queryset = Unit.objects.filter(
                property=lease.unit.property)

        # Custom label format
        def format_lease_label(obj):
            balance = "{:,.2f}".format(float(obj.get_balance))
            if lease:
                return f"{obj.tenant.get_full_name()} | {obj.unit.property.property_name} - {obj.unit.unit_number} | Balance: {balance}"
            else:
                return f"{obj.id} - {obj.tenant.get_full_name()} | {obj.unit.property.property_name} - {obj.unit.unit_number} | Balance: {balance}"

        self.fields['lease'].label_from_instance = format_lease_label

        # Set up the lease queryset based on filters
        if 'property' in self.data:
            try:
                property_id = int(self.data.get('property'))
                self.fields['unit'].queryset = Unit.objects.filter(
                    property_id=property_id)
            except (ValueError, TypeError):
                pass
        elif self.instance.pk and self.instance.lease:
            self.fields['unit'].queryset = Unit.objects.filter(
                property=self.instance.lease.unit.property)

    class Meta:
        model = Payment
        fields = ['lease', 'payment_date', 'amount',
                  'payment_method', 'reference_number', 'notes', 'send_receipt']
        widgets = {
            'payment_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
            'lease': forms.Select(attrs={'class': 'select-lease'}),
        }

    def clean_amount(self):
        raw = self.data.get("amount", "")
        raw = (raw or "").strip().replace(",", ".")  # tolerate commas
        if raw.endswith("."):                         # allow trailing dot
            raw += "0"

        # match xx | xx. | xx.x | xx.xx
        import re
        if not re.fullmatch(r"\d+(?:\.\d{0,2})?", raw):
            raise forms.ValidationError(
                "Enter a valid amount (e.g., 100, 100., 100.5, 100.50).")

        try:
            amt = Decimal(raw)
        except InvalidOperation:
            raise forms.ValidationError("Enter a valid number.")

        # normalize to 2 dp
        return amt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def clean(self):
        cleaned_data = super().clean()
        lease = cleaned_data.get('lease')
        include_inactive = cleaned_data.get('include_inactive')
        tenant_search = cleaned_data.get('tenant_search')
        property = cleaned_data.get('property')
        unit = cleaned_data.get('unit')

        # Get the filtered lease queryset
        lease_qs = self.get_filtered_leases(cleaned_data)

        # Set the lease queryset to the filtered one
        self.fields['lease'].queryset = lease_qs

        # Validate lease selection
        if lease and lease not in lease_qs:
            self.add_error(
                'lease', "Select a valid choice. That choice is not one of the available choices.")

        # Auto-select if only one lease matches and no lease is selected yet
        if lease_qs.count() == 1:
            single_lease = lease_qs.first()
            cleaned_data['lease'] = single_lease
            self.fields['lease'].initial = single_lease
            # Format the balance with commas and 2 decimal places
            cleaned_data['amount'] = "{:,.2f}".format(
                float(single_lease.get_balance))

        if not lease and not cleaned_data.get('lease'):
            self.add_error('lease', "A lease must be selected for payment.")

        return cleaned_data

    def get_filtered_leases(self, cleaned_data):
        Lease = apps.get_model('leases', 'Lease')
        lease_qs = Lease.objects.all()

        if not cleaned_data.get('include_inactive', False):
            lease_qs = lease_qs.filter(status='active')

        if cleaned_data.get('tenant_search'):
            lease_qs = lease_qs.filter(
                Q(tenant__id=cleaned_data['tenant_search'])
            )

        if cleaned_data.get('property'):
            lease_qs = lease_qs.filter(unit__property=cleaned_data['property'])
            if cleaned_data.get('unit'):
                lease_qs = lease_qs.filter(unit=cleaned_data['unit'])

        return lease_qs.order_by('tenant__first_name', 'tenant__last_name')
