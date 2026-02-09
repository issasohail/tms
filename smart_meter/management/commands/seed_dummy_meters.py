# smart_meter/management/commands/seed_dummy_meters.py
import random
from datetime import timedelta, datetime

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction

from properties.models import Unit
from smart_meter.models import Meter


def parse_unit_ids(expr: str):
    """
    Parses strings like "86-103,74,75,76,36,32,27,21" into a list of ints.
    Supports ranges and single ids. Ignores blanks and duplicates.
    """
    if not expr:
        return []
    out = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                a, b = int(a), int(b)
            except ValueError:
                continue
            if a > b:
                a, b = b, a
            for x in range(a, b + 1):
                out.add(x)
        else:
            try:
                out.add(int(part))
            except ValueError:
                continue
    return sorted(out)


def make_unique_meter_number(existing: set):
    """Return a unique 12-digit string not in `existing`."""
    while True:
        num = str(random.randint(10**11, 10**12 - 1))
        if num not in existing:
            return num


class Command(BaseCommand):
    help = "Create dummy meters attached to units (no readings)."

    def add_arguments(self, parser):
        parser.add_argument("--meters", type=int, default=20,
                            help="How many meters to create (default: 20)")
        parser.add_argument("--unit-ids", type=str, default="",
                            help='Units to attach meters to. Example: "86-103,74,75,76,36,32,27,21". '
                                 'If omitted, will use any units without a meter.')

    @transaction.atomic
    def handle(self, *args, **opts):
        target_count = int(opts["meters"])
        unit_expr = (opts.get("unit_ids") or "").strip()

        # Determine candidate units (must have no meter yet due to OneToOne)
        if unit_expr:
            unit_ids = parse_unit_ids(unit_expr)
            candidates = Unit.objects.filter(
                id__in=unit_ids, meter__isnull=True).order_by("id")
        else:
            candidates = Unit.objects.filter(meter__isnull=True).order_by("id")

        if not candidates.exists():
            self.stdout.write(self.style.WARNING(
                "No candidate units without a meter were found for the given selection."
            ))
            return

        # Existing meter_numbers to avoid duplicates
        existing_nums = set(Meter.objects.values_list(
            "meter_number", flat=True))

        created = []
        now = timezone.now()

        for u in candidates:
            if len(created) >= target_count:
                break

            meter_number = make_unique_meter_number(existing_nums)
            existing_nums.add(meter_number)

            installed_at = now - timedelta(days=random.randint(0, 300))

            m = Meter(
                unit=u,
                meter_number=meter_number,
                name=meter_number,               # name same as number per your spec
                power_status="on",
                unit_rate=50.00,                 # Rs. 50.00
                min_balance_alert=100.00,
                min_balance_cutoff=0.00,
                is_active=True,
                installed_at=installed_at,
                notes="Dummy seeded meter",
            )
            m.save()
            created.append(m)

        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Created {len(created)} meter(s)."))
            # Print IDs to feed into the readings seeder easily
            ids_line = ",".join(str(m.id) for m in created)
            self.stdout.write(f"New meter IDs: {ids_line}")
            self.stdout.write(
                "Next, seed readings for them:\n"
                f"  python manage.py seed_dummy_readings --meter-ids {ids_line} --days 60 --months 8"
            )
        else:
            self.stdout.write(self.style.WARNING(
                "No meters were created (not enough free units)."))
