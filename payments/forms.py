# payments/forms.py
from django import forms
from django.apps import apps
from .models import Payment, PaymentDetail
from properties.models import Property, Unit
from tenants.models import Tenant
from django.db.models import Case, DecimalField, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from leases.models import Lease
from invoices.models import Invoice
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from core.models import PaymentMethod


def optimize_lease_dropdown_queryset(qs):
    money_field = DecimalField(max_digits=12, decimal_places=2)
    zero = Value(Decimal("0.00"), output_field=money_field)

    invoice_total = (
        Invoice.objects.filter(lease_id=OuterRef("pk"))
        .values("lease_id")
        .annotate(total=Coalesce(Sum("amount"), zero))
        .values("total")[:1]
    )
    payment_total = (
        Payment.objects.filter(lease_id=OuterRef("pk"))
        .values("lease_id")
        .annotate(
            total=Coalesce(
                Sum(
                    Case(
                        When(detail__isnull=False, then=F("detail__lease_amount")),
                        default=F("amount"),
                        output_field=money_field,
                    )
                ),
                zero,
            )
        )
        .values("total")[:1]
    )

    return (
        qs.select_related('tenant', 'unit', 'unit__property')
        .annotate(
            _invoice_total=Coalesce(Subquery(invoice_total, output_field=money_field), zero),
            _payment_total=Coalesce(Subquery(payment_total, output_field=money_field), zero),
        )
        .annotate(
            cached_balance=ExpressionWrapper(
                F("_invoice_total") - F("_payment_total"),
                output_field=money_field,
            )
        )
    )


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
            "pattern": r"^-?\d+([.]\d{0,2})?$",  # xx | -xx | xx. | xx.x | xx.xx
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
        # PERF: lease dropdown label renders every option; keep related tenant/unit/property loaded.
        qs = Lease.objects.all()
        if selected_id:
            qs = qs.filter(Q(status='active') | Q(pk=selected_id))
        else:
            qs = qs.filter(status='active')

        self.fields['lease'].queryset = optimize_lease_dropdown_queryset(qs).order_by('tenant__first_name')

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
            # PERF: get_balance aggregates invoices/payments per lease option; Phase 5 replaces this with annotated values.
            balance_value = getattr(obj, "cached_balance", None)
            if balance_value is None:
                balance_value = obj.get_balance
            balance = "{:,.2f}".format(float(balance_value or Decimal("0.00")))
            return f"{obj.tenant.get_full_name()} | {obj.unit.property.property_name} - {obj.unit.unit_number} | Balance: {balance}"
        self.fields['lease'].label_from_instance = format_lease_label

        # 6) Unit list if a property is chosen
        if 'property' in self.data:
            try:
                property_id = int(self.data.get('property'))
                self.fields['unit'].queryset = Unit.objects.select_related("property").filter(
                    property_id=property_id)
            except (ValueError, TypeError):
                pass
        elif instance_lease:
            self.fields['unit'].queryset = Unit.objects.select_related("property").filter(
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

        # match xx | -xx | xx. | xx.x | xx.xx
        import re
        if not re.fullmatch(r"-?\d+(?:\.\d{0,2})?", raw):
            raise forms.ValidationError(
                "Enter a valid amount (e.g., 100, -100, 100.5, 100.50).")

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

        self.fields['lease'].queryset = optimize_lease_dropdown_queryset(lease_qs)

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

class PaymentDetailForm(forms.ModelForm):
    MODE_CHOICES = [
        ("LEASE", "Lease"),
        ("LEASE_REFUND", "Lease Refund"),
        ("SECURITY", "Security"),
        ("REFUND", "Security Refund"),
        ("SPLIT", "Split"),
    ]

    payment_type = forms.ChoiceField(
        label="Payment Type",
        choices=MODE_CHOICES,
        required=False,
        initial="LEASE",
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = PaymentDetail
        fields = [
            "payment_type",
            "lease_amount",
            "security_amount",
            "electricity_amount",
            "electricity_meter",
            "security_type",
        ]
        widgets = {
            "lease_amount": forms.NumberInput(attrs={"step": "0.01", "class": "form-control"}),
            "security_amount": forms.NumberInput(attrs={"step": "0.01", "class": "form-control"}),
            "electricity_amount": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "class": "form-control"}
            ),
            "electricity_meter": forms.Select(attrs={"class": "form-select"}),
            "security_type": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        self.payment_total = kwargs.pop("payment_total", None)
        self.lease = kwargs.pop("lease", None)
        super().__init__(*args, **kwargs)

        from smart_meter.models import Meter, MeterInstallation

        if self.lease is None:
            instance_payment = getattr(getattr(self, "instance", None), "payment", None)
            self.lease = getattr(instance_payment, "lease", None)
        meter_ids = []
        if self.lease:
            meter_ids = MeterInstallation.objects.filter(
                lease=self.lease,
            ).values_list("meter_id", flat=True)
        elif getattr(self.instance, "electricity_meter_id", None):
            meter_ids = [self.instance.electricity_meter_id]
        self.fields["electricity_meter"].queryset = Meter.objects.filter(
            pk__in=meter_ids
        ).order_by("meter_number")
        self.fields["electricity_amount"].required = False
        self.fields["electricity_amount"].initial = Decimal("0.00")
        self.fields["electricity_meter"].required = False
        self.fields["electricity_meter"].empty_label = "Select electricity meter"
        self.fields["electricity_amount"].label = "Electricity Allocation"
        self.fields["electricity_amount"].help_text = (
            "This is part of Lease Amount, not an additional charge. Enter the amount the tenant "
            "is specifically paying toward electricity."
        )
        self.fields["electricity_meter"].help_text = (
            "Select the meter whose electricity balance should receive this allocation."
        )

        # When editing an existing payment detail, pick the correct mode so JS doesn't overwrite values.
        inst = getattr(self, "instance", None)
        if inst and getattr(inst, "pk", None):
            lease_amt = inst.lease_amount or Decimal("0.00")
            sec_amt = inst.security_amount or Decimal("0.00")

            sec_type = (inst.security_type or "PAYMENT").upper()

            if lease_amt < 0 and sec_amt <= 0:
                mode = "LEASE_REFUND"
            elif sec_type == "REFUND" and sec_amt > 0 and lease_amt <= 0:
                mode = "REFUND"
            elif lease_amt > 0 and sec_amt > 0:
                mode = "SPLIT"
            elif sec_amt > 0 and lease_amt <= 0:
                mode = "SECURITY"
            else:
                mode = "LEASE"

            self.fields["payment_type"].initial = mode
            # If the field already has initial/posted value, don't fight it; but for GET edit this fixes display.
            if "payment_type" not in self.data:
                self.initial["payment_type"] = mode
            if sec_type == "REFUND" and sec_amt > 0 and "security_amount" not in self.data:
                self.initial["security_amount"] = -sec_amt
        else:
            self.fields["payment_type"].initial = "LEASE"

    def clean(self):
        cleaned_data = super().clean()
        lease_amount = cleaned_data.get("lease_amount") or Decimal("0.00")
        security_amount = cleaned_data.get("security_amount") or Decimal("0.00")
        electricity_amount = cleaned_data.get("electricity_amount") or Decimal("0.00")
        electricity_meter = cleaned_data.get("electricity_meter")

        if electricity_amount < 0:
            self.add_error("electricity_amount", "Electricity allocation cannot be negative.")
        if electricity_amount > max(lease_amount, Decimal("0.00")):
            self.add_error(
                "electricity_amount",
                "Electricity allocation cannot exceed the positive lease amount.",
            )
        if electricity_amount > 0 and not electricity_meter:
            self.add_error("electricity_meter", "Select the electricity meter for this allocation.")
        if electricity_meter and self.lease:
            from smart_meter.models import MeterInstallation

            if not MeterInstallation.objects.filter(
                lease=self.lease,
                meter=electricity_meter,
            ).exists():
                self.add_error("electricity_meter", "The selected meter is not linked to this lease.")

        if lease_amount < 0 and security_amount != 0:
            raise forms.ValidationError(
                "Record a lease refund separately; its security amount must be zero."
            )
        if security_amount < 0 and lease_amount != 0:
            raise forms.ValidationError(
                "Record a security refund separately; its lease amount must be zero."
            )

        signed_total = lease_amount + security_amount
        if lease_amount < 0:
            mode = "LEASE_REFUND"
            security_type = "PAYMENT"
        elif security_amount < 0:
            mode = "REFUND"
            security_type = "REFUND"
        elif lease_amount > 0 and security_amount > 0:
            mode = "SPLIT"
            security_type = "PAYMENT"
        elif security_amount > 0:
            mode = "SECURITY"
            security_type = "PAYMENT"
        else:
            mode = "LEASE"
            security_type = "PAYMENT"

        if self.payment_total is not None:
            payment_total = Decimal(self.payment_total or "0.00")
            if payment_total != signed_total:
                raise forms.ValidationError(
                    f"Total amount ({payment_total}) must equal lease plus security ({signed_total})."
                )

        cleaned_data["payment_type"] = mode
        cleaned_data["lease_amount"] = lease_amount
        cleaned_data["security_amount"] = abs(security_amount) if mode == "REFUND" else security_amount
        cleaned_data["electricity_amount"] = electricity_amount
        cleaned_data["security_type"] = security_type
        return cleaned_data
