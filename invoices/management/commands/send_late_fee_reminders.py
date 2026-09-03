from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import GlobalSettings
from core.scheduling import format_scheduler_time, scheduler_time_is_due
from invoices.historical_units import prepare_historical_invoice_units
from invoices.late_fees import run_due_late_fee_reminders
from invoices.models import Invoice, InvoiceLateFeeReminder


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
        parser.add_argument(
            "--scheduled",
            action="store_true",
            help="Run only during the late-fee reminder time configured in Settings.",
        )
        parser.add_argument(
            "--list-excluded",
            action="store_true",
            help=(
                "List overdue invoices older than the automation start date, "
                "without sending reminders or applying fees."
            ),
        )

    def _list_excluded_invoices(self):
        settings_obj = GlobalSettings.get_solo()
        start_date = settings_obj.late_fee_automation_start_date
        if not start_date:
            self.stdout.write("No late-fee automation start date is configured.")
            return

        invoices = prepare_historical_invoice_units(
            Invoice.objects
            .exclude(status__in=["paid", "cancelled"])
            .filter(
                amount__gt=0,
                due_date__lte=timezone.localdate(),
                due_date__lt=start_date,
            )
            .select_related("lease__tenant", "lease__unit", "lease__unit__property")
            .order_by("due_date", "invoice_number")
        )
        total = invoices.count()
        self.stdout.write(
            "Invoice # | Invoice date | Due date | Property | Unit | Tenant | Amount | Status"
        )
        for invoice in invoices.iterator(chunk_size=200):
            lease = invoice.lease
            tenant = lease.tenant
            unit = invoice.historical_unit
            self.stdout.write(
                f"{invoice.invoice_number} | {invoice.issue_date} | {invoice.due_date} | "
                f"{unit.property.property_name} | {unit.unit_number} | "
                f"{tenant.get_full_name()} | {invoice.amount} | {invoice.status}"
            )
        self.stdout.write(self.style.SUCCESS(f"Total excluded: {total}"))

    def handle(self, *args, **options):
        if options["list_excluded"]:
            self._list_excluded_invoices()
            return
        if options["scheduled"] and not options["dry_run"]:
            settings_obj = GlobalSettings.get_solo()
            if not scheduler_time_is_due(settings_obj.late_fee_reminder_time):
                self.stdout.write(
                    "Late-fee scheduler skipped. Configured Pakistan time: "
                    f"{format_scheduler_time(settings_obj.late_fee_reminder_time)}."
                )
                return
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
        if summary.get("automation_start_date"):
            self.stdout.write(
                "Automation start date: "
                f"{summary['automation_start_date']}. "
                f"Older overdue invoices excluded: {summary['excluded_before_start']}."
            )
        self.stdout.write(
            "Excluded by safety rules: "
            f"zero amount={summary.get('excluded_zero_amount', 0)}, "
            f"current month={summary.get('excluded_current_month', 0)}, "
            f"temporary hold={summary.get('excluded_on_hold', 0)}."
        )
        for detail in summary["details"]:
            prefix = "[dry-run] " if options["dry_run"] else ""
            message = (
                f"{prefix}Invoice #{detail['invoice_number']} | "
                f"{detail['property_name']} | Unit {detail['unit_name']} | "
                f"Due {detail['due_date']}: "
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
        if summary.get("staff_summary_sent"):
            self.stdout.write(self.style.SUCCESS("Accounts staff WhatsApp summary sent."))
        elif summary.get("staff_summary_error"):
            self.stdout.write(self.style.WARNING(
                f"Accounts staff WhatsApp summary failed: {summary['staff_summary_error']}"
            ))
