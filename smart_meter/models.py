import os
from decimal import Decimal, ROUND_HALF_UP
from django.db.models.signals import post_save

from decimal import Decimal
from django.dispatch import receiver
from django.db import models, transaction
from properties.models import Unit  # Adjust if your app name is different
from smart_meter.meter_client import send_meter_request
from datetime import timedelta
from django.utils.timezone import now
from django.utils import timezone
import datetime
from leases.models import Lease
from django.core.validators import MinValueValidator
import logging
from django.db import models
from django.utils.functional import cached_property
from smart_meter.meter_client import send_cutoff_command
from smart_meter.switch_OnOff import frame_command as build_switch_frame  # add at top
# add at top (same helpers used in views)
from smart_meter.utils.commands import send_via_listener, refresh_live
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q


class Meter(models.Model):
    BILLING_MODE_CHOICES = [
        ("postpaid", "Postpaid"),
        ("credit_controlled", "Postpaid with Credit Limit"),
        ("prepaid", "Prepaid (Legacy)"),
        ("prepaid_pilot", "DL/T645 Prepaid Pilot"),
    ]
    METER_TYPE_ELECTRIC = "electric"
    METER_TYPE_GAS = "gas"
    METER_TYPE_WATER = "water"
    METER_TYPE_SUB = "sub_meter"
    METER_TYPE_OTHER = "other"
    METER_TYPE_CHOICES = [
        (METER_TYPE_ELECTRIC, "Electric"),
        (METER_TYPE_GAS, "Gas"),
        (METER_TYPE_WATER, "Water"),
        (METER_TYPE_SUB, "Sub Meter"),
        (METER_TYPE_OTHER, "Other"),
    ]
    METER_ROLE_BILLING = "billing"
    METER_ROLE_CHECK = "check"
    METER_ROLE_CHOICES = [
        (METER_ROLE_BILLING, "Billing"),
        (METER_ROLE_CHECK, "Audit"),
    ]

    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_meters",
        help_text="Cached current unit only. Billing uses MeterInstallation history.",
    )
    meter_number = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100, blank=True, null=True)
    meter_type = models.CharField(
        max_length=20,
        choices=METER_TYPE_CHOICES,
        default=METER_TYPE_ELECTRIC,
    )
    billing_mode = models.CharField(
        max_length=20,
        choices=BILLING_MODE_CHOICES,
        default="postpaid",
    )
    meter_role = models.CharField(
        max_length=10,
        choices=METER_ROLE_CHOICES,
        default=METER_ROLE_BILLING,
    )
    power_status = models.CharField(
        max_length=10, choices=[("on", "On"), ("off", "Off")], default="on")
    unit_rate = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Optional meter electricity-rate override. Override order: Global, "
            "Property, Unit, Meter, then Lease; the last nonblank value wins."
        ),
    )
    service_charges = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("250.00"))
    min_balance_alert = models.DecimalField(
        max_digits=6, decimal_places=2, default=100.00)
    min_balance_cutoff = models.DecimalField(
        max_digits=6, decimal_places=2, default=0.00)
    is_active = models.BooleanField(default=True)
    installed_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    @cached_property
    def latest_live(self):
        """
        Prefer the OneToOne 'live' relation; if it doesn't exist yet,
        fall back to fetching the newest LiveReading.
        """
        lr = getattr(self, "live", None)   # related_name='live' on LiveReading
        if lr is not None:
            return lr
        from .models import LiveReading
        return LiveReading.objects.filter(meter=self).order_by("-ts").first()

    @property
    def is_prepaid(self):
        return self.billing_mode in {"prepaid", "prepaid_pilot"}

    @property
    def is_credit_controlled(self):
        return self.billing_mode == "credit_controlled"

    @property
    def is_check_meter(self):
        return self.meter_role == self.METER_ROLE_CHECK

    @property
    def effective_unit_rate(self):
        from smart_meter.rates import resolve_electricity_rate

        return resolve_electricity_rate(meter=self).rate

    @property
    def effective_unit_rate_source(self):
        from smart_meter.rates import resolve_electricity_rate

        return resolve_electricity_rate(meter=self).source

    def change_role(self, new_role, *, effective_date, user=None, reason=""):
        valid_roles = {value for value, _label in self.METER_ROLE_CHOICES}
        if new_role not in valid_roles:
            raise ValidationError({"meter_role": "Select a valid meter role."})
        if new_role != self.meter_role:
            if (
                new_role == self.METER_ROLE_BILLING and
                MeterCheckGroup.objects.filter(check_meter=self).exists()
            ):
                raise ValidationError({
                    "meter_role": "Remove this meter from its Check Group before changing it to Billing."
                })
            if (
                new_role == self.METER_ROLE_CHECK and
                self.check_group_memberships.filter(is_active=True, end_date__isnull=True).exists()
            ):
                raise ValidationError({
                    "meter_role": "End this meter's active Check Group membership before changing it to Audit."
                })

        with transaction.atomic():
            current = (
                self.role_history.select_for_update()
                .filter(is_active=True, end_date__isnull=True)
                .first()
            )
            if current and current.role == new_role:
                return current
            if current:
                current.close(end_date=effective_date, reason=reason)
            history = MeterRoleHistory.objects.create(
                meter=self,
                role=new_role,
                start_date=effective_date,
                is_active=True,
                changed_by=user,
                reason=reason,
            )
        self.meter_role = new_role
        return history

    @property
    def current_lease(self):
        installation = (
            self.installations
            .filter(is_active=True, end_date__isnull=True)
            .select_related("lease", "unit")
            .first()
        )
        if installation and installation.lease:
            return installation.lease
        unit = installation.unit if installation else self.unit
        if not unit:
            return None
        return (
            Lease.objects
            .filter(unit=unit, status="active")
            .order_by("-start_date")
            .first()
        )

    @property
    def current_tenant(self):
        lease = self.current_lease
        return lease.tenant if lease else None

    @property
    def display_location_name(self):
        """Return the unit label, or this meter's name on multi-meter units."""
        unit_number = getattr(self.unit, "unit_number", "") if self.unit_id else ""
        active_count = getattr(self, "_active_unit_meter_count", None)
        if active_count is None and self.unit_id:
            active_meter_ids = set(MeterInstallation.objects.filter(
                unit_id=self.unit_id,
                is_active=True,
                end_date__isnull=True,
            ).values_list("meter_id", flat=True))
            active_meter_ids.update(Meter.objects.filter(
                unit_id=self.unit_id,
                is_active=True,
            ).values_list("id", flat=True))
            active_count = len(active_meter_ids)
        if (active_count or 0) > 1:
            return (self.name or "").strip() or self.meter_number
        return unit_number or (self.name or "").strip() or self.meter_number

    @property
    def relay_state(self):
        """Authoritative relay state from the documented 0x028011FF status word."""
        from smart_meter.dlt645 import relay_state_from_status_word
        lr = self.latest_live
        return relay_state_from_status_word((lr and lr.status_word) or "")

    @property
    def is_cutoff(self) -> bool:
        # Unknown remains non-cutoff for backward-compatible display behavior.
        return self.relay_state == "off"

    def __str__(self):
        return f"Meter #{self.meter_number} → {self.unit}"

    class Meta:
        indexes = [
            models.Index(fields=['meter_number']),
        ]


