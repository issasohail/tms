from datetime import datetime, date
from django.core.management.base import BaseCommand, CommandError
from properties.models import Unit
from smart_meter.services.billing import generate_bill_for_unit

class Command(BaseCommand):
    help = "Generate bills for a given month (YYYY-MM) for all units with meters."

    def add_arguments(self, parser):
        parser.add_argument("--month", required=True, help="YYYY-MM")

    def handle(self, *args, **opts):
        try:
            y, m = map(int, opts["month"].split("-"))
            period_start = date(y, m, 1)
            # simple month end
            if m == 12:
                period_end = date(y, 12, 31)
            else:
                from calendar import monthrange
                period_end = date(y, m, monthrange(y, m)[1])
        except Exception:
            raise CommandError("Invalid --month. Use YYYY-MM.")

        count_ok = 0
        for unit in Unit.objects.all():
            meter = getattr(unit, "meter", None)
            if not meter:
                continue
            try:
                generate_bill_for_unit(unit, period_start, period_end)
                count_ok += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"{unit}: skipped ({e})"))
        self.stdout.write(self.style.SUCCESS(f"✅ Bills created for {count_ok} units"))
