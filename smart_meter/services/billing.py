from datetime import datetime, timedelta
from decimal import Decimal
from django.db.models import Min, Max
from django.utils import timezone
from ..models import Meter, MeterReading, Bill, Tariff

def _first_last_energy(meter: Meter, start, end):
    qs = MeterReading.objects.filter(meter=meter, ts__gte=start, ts__lte=end)
    first = qs.order_by('ts').first()
    last  = qs.order_by('-ts').first()
    if not first or not last or first.total_energy is None or last.total_energy is None:
        return None, None
    return Decimal(first.total_energy), Decimal(last.total_energy)

def generate_bill_for_unit(unit, period_start, period_end, tariff: Tariff = None) -> Bill:
    meter = unit.meter
    if not meter:
        raise ValueError("Unit has no meter")

    t = tariff or Tariff.objects.filter(active=True).first()
    if not t:
        raise ValueError("No active tariff configured")

    opening, closing = _first_last_energy(meter, period_start, period_end)
    if opening is None or closing is None:
        raise ValueError("Insufficient readings for the selected period")

    units = max(Decimal('0.000'), closing - opening)  # guard negative due to rollovers/resets
    amount = (units * t.rate_per_kwh).quantize(Decimal('0.01'))

    bill = Bill.objects.create(
        unit=unit,
        meter=meter,
        period_start=period_start,
        period_end=period_end,
        opening_kwh=opening,
        closing_kwh=closing,
        units_consumed=units,
        rate_per_kwh=t.rate_per_kwh,
        amount_due=amount,
    )
    return bill
