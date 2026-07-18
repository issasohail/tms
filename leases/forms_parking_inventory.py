from django import forms

from leases.models import LeaseVehicleType
from leases.models_parking_inventory import LeaseParkingAllocation, ParkingPolicy, ParkingSpace


class ParkingPolicyForm(forms.ModelForm):
    class Meta:
        model = ParkingPolicy
        fields = ["enabled", "monthly_rate", "unauthorized_parking_penalty"]
        widgets = {
            "enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "monthly_rate": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "unauthorized_parking_penalty": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
        }


class ParkingSpaceForm(forms.ModelForm):
    class Meta:
        model = ParkingSpace
        fields = ["label", "vehicle_type", "monthly_rate_override", "is_active", "notes"]
        widgets = {
            "label": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "vehicle_type": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "monthly_rate_override": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vehicle_type"].queryset = LeaseVehicleType.objects.filter(is_active=True)


class LeaseParkingAllocationForm(forms.ModelForm):
    class Meta:
        model = LeaseParkingAllocation
        fields = ["parking_space", "vehicle", "agreed_monthly_rate", "start_date", "notes"]
        widgets = {
            "parking_space": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "vehicle": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "agreed_monthly_rate": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "start_date": forms.DateInput(attrs={"class": "form-control form-control-sm", "type": "date"}),
            "notes": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
        }

    def __init__(self, *args, lease=None, **kwargs):
        super().__init__(*args, **kwargs)
        if lease is not None:
            occupied = LeaseParkingAllocation.objects.filter(
                is_active=True, end_date__isnull=True
            ).values_list("parking_space_id", flat=True)
            self.fields["parking_space"].queryset = lease.unit.property.parking_spaces.filter(
                is_active=True
            ).exclude(id__in=occupied)
            self.fields["vehicle"].queryset = lease.vehicles.filter(is_active=True)