class MeterInstallation(models.Model):
    meter = models.ForeignKey(
        Meter,
        on_delete=models.PROTECT,
        related_name="installations",
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        related_name="meter_installations",
    )
    lease = models.ForeignKey(
        Lease,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meter_installations",
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    start_reading = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    end_reading = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    active_meter_key = models.PositiveIntegerField(
        null=True,
        blank=True,
        editable=False,
        unique=True,
        help_text="Internal DB guard: meter id while active, NULL when closed.",
    )
    installed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meter_installations",
    )
    reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        indexes = [
            models.Index(fields=["meter", "is_active", "start_date"]),
            models.Index(fields=["unit", "is_active", "start_date"]),
            models.Index(fields=["lease", "start_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")),
                name="meter_installation_end_after_start",
            ),
        ]

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be before start date."})
        if self.is_active and self.end_date:
            raise ValidationError({"is_active": "Closed installations cannot remain active."})
        if self.is_active:
            clash = MeterInstallation.objects.filter(
                meter=self.meter,
                is_active=True,
                end_date__isnull=True,
            )
            if self.pk:
                clash = clash.exclude(pk=self.pk)
            if clash.exists():
                raise ValidationError("This meter already has an active installation.")

    def save(self, *args, **kwargs):
        self.active_meter_key = self.meter_id if self.is_active and self.end_date is None else None
        self.full_clean()
        super().save(*args, **kwargs)
        if self.is_active and self.end_date is None and self.meter.unit_id != self.unit_id:
            Meter.objects.filter(pk=self.meter_id).update(unit=self.unit)

    def close(self, *, end_date, end_reading=None, notes=""):
        self.end_date = end_date
        self.end_reading = end_reading
        self.is_active = False
        if notes:
            self.notes = (self.notes + "\n" + notes).strip()
        self.save()

    def __str__(self):
        end = self.end_date or "current"
        return f"{self.meter.meter_number} @ {self.unit} ({self.start_date} to {end})"


