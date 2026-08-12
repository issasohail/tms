from django.core.management.base import BaseCommand, CommandError
from smart_meter.services.prepaid_pilot import read_supported_prepaid_snapshot, PrepaidProtocolSafetyError


class Command(BaseCommand):
    help = "Read/store the safely supported prepaid pilot values for one explicit meter."

    def add_arguments(self, parser):
        parser.add_argument("--meter-id", type=int, required=True)

    def handle(self, *args, **opts):
        try:
            rows = read_supported_prepaid_snapshot(opts["meter_id"])
        except PrepaidProtocolSafetyError as exc:
            raise CommandError(str(exc))
        for row in rows:
            self.stdout.write(f"{row.parameter}: {row.parsed_value or row.parse_status} {row.unit}".strip())
