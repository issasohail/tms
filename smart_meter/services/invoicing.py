from datetime import date, datetime, time
from calendar import monthrange
from decimal import Decimal

from django.db import transaction
from django.db.models import Min, Max
from django.utils import timezone

from smart_meter.models import MeterReading, Meter
from invoices.models import Invoice, InvoiceItem, ItemCategory
from leases.models import Lease


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


def _trim_desc(s: str, maxlen: int = 190) -> str:
    """InvoiceItem.description is CharField(200) in your models; keep headroom."""
    return s if len(s) <= maxlen else s[: maxlen - 1] + "…"


def _detect_unit_rate(meter: Meter, lease: Lease) -> Decimal:
    # Prefer explicit meter.unit_rate, else lease.electric_unit_rate, else 0
    rate = getattr(meter, "unit_rate", None)
    if rate is None:
        rate = getattr(lease, "electric_unit_rate", None)
    try:
        return Decimal(str(rate or "0"))
    except Exception:
        return Decimal("0")


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
                 service_charges: Decimal):
        self.lease = lease
        self.meter = meter
        self.period_start = period_start
        self.period_end = period_end
        self.beg_kwh = beg_kwh
        self.end_kwh = end_kwh
        self.units = units
        self.unit_rate = unit_rate
        self.service_charges = service_charges

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


def compute_electric_bill(lease, meter, period_start: date, period_end: date) -> ElectricBillContext:
    # Use timezone-aware bounds to avoid dropping edge readings
    sdt, ndt = _month_window_local(period_start)

    agg = (
        MeterReading.objects
        .filter(meter=meter, ts__gte=sdt, ts__lt=ndt)
        .aggregate(beg=Min("total_energy"), end=Max("total_energy"))
    )

    beg_raw = agg["beg"]
    end_raw = agg["end"]

    beg = Decimal(str(beg_raw if beg_raw is not None else "0"))
    end = Decimal(str(end_raw if end_raw is not None else "0"))

    units = (
        end - beg) if (beg_raw is not None and end_raw is not None) else Decimal("0")
    if units < 0:
        units = Decimal("0")  # guard against meter reset

    unit_rate = _detect_unit_rate(meter, lease)
    service_charges = _detect_service_charges(meter)

    return ElectricBillContext(
        lease=lease,
        meter=meter,
        period_start=period_start,
        period_end=period_end,
        beg_kwh=beg,
        end_kwh=end,
        units=units,
        unit_rate=unit_rate,
        service_charges=service_charges,
    )


def _next_month_start(d: date) -> date:
    return date(d.year + (1 if d.month == 12 else 0),
                1 if d.month == 12 else d.month + 1, 1)


@transaction.atomic
def upsert_invoice_with_electric_item(ctx, *, item_category_id: int = 7) -> Invoice:
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

    # Find an invoice for the same lease & month; else create a new one (draft)
    month_start = ctx.period_start.replace(day=1)
    posting_month = _next_month_start(
        month_start)  # <-- bill goes on next month
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
