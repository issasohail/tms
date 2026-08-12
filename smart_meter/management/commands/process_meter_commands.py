from django.core.management.base import BaseCommand
from django.utils import timezone
from smart_meter.models import MeterCommand
from smart_meter.services.command_lifecycle import revalidate_command


class Command(BaseCommand):
    help = "Revalidate/consolidate queued meter commands; the live listener performs socket dispatch."

    def add_arguments(self, parser):
        parser.add_argument("--meter-id", type=int)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--retry", action="store_true")

    def handle(self, *args, **opts):
        statuses = ["pending", "waiting_online", "retry", "new"]
        qs = MeterCommand.objects.filter(status__in=statuses).order_by("priority", "created_at")
        if opts["meter_id"]:
            qs = qs.filter(meter_id=opts["meter_id"])
        for cmd in qs[: opts["limit"]]:
            result = revalidate_command(cmd)
            if not result.allowed:
                self.stdout.write(f"cancel command={cmd.pk}: {result.reason}")
                if not opts["dry_run"]:
                    MeterCommand.objects.filter(pk=cmd.pk).update(status="cancelled", cancelled_at=timezone.now(), cancelled_reason=result.reason[:255])
            elif opts["retry"] and cmd.status in {"retry", "waiting_online"}:
                self.stdout.write(f"wake command={cmd.pk}")
                if not opts["dry_run"]:
                    MeterCommand.objects.filter(pk=cmd.pk).update(status="pending", next_attempt_at=None)
            else:
                self.stdout.write(f"valid command={cmd.pk}: {result.reason}")
