import json
from decimal import Decimal

from django import forms

from smart_meter.models import Meter


class TariffConfigurationForm(forms.Form):
    mode = forms.ChoiceField(
        choices=(("flat", "Flat Rate"), ("time_of_use", "Time-of-Use Rate")),
        widget=forms.RadioSelect,
    )
    flat_rate = forms.DecimalField(
        required=False, min_value=Decimal("0"), max_value=Decimal("9999.9999"),
        decimal_places=4, max_digits=8,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.0001"}),
    )
    active_rate_count = forms.TypedChoiceField(
        choices=((1, "1"), (2, "2"), (3, "3"), (4, "4")),
        coerce=int, initial=1, widget=forms.Select(attrs={"class": "form-select"}),
    )
    schedule_json = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, meter=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.meter = meter
        defaults = ("Valley", "Flat", "Peak", "Shoulder")
        for index in range(1, 5):
            self.fields[f"rate_{index}_label"] = forms.CharField(
                required=False, max_length=64, initial=defaults[index - 1],
                widget=forms.TextInput(attrs={"class": "form-control"}),
            )
            self.fields[f"rate_{index}_price"] = forms.DecimalField(
                required=False, min_value=Decimal("0"), max_value=Decimal("9999.9999"),
                decimal_places=4, max_digits=8,
                widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.0001"}),
            )
        if meter and meter.tariff_capability == Meter.TARIFF_CAPABILITY_SINGLE:
            self.fields["mode"].initial = "flat"

    @staticmethod
    def _minute(value):
        try:
            hour, minute = value.split(":")
            hour, minute = int(hour), int(minute)
        except (AttributeError, TypeError, ValueError) as exc:
            raise forms.ValidationError("Schedule times must use HH:MM format.") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise forms.ValidationError("Schedule contains an invalid time.")
        return hour * 60 + minute

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("mode")
        if self.meter and self.meter.tariff_capability == Meter.TARIFF_CAPABILITY_UNKNOWN:
            raise forms.ValidationError("Confirm the tariff capability before configuration.")
        if self.meter and self.meter.tariff_capability == Meter.TARIFF_CAPABILITY_SINGLE:
            mode = cleaned["mode"] = "flat"
        if mode == "flat":
            if cleaned.get("flat_rate") is None:
                self.add_error("flat_rate", "Enter the flat unit rate.")
            cleaned["active_rate_count"] = 1
            cleaned["prices"] = [cleaned.get("flat_rate")]
            cleaned["labels"] = ["Flat", "Rate 2", "Rate 3", "Rate 4"]
            cleaned["schedule"] = []
            return cleaned

        count = cleaned.get("active_rate_count") or 0
        if count not in {2, 3, 4}:
            self.add_error("active_rate_count", "Time-of-use requires 2, 3, or 4 active rates.")
        labels, prices = [], []
        for index in range(1, 5):
            label, price = cleaned.get(f"rate_{index}_label"), cleaned.get(f"rate_{index}_price")
            if index <= count:
                if not label:
                    self.add_error(f"rate_{index}_label", "Each active rate requires a label.")
                if price is None:
                    self.add_error(f"rate_{index}_price", "Each active rate requires a price.")
                labels.append(label or "")
                prices.append(price)
        try:
            rows = json.loads(cleaned.get("schedule_json") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            self.add_error("schedule_json", "Schedule data is invalid.")
            rows = []
        if not isinstance(rows, list) or not rows:
            self.add_error("schedule_json", "Add schedule periods covering all 24 hours.")
            rows = []
        coverage = [0] * 1440
        normalized = []
        for row in rows:
            try:
                start_text, end_text = row["start"], row["end"]
                start, end = self._minute(start_text), self._minute(end_text)
                rate = int(row["rate"])
                if not 1 <= rate <= count:
                    raise forms.ValidationError("A schedule period references an inactive rate.")
                if start == end:
                    raise forms.ValidationError("A schedule period cannot have identical start and end times.")
                ranges = [(start, end)] if start < end else [(start, 1440), (0, end)]
                for lower, upper in ranges:
                    for minute in range(lower, upper):
                        coverage[minute] += 1
                normalized.append({"start": start_text, "end": end_text, "rate": rate})
            except (KeyError, TypeError, ValueError, forms.ValidationError) as exc:
                self.add_error("schedule_json", str(exc))
        if rows and any(value > 1 for value in coverage):
            self.add_error("schedule_json", "Schedule periods overlap.")
        if rows and any(value == 0 for value in coverage):
            self.add_error("schedule_json", "Schedule must cover the full 24-hour day without gaps.")
        cleaned.update(labels=labels, prices=prices, schedule=normalized)
        return cleaned


class BulkTariffForm(forms.Form):
    price = forms.DecimalField(
        min_value=Decimal("0"), max_value=Decimal("9999.9999"),
        decimal_places=4, max_digits=8,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.0001"}),
    )
    meter_ids = forms.CharField(widget=forms.HiddenInput)

    def clean_meter_ids(self):
        values = []
        for value in self.cleaned_data["meter_ids"].split(","):
            if value.strip().isdigit():
                values.append(int(value.strip()))
        if not values:
            raise forms.ValidationError("Select at least one meter.")
        return list(dict.fromkeys(values))
