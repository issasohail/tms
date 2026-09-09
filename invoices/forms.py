from .models import IescoStandaloneMeter, RecurringCharge, WaterBill
from django import forms
from .models import Invoice, InvoiceItem
from django.forms import inlineformset_factory
from django.utils import timezone
from datetime import timedelta
import re
from properties.models import Unit


class IescoStandaloneMeterForm(forms.ModelForm):
    class Meta:
        model = IescoStandaloneMeter
        fields = ("reference_no", "description", "is_active")
        widgets = {
            "reference_no": forms.TextInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "inputmode": "numeric",
                    "maxlength": "14",
                    "placeholder": "14-digit reference",
                }
            ),
            "description": forms.TextInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "placeholder": "e.g. Office common meter",
                }
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_reference_no(self):
        value = (self.cleaned_data.get("reference_no") or "").strip()
        if not re.fullmatch(r"\d{14}", value):
            raise forms.ValidationError("Enter a valid 14-digit IESCO reference number.")
        if Unit.objects.filter(electric_meter_num=value).exists():
            raise forms.ValidationError(
                "This reference number is already assigned to a property unit."
            )
        return value


class IescoUnitReferenceForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ("electric_meter_num",)
        labels = {"electric_meter_num": "IESCO reference number"}
        widgets = {
            "electric_meter_num": forms.TextInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "inputmode": "numeric",
                    "maxlength": "14",
                    "placeholder": "14-digit reference",
                }
            )
        }

    def clean_electric_meter_num(self):
        value = (self.cleaned_data.get("electric_meter_num") or "").strip()
        if not re.fullmatch(r"\d{14}", value):
            raise forms.ValidationError("Enter a valid 14-digit IESCO reference number.")
        duplicate_units = Unit.objects.filter(electric_meter_num=value)
        if self.instance.pk:
            duplicate_units = duplicate_units.exclude(pk=self.instance.pk)
        if duplicate_units.exists():
            raise forms.ValidationError(
                "This reference number is already assigned to another unit."
            )
        if IescoStandaloneMeter.objects.filter(reference_no=value).exists():
            raise forms.ValidationError(
                "This reference number is registered as a standalone meter."
            )
        return value


class IescoMeterAssignmentForm(forms.Form):
    reference_no = forms.CharField(
        max_length=14,
        label="IESCO reference number",
        widget=forms.TextInput(
            attrs={"class": "form-control", "inputmode": "numeric", "maxlength": "14"}
        ),
    )
    unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        required=False,
        empty_label="No property/unit (standalone meter)",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    description = forms.CharField(
        required=False,
        max_length=255,
        help_text="Required when no property/unit is selected.",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. Solar or common-area meter"}
        ),
    )
    is_active = forms.BooleanField(
        required=False,
        label="Active",
        help_text="Assigned meters must also have a current active lease to be included in Fetch All Active.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, unit_queryset=None, current_unit=None, current_standalone=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_unit = current_unit
        self.current_standalone = current_standalone
        self.fields["unit"].queryset = unit_queryset or Unit.objects.none()

    def clean_reference_no(self):
        value = (self.cleaned_data.get("reference_no") or "").strip()
        if not re.fullmatch(r"\d{14}", value):
            raise forms.ValidationError("Enter a valid 14-digit IESCO reference number.")
        duplicate_units = Unit.objects.filter(electric_meter_num=value)
        if self.current_unit:
            duplicate_units = duplicate_units.exclude(pk=self.current_unit.pk)
        if duplicate_units.exists():
            raise forms.ValidationError("This reference number is already assigned to another unit.")
        duplicate_standalone = IescoStandaloneMeter.objects.filter(reference_no=value)
        if self.current_standalone:
            duplicate_standalone = duplicate_standalone.exclude(pk=self.current_standalone.pk)
        if duplicate_standalone.exists():
            raise forms.ValidationError("This reference number is already registered as a standalone meter.")
        return value

    def clean(self):
        cleaned = super().clean()
        unit = cleaned.get("unit")
        reference_no = cleaned.get("reference_no")
        existing_unit_reference = str(getattr(unit, "electric_meter_num", "") or "").strip()
        if (
            unit
            and unit != self.current_unit
            and re.fullmatch(r"\d{14}", existing_unit_reference)
            and existing_unit_reference != reference_no
        ):
            self.add_error("unit", "The selected unit already has another IESCO reference number.")
        if not unit and not (cleaned.get("description") or "").strip():
            self.add_error("description", "Enter a description for a standalone meter.")
        return cleaned


from django import forms
from django.forms import inlineformset_factory
from .models import Invoice, InvoiceItem, ItemCategory
from .widgets import LeaseSelect2


from django import forms
from django.utils import timezone
from .models import Invoice


class InvoiceForm(forms.ModelForm):
    # Accept both the US format users type AND the HTML5 yyyy-mm-dd (in case a browser posts that)
    issue_date = forms.DateField(
        input_formats=["%m/%d/%Y", "%Y-%m-%d"],
        widget=forms.DateInput(
            format="%m/%d/%Y",
            attrs={
                "class": "form-control form-control-sm datepicker",
                "placeholder": "MM/DD/YYYY",
                "autocomplete": "off",
                "inputmode": "numeric",
            },
        ),
    )
    due_date = forms.DateField(
        input_formats=["%m/%d/%Y", "%Y-%m-%d"],
        widget=forms.DateInput(
            format="%m/%d/%Y",
            attrs={
                "class": "form-control form-control-sm datepicker",
                "placeholder": "MM/DD/YYYY",
                "autocomplete": "off",
                "inputmode": "numeric",
            },
        ),
    )

    class Meta:
        model = Invoice
        # include your other fields too:
        fields = "__all__"
        exclude = [
            "lifecycle_status",
            "lifecycle_status_reason",
            "lifecycle_status_updated_by",
            "lifecycle_status_updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure these are editable (remove any previous 'disabled' logic)
        self.fields["issue_date"].disabled = False
        self.fields["due_date"].disabled = False

        # Optional: default both to today on CREATE (no pk yet)
        if not self.instance or not self.instance.pk:
            today = timezone.localdate()
            self.fields["issue_date"].initial = today
            self.fields["due_date"].initial = today


InvoiceItemFormSet = inlineformset_factory(
    Invoice, InvoiceItem,
    fields=["category", "description", "amount"],
    extra=1, can_delete=True
)


class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ["category", "description", "amount",
                  "is_recurring"]  # <- amount included
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3, "maxlength": "500", "class": "form-control form-control-sm"}),
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Force required at form level
        self.fields["amount"].required = True


