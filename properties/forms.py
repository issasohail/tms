# forms.py
from crispy_forms.layout import Layout, Div
from .models import Unit
from django import forms
from .models import Property, Unit

# NEW imports
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Div
from tenants.models import TenantInterestType


def default_interest_type_for_property(property_obj):
    property_name = (property_obj.property_name if property_obj else "").lower()
    code = (
        "single_room_attached_bath_kitchen"
        if "f56" in property_name and "basement" in property_name
        else "two_room_flat"
    )
    return TenantInterestType.objects.filter(code=code, is_active=True).first()


class PropertyForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = '__all__'
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'description': forms.Textarea(attrs={'rows': 3}),
            'bank_account_details': forms.Textarea(attrs={'rows': 3}),
        }


# forms.py


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = '__all__'
        widgets = {
            # your model uses "comments", not "notes"
            'comments': forms.Textarea(attrs={'rows': 3}),
            'bank_account_details': forms.Textarea(attrs={
                'rows': 3,
                'data-unit-bank-account': '1',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Add Bootstrap classes (checkbox vs everything else)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': 'form-check-input'})
            else:
                # don't fight crispy; just ensure controls look fine
                field.widget.attrs.setdefault('class', 'form-control')
        self.fields['use_property_bank_account'].widget.attrs.update({
            'data-use-property-bank-account': '1',
        })

        # quick visual proof you're on the right file
        self.fields['unit_number'].label = 'Unit #'
        self.fields['interest_type'].label = 'Building Type'
        self.fields['status'].label = 'Unit State'
        self.fields['status'].help_text = 'Occupancy is calculated from current lease history dates.'
        if (
            not self.is_bound
            and self.instance
            and self.instance.pk
            and not self.instance.interest_type_id
            and self.instance.property_id
        ):
            default_interest_type = default_interest_type_for_property(self.instance.property)
            if default_interest_type:
                self.fields['interest_type'].initial = default_interest_type.pk

        self.helper = FormHelper()
        self.helper.form_tag = False  # <form> tag lives in the template

        # 1/1 on xs, 1/2 on sm, 1/4 on lg+
        col2_4 = 'col-12 col-sm-6 col-lg-3'

        self.helper.layout = Layout(
            # First line: Property + Unit
            Div(
                Div('property',    css_class='col-12 col-md-6'),
                Div('unit_number', css_class='col-12 col-md-6'),
                css_class='row g-3'
            ),
            Div(
                Div('interest_type', css_class='col-12 col-md-6'),
                css_class='row g-3'
            ),

            # Then 4 per row (lg) / 2 per row (sm)
            Div(
                Div('electric_meter_num',  css_class=col2_4),
                Div('is_smart_meter',      css_class=col2_4),
                Div('gas_meter_num',       css_class=col2_4),
                Div('society_maintenance', css_class=col2_4),
                css_class='row g-3'
            ),
            Div(
                Div('water_charges',     css_class=col2_4),
                Div('monthly_rent',      css_class=col2_4),
                Div('security_requires', css_class=col2_4),
                Div('ceiling_fan',       css_class=col2_4),
                css_class='row g-3'
            ),
            Div(
                Div('exhaust_fan',    css_class=col2_4),
                Div('ceiling_lights', css_class=col2_4),
                Div('stove',          css_class=col2_4),
                Div('keys',           css_class=col2_4),
                css_class='row g-3'
            ),
            Div(
                Div('wardrobes', css_class=col2_4),
                Div('bedrooms',  css_class=col2_4),
                Div('bathrooms', css_class=col2_4),
                Div('kitchens',  css_class=col2_4),
                css_class='row g-3'
            ),
            Div(
                Div('hall',           css_class=col2_4),
                Div('square_footage', css_class=col2_4),
                Div('status',         css_class=col2_4),
                # spacer to keep 4-up
                Div('', css_class='d-none d-lg-block col-lg-3'),
                css_class='row g-3'
            ),

            # Full-width text fields (keep these readable)
            Div(
                Div('use_property_bank_account', css_class='col-12 col-md-4'),
                Div('bank_account_details', css_class='col-12 col-md-8'),
                css_class='row g-3'
            ),
            Div(Div('paint_condition', css_class='col-12'), css_class='row g-3'),
            Div(Div('comments',        css_class='col-12'), css_class='row g-3'),
        )

    @property
    def property_bank_account_map(self):
        return {
            str(prop.pk): prop.bank_account_details or ""
            for prop in Property.objects.order_by("property_name")
        }

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get('interest_type') and cleaned_data.get('property'):
            cleaned_data['interest_type'] = default_interest_type_for_property(cleaned_data['property'])
        return cleaned_data
