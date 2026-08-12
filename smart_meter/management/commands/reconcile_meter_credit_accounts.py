from django.core.management.base import BaseCommand
from smart_meter.models import MeterCreditAccount
from smart_meter.services.credit_control import evaluate_credit_account


class Command(BaseCommand):
    help = "Recalculate enabled meter-credit accounts from current accounting and meter data."

    def add_arguments(self, parser):
        parser.add_argument("--meter-id", type=int)
        parser.add_argument("--account-id", type=int)
        parser.add_argument("--installation-id", type=int)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        qs = MeterCreditAccount.objects.filter(is_enabled=True).order_by("id")
        for key in ("meter_id", "account_id", "installation_id"):
            value = opts.get(key)
            if value:
                qs = qs.filter(**({"pk": value} if key == "account_id" else {key: value}))
        for account in qs[: opts["limit"]]:
            result = evaluate_credit_account(account.pk, dry_run=opts["dry_run"], source="scheduled")
            self.stdout.write(str(result))
