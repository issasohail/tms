from django import forms
from .models import Document, DocumentCategory, LeaseDocument
from tenants.models import Tenant

class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['tenant', 'category', 'title', 'file', 'description']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tenant'].queryset = Tenant.objects.filter(is_active=True)
        self.fields['category'].queryset = DocumentCategory.objects.all()

class LeaseDocumentForm(forms.ModelForm):
    class Meta:
        model = LeaseDocument
        fields = ['tenant', 'category', 'title', 'file', 'description',
                 'start_date', 'end_date', 'monthly_rent', 'deposit_amount', 
                 'terms', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'terms': forms.Textarea(attrs={'rows': 5}),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tenant'].queryset = Tenant.objects.filter(is_active=True)
        self.fields['category'].queryset = DocumentCategory.objects.all()