from django.core.management.base import BaseCommand

from invoices.services import parse_billing_month, run_monthly_billing_preflight


class Command(BaseCommand):
    help = "Run safe monthly billing preflight checks for a selected billing month."

    def add_arguments(self, parser):
        parser.add_argument("--month", help="Billing month in YYYY-MM format. Defaults to previous month on the 1st.")
        parser.add_argument("--dry-run", action="store_true", help="Inspect only; do not create a billing run.")
        parser.add_argument("--created-by", default="system", help="Audit label for system-created runs.")

    def handle(self, *args, **options):
        billing_month = parse_billing_month(options.get("month"))
        result = run_monthly_billing_preflight(
            billing_month,
            created_by_label=options.get("created_by") or "system",
            dry_run=options["dry_run"],
        )
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN {billing_month:%Y-%m}: active={result['active_leases']} missing_recurring={result['missing_recurring']}"
            ))
            return
        self.stdout.write(self.style.SUCCESS(f"Preflight run #{result.pk} completed for {billing_month:%Y-%m}."))
