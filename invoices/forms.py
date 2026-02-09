from .models import RecurringCharge, WaterBill
from django import forms
from .models import Invoice, InvoiceItem
from django.forms import inlineformset_factory
from django.utils import timezone
from datetime import timedelta


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


class SecurityDepositTransactionForm(forms.ModelForm):
    class Meta:
        model = SecurityDepositTransaction
        fields = ['date', 'type', 'amount', 'notes']
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
            'notes': forms.Textarea(
                attrs={'rows': 2, 'class': 'form-control form-control-sm'}
            ),
        }
