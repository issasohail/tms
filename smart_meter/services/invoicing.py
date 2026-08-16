from datetime import date, datetime, time, timedelta
from calendar import monthrange
from decimal import Decimal

from django.db import transaction
from django.db.models import Min, Max, Q
from django.utils import timezone

from smart_meter.models import MeterReading, Meter, MeterInstallation
from invoices.models import Invoice, InvoiceItem, ItemCategory
from leases.models import Lease, LeaseUnitOccupancy
from smart_meter.rates import resolve_electricity_rate


# ---- helpers ---------------------------------------------------------------
def _month_window_local(period_start: date):
    """Return [start_of_month_local, start_of_next_month_local) as aware datetimes."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(
        period_start.replace(day=1), time.min), tz)
    y, m = period_start.year, period_start.month
    if m == 12:
        y, m = y + 1, 1
    else:
        m += 1
    next_start = timezone.make_aware(
        datetime.combine(date(y, m, 1), time.min), tz)
    return start, next_start


def _billing_period_label(start: date, end: date) -> str:
    return f"{start:%Y-%m-%d} to {end:%Y-%m-%d}"


def _trim_desc(s: str, maxlen: int = 490) -> str:
    """Keep generated descriptions within InvoiceItem.description (max_length=500)."""
    return s if len(s) <= maxlen else s[: maxlen - 1] + "…"


def _detect_unit_rate(meter: Meter, lease: Lease) -> Decimal:
    return resolve_electricity_rate(meter=meter, lease=lease).rate


def _detect_service_charges(meter: Meter) -> Decimal:
    svc = getattr(meter, "service_charges", None)
    try:
        return Decimal(str(svc or "0"))
    except Exception:
        return Decimal("0")


class ElectricBillContext:
    """A simple container for computed values shown on preview."""

    def __init__(self, *,
                 lease: Lease,
                 meter: Meter,
                 period_start: date,
                 period_end: date,
                 beg_kwh: Decimal,
                 end_kwh: Decimal,
                 units: Decimal,
                 unit_rate: Decimal,
                 service_charges: Decimal,
                 segments: list | None = None):
        self.lease = lease
        self.meter = meter
        self.period_start = period_start
        self.period_end = period_end
        self.beg_kwh = beg_kwh
        self.end_kwh = end_kwh
        self.units = units
        self.unit_rate = unit_rate
        self.service_charges = service_charges
        self.segments = segments or []

    @property
    def usage_amount(self) -> Decimal:
        return (self.units * self.unit_rate).quantize(Decimal("0.01"))

    @property
    def line_total(self) -> Decimal:
        return (self.usage_amount + self.service_charges).quantize(Decimal("0.01"))

    @property
    def billing_period_label(self) -> str:
        return _billing_period_label(self.period_start, self.period_end)

    @property
    def description_text(self) -> str:
        raw = (
            f"Meter#={self.meter.meter_number}, "
            f"Billing Period={self.billing_period_label}, "
            f"Beg Unit={self.beg_kwh} - end unit={self.end_kwh}, "
            f"unit consume={self.units}, unit rate={self.unit_rate}="
            f"total usage={self.usage_amount}, service charges={self.service_charges}. "
            f"total={self.line_total}."
        )
        return _trim_desc(raw)


def _overlap(start_a: date, end_a: date | None, start_b: date, end_b: date | None):
    start = max(start_a, start_b)
    end = min(end_a or date.max, end_b or date.max)
    if start > end:
        return None
    return start, end


def _month_window_local(period_start: date):
    """[start_of_month@00:00 local, start_of_next_month@00:00 local)."""
    tz = timezone.get_current_timezone()
    # month start (aware)
    sdt = timezone.make_aware(datetime.combine(
        period_start.replace(day=1), time.min), tz)
    # next month start (aware)
    y, m = period_start.year, period_start.month
    if m == 12:
        y, m = y + 1, 1
    else:
        m = m + 1
    ndt = timezone.make_aware(datetime.combine(date(y, m, 1), time.min), tz)
    return sdt, ndt


def _reading_bounds(meter: Meter, start: date, end: date):
    tz = timezone.get_current_timezone()
    sdt = timezone.make_aware(datetime.combine(start, time.min), tz)
    edt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)

    agg = (
        MeterReading.objects
        .filter(meter=meter, ts__gte=sdt, ts__lt=edt)
        .aggregate(beg=Min("total_energy"), end=Max("total_energy"))
    )
    return agg["beg"], agg["end"]


def compute_electric_bill(lease, meter, period_start: date, period_end: date) -> ElectricBillContext:
    installations = MeterInstallation.objects.filter(
        meter=meter,
        start_date__lte=period_end,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=period_start)).select_related("unit")

    occupancies = LeaseUnitOccupancy.objects.filter(
        lease=lease,
        move_in_date__lte=period_end,
    ).filter(Q(move_out_date__isnull=True) | Q(move_out_date__gte=period_start)).select_related("unit")

    segments = []
    total_units = Decimal("0.000")
    first_beg = None
    last_end = None

    for installation in installations.order_by("start_date", "id"):
        for occupancy in occupancies:
            if occupancy.unit_id != installation.unit_id:
                continue
            installation_window = _overlap(
                installation.start_date,
                installation.end_date,
                period_start,
                period_end,
            )
            occupancy_window = _overlap(
                occupancy.move_in_date,
                occupancy.move_out_date,
                period_start,
                period_end,
            )
            if not installation_window or not occupancy_window:
                continue
            segment_window = _overlap(
                installation_window[0],
                installation_window[1],
                occupancy_window[0],
                occupancy_window[1],
            )
            if not segment_window:
                continue

            seg_start, seg_end = segment_window
            beg_raw, end_raw = _reading_bounds(meter, seg_start, seg_end)
            if beg_raw is None and seg_start == installation.start_date:
                beg_raw = installation.start_reading
            if end_raw is None and installation.end_date and seg_end == installation.end_date:
                end_raw = installation.end_reading

            beg = Decimal(str(beg_raw if beg_raw is not None else "0"))
            end = Decimal(str(end_raw if end_raw is not None else "0"))
            units = (end - beg) if (beg_raw is not None and end_raw is not None) else Decimal("0")
            if units < 0:
                units = Decimal("0")

            if first_beg is None:
                first_beg = beg
            last_end = end
            total_units += units
            segments.append(
                {
                    "meter": meter,
                    "unit": installation.unit,
                    "installation": installation,
                    "occupancy": occupancy,
                    "period_start": seg_start,
                    "period_end": seg_end,
                    "beg_kwh": beg,
                    "end_kwh": end,
                    "units": units,
                }
            )

    if not segments:
        beg_raw, end_raw = _reading_bounds(meter, period_start, period_end)
        first_beg = Decimal(str(beg_raw if beg_raw is not None else "0"))
        last_end = Decimal(str(end_raw if end_raw is not None else "0"))
        total_units = (
            last_end - first_beg
            if (beg_raw is not None and end_raw is not None)
            else Decimal("0")
        )
        if total_units < 0:
            total_units = Decimal("0")

    beg = first_beg if first_beg is not None else Decimal("0")
    end = last_end if last_end is not None else Decimal("0")

    unit_rate = _detect_unit_rate(meter, lease)
    service_charges = _detect_service_charges(meter)

    return ElectricBillContext(
        lease=lease,
        meter=meter,
        period_start=period_start,
        period_end=period_end,
        beg_kwh=beg,
        end_kwh=end,
        units=total_units,
        unit_rate=unit_rate,
        service_charges=service_charges,
        segments=segments,
    )


def billing_contexts_for_period(period_start: date, period_end: date, *, property_id=None, unit_id=None, meter_id=None):
    installations = MeterInstallation.objects.filter(
        meter__billing_mode__in=("postpaid", "credit_controlled"),
        start_date__lte=period_end,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=period_start)).select_related(
        "meter",
        "unit",
        "unit__property",
    )
    if property_id:
        installations = installations.filter(unit__property_id=property_id)
    if unit_id:
        installations = installations.filter(unit_id=unit_id)
    if meter_id:
        installations = installations.filter(meter_id=meter_id)

    seen = set()
    contexts = []
    for installation in installations.order_by("unit__property__property_name", "unit__unit_number", "meter__meter_number"):
        occupancies = LeaseUnitOccupancy.objects.filter(
            unit=installation.unit,
            move_in_date__lte=period_end,
        ).filter(Q(move_out_date__isnull=True) | Q(move_out_date__gte=period_start)).select_related("lease")
        for occupancy in occupancies:
            lease = occupancy.lease
            key = (lease.pk, installation.meter_id)
            if key in seen:
                continue
            seen.add(key)
            ctx = compute_electric_bill(lease, installation.meter, period_start, period_end)
            if ctx.segments:
                contexts.append(ctx)
    return contexts


def _next_month_start(d: date) -> date:
    return date(d.year + (1 if d.month == 12 else 0),
                1 if d.month == 12 else d.month + 1, 1)


@transaction.atomic
def upsert_invoice_with_electric_item(ctx, *, item_category_id: int = 7, posting_month: date | None = None) -> Invoice:
    """
    Accepts ElectricBillContext **or** any object with:
      lease, meter, period_start, period_end, beg_kwh, end_kwh, units, unit_rate, service_charges
    """
    lease = ctx.lease
    meter = ctx.meter
    period_start = ctx.period_start
    period_end = ctx.period_end

    # Normalize computed values in case ctx is a SimpleNamespace
    units = Decimal(str(getattr(ctx, "units")))
    unit_rate = Decimal(str(getattr(ctx, "unit_rate")))
    service_charges = Decimal(str(getattr(ctx, "service_charges")))
    usage_amount = (units * unit_rate).quantize(Decimal("0.01"))
    line_total = (usage_amount + service_charges).quantize(Decimal("0.01"))

    billing_period_label = getattr(ctx, "billing_period_label", None)
    if not billing_period_label:
        billing_period_label = _billing_period_label(period_start, period_end)

    description_text = getattr(ctx, "description_text", None)
    if not description_text:
        description_text = _trim_desc(
            f"Meter#={meter.meter_number}, "
            f"Billing Period={billing_period_label}, "
            f"Beg Unit={ctx.beg_kwh} - end unit={ctx.end_kwh}, "
            f"unit consume={units}, unit rate={unit_rate}="
            f"total usage={usage_amount}, service charges={service_charges}. "
            f"total={line_total}."
        )

    # Find an invoice for the posting month; default keeps the legacy smart-meter behavior.
    month_start = ctx.period_start.replace(day=1)
    posting_month = (posting_month or _next_month_start(month_start)).replace(day=1)
    month_end = period_end

    inv = (Invoice.objects
           .filter(lease=lease, issue_date__year=posting_month.year,
                   issue_date__month=posting_month.month)
           .order_by("issue_date").first())
    if not inv:
        # due date ~10th of posting month (or last day if shorter)
        dd = min(10, monthrange(posting_month.year, posting_month.month)[1])
        inv = Invoice(
            lease=lease,
            issue_date=posting_month,
            due_date=posting_month.replace(day=dd),
            status="draft",
            description=f"Monthly invoice for {posting_month:%b %Y}",
        )
        inv.save()

    category = ItemCategory.objects.get(pk=item_category_id)

    existing = inv.items.filter(
        category=category,
        description__icontains=f"Meter#={meter.meter_number}",
    ).filter(description__icontains=f"Billing Period={billing_period_label}").first()

    if existing:
        existing.description = description_text
        existing.amount = line_total
        existing.save()
    else:
        InvoiceItem.objects.create(
            invoice=inv,
            category=category,
            description=description_text,
            amount=line_total,
            is_recurring=False,
        )

    # Sync invoice cached amount field
    inv.amount = sum((li.amount for li in inv.items.all()), Decimal("0"))
    inv.save(update_fields=["amount", "updated_at"])

    return inv
