from django.core.management.base import BaseCommand

from invoices.services import (
    generate_monthly_billing_electric,
    generate_monthly_billing_invoices,
    get_or_create_monthly_billing_run,
    parse_billing_month,
    prepare_monthly_billing_ready,
    run_monthly_billing_preflight,
)


class Command(BaseCommand):
    help = "Generate monthly recurring invoices and electric billing checks safely."

    def add_arguments(self, parser):
        parser.add_argument("--month", help="Billing month in YYYY-MM format. Defaults to previous month on the 1st.")
        parser.add_argument("--dry-run", action="store_true", help="Inspect only; do not create invoices or charges.")
        parser.add_argument("--created-by", default="system", help="Audit label for system-created runs.")

    def handle(self, *args, **options):
        billing_month = parse_billing_month(options.get("month"))
        if options["dry_run"]:
            result = run_monthly_billing_preflight(billing_month, created_by_label=options["created_by"], dry_run=True)
            self.stdout.write(self.style.WARNING(
                f"DRY RUN {billing_month:%Y-%m}: active={result['active_leases']} missing_recurring={result['missing_recurring']}"
            ))
            return

        run = run_monthly_billing_preflight(billing_month, created_by_label=options["created_by"])
        generate_monthly_billing_invoices(run)
        generate_monthly_billing_electric(run)
        prepare_monthly_billing_ready(run)
        run = get_or_create_monthly_billing_run(billing_month, created_by_label=options["created_by"])
        self.stdout.write(self.style.SUCCESS(
            f"Generation completed for run #{run.pk}: ready={run.ready_to_send_count}, pending={run.pending_attention_count}, failed={run.failed_count}."
        ))
