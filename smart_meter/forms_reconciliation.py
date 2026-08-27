from django import forms
from django.db.models import Q

from smart_meter.models import (
    EnergySystemMeterAssignment,
    InverterPeriodStatement,
    Meter,
    UtilityBillCycle,
    UtilityBillPayment,
)


BOOTSTRAP_INPUT = {"class": "form-control"}


class EnergySystemReassignmentForm(forms.Form):
    role = forms.ChoiceField(
        choices=EnergySystemMeterAssignment.ROLE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    meter = forms.ModelChoiceField(
        queryset=Meter.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    effective_date = forms.DateField(widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}))

    def __init__(self, *args, energy_system=None, **kwargs):
        super().__init__(*args, **kwargs)
        role = (self.data.get("role") if self.is_bound else self.initial.get("role")) or ""
        point = {
            EnergySystemMeterAssignment.ROLE_GRID_INTERFACE: Meter.MEASUREMENT_POINT_GRID_INTERFACE,
            EnergySystemMeterAssignment.ROLE_OUTPUT: Meter.MEASUREMENT_POINT_INVERTER_OUTPUT,
        }.get(role)
        queryset = Meter.objects.filter(meter_role=Meter.METER_ROLE_CHECK, is_active=True)
        if point:
            queryset = queryset.filter(measurement_point=point)
        if energy_system:
            queryset = queryset.filter(
                Q(energy_system_assignments__end_date__isnull=True, energy_system_assignments__energy_system=energy_system)
                | Q(energy_system_assignments__isnull=True)
            ).distinct()
        self.fields["meter"].queryset = queryset.order_by("meter_number")


class UtilityBillCycleForm(forms.ModelForm):
    MAX_UPLOAD_BYTES = 10 * 1024 * 1024

    class Meta:
        model = UtilityBillCycle
        fields = [
            "utility_connection", "bill_month", "period_start", "period_end",
            "reading_date", "issue_date", "due_date",
            "import_off_peak_previous", "import_off_peak_current", "import_off_peak_kwh",
            "import_peak_previous", "import_peak_current", "import_peak_kwh",
            "export_off_peak_previous", "export_off_peak_current", "export_off_peak_kwh",
            "export_peak_previous", "export_peak_current", "export_peak_kwh",
            "total_electricity_charges", "taxes", "current_bill", "arrears",
            "total_fpa", "grand_total", "attachment",
        ]
        widgets = {
            "utility_connection": forms.Select(attrs={"class": "form-select"}),
            "bill_month": forms.TextInput(attrs=BOOTSTRAP_INPUT),
            "period_start": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "period_end": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "reading_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "issue_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "due_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.Select, forms.ClearableFileInput, forms.FileInput)):
                field.widget.attrs.setdefault("class", "form-control")

    def clean_attachment(self):
        upload = self.cleaned_data.get("attachment")
        if not upload or not hasattr(upload, "read"):
            return upload
        if upload.size > self.MAX_UPLOAD_BYTES:
            raise forms.ValidationError("PDF files may not exceed 10 MB.")
        header = upload.read(5)
        upload.seek(0)
        if header != b"%PDF-":
            raise forms.ValidationError("The uploaded content is not a PDF file.")
        return upload

    def clean(self):
        cleaned = super().clean()
        connection = cleaned.get("utility_connection")
        bill_month = (cleaned.get("bill_month") or "").strip()
        if connection and bill_month:
            duplicate = UtilityBillCycle.objects.filter(
                utility_connection=connection,
                bill_month__iexact=bill_month,
            )
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                self.add_error("bill_month", "A bill for this consumer and bill month already exists; review it before adding another.")
        return cleaned


class InverterPeriodStatementForm(forms.ModelForm):
    class Meta:
        model = InverterPeriodStatement
        fields = [
            "period_start", "period_end", "pv_reading_start_kwh", "pv_reading_end_kwh",
            "start_screenshot", "end_screenshot", "notes",
        ]
        widgets = {
            "period_start": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "period_end": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "pv_reading_start_kwh": forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
            "pv_reading_end_kwh": forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class UtilityBillPaymentForm(forms.ModelForm):
    class Meta:
        model = UtilityBillPayment
        fields = ["amount", "paid_at", "reference", "proof", "notes"]
        widgets = {
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
            "paid_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "reference": forms.TextInput(attrs=BOOTSTRAP_INPUT),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paid_at"].input_formats = ["%Y-%m-%dT%H:%M"]