class MeterRoleHistory(models.Model):
    meter = models.ForeignKey(
        Meter,
        on_delete=models.CASCADE,
        related_name="role_history",
    )
    role = models.CharField(max_length=10, choices=Meter.METER_ROLE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    active_role_key = models.PositiveIntegerField(
        null=True,
        blank=True,
        editable=False,
        unique=True,
        help_text="Internal DB guard: meter id while this role record is active, NULL when closed.",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.CheckConstraint(
                check=Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")),
                name="meter_role_history_end_after_start",
            ),
        ]

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be before start date."})
        if self.is_active and self.end_date:
            raise ValidationError({"is_active": "Closed role records cannot remain active."})
        if self.is_active:
            clash = MeterRoleHistory.objects.filter(
                meter=self.meter,
                is_active=True,
                end_date__isnull=True,
            )
            if self.pk:
                clash = clash.exclude(pk=self.pk)
            if clash.exists():
                raise ValidationError("This meter already has an active role record.")

    def save(self, *args, **kwargs):
        self.active_role_key = self.meter_id if self.is_active and self.end_date is None else None
        self.full_clean()
        super().save(*args, **kwargs)
        if self.is_active and self.end_date is None:
            Meter.objects.filter(pk=self.meter_id).update(meter_role=self.role)

    def close(self, *, end_date, reason=""):
        self.end_date = end_date
        self.is_active = False
        if reason:
            self.reason = (self.reason + "\n" + reason).strip()
        self.save()

    def __str__(self):
        end = self.end_date or "current"
        return f"{self.meter.meter_number}: {self.get_role_display()} ({self.start_date} to {end})"


class MeterCheckGroup(models.Model):
    name = models.CharField(max_length=100)
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="meter_check_groups",
    )
    check_meter = models.OneToOneField(
        Meter,
        on_delete=models.CASCADE,
        related_name="check_group",
        limit_choices_to={"meter_role": "check"},
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.check_meter_id and self.check_meter.meter_role != Meter.METER_ROLE_CHECK:
            raise ValidationError({"check_meter": "Selected meter is not marked as an Audit meter."})

    def active_billing_meters(self, as_of=None):
        as_of = as_of or timezone.localdate()
        return Meter.objects.filter(
            meter_role=Meter.METER_ROLE_BILLING,
            check_group_memberships__group=self,
            check_group_memberships__start_date__lte=as_of,
        ).filter(
            Q(check_group_memberships__end_date__isnull=True) |
            Q(check_group_memberships__end_date__gte=as_of)
        ).distinct()

    def __str__(self):
        return self.name


class MeterCheckGroupMembership(models.Model):
    group = models.ForeignKey(
        MeterCheckGroup,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    billing_meter = models.ForeignKey(
        Meter,
        on_delete=models.CASCADE,
        related_name="check_group_memberships",
        limit_choices_to={"meter_role": "billing"},
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.CheckConstraint(
                check=Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")),
                name="meter_check_group_membership_end_after_start",
            ),
        ]

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be before start date."})
        if self.is_active and self.end_date:
            raise ValidationError({"is_active": "Closed memberships cannot remain active."})
        if self.billing_meter_id and self.billing_meter.meter_role != Meter.METER_ROLE_BILLING:
            raise ValidationError({"billing_meter": "Selected meter is not marked as a Billing meter."})
        if self.billing_meter_id:
            clash = MeterCheckGroupMembership.objects.filter(
                billing_meter=self.billing_meter,
            ).exclude(group=self.group)
            if self.pk:
                clash = clash.exclude(pk=self.pk)
            if self.end_date:
                clash = clash.filter(start_date__lte=self.end_date)
            clash = clash.filter(Q(end_date__isnull=True) | Q(end_date__gte=self.start_date))
            if clash.exists():
                raise ValidationError(
                    "This billing meter already has an overlapping membership in another check group."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def close(self, *, end_date, notes=""):
        self.end_date = end_date
        self.is_active = False
        if notes:
            self.notes = (self.notes + "\n" + notes).strip()
        self.save()

    def __str__(self):
        end = self.end_date or "current"
        return f"{self.billing_meter.meter_number} in {self.group.name} ({self.start_date} to {end})"


class MeterAssignmentHistory(models.Model):
    meter = models.ForeignKey(
        Meter,
        on_delete=models.CASCADE,
        related_name="assignment_history",
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meter_assignment_changes",
    )
    lease = models.ForeignKey(
        Lease,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meter_assignment_changes",
    )
    old_meter = models.ForeignKey(
        Meter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="old_assignment_changes",
    )
    new_meter = models.ForeignKey(
        Meter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="new_assignment_changes",
    )
    old_unit = models.ForeignKey(
        Unit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="old_meter_assignment_changes",
    )
    new_unit = models.ForeignKey(
        Unit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="new_meter_assignment_changes",
    )
    old_lease = models.ForeignKey(
        Lease,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="old_meter_assignment_changes",
    )
    new_lease = models.ForeignKey(
        Lease,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="new_meter_assignment_changes",
    )
    change_date = models.DateTimeField(default=timezone.now)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meter_assignment_changes",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-change_date", "-id"]

    def __str__(self):
        return f"{self.meter.meter_number} assignment change"


class MeterReading(models.Model):
    """
    Historical snapshots (for billing & reports). Keep it modest: every 15 minutes or hourly.
    """
    meter = models.ForeignKey(
        Meter, on_delete=models.CASCADE, related_name='readings')
    ts = models.DateTimeField(db_index=True, default=timezone.now)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    source_port = models.PositiveIntegerField(null=True, blank=True)
    total_energy = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True)
    peak_total_energy = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True)
    valley_total_consumption = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True)
    flat_total_consumption = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True)

    total_power = models.DecimalField(
        max_digits=9, decimal_places=3, null=True, blank=True)
    pf_total = models.DecimalField(
        max_digits=5, decimal_places=3, null=True, blank=True)

    voltage_a = models.DecimalField(
        max_digits=7, decimal_places=1, null=True, blank=True)
    voltage_b = models.DecimalField(
        max_digits=7, decimal_places=1, null=True, blank=True)
    voltage_c = models.DecimalField(
        max_digits=7, decimal_places=1, null=True, blank=True)

    current_a = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True)
    current_b = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True)
    current_c = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['meter', 'ts']),
        ]
        ordering = ['-ts']

    def __str__(self):
        return f"{self.meter.meter_number} @ {self.ts}"


class Tariff(models.Model):
    """
    Simple flat tariff. If you do TOU later, extend with time bands.
    """
    name = models.CharField(max_length=64, default="Default")
    rate_per_kwh = models.DecimalField(
        max_digits=8, decimal_places=4, default=Decimal("7.50"))
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ₹{self.rate_per_kwh}/kWh"


