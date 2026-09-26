# forms.py
# NEW imports
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Div, Layout
from django import forms
from django.db.models import Q

from core.utils.text import add_auto_titlecase_class
from punjab_estamp.models import PunjabEStampDistrict, PunjabEStampTehsil
from tenants.models import Tenant

from .models import BuildingType, Property, PropertyBankAccount, Unit


class TenantIdentityChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, tenant):
        cnic = tenant.cnic or "No CNIC"
        return f"{tenant.get_full_name()} — {cnic}"


def default_building_type_for_property(property_obj):
    property_name = (property_obj.property_name if property_obj else "").lower()
    preferred_codes = (
        ("single_room_attached_bath_kitchen", "single_room")
        if "f56" in property_name and "basement" in property_name
        else ("two_room_flat",)
    )
    building_type = (
        BuildingType.objects.filter(code__in=preferred_codes, is_active=True)
        .order_by("sort_order", "name")
        .first()
    )
    return (
        building_type
        or BuildingType.objects.filter(is_active=True)
        .order_by("sort_order", "name")
        .first()
    )


class PropertyForm(forms.ModelForm):
    owner_tenant = TenantIdentityChoiceField(
        queryset=Tenant.objects.none(), required=False, label="Owner Tenant"
    )
    caretaker_tenant = TenantIdentityChoiceField(
        queryset=Tenant.objects.none(), required=False, label="Caretaker Tenant"
    )

    class Meta:
        model = Property
        fields = "__all__"
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "bank_account_details": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tenant_queryset = Tenant.objects.filter(is_active=True)
        selected_tenant_ids = {
            tenant_id
            for tenant_id in (
                getattr(self.instance, "owner_tenant_id", None),
                getattr(self.instance, "caretaker_tenant_id", None),
            )
            if tenant_id
        }
        if selected_tenant_ids:
            tenant_queryset = Tenant.objects.filter(
                Q(is_active=True) | Q(pk__in=selected_tenant_ids)
            )
        tenant_queryset = tenant_queryset.order_by("first_name", "last_name", "pk")
        for field_name in ("owner_tenant", "caretaker_tenant"):
            self.fields[field_name].queryset = tenant_queryset
            self.fields[field_name].empty_label = "Search and select Tenant"
            self.fields[field_name].widget.attrs.update(
                {
                    "class": "form-select form-select-sm tenant-identity-select",
                    "data-tenant-role": field_name.removesuffix("_tenant"),
                }
            )
        self.fields["owner_name"].required = False
        self.fields["owner_cnic"].required = False
        selected_district_id = None
        if self.is_bound:
            selected_district_id = self.data.get(self.add_prefix("zila"))
        elif self.instance and self.instance.pk:
            selected_district_id = self.instance.zila_id
        else:
            selected_district_id = self.initial.get("zila")

        district_queryset = PunjabEStampDistrict.objects.filter(active=True)
        if self.instance and self.instance.pk and self.instance.zila_id:
            district_queryset = PunjabEStampDistrict.objects.filter(
                Q(active=True) | Q(pk=self.instance.zila_id)
            )
        self.fields["zila"].queryset = district_queryset.order_by("sort_order", "name")
        self.fields["zila"].label = "Zila / District"
        self.fields["zila"].required = True
        self.fields["zila"].empty_label = "Select Zila / District"
        self.fields["zila"].widget.attrs.update(
            {"class": "form-select form-select-sm", "data-punjab-district": "1"}
        )

        if (
            not self.is_bound
            and not (self.instance and self.instance.pk)
            and not selected_district_id
        ):
            rawalpindi = district_queryset.filter(
                portal_value="18", active=True
            ).first()
            if rawalpindi:
                selected_district_id = rawalpindi.pk
                self.initial.setdefault("zila", rawalpindi.pk)

        tehsil_queryset = PunjabEStampTehsil.objects.none()
        if selected_district_id:
            try:
                tehsil_queryset = PunjabEStampTehsil.objects.filter(
                    district_id=int(selected_district_id), active=True
                )
            except (TypeError, ValueError):
                pass
        if self.instance and self.instance.pk and self.instance.tehsil_id:
            tehsil_queryset = PunjabEStampTehsil.objects.filter(
                Q(active=True, district_id=selected_district_id)
                | Q(pk=self.instance.tehsil_id)
            )
        self.fields["tehsil"].queryset = tehsil_queryset.order_by("sort_order", "name")
        self.fields["tehsil"].required = True
        self.fields["tehsil"].empty_label = "Select Tehsil"
        self.fields["tehsil"].widget.attrs.update(
            {"class": "form-select form-select-sm", "data-punjab-tehsil": "1"}
        )

        if not self.is_bound and not (self.instance and self.instance.pk):
            rawalpindi_tehsil = tehsil_queryset.filter(portal_value="72").first()
            if rawalpindi_tehsil:
                self.initial.setdefault("tehsil", rawalpindi_tehsil.pk)

        self.fields["bank_account_details"].label = "Legacy Bank Account Fallback"
        self.fields[
            "bank_account_details"
        ].help_text = "Kept for compatibility. Manage multiple structured accounts on the property detail page."
        if "electricity_unit_rate" in self.fields:
            self.fields["electricity_unit_rate"].label = "Property rate override"
            self.fields["electricity_unit_rate"].help_text = (
                "Optional property-level electricity rate override. "
                "Leave blank to use the global electricity rate."
            )
            self.fields["electricity_unit_rate"].widget.attrs.update(
                {
                    "class": "form-control form-control-sm",
                    "step": "0.0001",
                    "min": "0",
                }
            )
        add_auto_titlecase_class(
            self.fields,
            {
                "property_name",
                "owner_name",
                "owner_father_name",
                "caretaker_name",
                "caretaker_father_name",
                "property_city",
                "property_state",
            },
        )

    def clean(self):
        cleaned = super().clean()
        for role in ("owner", "caretaker"):
            tenant = cleaned.get(f"{role}_tenant")
            if not tenant:
                continue
            relation = tenant.relation.name if tenant.relation_id else tenant.relation_legacy
            cleaned[f"{role}_prefix"] = tenant.prefix
            cleaned[f"{role}_name"] = tenant.first_name
            cleaned[f"{role}_father_name"] = tenant.last_name
            cleaned[f"{role}_cnic"] = tenant.cnic
            cleaned[f"{role}_phone"] = tenant.phone
            cleaned[f"{role}_address"] = tenant.permanent_address or tenant.address
            cleaned["relation" if role == "owner" else "caretaker_relation"] = relation
        if not cleaned.get("owner_tenant"):
            if not cleaned.get("owner_name"):
                self.add_error("owner_name", "Select an Owner Tenant or enter the legacy owner name.")
            if not cleaned.get("owner_cnic"):
                self.add_error("owner_cnic", "Select an Owner Tenant or enter the legacy owner CNIC.")
        district = cleaned.get("zila")
        tehsil = cleaned.get("tehsil")
        if district and tehsil and tehsil.district_id != district.pk:
            self.add_error(
                "tehsil",
                "The selected Tehsil does not belong to the selected Zila / District.",
            )
        return cleaned


