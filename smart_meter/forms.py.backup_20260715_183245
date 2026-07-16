from .models import MeterReading, Meter  # <-- add Meter
from .models import MeterReading
from .models import MeterReading  # adjust to your actual model path
from django.utils import timezone
from smart_meter.models import Meter, MeterPrepaidSettings
from django.contrib import messages
from django.shortcuts import render
from decimal import Decimal
from .models import MeterBalance
from django import forms

from django import forms
from smart_meter.models import MeterSettings
from .models import Meter, MeterReading
from django import forms
from .models import Meter, LiveReading, MeterReading, Tariff, Bill  # adjust as needed
# smart_meter/forms.py
from django import forms
from .models import Meter, UnknownMeter
from properties.models import Unit  # adjust if your Unit lives elsewhere
# smart_meter/forms.py
from decimal import Decimal
from django import forms
from leases.models import Lease
from leases.models import LeaseUnitOccupancy


from .models import (
    MeterSettings, Meter, LiveReading, MeterReading, Tariff, Bill, MeterBalance,
    MeterInstallation,
)


class AssignMeterForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["unit_number", "electric_meter_num", "is_smart_meter"]


class RechargeForm(forms.Form):
    amount = forms.DecimalField(
        label="Recharge Amount", min_value=Decimal("1.00"))


class MeterSettingsForm(forms.ModelForm):
    class Meta:
        model = MeterSettings
        fields = "__all__"


class MeterForm(forms.ModelForm):
    class Meta:
        model = Meter
        fields = "__all__"
        widgets = {
            'meter_number': forms.TextInput(attrs={'class': 'form-control'}),
            'unit': forms.Select(attrs={'class': 'form-select'}),
            'billing_mode': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'balance': forms.NumberInput(attrs={'class': 'form-control'}),
            'credit_balance': forms.NumberInput(attrs={'class': 'form-control'}),
        }


class MeterReadingForm(forms.ModelForm):
    """
    Optional admin/UX form to view or add historical snapshots.
    """
    class Meta:
        model = MeterReading
        fields = [
            "meter", "ts",
            "total_energy", "peak_total_energy", "valley_total_consumption", "flat_total_consumption",
            "total_power", "pf_total",
            "voltage_a", "voltage_b", "voltage_c",
            "current_a", "current_b", "current_c",
        ]


class UnknownToMeterForm(forms.ModelForm):
    unit = forms.ModelChoiceField(
        queryset=Unit.objects.all(),
        required=True,

    )

    class Meta:
        model = Meter
        fields = "__all__"
        widgets = {
            "meter_number": forms.TextInput(attrs={"readonly": "readonly"}),
        }

# --- Switch ON/OFF Lab (meter-number based) ---


class SwitchLabForm(forms.Form):
    meter_number = forms.CharField(
        label="Meter number (hex)",
        help_text="Even-length HEX, e.g. 250619510017",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "250619510017"})
    )
    action = forms.ChoiceField(
        choices=[("on", "Turn ON (0x1C)"), ("off", "Turn OFF (0x1A)")],
        widget=forms.RadioSelect
    )
    preview_only = forms.BooleanField(
        required=False,
        initial=True,
        label="Preview only (don’t send)",
        help_text="Uncheck to also send via listener"
    )

    def clean_meter_number(self):
        s = (self.cleaned_data["meter_number"] or "").replace(" ", "").upper()
        import re
        if not re.fullmatch(r"[0-9A-F]+", s) or len(s) % 2:
            raise forms.ValidationError(
                "Enter an even-length HEX string (0-9, A-F).")
        return s


# smart_meter/forms.py