class LiveReading(models.Model):
    """
    Exactly one row per Meter (overwritten every time). Small & hot.
    """
    meter = models.OneToOneField(
        Meter, on_delete=models.CASCADE, related_name='live')
    ts = models.DateTimeField(auto_now=True)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    source_port = models.PositiveIntegerField(null=True, blank=True)
    balance = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    overdraft = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)

    voltage_a = models.DecimalField(
        max_digits=7, decimal_places=1, null=True, blank=True)
    voltage_b = models.DecimalField(
        max_digits=7, decimal_places=1, null=True, blank=True)
    voltage_c = models.DecimalField(
        max_digits=7, decimal_places=1, null=True, blank=True)

    current_a = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True)
    current_b = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True)
    current_c = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True)

    total_power = models.DecimalField(
        max_digits=9, decimal_places=3, null=True, blank=True)
    power_a = models.DecimalField(
        max_digits=9, decimal_places=3, null=True, blank=True)
    power_b = models.DecimalField(
        max_digits=9, decimal_places=3, null=True, blank=True)
    power_c = models.DecimalField(
        max_digits=9, decimal_places=3, null=True, blank=True)

    pf_total = models.DecimalField(
        max_digits=5, decimal_places=3, null=True, blank=True)
    pf_a = models.DecimalField(
        max_digits=5, decimal_places=3, null=True, blank=True)
    pf_b = models.DecimalField(
        max_digits=5, decimal_places=3, null=True, blank=True)
    pf_c = models.DecimalField(
        max_digits=5, decimal_places=3, null=True, blank=True)

    total_energy = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True)
    peak_total_energy = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True)
    valley_total_consumption = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True)
    flat_total_consumption = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True)

    prev1_day_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    prev1_day_peak_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    prev1_day_valley_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    prev1_day_flat_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)

    last2_days_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    last2_days_peak_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    last2_days_valley_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    last2_days_flat_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)

    last3_days_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    last3_days_peak_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    last3_days_valley_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    last3_days_flat_energy = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)

    status_word = models.CharField(max_length=16, blank=True, null=True)

    def __str__(self):
        return f"Live {self.meter.meter_number} @ {self.ts}"


class Bill(models.Model):
    unit = models.ForeignKey(
        'properties.Unit', on_delete=models.CASCADE, related_name='bills')
    meter = models.ForeignKey(
        Meter, on_delete=models.PROTECT, related_name='bills')
    period_start = models.DateField()
    period_end = models.DateField()

    opening_kwh = models.DecimalField(max_digits=14, decimal_places=3)
    closing_kwh = models.DecimalField(max_digits=14, decimal_places=3)
    units_consumed = models.DecimalField(max_digits=14, decimal_places=3)

    rate_per_kwh = models.DecimalField(max_digits=8, decimal_places=4)
    amount_due = models.DecimalField(max_digits=12, decimal_places=2)

    issued_date = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=[(
        'unpaid', 'Unpaid'), ('paid', 'Paid')], default='unpaid')

    def __str__(self):
        return f"Bill {self.unit} {self.period_start} → {self.period_end}"


class Payment(models.Model):
    bill = models.ForeignKey(
        Bill, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[
                                 MinValueValidator(Decimal('0.01'))])
    date = models.DateField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"₹{self.amount} for {self.bill}"


class MeterBalance(models.Model):
    unit = models.OneToOneField(Unit, on_delete=models.CASCADE)
    balance = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"))
    security_deposit = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00)
    last_alert_sent = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.unit} balance: ₹{self.balance}"


class CutoffEvent(models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=255, default="Balance depleted")


logger = logging.getLogger(__name__)


def D(x, q=None):
    """
    Safe Decimal coercion: handles None/float/Decimal/str.
    If q is provided (like Decimal('0.01')), quantize to that step.
    """
    if x is None:
        x = '0'
    else:
        # Convert through str to avoid binary float artifacts
        x = str(x)
    d = Decimal(x)
    return d.quantize(q) if q is not None else d


logger = logging.getLogger(__name__)
D = Decimal  # convenience alias


