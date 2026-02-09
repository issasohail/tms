from invoices.models import ItemCategory
from properties.models import Property, Unit
from django import forms
from .models import FinancialReport
from django.utils import timezone
from datetime import timedelta


class FinancialReportForm(forms.ModelForm):
    class Meta:
        model = FinancialReport
        fields = ['report_type', 'start_date', 'end_date', 'notes']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Set default dates based on report type
        if 'report_type' in self.data:
            report_type = self.data['report_type']
            today = timezone.now().date()

            if report_type == 'monthly':
                self.initial['start_date'] = today.replace(day=1)
                self.initial['end_date'] = (today.replace(
                    day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            elif report_type == 'quarterly':
                quarter = (today.month - 1) // 3
                self.initial['start_date'] = today.replace(
                    month=quarter * 3 + 1, day=1)
                self.initial['end_date'] = (
                    self.initial['start_date'] + timedelta(days=92)).replace(day=1) - timedelta(days=1)
            elif report_type == 'annual':
                self.initial['start_date'] = today.replace(month=1, day=1)
                self.initial['end_date'] = today.replace(month=12, day=31)


# reports/forms.py

PRESETS = [
    ('today', 'Today'), ('yesterday', 'Yesterday'),
    ('this_week', 'This week'), ('last_week', 'Last week'),
    ('this_month', 'This month'), ('last_month', 'Last month'),
    ('this_year', 'This year'),
    ('custom', 'Custom range'),
]


class ExpenseFilterForm(forms.Form):
    property = forms.ModelChoiceField(
        queryset=Property.objects.all(), required=False)
    unit = forms.ModelChoiceField(queryset=Unit.objects.all(), required=False)
    category = forms.ModelChoiceField(
        queryset=ItemCategory.objects.all(), required=False)
    preset = forms.ChoiceField(
        choices=PRESETS, required=False, initial='this_month')
    start = forms.DateField(
        required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(
        required=False, widget=forms.DateInput(attrs={'type': 'date'}))