class MeterPrepaidSettingsForm(forms.ModelForm):
    meter = forms.ModelChoiceField(
        queryset=Meter.objects.order_by("meter_number"),
        label="Meter",
        widget=forms.Select(attrs={"class": "form-select"})
    )

    class Meta:
        model = MeterPrepaidSettings
        fields = [
            "meter",
            "rate1_price_1", "rate2_price_1",
            "alarm_amount_1", "alarm_amount_2", "overdraft_limit",
            "rate_switch_time", "step_switch_time",
            "step1_value_1", "step2_value_1",
        ]
        widgets = {
            "rate1_price_1": forms.NumberInput(attrs={"step": "0.0001"}),
            "rate2_price_1": forms.NumberInput(attrs={"step": "0.0001"}),
            "alarm_amount_1": forms.NumberInput(attrs={"step": "0.01"}),
            "alarm_amount_2": forms.NumberInput(attrs={"step": "0.01"}),
            "overdraft_limit": forms.NumberInput(attrs={"step": "0.01"}),
        }

    def clean_rate_switch_time(self):
        v = self.cleaned_data.get("rate_switch_time") or 0
        if v and len(str(v)) != 10:
            raise forms.ValidationError(
                "Use yymmddhhmm (10 digits), e.g. 2401010000 for 2024-01-01 00:00.")
        return v

    def clean_step_switch_time(self):
        v = self.cleaned_data.get("step_switch_time") or 0
        if v and len(str(v)) != 10:
            raise forms.ValidationError(
                "Use yymmddhhmm (10 digits) or leave 0.")
        return v


# smart_meter/forms.py
# smart_meter/forms.py


class ReadingManualForm(forms.ModelForm):
    class Meta:
        model = MeterReading
        fields = [
            "ts", "meter", "source_ip", "source_port",
            "voltage_a", "current_a", "total_power", "total_energy", "pf_total",
        ]
        widgets = {"ts": forms.DateTimeInput(attrs={"type": "datetime-local"})}

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

        # Default timestamp (trim seconds)
        if not self.instance.pk and not self.fields["ts"].initial:
            now = timezone.localtime().replace(second=0, microsecond=0)
            self.fields["ts"].initial = now.strftime("%Y-%m-%dT%H:%M")

        # Make IP/port optional
        self.fields["source_ip"].required = False
        self.fields["source_port"].required = False

        # ORDER meters by Property name, Unit number, Meter number
        self.fields["meter"].queryset = (
            Meter.objects
            .select_related("unit__property")
            .order_by("unit__property__property_name", "unit__unit_number", "meter_number")
        )

        def meter_label(m):
            if m.unit_id and getattr(m.unit, "property_id", None):
                return f"{m.unit.property.property_name} / Unit {m.unit.unit_number} - Meter {m.meter_number}"
            return f"Uninstalled - Meter {m.meter_number}"

        self.fields["meter"].label_from_instance = meter_label

        # Bootstrap classes
        for name, field in self.fields.items():
            base = field.widget.attrs.get("class", "")
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = f"{base} form-select".strip()
            else:
                field.widget.attrs["class"] = f"{base} form-control".strip()

        # Numeric steps
        for f in ["voltage_a", "current_a", "total_power", "total_energy", "pf_total"]:
            self.fields[f].widget.attrs["step"] = "any"

        # Preselect meter from ?meter=<id> if present
        if self.request and self.request.GET.get("meter"):
            try:
                self.fields["meter"].initial = int(self.request.GET["meter"])
            except (TypeError, ValueError):
                pass

    def clean(self):
        cleaned = super().clean()
        for nonneg in ["voltage_a", "current_a", "total_power", "total_energy"]:
            v = cleaned.get(nonneg)
            if v is not None and v < 0:
                self.add_error(nonneg, "Must be ≥ 0.")
        pf = cleaned.get("pf_total")
        if pf is not None and not (0 <= pf <= 1.0):
            self.add_error("pf_total", "Power factor must be between 0 and 1.")
        return cleaned