@receiver(post_save, sender=MeterReading)
def deduct_balance_on_reading(sender, instance, created, **kwargs):
    if not created:
        return

    # Master switch: skip *everything* if you don't want deductions yet
    if not getattr(settings, "METER_ENABLE_BALANCE_DEDUCTION", True):
        return

    if getattr(instance.meter, "billing_mode", "postpaid") != "prepaid":
        return

    unit = instance.meter.unit

    # Need previous + current to compute delta
    qs = (
        MeterReading.objects
        .filter(meter__unit=unit)
        .order_by("-ts")[:2]
    )
    if len(qs) < 2:
        return

    current, prev = qs[0], qs[1]

    prev_kwh = D(prev.total_energy)
    curr_kwh = D(current.total_energy)
    delta_kwh = curr_kwh - prev_kwh
    if delta_kwh <= 0:
        return

    # Get active tariff rate; default 7.50 if missing
    try:
        from .models import Tariff, MeterBalance
        rate = Tariff.objects.filter(active=True).values_list(
            "rate_per_kwh", flat=True).first()
    except Exception:
        rate = None

    rate = D(str(rate)) if rate is not None else D("7.50")
    # round to 2dp
    cost = (delta_kwh * rate).quantize(D("0.01"), rounding=ROUND_HALF_UP)

    balance, _ = MeterBalance.objects.get_or_create(unit=unit)

    bal = D(balance.balance)
    dep = D(balance.security_deposit)

    if bal >= cost:
        bal = bal - cost
    else:
        deficit = cost - bal
        bal = D("0.00")
        if dep >= deficit:
            dep = dep - deficit
        else:
            # Not enough in deposit either → consider prepaid cutoff,
            # but only if explicitly enabled AND meter is prepaid.
            prepaid_enabled = getattr(
                settings, "METER_ENABLE_PREPAID_CUTOFF", False)

            # Treat presence of related prepaid row (and active=True if present) as per-meter guard
            has_prepaid = False
            try:
                prepaid_obj = getattr(instance.meter, "prepaid", None)
                if prepaid_obj is not None:
                    # if the model has an 'active' field, respect it; otherwise just the presence is enough
                    has_prepaid = getattr(prepaid_obj, "active", True)
            except Exception:
                has_prepaid = False

            # Extra safety: allow env kill-switch to block any cutoff packets
            cutoff_env_blocked = os.environ.get("DISABLE_CUTOFF") == "1"

            if prepaid_enabled and has_prepaid and not cutoff_env_blocked:
                try:
                    frame = build_switch_frame(
                        instance.meter.meter_number, 0x1A)  # 0x1A = OFF
                    send_via_listener(
                        instance.meter.meter_number, frame, timeout=32.0)

                    # optional best-effort live refresh so status flips quickly
                    try:
                        refresh_live(instance.meter.meter_number)
                    except Exception:
                        pass

                    logger.info(
                        "%s: ⚡ Cutoff sent for %s (meter=%s)",
                        datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                        unit,
                        instance.meter.meter_number,
                    )
                except Exception as e:
                    logger.warning(
                        "%s: Cutoff failed for %s (meter=%s): %s",
                        datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                        unit,
                        instance.meter.meter_number,
                        e,
                    )
            else:
                # Skipped cutoff due to flags or kill-switch
                logger.info(
                    "Skipping cutoff for %s (meter=%s) — prepaid_enabled=%s has_prepaid=%s cutoff_env_blocked=%s",
                    unit,
                    instance.meter.meter_number,
                    prepaid_enabled,
                    has_prepaid,
                    cutoff_env_blocked,
                )

    # write back as Decimals
    balance.balance = bal.quantize(D("0.01"), rounding=ROUND_HALF_UP)
    balance.security_deposit = dep.quantize(D("0.01"), rounding=ROUND_HALF_UP)
    balance.save()


class MeterEvent(models.Model):
    EVENT_TYPES = [
        ("cutoff", "Power Cut-Off"),
        ("restore", "Power Restored"),
        ("recharge", "Recharge"),
        ("payment", "Payment Recorded"),
        ("alert", "Low Balance Alert"),
    ]

    unit = models.ForeignKey(Unit, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)
    timestamp = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)

    def __str__(self):
        return f"{self.unit} - {self.event_type} at {self.timestamp}"


class MeterSettings(models.Model):
    unit_rate = models.DecimalField(
        max_digits=6, decimal_places=2, default=7.50)
    low_balance_threshold = models.DecimalField(
        max_digits=6, decimal_places=2, default=100.00)
    peak_start_hour = models.IntegerField(default=17)
    peak_end_hour = models.IntegerField(default=22)

    def __str__(self):
        return f"Global Meter Settings: ₹{self.unit_rate}/kWh"

# smart_meter/models.py


class UnknownMeter(models.Model):
    meter_number = models.CharField(max_length=32, unique=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    seen_count = models.PositiveIntegerField(default=1)
    last_raw_hex = models.TextField(blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=(("new", "New"), ("ignored", "Ignored"), ("added", "Added")),
        default="new"
    )

    def __str__(self):
        return f"{self.meter_number} ({self.status})"


# smart_meter/models.py


class MeterPrepaidSettings(models.Model):
    meter = models.OneToOneField(
        "smart_meter.Meter", on_delete=models.CASCADE, related_name="prepaid")
    # ---- core amounts in rupees (human friendly); we’ll convert to fen/cents for the frame ----
    alarm_amount_1 = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    alarm_amount_2 = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    overdraft_limit = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # two simple rates (Rs/kWh) with 4 decimal places to match the vendor’s 4-dec BCD
    rate1_price_1 = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal("0.0000"))
    rate2_price_1 = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal("0.0000"))

    # optional step values (kWh) if you want to use step tariffs later
    step1_value_1 = models.PositiveIntegerField(
        default=0, help_text="kWh in first step (optional)")
    step2_value_1 = models.PositiveIntegerField(
        default=0, help_text="kWh in second step (optional)")

    # timing fields in BCD yymmddhhmm form as integers, default 0 = no switch schedule
    rate_switch_time = models.BigIntegerField(default=0)
    step_switch_time = models.BigIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Prepaid settings for {self.meter.meter_number}"

    # Helper that returns a dict vendor.build_frame() expects
    def to_vendor_parameters(self) -> dict:
        # Convert rupees → fen/cents style “integer of 2 decimals”
        def rupees_to_fen(d: Decimal) -> int:
            return int(Decimal(d).quantize(Decimal("0.01")) * 100)

        return {
            # simple subset first; you can fill out the rest over time
            "alarm_amount_1": rupees_to_fen(self.alarm_amount_1),
            "alarm_amount_2": rupees_to_fen(self.alarm_amount_2),
            "overdraft_limit": rupees_to_fen(self.overdraft_limit),

            # prices are floats with 4 decimal places in the vendor frame
            "rate1_price_1": float(self.rate1_price_1),
            "rate2_price_1": float(self.rate2_price_1),

            # optional switches (5-byte BCD times). keep 0 to ignore
            "rate_switch_time": int(self.rate_switch_time or 0),
            "step_switch_time": int(self.step_switch_time or 0),

            # if you enable steps later:
            "step1_value_1": int(self.step1_value_1 or 0),
            "step2_value_1": int(self.step2_value_1 or 0),
        }

