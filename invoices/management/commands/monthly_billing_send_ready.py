from django.core.management.base import BaseCommand, CommandError
from django.db.utils import ProgrammingError

from invoices.models import MonthlyBillingRun
from invoices.services import (
    generate_monthly_billing_pdfs,
    get_or_create_monthly_billing_run,
    parse_billing_month,
    send_monthly_billing_ready,
)


class Command(BaseCommand):
    help = "Generate PDFs and send Ready to Send monthly invoices through WhatsApp."

    def add_arguments(self, parser):
        parser.add_argument("--month", help="Billing month in YYYY-MM format. Defaults to previous month on the 1st.")
        parser.add_argument("--dry-run", action="store_true", help="Show ready count without generating PDFs or sending.")
        parser.add_argument("--send", action="store_true", help="Actually send WhatsApp invoices. Required unless --dry-run is used.")
        parser.add_argument("--retry", action="store_true", help="Retry Failed rows instead of normal Ready rows.")
        parser.add_argument("--created-by", default="system", help="Audit label for system-created sends.")

    def handle(self, *args, **options):
        billing_month = parse_billing_month(options.get("month"))
        if options["dry_run"]:
            try:
                run = MonthlyBillingRun.objects.filter(billing_month=billing_month).first()
            except ProgrammingError as exc:
                raise CommandError("Monthly billing tables are not available. Run migrations before send dry-run.") from exc
            ready = run.items.filter(status="ready_to_send").count() if run else 0
            failed = run.items.filter(status="failed").count() if run else 0
            self.stdout.write(self.style.WARNING(f"DRY RUN {billing_month:%Y-%m}: ready={ready}, failed={failed}"))
            return
        if not options["send"]:
            raise CommandError("Refusing to send without --send. Use --dry-run to inspect safely.")

        run = get_or_create_monthly_billing_run(billing_month, created_by_label=options["created_by"])
        generate_monthly_billing_pdfs(run)
        send_monthly_billing_ready(run, retry_failed=options["retry"])
        run = MonthlyBillingRun.objects.get(pk=run.pk)
        self.stdout.write(self.style.SUCCESS(
            f"Send completed for run #{run.pk}: sent={run.sent_count}, failed={run.failed_count}, pending={run.pending_attention_count}."
        ))
