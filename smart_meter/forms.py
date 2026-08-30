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
from django.db.models import Count, Q

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
    MeterInstallation, MeterCheckGroup, MeterCheckGroupMembership,
    MeterCreditAccount,
)


def _ordered_units():
    return Unit.objects.select_related("property").order_by(
        "property__property_name", "unit_number", "pk"
    )


def _unit_choice_label(unit):
    return f"{unit.property.property_name} / Unit {unit.unit_number}"


def _meter_choice_label(meter):
    annotated_count = getattr(meter, "active_unit_meter_count", None)
    if annotated_count is not None:
        meter._active_unit_meter_count = annotated_count
    property_name = getattr(getattr(meter.unit, "property", None), "property_name", "Unassigned")
    return f"Meter {meter.meter_number} — {property_name} / {meter.display_location_name}"


def _with_active_unit_meter_count(queryset):
    return queryset.annotate(
        active_unit_meter_count=Count(
            "unit__current_meters",
            filter=Q(unit__current_meters__is_active=True),
            distinct=True,
        )
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
    reading_profile = forms.ChoiceField(
        choices=Meter.READING_PROFILE_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    replacement_check_meter = forms.ModelChoiceField(
        queryset=Meter.objects.none(),
        required=False,
        label="Replacement Audit meter",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Required when changing an Audit meter to Billing while it owns a Check Group.",
    )

    class Meta:
        model = Meter
        fields = "__all__"
        widgets = {
            'meter_number': forms.TextInput(attrs={'class': 'form-control'}),
            'unit': forms.Select(attrs={'class': 'form-select'}),
            'billing_mode': forms.Select(attrs={'class': 'form-select'}),
            'meter_role': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'balance': forms.NumberInput(attrs={'class': 'form-control'}),
            'credit_balance': forms.NumberInput(attrs={'class': 'form-control'}),
        }


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_meter_role = self.instance.meter_role if self.instance.pk else None
        self.fields["unit_rate"].label = "Meter rate override"
        self.fields["unit_rate"].widget.attrs.update({"step": "0.0001", "min": "0"})
        self.fields["unit"].queryset = _ordered_units()
        self.fields["unit"].label_from_instance = _unit_choice_label
        replacement_qs = _with_active_unit_meter_count(Meter.objects.filter(
            meter_role=Meter.METER_ROLE_CHECK,
            is_active=True,
            check_group__isnull=True,
        ).exclude(pk=self.instance.pk).select_related(
            "unit", "unit__property"
        )).order_by("unit__property__property_name", "unit__unit_number", "meter_number")
        self.fields["replacement_check_meter"].queryset = replacement_qs
        self.fields["replacement_check_meter"].label_from_instance = _meter_choice_label

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("reading_profile"):
            cleaned["reading_profile"] = (
                self.instance.reading_profile
                or Meter.READING_PROFILE_AUTO
            )
        new_role = cleaned.get("meter_role")
        if (
            self.instance.pk
            and self.original_meter_role == Meter.METER_ROLE_CHECK
            and new_role == Meter.METER_ROLE_BILLING
            and MeterCheckGroup.objects.filter(
                check_meter_id=self.instance.pk,
                is_active=True,
                memberships__is_active=True,
                memberships__end_date__isnull=True,
            ).exists()
            and not cleaned.get("replacement_check_meter")
        ):
            self.add_error(
                "replacement_check_meter",
                "Select another active Audit meter to take over this meter's Check Group.",
            )
        return cleaned


class MeterReadingProfileForm(forms.ModelForm):
    class Meta:
        model = Meter
        fields = ["reading_profile"]
        widgets = {
            "reading_profile": forms.Select(attrs={"class": "form-select form-select-sm"}),
        }


class MeterCheckGroupForm(forms.ModelForm):
    class Meta:
        model = MeterCheckGroup
        fields = ["name", "property", "check_meter", "notes", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "property": forms.Select(attrs={"class": "form-select"}),
            "check_meter": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].required = False
        self.fields["property"].help_text = (
            "Optional reference only. Coverage is determined by the billing meters assigned to this group."
        )
        if self.instance.pk:
            check_meters = Meter.objects.filter(
                Q(pk=self.instance.check_meter_id)
                | Q(meter_role=Meter.METER_ROLE_CHECK, is_active=True)
            ).filter(
                Q(check_group__isnull=True) | Q(check_group=self.instance)
            )
        else:
            check_meters = Meter.objects.filter(
                meter_role=Meter.METER_ROLE_CHECK,
                is_active=True,
                check_group__isnull=True,
            )
        check_meters = check_meters.select_related(
            "unit", "unit__property"
        ).order_by("meter_number")
        self.fields["check_meter"].queryset = _with_active_unit_meter_count(check_meters)
        self.fields["check_meter"].label_from_instance = _meter_choice_label


class MeterCheckGroupMembershipForm(forms.ModelForm):
    class Meta:
        model = MeterCheckGroupMembership
        fields = ["billing_meter", "start_date", "notes"]
        widgets = {
            "billing_meter": forms.Select(attrs={
                "class": "form-select ld-billing-meter-select",
                "data-placeholder": "Search billing meter",
            }),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "notes": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        if group is not None:
            self.instance.group = group
        self.fields["billing_meter"].queryset = _with_active_unit_meter_count(Meter.objects.filter(
            meter_role=Meter.METER_ROLE_BILLING,
            is_active=True,
        ).exclude(
            check_group_memberships__is_active=True,
            check_group_memberships__end_date__isnull=True,
        ).select_related("unit", "unit__property").order_by(
            "unit__property__property_name", "unit__unit_number", "meter_number"
        ).distinct())
        self.fields["billing_meter"].label_from_instance = _meter_choice_label

    def save(self, commit=True):
        membership = super().save(commit=False)
        if self.group is not None:
            membership.group = self.group
        if commit:
            membership.save()
        return membership


class MeterReadingForm(forms.ModelForm):
    """
    Optional admin/UX form to view or add historical snapshots.
    """
    class Meta:
        model = MeterReading
        fields = [
            "meter", "ts",
            "total_energy", "forward_active_energy_kwh", "reverse_active_energy_kwh",
            "peak_total_energy", "valley_total_consumption", "flat_total_consumption",
            "total_power", "power_a", "power_b", "power_c", "pf_total",
            "voltage_a", "voltage_b", "voltage_c",
            "current_a", "current_b", "current_c",
        ]


class UnknownToMeterForm(forms.ModelForm):
    unit = forms.ModelChoiceField(
        queryset=_ordered_units(),
        required=True,
    )

    class Meta:
        model = Meter
        fields = "__all__"
        widgets = {
            "meter_number": forms.TextInput(attrs={"readonly": "readonly"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = _ordered_units()
        self.fields["unit"].label_from_instance = _unit_choice_label

# --- Switch ON/OFF Lab (meter-number based) ---


class SwitchLabForm(forms.Form):
    meter_number = forms.CharField(
        label="Meter number (hex)",
        help_text="Even-length HEX, e.g. 250619510017",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "250619510017"})
    )
    action = forms.ChoiceField(
        choices=[("on", "Turn ON (0x1B)"), ("off", "Turn OFF (0x1A)")],
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
            "alarm_amount_1", "alarm_amount_2", "overdraft_limit",
            "maximum_balance", "reconnect_amount", "max_load", "load_delay",
            "rate1_price_1", "rate1_price_2", "rate1_price_3", "rate1_price_4",
            "rate2_price_1", "rate2_price_2", "rate2_price_3", "rate2_price_4",
            "step_count", "step1_value_1", "step1_value_2", "step1_value_3",
            "step1_price_1", "step1_price_2", "step1_price_3", "step1_price_4",
            "step2_value_1", "step2_value_2", "step2_value_3",
            "step2_price_1", "step2_price_2", "step2_price_3", "step2_price_4",
            "timezone_count", "schedule_count", "time_period_count", "rate_count",
            "voltage_ratio", "current_ratio", "rate_switch_time", "step_switch_time",
            "timezone_switch_time", "schedule_switch_time",
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


class MeterCreditAccountForm(forms.ModelForm):
    automatic_cutoff_and_restore = forms.BooleanField(
        label="Automatic cutoff & restore",
        required=False,
        help_text=(
            "Treats automatic disconnection and reconnection as one account policy. At the cutoff "
            "threshold the meter may be switched OFF; after a qualifying payment reduces exposure "
            "below the reconnect threshold, it may be switched ON again. All server safety gates "
            "must also pass."
        ),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = MeterCreditAccount
        fields = [
            "credit_limit_source",
            "fixed_credit_limit",
            "deposit_percentage",
            "lease_override_limit",
            "warning_threshold_percent",
            "final_warning_threshold_percent",
            "cutoff_threshold_percent",
            "reconnect_threshold_percent",
            "automatic_cutoff_and_restore",
            "staff_approval_required",
        ]
        widgets = {
            "credit_limit_source": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "fixed_credit_limit": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01", "min": "0"}),
            "deposit_percentage": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01", "min": "0"}),
            "lease_override_limit": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01", "min": "0"}),
            "warning_threshold_percent": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01", "min": "0"}),
            "final_warning_threshold_percent": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01", "min": "0"}),
            "cutoff_threshold_percent": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01", "min": "0"}),
            "reconnect_threshold_percent": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01", "min": "0"}),
            "staff_approval_required": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        help_texts = {
            "credit_limit_source": (
                "Chooses how the effective credit limit is calculated. To use the Lease "
                "Override Limit, select Lease-specific manual override from this dropdown."
            ),
            "fixed_credit_limit": (
                "A manually entered currency limit. It is used when the credit limit source is "
                "Fixed monetary limit, or as one side of Lower of fixed and deposit-derived."
            ),
            "deposit_percentage": (
                "Percentage of the lease's electricity security deposit used to calculate a "
                "deposit-derived credit limit. For example, 50% of 20,000 is 10,000."
            ),
            "lease_override_limit": (
                "A special currency limit for this meter and lease. It only becomes effective when "
                "Credit limit source is Lease-specific manual override."
            ),
            "warning_threshold_percent": (
                "Sends or records the first warning when exposure reaches this percentage of the "
                "effective credit limit."
            ),
            "final_warning_threshold_percent": (
                "Sends or records the final warning when exposure reaches this percentage of the "
                "effective credit limit."
            ),
            "cutoff_threshold_percent": (
                "Makes the account eligible for disconnection at this percentage of the effective "
                "credit limit. Actual automatic cutoff still requires all safety gates."
            ),
            "reconnect_threshold_percent": (
                "After a cutoff, exposure must fall below this percentage before automatic restore "
                "can reconnect the meter."
            ),
            "staff_approval_required": (
                "Marks this account as requiring staff approval in approval-based credit-control "
                "workflows. It does not enable the server safety gates."
            ),
        }

    def clean(self):
        cleaned = super().clean()
        warning = cleaned.get("warning_threshold_percent")
        final = cleaned.get("final_warning_threshold_percent")
        cutoff = cleaned.get("cutoff_threshold_percent")
        reconnect = cleaned.get("reconnect_threshold_percent")
        if warning is not None and final is not None and warning > final:
            self.add_error("warning_threshold_percent", "Warning must not exceed final warning.")
        if final is not None and cutoff is not None and final > cutoff:
            self.add_error("final_warning_threshold_percent", "Final warning must not exceed cutoff.")
        if reconnect is not None and cutoff is not None and reconnect > cutoff:
            self.add_error("reconnect_threshold_percent", "Reconnect must not exceed cutoff.")
        return cleaned

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and "automatic_cutoff_and_restore" not in self.data:
            self.initial["automatic_cutoff_and_restore"] = bool(
                self.instance.automatic_cutoff and self.instance.automatic_restore
            )

    def save(self, commit=True):
        account = super().save(commit=False)
        automatic = bool(self.cleaned_data.get("automatic_cutoff_and_restore"))
        account.automatic_cutoff = automatic
        account.automatic_restore = automatic
        account.manual_only_cutoff = not automatic
        if commit:
            account.save()
            self.save_m2m()
        return account


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