# models.py


class MeterCommand(models.Model):
    STATUS_CHOICES = [
        ("new", "New (legacy)"),
        ("pending", "Pending"),
        ("waiting_online", "Waiting for Meter"),
        ("claimed", "Claimed"),
        ("sent", "Sent"),
        ("acknowledged", "Acknowledged"),
        ("verified", "Verified"),
        ("retry", "Retry Scheduled"),
        ("cancelled", "Cancelled"),
        ("expired", "Expired"),
        ("failed", "Failed"),
        ("ok", "OK (legacy)"),
        ("timeout", "Timeout (legacy)"),
        ("error", "Error (legacy)"),
    ]
    COMMAND_TYPES = [
        ("relay", "Relay"),
        ("read", "Read"),
        ("prepaid_read", "Prepaid Read"),
        ("prepaid_write", "Prepaid Write"),
        ("prepaid_recharge", "Prepaid Recharge"),
        ("other", "Other"),
    ]
    DESIRED_STATES = [("", "Not applicable"), ("on", "On"), ("off", "Off")]
    SOURCES = [
        ("manual", "Manual"),
        ("credit_control", "Credit Control"),
        ("payment", "Payment"),
        ("prepaid", "Prepaid Pilot"),
        ("system", "System"),
    ]

    meter = models.ForeignKey(
        "smart_meter.Meter", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="commands",
    )
    meter_number = models.CharField(max_length=32, db_index=True)
    frame_hex = models.TextField()
    expect_di = models.CharField(max_length=16, blank=True)
    timeout = models.FloatField(default=12.0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    reply_hex = models.TextField(blank=True)
    error = models.TextField(blank=True)

    command_type = models.CharField(max_length=24, choices=COMMAND_TYPES, default="other", db_index=True)
    desired_state = models.CharField(max_length=8, choices=DESIRED_STATES, blank=True, default="")
    source = models.CharField(max_length=24, choices=SOURCES, default="manual", db_index=True)
    priority = models.PositiveSmallIntegerField(default=50)
    idempotency_key = models.CharField(max_length=160, null=True, blank=True, unique=True)
    not_before = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    requires_verification = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)
    raw_ack_hex = models.TextField(blank=True)
    status_query_hex = models.TextField(blank=True)
    parsed_relay_state = models.CharField(max_length=16, blank=True)

    related_credit_account = models.ForeignKey(
        "smart_meter.MeterCreditAccount", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="commands",
    )
    related_payment = models.ForeignKey(
        "payments.Payment", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="meter_commands",
    )
    related_invoice = models.ForeignKey(
        "invoices.Invoice", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="meter_commands",
    )
    related_enforcement_event = models.ForeignKey(
        "smart_meter.MeterCreditAudit", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="commands",
    )

    initiated_by = models.CharField(max_length=128, blank=True)
    reason = models.CharField(max_length=256, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["meter_number", "status", "priority"]),
            models.Index(fields=["source", "desired_state", "status"]),
        ]
        permissions = [
            ("cancel_meter_command", "Can cancel pending meter command"),
            ("view_raw_dlt645_frames", "Can view raw DL/T645 frames"),
        ]

    @property
    def is_terminal(self):
        return self.status in {"verified", "cancelled", "expired", "failed", "ok", "error"}

    def __str__(self):
        return f"{self.meter_number} {self.status} {self.created_at:%Y-%m-%d %H:%M:%S}"