InvoiceItemFormSet = inlineformset_factory(
    Invoice, InvoiceItem,
    form=InvoiceItemForm,
    extra=1, can_delete=True
)
# invoices/forms.py


class RecurringChargeForm(forms.ModelForm):
    class Meta:
        model = RecurringCharge
        fields = [
            'kind', 'scope', 'lease', 'property',
            'category', 'description', 'amount',
            'day_of_month', 'start_date', 'end_date', 'active'
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date', 'placeholder': 'YYYY-MM-DD'}),
            'end_date': forms.DateInput(attrs={'type': 'date', 'placeholder': 'YYYY-MM-DD'}),
            'description': forms.TextInput(attrs={'placeholder': 'e.g., Parking, Gas, etc.'}),
        }
        help_texts = {
            'start_date': 'Format: YYYY-MM-DD',
            'end_date': 'Format: YYYY-MM-DD',
        }


class WaterBillForm(forms.ModelForm):
    class Meta:
        model = WaterBill
        fields = ['property', 'period', 'total_amount', 'description']
        widgets = {
            'period': forms.DateInput(attrs={'type': 'date'}),
        }
# invoices/forms.py
from django import forms
from .models import SecurityDepositTransaction
from .services import security_deposit_totals
from decimal import Decimal


class SecurityDepositTransactionForm(forms.ModelForm):
    def __init__(self, *args, lease=None, **kwargs):
        self.lease = lease
        super().__init__(*args, **kwargs)

    class Meta:
        model = SecurityDepositTransaction
        fields = [
            'date', 'type', 'amount', 'deduction_amount', 'deduction_reason',
            'refund_payment_method', 'refund_status', 'notes', 'refund_notes'
        ]
        widgets = {
            'date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control form-control-sm'}
            ),
            'type': forms.Select(
                attrs={'class': 'form-select form-select-sm'}
            ),
            'amount': forms.NumberInput(
                attrs={'class': 'form-control form-control-sm'}
            ),
            'deduction_amount': forms.NumberInput(
                attrs={'class': 'form-control form-control-sm', 'min': '0', 'step': '0.01'}
            ),
            'deduction_reason': forms.Textarea(
                attrs={'rows': 2, 'class': 'form-control form-control-sm'}
            ),
            'refund_payment_method': forms.Select(
                attrs={'class': 'form-select form-select-sm'}
            ),
            'refund_status': forms.Select(
                attrs={'class': 'form-select form-select-sm'}
            ),
            'notes': forms.Textarea(
                attrs={'rows': 2, 'class': 'form-control form-control-sm'}
            ),
            'refund_notes': forms.Textarea(
                attrs={'rows': 2, 'class': 'form-control form-control-sm'}
            ),
        }

    def clean(self):
        cleaned = super().clean()
        tx_type = cleaned.get('type')
        amount = cleaned.get('amount') or Decimal('0.00')
        deduction = cleaned.get('deduction_amount') or Decimal('0.00')

        if tx_type == 'REFUND' and self.lease:
            totals = security_deposit_totals(self.lease)
            available = totals.get('currently_held') or Decimal('0.00')
            if self.instance and self.instance.pk and self.instance.type == 'REFUND':
                available += self.instance.amount or Decimal('0.00')
                available += self.instance.deduction_amount or Decimal('0.00')
            if amount + deduction > available:
                raise forms.ValidationError(
                    "Refund plus deductions cannot exceed the currently held security deposit."
                )
        return cleaned
