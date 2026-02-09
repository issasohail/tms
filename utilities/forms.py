from django import forms
from .models import Utility


class UtilityForm(forms.ModelForm):
    class Meta:
        model = Utility
        fields = '__all__'
        widgets = {
            'billing_date': forms.DateInput(attrs={'type': 'date'}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add Bootstrap classes to form fields
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'form-control'})
        self.fields['description'].widget.attrs.update(
            {'class': 'form-control', 'rows': 3})