class MeterCreditAccount(models.Model):
    MODE_CHOICES = [("credit_controlled", "Postpaid with Credit Limit")]
    LIMIT_SOURCES = [
        ("fixed", "Fixed monetary limit"),
        ("deposit_percent", "Percentage of electricity security deposit"),
        ("lower_of", "Lower of fixed and deposit-derived"),
        ("lease_override", "Lease-specific manual override"),
    ]
    STATES = [
        ("normal", "Normal"), ("warning_1", "Warning 1"), ("warning_2", "Warning 2"),
        ("cutoff_eligible", "Cutoff eligible"), ("cutoff_pending", "Cutoff pending"),
        ("cutoff_sent", "Cutoff sent"), ("disconnected", "Disconnected"),
        ("reconnect_eligible", "Reconnect eligible"), ("reconnect_pending", "Reconnect pending"),
        ("reconnect_sent", "Reconnect sent"), ("connected", "Connected"),
        ("manual_hold", "Manual hold"), ("data_review_required", "Data review required"),
        ("reading_reset_detected", "Reading reset detected"), ("tariff_missing", "Tariff missing"),
        ("stale_reading", "Stale reading"), ("installation_mismatch", "Installation mismatch"),
        ("command_failed", "Command failed"),
    ]
    RECONNECT_POLICIES = [
        ("below_reconnect", "Exposure below reconnect threshold"),
        ("below_cutoff", "Exposure below cutoff threshold"),
        ("full_balance", "Full enforceable electricity balance paid"),
        ("minimum_payment", "Minimum fixed payment"),
        ("staff_approval", "Staff approval required"),
    ]

    meter = models.ForeignKey(Meter, on_delete=models.PROTECT, related_name="credit_accounts")
    installation = models.ForeignKey(MeterInstallation, on_delete=models.PROTECT, related_name="credit_accounts")
    lease = models.ForeignKey(Lease, on_delete=models.PROTECT, related_name="meter_credit_accounts")
    mode = models.CharField(max_length=24, choices=MODE_CHOICES, default="credit_controlled")
    is_enabled = models.BooleanField(default=False, db_index=True)
    active_installation_key = models.PositiveBigIntegerField(null=True, blank=True, unique=True, editable=False)

    credit_limit_source = models.CharField(max_length=24, choices=LIMIT_SOURCES, default="fixed")
    fixed_credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    deposit_percentage = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("100.00"))
    lease_override_limit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    deposit_reference_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    effective_credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    limit_explanation = models.CharField(max_length=255, blank=True)

    warning_threshold_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("75.00"))
    final_warning_threshold_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("90.00"))
    cutoff_threshold_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("100.00"))
    reconnect_threshold_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("80.00"))
    reconnect_policy = models.CharField(max_length=24, choices=RECONNECT_POLICIES, default="below_reconnect")
    minimum_reconnect_payment = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    automatic_cutoff = models.BooleanField(default=False)
    automatic_restore = models.BooleanField(default=False)
    manual_only_cutoff = models.BooleanField(default=True)
    staff_approval_required = models.BooleanField(default=True)

    activated_at = models.DateTimeField(null=True, blank=True)
    activation_reading_kwh = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    checkpoint_reading_kwh = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    checkpoint_at = models.DateTimeField(null=True, blank=True)
    starting_tariff = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    policy_snapshot = models.JSONField(default=dict, blank=True)
    last_evaluated_reading_kwh = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    last_evaluated_at = models.DateTimeField(null=True, blank=True)

    accrued_usage_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    previous_unpaid_electricity = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    payments_applied = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    credits_applied = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    current_exposure = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    enforcement_state = models.CharField(max_length=32, choices=STATES, default="normal", db_index=True)
    data_quality_reason = models.CharField(max_length=255, blank=True)
    last_warning_level = models.PositiveSmallIntegerField(default=0)
    max_consumption_jump_kwh = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("250.000"))
    stale_after_minutes = models.PositiveIntegerField(default=30)

    notifications_muted_until = models.DateTimeField(null=True, blank=True)
    notifications_muted_for_period = models.CharField(max_length=20, blank=True)
    notification_mute_reason = models.CharField(max_length=255, blank=True)
    notifications_muted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="meter_credit_notification_mutes")
    notification_muted_at = models.DateTimeField(null=True, blank=True)

    enforcement_hold_until = models.DateTimeField(null=True, blank=True)
    enforcement_hold_for_period = models.CharField(max_length=20, blank=True)
    enforcement_hold_reason = models.CharField(max_length=255, blank=True)
    enforcement_hold_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="meter_credit_enforcement_holds")
    enforcement_hold_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["meter", "is_enabled"]),
            models.Index(fields=["installation", "is_enabled"]),
            models.Index(fields=["lease", "is_enabled"]),
            models.Index(fields=["enforcement_state", "is_enabled"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(reconnect_threshold_percent__lte=models.F("cutoff_threshold_percent")), name="meter_credit_reconnect_lte_cutoff"),
            models.CheckConstraint(check=Q(warning_threshold_percent__lte=models.F("final_warning_threshold_percent")), name="meter_credit_warning_order_1"),
            models.CheckConstraint(check=Q(final_warning_threshold_percent__lte=models.F("cutoff_threshold_percent")), name="meter_credit_warning_order_2"),
        ]
        permissions = [
            ("view_meter_credit_details", "Can view meter credit details"),
            ("change_meter_credit_settings", "Can change meter credit settings"),
            ("activate_meter_credit", "Can activate meter credit control"),
            ("deactivate_meter_credit", "Can deactivate meter credit control"),
            ("mute_meter_credit_notifications", "Can mute meter credit notifications"),
            ("hold_meter_credit_enforcement", "Can hold meter credit enforcement"),
            ("approve_meter_credit_cutoff", "Can approve meter credit cutoff"),
            ("override_meter_credit_reconnect", "Can override meter credit reconnection policy"),
            ("use_meter_credit_emergency_stop", "Can use meter credit emergency stop"),
        ]

    def save(self, *args, **kwargs):
        self.active_installation_key = self.installation_id if self.is_enabled else None
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        if self.warning_threshold_percent > self.final_warning_threshold_percent:
            errors["warning_threshold_percent"] = "Warning threshold must not exceed final warning threshold."
        if self.final_warning_threshold_percent > self.cutoff_threshold_percent:
            errors["final_warning_threshold_percent"] = "Final warning threshold must not exceed cutoff threshold."
        if self.reconnect_threshold_percent > self.cutoff_threshold_percent:
            errors["reconnect_threshold_percent"] = "Reconnect threshold must not exceed cutoff threshold."
        if errors:
            raise ValidationError(errors)

    @property
    def percent_used(self):
        if not self.effective_credit_limit or self.effective_credit_limit <= 0:
            return Decimal("0.00")
        return (self.current_exposure * Decimal("100") / self.effective_credit_limit).quantize(Decimal("0.01"))

    @property
    def remaining_credit(self):
        return max(Decimal("0.00"), self.effective_credit_limit - self.current_exposure)

    def __str__(self):
        return f"Credit account {self.pk or 'new'} - {self.meter.meter_number}"


