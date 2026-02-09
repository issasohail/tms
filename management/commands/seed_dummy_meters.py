# smart_meter/management/commands/seed_dummy_meters.py
import random
from datetime import datetime, date, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from properties.models import Unit
from smart_meter.models import Meter, MeterReading


UNIT_POOL = list(range(86, 104)) + [74, 75, 76, 36, 32, 27, 21]  # IDs provided


def aware(dt: datetime):
    tz = timezone.get_current_timezone()
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, tz)
    return dt.astimezone(tz)


def month_end(d: date) -> date:
    # last day of month
    next_month = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month - timedelta(days=1)


class Command(BaseCommand):
    help = "Seed ~20 meters and synthetic readings: 60 daily days + 8 monthly snapshots. Names = meter numbers."

    def add_arguments(self, parser):
        parser.add_argument("--meters", type=int, default=20,
                            help="How many meters to create (default 20)")
        parser.add_argument("--days", type=int, default=60,
                            help="How many daily days to generate (default 60)")
        parser.add_argument("--months", type=int, default=8,
                            help="How many trailing months to add snapshot for (default 8)")

    @transaction.atomic
    def handle(self, *args, **opts):
        want = int(opts["meters"])
        days = int(opts["days"])
        months = int(opts["months"])

        # Pick Units from the pool that actually exist and aren't already bound to meters
        units_available = (Unit.objects
                           .filter(id__in=UNIT_POOL)
                           .exclude(meter__isnull=False)
                           .order_by("id"))
        units = list(units_available[:want])
        if not units:
            self.stdout.write(self.style.WARNING(
                "No free Units in the provided pool."))
            return
        if len(units) < want:
            self.stdout.write(self.style.WARNING(
                f"Only {len(units)} free Units found; will create that many meters."))

        # Create meters
        created_meters = []
        for u in units:
            # 12-digit random number as a string (no leading zero issues)
            meter_no = "".join(str(random.randint(0, 9)) for _ in range(12))
            m = Meter(unit=u,
                      meter_number=meter_no,
                      name=meter_no,
                      power_status="on",
                      unit_rate=Decimal("50.00"),
                      min_balance_alert=Decimal("100.00"),
                      min_balance_cutoff=Decimal("0.00"),
                      is_active=True)
            m.save()
            created_meters.append(m)
        self.stdout.write(self.style.SUCCESS(
            f"Created {len(created_meters)} meters."))

        # Build readings
        now = timezone.now()
        today = now.date()
        start_daily = today - timedelta(days=days - 1)  # inclusive

        for m in created_meters:
            total = Decimal(str(random.uniform(800, 1600))).quantize(
                Decimal("0.001"))  # starting energy

            # 1) Monthly end snapshots for the past `months` months (older to newer, up to last month)
            # last day of previous month
            month_cursor = (today.replace(day=1) - timedelta(days=1))
            month_ends = []
            for _ in range(months):
                month_ends.append(month_cursor)
                # go to end of previous month
                month_cursor = (month_cursor.replace(
                    day=1) - timedelta(days=1))
            for md in reversed(month_ends):
                # random month increment 120–420 kWh
                inc = Decimal(str(random.uniform(120, 420))
                              ).quantize(Decimal("0.001"))
                total += inc
                ts = aware(datetime.combine(md, time(hour=23, minute=55)))
                MeterReading.objects.create(
                    meter=m, ts=ts,
                    total_energy=total,
                    voltage_a=Decimal("220.0"),
                    current_a=Decimal("1.500"),
                )

            # 2) Daily snapshots for last `days` days at 12:00 (noon), increasing total
            day = start_daily
            while day <= today:
                inc = Decimal(str(random.uniform(4, 20))
                              ).quantize(Decimal("0.001"))
                total += inc
                ts = aware(datetime.combine(day, time(hour=12, minute=0)))
                # small variation for voltage/current
                v = Decimal(str(random.uniform(215, 235))
                            ).quantize(Decimal("0.1"))
                c = Decimal(str(random.uniform(0.5, 6.0))
                            ).quantize(Decimal("0.001"))

                MeterReading.objects.create(
                    meter=m, ts=ts,
                    total_energy=total,
                    voltage_a=v,
                    current_a=c,
                )
                day += timedelta(days=1)

            # 3) Optional: a light set of hourly points for the last 7 days to make "hourly" useful
            #    (one snapshot per hour at :50, with day/evening peaks)
            for d in range(7, 0, -1):
                the_day = today - timedelta(days=d-1)
                for hh in range(0, 24):
                    # small hourly increments (heavier usage 09–11 and 18–22)
                    base = 0.25
                    if 9 <= hh <= 11:
                        base = 0.8
                    if 18 <= hh <= 22:
                        base = 1.2
                    jitter = random.uniform(0.0, 0.3)
                    inc_h = Decimal(str(base + jitter)
                                    ).quantize(Decimal("0.001"))
                    total += inc_h
                    ts = aware(datetime.combine(
                        the_day, time(hour=hh, minute=50)))
                    v = Decimal(str(random.uniform(215, 235))
                                ).quantize(Decimal("0.1"))
                    c = Decimal(str(random.uniform(0.3, 10.0))
                                ).quantize(Decimal("0.001"))
                    MeterReading.objects.create(
                        meter=m, ts=ts,
                        total_energy=total,
                        voltage_a=v, current_a=c,
                    )

        self.stdout.write(self.style.SUCCESS("Dummy readings generated."))
