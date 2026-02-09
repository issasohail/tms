# payments/forms.py
from django import forms
from django.apps import apps
from .models import Payment
from properties.models import Property, Unit
from tenants.models import Tenant
from django.db.models import Q
from leases.models import Lease
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from core.models import PaymentMethod

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
        # Accept an explicit lease from the view
        lease_param = kwargs.pop('lease', None)
        super().__init__(*args, **kwargs)

        # 1) Which lease id must be allowed even if inactive?
        posted_lease_id = self.data.get('lease') or None         # when POSTing
        instance_lease = getattr(self.instance, 'lease', None)  # when editing
        selected_id = None
        if posted_lease_id:
            selected_id = str(posted_lease_id).strip()
        elif lease_param:
            selected_id = str(lease_param.pk)
        elif instance_lease:
            selected_id = str(instance_lease.pk)

        # 2) Build queryset: active + (optionally) the selected (possibly inactive) one
        qs = Lease.objects.all()
        if selected_id:
            qs = qs.filter(Q(status='active') | Q(pk=selected_id))
        else:
            qs = qs.filter(status='active')

        self.fields['lease'].queryset = qs.select_related(
            'tenant', 'unit', 'unit__property'
        ).order_by('tenant__first_name')

        # 3) Preselect the lease if we have one
        if lease_param:
            self.fields['lease'].initial = lease_param.pk
        elif instance_lease:
            self.fields['lease'].initial = instance_lease.pk
        elif posted_lease_id and qs.filter(pk=posted_lease_id).exists():
            self.fields['lease'].initial = posted_lease_id

        # 4) Styling only — do NOT make it readonly; that breaks Select2/validation
        self.fields['lease'].widget.attrs.update({
            'class': 'form-control select2',
            'style': 'min-width: 200px;',
        })

        # 5) Your label logic (kept simple and one-liner)
        def format_lease_label(obj):
            balance = "{:,.2f}".format(float(obj.get_balance))
            return f"{obj.tenant.get_full_name()} | {obj.unit.property.property_name} - {obj.unit.unit_number} | Balance: {balance}"
        self.fields['lease'].label_from_instance = format_lease_label

        # 6) Unit list if a property is chosen
        if 'property' in self.data:
            try:
                property_id = int(self.data.get('property'))
                self.fields['unit'].queryset = Unit.objects.filter(
                    property_id=property_id)
            except (ValueError, TypeError):
                pass
        elif instance_lease:
            self.fields['unit'].queryset = Unit.objects.filter(
                property=instance_lease.unit.property)
            
        # 7) Dynamic payment methods (NEW)
        if 'payment_method' in self.fields:
            self.fields['payment_method'].queryset = PaymentMethod.objects.filter(
                is_active=True
            ).order_by('sort_order', 'name')
            self.fields['payment_method'].empty_label = "Select payment method"
            self.fields['payment_method'].widget.attrs.update({
                'class': 'form-control',
                'id': 'id_payment_method',  # used by JS
            })

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

        lease_qs = self.get_filtered_leases(cleaned_data)

        # ✅ include current lease even if inactive
        if lease:
            lease_qs = lease_qs | Lease.objects.filter(pk=lease.pk)

        self.fields['lease'].queryset = lease_qs

        # Auto-select and validation
        if lease_qs.count() == 1 and not lease:
            single_lease = lease_qs.first()
            cleaned_data['lease'] = single_lease
            self.fields['lease'].initial = single_lease
            cleaned_data['amount'] = "{:,.2f}".format(
                float(single_lease.get_balance))

        if not cleaned_data.get('lease'):
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

from django import forms
from payments.models import PaymentAllocation

from decimal import Decimal
from django import forms
from payments.models import PaymentAllocation

class PaymentAllocationForm(forms.ModelForm):
    MODE_CHOICES = [
        ("LEASE", "Lease"),
        ("SECURITY", "Security"),
        ("SPLIT", "Split"),
    ]

    allocation_mode = forms.ChoiceField(
        choices=MODE_CHOICES,
        required=False,
        initial="LEASE",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = PaymentAllocation
        fields = ["allocation_mode", "lease_amount", "security_amount", "security_type"]
        widgets = {
            "lease_amount": forms.NumberInput(attrs={"step": "0.01", "class": "form-control"}),
            "security_amount": forms.NumberInput(attrs={"step": "0.01", "class": "form-control"}),
            "security_type": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # When editing an existing allocation, pick the correct mode so JS doesn't overwrite values
        inst = getattr(self, "instance", None)
        if inst and getattr(inst, "pk", None):
            lease_amt = inst.lease_amount or Decimal("0.00")
            sec_amt = inst.security_amount or Decimal("0.00")

            if lease_amt > 0 and sec_amt > 0:
                mode = "SPLIT"
            elif sec_amt > 0 and lease_amt <= 0:
                mode = "SECURITY"
            else:
                mode = "LEASE"

            self.fields["allocation_mode"].initial = mode
            # If the field already has initial/posted value, don't fight it; but for GET edit this fixes display.
            if "allocation_mode" not in self.data:
                self.initial["allocation_mode"] = mode

    MODE_CHOICES = [
            ("LEASE", "Lease"),
            ("SECURITY", "Security"),
            ("SPLIT", "Split"),
        ]
    allocation_mode = forms.ChoiceField(
            choices=MODE_CHOICES,
            required=False,
            initial="LEASE",
            widget=forms.Select(attrs={"class": "form-select"})
        )
    class Meta:

        model = PaymentAllocation
        fields = ["allocation_mode","lease_amount", "security_amount", "security_type"]
        widgets = {
           
            "lease_amount": forms.NumberInput(attrs={ "class": "form-control"}),
            "security_amount": forms.NumberInput(attrs={ "class": "form-control"}),
            "security_type": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # If editing an existing allocation, set mode based on stored split
        inst = getattr(self, "instance", None)
        if inst and getattr(inst, "pk", None):
            la = inst.lease_amount or Decimal("0.00")
            sa = inst.security_amount or Decimal("0.00")

            if la > 0 and sa > 0:
                mode = "SPLIT"
            elif sa > 0 and la == 0:
                mode = "SECURITY"
            else:
                mode = "LEASE"

            self.fields["allocation_mode"].initial = mode
        else:
            # create default
            self.fields["allocation_mode"].initial = "LEASE"