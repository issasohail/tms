
from django import forms
from properties.models import Unit
from .models import UtilityBillAccount

class UtilityBillAccountForm(forms.ModelForm):
    class Meta:
        model = UtilityBillAccount
        fields = ("provider","unit","description","consumer_number","ptcl_account_id","ptcl_phone","is_active")
        widgets = {
            "provider": forms.HiddenInput(),
            "unit": forms.Select(attrs={"class":"form-select"}),
            "description": forms.TextInput(attrs={"class":"form-control"}),
            "consumer_number": forms.TextInput(attrs={"class":"form-control"}),
            "ptcl_account_id": forms.TextInput(attrs={"class":"form-control"}),
            "ptcl_phone": forms.TextInput(attrs={"class":"form-control","placeholder":"0515921709"}),
            "is_active": forms.CheckboxInput(attrs={"class":"form-check-input"}),
        }

    def __init__(self,*args,provider=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["unit"].queryset = Unit.objects.select_related("property").order_by("property__property_name","unit_number")
        self.fields["unit"].required=False
        if provider: self.fields["provider"].initial=provider
