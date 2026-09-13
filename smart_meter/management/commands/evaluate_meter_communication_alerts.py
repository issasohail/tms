from django.core.management.base import BaseCommand, CommandError

from smart_meter.services.communication_alerts import (
    communication_alert_threshold_minutes,
    evaluate_meter_communication_alerts,
)


class Command(BaseCommand):
    help = "Create and resolve persistent smart-meter communication-loss alerts."

    def add_arguments(self, parser):
        parser.add_argument("--meter", dest="meter_number")
        parser.add_argument("--threshold-minutes", type=int)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        threshold = options["threshold_minutes"]
        if threshold is None:
            threshold = communication_alert_threshold_minutes()
        if threshold < 1:
            raise CommandError("--threshold-minutes must be at least 1")

        result = evaluate_meter_communication_alerts(
            threshold_minutes=threshold,
            meter_number=options.get("meter_number"),
            dry_run=options["dry_run"],
        )
        prefix = "DRY RUN " if result["dry_run"] else ""
        self.stdout.write(
            f"{prefix}checked={result['checked']} created={result['created']} "
            f"resolved={result['resolved']} still_open={result['still_open']} "
            f"threshold={threshold}m"
        )
        for action, number in result["actions"]:
            self.stdout.write(f"{action.upper()} {number}")