class MeterEvaluationRequest(models.Model):
    STATUS_CHOICES = [("pending", "Pending"), ("processing", "Processing"), ("done", "Done"), ("failed", "Failed")]
    meter = models.ForeignKey(Meter, on_delete=models.CASCADE, related_name="credit_evaluation_requests")
    latest_reading_id = models.PositiveBigIntegerField(null=True, blank=True)
    reading_timestamp = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"]), models.Index(fields=["meter", "status"])]


class MeterCreditAudit(models.Model):
    SOURCES = [("automatic", "Automatic"), ("scheduled", "Scheduled"), ("manual", "Manual"), ("payment", "Payment"), ("reading", "Reading"), ("system", "System")]
    action_type = models.CharField(max_length=64, db_index=True)
    meter = models.ForeignKey(Meter, null=True, blank=True, on_delete=models.SET_NULL, related_name="credit_audits")
    installation = models.ForeignKey(MeterInstallation, null=True, blank=True, on_delete=models.SET_NULL, related_name="credit_audits")
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.SET_NULL, related_name="meter_credit_audits")
    tenant = models.ForeignKey("tenants.Tenant", null=True, blank=True, on_delete=models.SET_NULL, related_name="meter_credit_audits")
    credit_account = models.ForeignKey(MeterCreditAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    invoice = models.ForeignKey("invoices.Invoice", null=True, blank=True, on_delete=models.SET_NULL, related_name="meter_credit_audits")
    payment = models.ForeignKey("payments.Payment", null=True, blank=True, on_delete=models.SET_NULL, related_name="meter_credit_audits")
    previous_state = models.CharField(max_length=64, blank=True)
    new_state = models.CharField(max_length=64, blank=True)
    exposure_before = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    exposure_after = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    threshold = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="meter_credit_audits")
    source = models.CharField(max_length=16, choices=SOURCES, default="system")
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["meter", "created_at"]), models.Index(fields=["credit_account", "created_at"])]


class MeterPrepaidPilot(models.Model):
    STATUSES = [
        ("disabled", "Disabled"), ("read_only", "Read only"), ("configuration_pending", "Configuration pending"),
        ("configuration_sent", "Configuration sent"), ("configuration_verified", "Configuration verified"),
        ("recharge_pending", "Recharge pending"), ("active_test", "Active test"), ("failed", "Failed"),
        ("rolled_back", "Rolled back"),
    ]
    meter = models.OneToOneField(Meter, on_delete=models.CASCADE, related_name="prepaid_pilot")
    installation = models.ForeignKey(MeterInstallation, null=True, blank=True, on_delete=models.PROTECT, related_name="prepaid_pilots")
    status = models.CharField(max_length=32, choices=STATUSES, default="disabled", db_index=True)
    model_name = models.CharField(max_length=100, blank=True)
    firmware_version = models.CharField(max_length=100, blank=True)
    display_balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    enabled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="enabled_prepaid_pilots")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ("enable_prepaid_pilot", "Can enable prepaid pilot"),
            ("read_prepaid_parameters", "Can read prepaid parameters"),
            ("write_prepaid_parameters", "Can write prepaid parameters"),
            ("recharge_prepaid_meter", "Can recharge prepaid meter"),
            ("rollback_prepaid_meter", "Can roll back prepaid meter"),
        ]


class MeterPrepaidParameterRead(models.Model):
    pilot = models.ForeignKey(MeterPrepaidPilot, on_delete=models.CASCADE, related_name="parameter_reads")
    di = models.CharField(max_length=16, blank=True)
    parameter = models.CharField(max_length=64)
    raw_response = models.TextField(blank=True)
    parsed_value = models.CharField(max_length=128, blank=True)
    unit = models.CharField(max_length=32, blank=True)
    parse_status = models.CharField(max_length=24, default="pending")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class MeterPrepaidWriteAttempt(models.Model):
    STATUSES = [("pending", "Pending"), ("sent", "Sent"), ("verified", "Verified"), ("failed", "Failed"), ("rolled_back", "Rolled back")]
    pilot = models.ForeignKey(MeterPrepaidPilot, on_delete=models.CASCADE, related_name="write_attempts")
    parameter = models.CharField(max_length=64)
    requested_value = models.CharField(max_length=128)
    original_value = models.CharField(max_length=128, blank=True)
    read_before_hex = models.TextField(blank=True)
    command_hex = models.TextField(blank=True)
    ack_hex = models.TextField(blank=True)
    read_back_hex = models.TextField(blank=True)
    actual_value = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, choices=STATUSES, default="pending")
    reason = models.CharField(max_length=255)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="prepaid_write_attempts")
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)


class MeterPrepaidRecharge(models.Model):
    STATUSES = [("disabled", "Disabled"), ("pending", "Pending"), ("verified", "Verified"), ("failed", "Failed"), ("uncertain", "Uncertain")]
    pilot = models.ForeignKey(MeterPrepaidPilot, on_delete=models.CASCADE, related_name="recharges")
    transaction_id = models.CharField(max_length=64, unique=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    manufacturer_sequence = models.CharField(max_length=64, blank=True)
    before_balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    after_balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUSES, default="disabled")
    reconciliation_note = models.TextField(blank=True)
    raw_command = models.TextField(blank=True)
    raw_ack = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="prepaid_recharges")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
