from django.core.management.base import BaseCommand

from invoices.services import run_scheduled_monthly_billing


class Command(BaseCommand):
    help = "Run monthly billing when the configured monthly billing date is due."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the current period without creating invoices or sending messages.",
        )

    def handle(self, *args, **options):
        result = run_scheduled_monthly_billing(dry_run=options["dry_run"])
        self.stdout.write("Scheduled billing run")
        self.stdout.write(f"Period: {result['billing_month']}")
        self.stdout.write(f"Scheduled date: {result['scheduled_date']}")
        if result.get("reason"):
            self.stdout.write(self.style.WARNING(result["reason"]))
            return
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(
                "DRY RUN: "
                f"eligible leases={result.get('active_leases', 0)}, "
                f"would send={result.get('would_send', 0)}, "
                f"already billed={int(bool(result.get('already_billed')))}."
            ))
            return
        self.stdout.write(self.style.SUCCESS(
            f"Eligible leases: {result.get('eligible_leases', 0)}. "
            f"Created: {result.get('created', 0)}. "
            f"Already billed: {result.get('already_billed', 0)}. "
            f"Failed: {result.get('failed', 0)}."
        ))