# forms.py


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = "__all__"
        exclude = ("interest_type",)
        widgets = {
            # your model uses "comments", not "notes"
            "comments": forms.Textarea(attrs={"rows": 3}),
            "bank_account_details": forms.Textarea(
                attrs={
                    "rows": 3,
                    "data-unit-bank-account": "1",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Add Bootstrap classes (checkbox vs everything else)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({"class": "form-check-input"})
            else:
                # don't fight crispy; just ensure controls look fine
                field.widget.attrs.setdefault("class", "form-control")
        self.fields["use_property_bank_account"].widget.attrs.update(
            {
                "data-use-property-bank-account": "1",
            }
        )

        # quick visual proof you're on the right file
        self.fields["unit_number"].label = "Unit #"
        self.fields["building_type"].label = "Building Type"
        self.fields["security_requires"].label = "Security Requirement Text"
        self.fields["security_deposit_amount"].label = "Security Deposit Amount"
        if "electricity_unit_rate" in self.fields:
            self.fields["electricity_unit_rate"].label = "Unit rate override"
            self.fields["electricity_unit_rate"].help_text = (
                "Optional unit-level electricity rate override. "
                "Leave blank to use the property/global electricity rate."
            )
            self.fields["electricity_unit_rate"].widget.attrs.update(
                {
                    "class": "form-control form-control-sm",
                    "step": "0.0001",
                    "min": "0",
                }
            )
        self.fields["building_type"].queryset = BuildingType.objects.filter(
            is_active=True
        ).order_by("sort_order", "name")
        self.fields["status"].label = "Unit State"
        self.fields[
            "status"
        ].help_text = "Occupancy is calculated from current lease history dates."
        if "show_publicly" in self.fields:
            self.fields["show_publicly"].label = "Show Publicly"
            self.fields[
                "show_publicly"
            ].help_text = "Show this unit in WhatsApp/public vacant unit list."
        add_auto_titlecase_class(self.fields)
        if (
            not self.is_bound
            and self.instance
            and self.instance.pk
            and not self.instance.building_type_id
            and self.instance.property_id
        ):
            default_building_type = default_building_type_for_property(
                self.instance.property
            )
            if default_building_type:
                self.fields["building_type"].initial = default_building_type.pk
        self.helper = FormHelper()
        self.helper.form_tag = False  # <form> tag lives in the template

        # 1/1 on xs, 1/2 on sm, 1/4 on lg+
        col2_4 = "col-12 col-sm-6 col-lg-3"

        self.helper.layout = Layout(
            # First line: Property + Unit
            Div(
                Div("property", css_class="col-12 col-md-6"),
                Div("unit_number", css_class="col-12 col-md-6"),
                css_class="row g-3",
            ),
            Div(
                Div("building_type", css_class="col-12 col-md-6"),
                css_class="row g-3",
            ),
            # Then 4 per row (lg) / 2 per row (sm)
            Div(
                Div("electric_meter_num", css_class=col2_4),
                Div("is_smart_meter", css_class=col2_4),
                Div("gas_meter_num", css_class=col2_4),
                Div("society_maintenance", css_class=col2_4),
                css_class="row g-3",
            ),
            Div(
                Div("water_charges", css_class=col2_4),
                Div("internet_charges", css_class=col2_4),
                Div("monthly_rent", css_class=col2_4),
                Div("security_requires", css_class=col2_4),
                css_class="row g-3",
            ),
            Div(
                Div("security_deposit_amount", css_class=col2_4),
                Div("ceiling_fan", css_class=col2_4),
                css_class="row g-3",
            ),
            Div(
                Div("inspection_incomplete_charge", css_class=col2_4),
                Div("key_card_not_returned_charge", css_class=col2_4),
                css_class="row g-3",
            ),
            Div(
                Div("exhaust_fan", css_class=col2_4),
                Div("ceiling_lights", css_class=col2_4),
                Div("stove", css_class=col2_4),
                Div("keys", css_class=col2_4),
                css_class="row g-3",
            ),
            Div(
                Div("wardrobes", css_class=col2_4),
                Div("bedrooms", css_class=col2_4),
                Div("bathrooms", css_class=col2_4),
                Div("kitchens", css_class=col2_4),
                css_class="row g-3",
            ),
            Div(
                Div("hall", css_class=col2_4),
                Div("square_footage", css_class=col2_4),
                Div("status", css_class=col2_4),
                Div("show_publicly", css_class=col2_4),
                css_class="row g-3",
            ),
            # Full-width text fields (keep these readable)
            Div(
                Div("use_property_bank_account", css_class="col-12 col-md-4"),
                Div("bank_account_details", css_class="col-12 col-md-8"),
                css_class="row g-3",
            ),
            Div(Div("paint_condition", css_class="col-12"), css_class="row g-3"),
            Div(Div("comments", css_class="col-12"), css_class="row g-3"),
        )

    @property
    def property_bank_account_map(self):
        return {
            str(prop.pk): prop.bank_account_details or ""
            for prop in Property.objects.order_by("property_name")
        }

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("building_type") and cleaned_data.get("property"):
            cleaned_data["building_type"] = default_building_type_for_property(
                cleaned_data["property"]
            )
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        building_type = self.cleaned_data.get("building_type")
        instance.interest_type = (
            getattr(building_type, "lead_interest_type", None)
            if building_type
            else None
        )
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class PropertyBankAccountForm(forms.ModelForm):
    class Meta:
        model = PropertyBankAccount
        fields = [
            "account_label",
            "bank_name",
            "account_title",
            "account_number",
            "iban",
            "branch",
            "additional_details",
            "is_default",
            "is_active",
            "sort_order",
        ]
        widgets = {
            "additional_details": forms.Textarea(attrs={"rows": 2}),
            "sort_order": forms.NumberInput(attrs={"min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                field.widget.attrs.setdefault("class", "form-control form-control-sm")
