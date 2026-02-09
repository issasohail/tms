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


from .models import (
    MeterSettings, Meter, LiveReading, MeterReading, Tariff, Bill, MeterBalance
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

        # Friendly labels: "Property / Unit X — Meter Y"
        self.fields["meter"].label_from_instance = (
            lambda m: f"{m.unit.property.property_name} / Unit {m.unit.unit_number} — Meter {m.meter_number}"
        )

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
