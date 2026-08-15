from django.core.management.base import BaseCommand
from invoices.late_fees import run_due_late_fee_reminders
from invoices.models import InvoiceLateFeeReminder


class Command(BaseCommand):
    help = "Send due WhatsApp late fee reminders and apply or queue reminder-based late fees."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even when automatic late fee reminders are disabled in settings.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show invoices that would receive a reminder without sending WhatsApp messages or fees.",
        )

    def handle(self, *args, **options):
        source = (
            InvoiceLateFeeReminder.SOURCE_MANUAL
            if options["force"]
            else InvoiceLateFeeReminder.SOURCE_AUTO
        )
        summary = run_due_late_fee_reminders(
            source=source,
            dry_run=options["dry_run"],
        )
        if summary.get("reason"):
            self.stdout.write(self.style.WARNING(summary["reason"]))
        for detail in summary["details"]:
            prefix = "[dry-run] " if options["dry_run"] else ""
            message = (
                f"{prefix}Invoice #{detail['invoice_number']}: "
                f"reminder #{detail['reminder_number']}"
            )
            if detail.get("error"):
                message += f" failed: {detail['error']}"
            elif options["dry_run"]:
                message += " would be processed."
            self.stdout.write(message)
        self.stdout.write(self.style.SUCCESS(
            "Late fee reminders complete. "
            f"Examined: {summary['examined']}. Due: {summary['due']}. "
            f"Processed: {summary['processed']}. Fees applied: {summary['fees_applied']}. "
            f"Fees pending: {summary['fees_pending']}. Failed: {summary['failed']}. "
            f"Skipped: {summary['skipped']}."
        ))
