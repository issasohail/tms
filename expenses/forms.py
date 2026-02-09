from django import forms
from .models import Expense, ExpenseCategory, ExpenseDistribution
from properties.models import Property
from django.utils.timezone import now
from invoices.models import ItemCategory


class ExpenseForm(forms.ModelForm):
    category = forms.ModelChoiceField(
        queryset=ItemCategory.objects.filter(is_active=True).order_by('name'),
        required=False,
        label="Category"
    )

    class Meta:
        model = Expense
        fields = ['property', 'unit', 'category', 'amount',
                  'date', 'description', 'receipt']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = ItemCategory.objects.all().order_by(
            'name')
        self.fields['property'].queryset = Property.objects.all()
        self.fields['date'].initial = now().date()


class ExpenseDistributionForm(forms.ModelForm):
    class Meta:
        model = ExpenseDistribution
        fields = ['expense', 'unit', 'amount']
        widgets = {
            'expense': forms.Select(attrs={'class': 'form-control'}),
            'unit': forms.Select(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        expense_id = kwargs.pop('expense_id', None)
        super().__init__(*args, **kwargs)

        if expense_id:
            self.fields['expense'].initial = expense_id
            self.fields['expense'].disabled = True
            self.fields['unit'].queryset = self.fields['unit'].queryset.filter(
                property_id=self.instance.expense.property_id
            )