class InstallMeterToUnitForm(forms.ModelForm):
    class Meta:
        model = MeterInstallation
        fields = [
            "meter",
            "unit",
            "lease",
            "start_date",
            "start_reading",
            "reason",
            "notes",
        ]
        widgets = {
            "meter": forms.Select(attrs={"class": "form-select"}),
            "unit": forms.Select(attrs={"class": "form-select"}),
            "lease": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "start_reading": forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
            "reason": forms.TextInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, unit=None, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["meter"].queryset = (
            Meter.objects
            .exclude(installations__is_active=True, installations__end_date__isnull=True)
            .order_by("meter_number")
            .distinct()
        )
        self.fields["unit"].queryset = Unit.objects.select_related("property").order_by(
            "property__property_name", "unit_number"
        )
        self.fields["lease"].queryset = Lease.objects.select_related("tenant", "unit").order_by("-start_date")
        self.fields["lease"].required = False
        if unit:
            self.fields["unit"].initial = unit
            self.fields["lease"].queryset = self.fields["lease"].queryset.filter(unit=unit)

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.is_active = True
        obj.end_date = None
        obj.end_reading = None
        obj.installed_by = self.user if getattr(self.user, "is_authenticated", False) else None
        if commit:
            obj.save()
        return obj


class SwitchMeterForm(forms.Form):
    old_installation = forms.ModelChoiceField(
        queryset=MeterInstallation.objects.none(),
        label="Current Meter",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    new_meter = forms.ModelChoiceField(
        queryset=Meter.objects.none(),
        label="New Meter",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    lease = forms.ModelChoiceField(
        queryset=Lease.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    switch_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    old_end_reading = forms.DecimalField(
        max_digits=14,
        decimal_places=3,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )
    new_start_reading = forms.DecimalField(
        max_digits=14,
        decimal_places=3,
        initial=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )
    reason = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}))

    def __init__(self, *args, unit=None, **kwargs):
        self.unit = unit
        super().__init__(*args, **kwargs)
        active = MeterInstallation.objects.filter(
            is_active=True,
            end_date__isnull=True,
        ).select_related("meter", "unit")
        if unit:
            active = active.filter(unit=unit)
        self.fields["old_installation"].queryset = active.order_by("meter__meter_number")
        self.fields["new_meter"].queryset = (
            Meter.objects
            .exclude(installations__is_active=True, installations__end_date__isnull=True)
            .order_by("meter_number")
            .distinct()
        )
        lease_qs = Lease.objects.select_related("tenant", "unit").order_by("-start_date")
        if unit:
            lease_qs = lease_qs.filter(unit=unit)
        self.fields["lease"].queryset = lease_qs

    def clean(self):
        cleaned = super().clean()
        old_installation = cleaned.get("old_installation")
        switch_date = cleaned.get("switch_date")
        if old_installation and switch_date and switch_date < old_installation.start_date:
            self.add_error("switch_date", "Switch date cannot be before the current installation start date.")
        return cleaned


class CloseMeterInstallationForm(forms.Form):
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    end_reading = forms.DecimalField(
        max_digits=14,
        decimal_places=3,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}))

    def __init__(self, *args, installation=None, **kwargs):
        self.installation = installation
        super().__init__(*args, **kwargs)

    def clean_end_date(self):
        end_date = self.cleaned_data["end_date"]
        if self.installation and end_date < self.installation.start_date:
            raise forms.ValidationError("End date cannot be before the installation start date.")
        return end_date


class MoveLeaseUnitForm(forms.Form):
    new_unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        label="New Unit / Room",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    move_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}))

    def __init__(self, *args, lease=None, **kwargs):
        self.lease = lease
        super().__init__(*args, **kwargs)
        self.fields["new_unit"].queryset = Unit.objects.select_related("property").order_by(
            "property__property_name", "unit_number"
        )

    def clean(self):
        cleaned = super().clean()
        new_unit = cleaned.get("new_unit")
        move_date = cleaned.get("move_date")
        current_unit_id = getattr(getattr(self.lease, "current_unit", None), "pk", None)
        if self.lease and new_unit and current_unit_id == new_unit.pk:
            self.add_error("new_unit", "The lease is already assigned to this unit.")
        if self.lease and move_date and move_date < self.lease.start_date:
            self.add_error("move_date", "Move date cannot be before the lease start date.")
        return cleaned
