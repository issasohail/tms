from django.core.management.base import BaseCommand

from invoices.services_iesco_reminders import run_due_iesco_reminders


class Command(BaseCommand):
    help = "Send one Meta WhatsApp reminder for each unpaid positive IESCO bill that is due."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List due reminders without sending WhatsApp messages.",
        )

    def handle(self, *args, **options):
        summary = run_due_iesco_reminders(dry_run=options["dry_run"])
        for detail in summary["details"]:
            prefix = "[dry-run] " if options["dry_run"] else ""
            status = "ready" if detail["ok"] else f"skipped: {detail['reason']}"
            self.stdout.write(
                f"{prefix}{detail['reference_no']} | {detail['bill_month']} | "
                f"due {detail['due_date']} | {detail['recipient']} "
                f"{detail['phone'] or 'no phone'} | {status}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"IESCO reminders complete. Due: {summary['due']}. "
                f"Ready: {summary['ready']}. Sent: {summary['sent']}. "
                f"Failed/skipped: {summary['failed']}."
            )
        )
