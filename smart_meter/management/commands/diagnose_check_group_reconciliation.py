from datetime import date

from django.core.management.base import BaseCommand, CommandError

from smart_meter.models import MeterCheckGroup
from smart_meter.services.reconciliation import calculate_check_group_period


class Command(BaseCommand):
    help = "Read-only local diagnostic for real Check Groups 1, 2, and 5 (when present)."

    def add_arguments(self, parser):
        parser.add_argument("--start", required=True, help="YYYY-MM-DD")
        parser.add_argument("--end", required=True, help="YYYY-MM-DD")

    def handle(self, *args, **options):
        try:
            start = date.fromisoformat(options["start"])
            end = date.fromisoformat(options["end"])
        except ValueError as exc:
            raise CommandError("Dates must use YYYY-MM-DD.") from exc
        if end < start:
            raise CommandError("End date cannot be before start date.")
        for pk in (1, 2, 5):
            group = MeterCheckGroup.objects.filter(pk=pk).select_related("check_meter").first()
            if not group:
                self.stdout.write(f"Check Group {pk}: not present (skipped)")
                continue
            result = calculate_check_group_period(group, start, end)
            self.stdout.write(
                f"Check Group {pk} ({group.name}): output={result['check_kwh']} "
                f"billing={result['billing_kwh']} variance={result['variance_kwh']} "
                f"leakage={result['leakage_percent']}%"
            )
