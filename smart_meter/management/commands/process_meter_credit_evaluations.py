from django.core.management.base import BaseCommand
from django.db.models import Q
from smart_meter.models import MeterEvaluationRequest, MeterCreditAccount
from smart_meter.services.credit_control import evaluate_credit_account, process_evaluation_request


class Command(BaseCommand):
    help = "Process debounced smart-meter credit evaluation requests."

    def add_arguments(self, parser):
        parser.add_argument("--meter-id", type=int)
        parser.add_argument("--account-id", type=int)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--retry-failed", action="store_true")

    def handle(self, *args, **options):
        if options["account_id"]:
            result = evaluate_credit_account(options["account_id"], dry_run=options["dry_run"], source="scheduled")
            self.stdout.write(str(result))
            return
        statuses = ["pending"] + (["failed"] if options["retry_failed"] else [])
        qs = MeterEvaluationRequest.objects.filter(status__in=statuses).order_by("created_at")
        if options["meter_id"]:
            qs = qs.filter(meter_id=options["meter_id"])
        count = 0
        for req in qs[: options["limit"]]:
            try:
                result = process_evaluation_request(req.pk, dry_run=options["dry_run"])
                self.stdout.write(f"request={req.pk} result={result}")
            except Exception as exc:
                self.stderr.write(f"request={req.pk} failed: {exc}")
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Processed {count} evaluation request(s)."))
