from .models import RecurringCharge, WaterBill
from django import forms
from .models import Invoice, InvoiceItem
from django.forms import inlineformset_factory
from django.utils import timezone
from datetime import timedelta


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['lease', 'issue_date',
                  'due_date', 'amount', 'description', 'notes']
        widgets = {
            'issue_date': forms.DateInput(attrs={'type': 'date'}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.initial['issue_date'] = timezone.now().date()
            self.initial['due_date'] = (
                timezone.now() + timedelta(days=7)).date()


class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ['category',
                  'description', 'amount', 'is_recurring']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].required = True


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
