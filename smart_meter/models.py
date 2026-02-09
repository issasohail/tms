import os
from decimal import Decimal, ROUND_HALF_UP
from django.db.models.signals import post_save

from decimal import Decimal
from django.dispatch import receiver
from django.db import models
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


class Meter(models.Model):
    unit = models.OneToOneField("properties.Unit", on_delete=models.CASCADE)
    meter_number = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100, blank=True, null=True)
    power_status = models.CharField(
        max_length=10, choices=[("on", "On"), ("off", "Off")], default="on")
    unit_rate = models.DecimalField(
        max_digits=6, decimal_places=2, default=50.00)
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
    def is_cutoff(self) -> bool:
        """
        Compute OFF/ON from the live status_word.
        TODO: Replace the heuristic with your meter's exact bit map.
        """
        lr = self.latest_live
        sw = (lr and lr.status_word) or ""
        sw = str(sw).strip()
        if not sw:
            return False  # unknown => treat as ON

        # Heuristic that matches what you had; safe-guarded:
        try:
            # If it's binary like '0110'
            if set(sw) <= {"0", "1"}:
                return sw[0] == "1" or sw.endswith("10")
            # If it's hex like '0000', parse and check a relay bit (example: bit 0)
            bits = int(sw, 16)
            return bool(bits & 0x01)
        except Exception:
            return False

    def __str__(self):
        return f"Meter #{self.meter_number} → {self.unit}"

    class Meta:
        indexes = [
            models.Index(fields=['meter_number']),
        ]


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
        ("new", "New"),
        ("sent", "Sent"),
        ("ok", "OK"),
        ("timeout", "Timeout"),
        ("error", "Error"),
    ]

    meter = models.ForeignKey(
        "smart_meter.Meter", null=True, blank=True, on_delete=models.SET_NULL)
    meter_number = models.CharField(max_length=32, db_index=True)
    # DL/T645 frame hex (FEFE...16)
    frame_hex = models.TextField()
    expect_di = models.CharField(
        max_length=16, blank=True)  # optional DI to match
    timeout = models.FloatField(default=12.0)               # seconds
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="new", db_index=True)
    reply_hex = models.TextField(blank=True)
    error = models.TextField(blank=True)

    initiated_by = models.CharField(max_length=128, blank=True)
    reason = models.CharField(max_length=256, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.meter_number} {self.status} {self.created_at:%Y-%m-%d %H:%M:%S}"
