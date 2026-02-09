# smart_meter/management/commands/seed_dummy_readings.py
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models.signals import post_save
from django.utils import timezone
from decimal import Decimal
import random
from datetime import timedelta, datetime

from smart_meter.models import Meter, MeterReading
from smart_meter.models import deduct_balance_on_reading  # your post_save signal


class Command(BaseCommand):
    help = "Seed HOURLY readings for last N days + month-end anchors for last M months"

    def add_arguments(self, parser):
        parser.add_argument("--meter-ids", default="",
                            help="Comma sep IDs, blank=all meters")
        parser.add_argument("--days", type=int, default=60,
                            help="How many days of hourly data")
        parser.add_argument("--months", type=int, default=8,
                            help="How many month-end anchors")

    @transaction.atomic
    def handle(self, *args, **opts):
        # Choose meters
        ids = [s.strip()
               for s in (opts["meter_ids"] or "").split(",") if s.strip()]
        if ids:
            meters = Meter.objects.filter(id__in=ids).order_by("id")
        else:
            meters = Meter.objects.all().order_by("id")

        if not meters.exists():
            self.stdout.write(self.style.WARNING("No meters found."))
            return

        # Temporarily disconnect balance/cutoff signal to avoid socket spam
        post_save.disconnect(deduct_balance_on_reading, sender=MeterReading)
        try:
            now = timezone.now()
            start_day = (now - timedelta(days=opts["days"])).date()

            for m in meters:
                # Start baseline from latest snapshot if present (keeps monotonic)
                latest = (MeterReading.objects
                          .filter(meter=m)
                          .order_by("-ts")
                          .values_list("total_energy", flat=True)
                          .first())
                base_kwh = Decimal(latest or 0)
                if base_kwh == 0:
                    base_kwh = Decimal(random.randint(
                        100, 800))  # starting offset

                bulk = []

                # --- Month-end anchors (older → newer) ---
                # place exactly one reading on the last day of each previous month (00:00)
                cur = now
                for i in range(1, opts["months"] + 1):
                    # jump to previous month end
                    first_of_month = (cur.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                                      - timedelta(days=1))
                    ts = first_of_month.replace(
                        hour=0, minute=0, second=0, microsecond=0)
                    # small backward step so anchors are below current baseline
                    step = Decimal("5.0") * (opts["months"] - i + 1)
                    anchor_kwh = max(base_kwh - step, Decimal("0"))
                    bulk.append(MeterReading(
                        meter=m, ts=ts,
                        total_energy=anchor_kwh,
                        voltage_a=230 + random.randint(-6, 6),
                        current_a=Decimal(random.uniform(
                            0.5, 4.0)).quantize(Decimal("0.001")),
                        total_power=Decimal(random.uniform(
                            50, 1200)).quantize(Decimal("0.001")),
                        pf_total=Decimal(random.uniform(
                            0.85, 0.99)).quantize(Decimal("0.001")),
                    ))
                    cur = first_of_month

                # --- Hourly series for last N days ---
                # Monotonic increase with random hourly increments
                day = start_day
                while day <= now.date():
                    for h in range(24):
                        # Skip future hours of the current day
                        ts = timezone.make_aware(
                            datetime(day.year, day.month, day.day, h, 0, 0))
                        if ts > now:
                            break

                        inc = Decimal(random.uniform(0.05, 1.8)
                                      ).quantize(Decimal("0.001"))
                        base_kwh += inc
                        bulk.append(MeterReading(
                            meter=m, ts=ts,
                            total_energy=base_kwh,
                            voltage_a=230 + random.randint(-6, 6),
                            current_a=Decimal(random.uniform(
                                0.3, 8.5)).quantize(Decimal("0.001")),
                            total_power=Decimal(random.uniform(
                                40, 2500)).quantize(Decimal("0.001")),
                            pf_total=Decimal(random.uniform(
                                0.80, 0.99)).quantize(Decimal("0.001")),
                        ))
                    day += timedelta(days=1)

                # bulk insert in chunks
                MeterReading.objects.bulk_create(bulk, batch_size=2000)
                self.stdout.write(self.style.SUCCESS(
                    f"Seeded {len(bulk)} rows → meter {m.id} ({m.meter_number})"))

        finally:
            # Reconnect your signal
            post_save.connect(deduct_balance_on_reading, sender=MeterReading)

        self.stdout.write(self.style.SUCCESS("✅ Done seeding readings."))
