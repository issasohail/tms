from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import GlobalSettings
from invoices.late_fees import collect_due_invoices, get_due_reminder_number, process_invoice_late_fee_reminder
from invoices.models import InvoiceLateFeeReminder
from leases.models_late_fee import get_effective_late_fee_settings


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
        settings_obj = GlobalSettings.get_solo()
        if not settings_obj.late_fee_enabled:
            self.stdout.write(self.style.WARNING("Late fees are disabled. Nothing to do."))
            return
        if not settings_obj.late_fee_auto_send_reminders and not options["force"]:
            self.stdout.write(self.style.WARNING(
                "Automatic late fee reminders are off. Turn them on in Settings or use --force."
            ))
            return

        today = timezone.localdate()
        sent = failed = skipped = 0
        for invoice in collect_due_invoices(today=today):
            cfg = get_effective_late_fee_settings(invoice.lease)
            reminder_number = get_due_reminder_number(invoice, cfg, today=today)
            if reminder_number is None:
                skipped += 1
                continue

            if options["dry_run"]:
                self.stdout.write(
                    f"[dry-run] Invoice #{invoice.invoice_number}: reminder #{reminder_number} is due."
                )
                sent += 1
                continue

            result = process_invoice_late_fee_reminder(
                invoice,
                sent_via=InvoiceLateFeeReminder.SOURCE_AUTO,
                user=None,
            )
            if result.get("ok"):
                sent += 1
            else:
                failed += 1
                self.stdout.write(self.style.WARNING(
                    f"Invoice #{invoice.invoice_number}: {result.get('reason') or 'failed'}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"Late fee reminders complete. Sent: {sent}. Failed: {failed}. Skipped: {skipped}."
        ))
