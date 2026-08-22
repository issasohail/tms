from django.core.management.base import BaseCommand
from smart_meter.services.timing_schedule import enforce_all_timing_schedules


class Command(BaseCommand):
    help = "Evaluate all recurring meter timing schedules and queue required relay commands."

    def handle(self, *args, **options):
        queued = enforce_all_timing_schedules()
        self.stdout.write(self.style.SUCCESS(f"Queued {len(queued)} timing command(s)."))
        for cmd in queued:
            self.stdout.write(f"meter={cmd.meter_number} command={cmd.pk} desired={cmd.desired_state}")
