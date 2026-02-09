# management/commands/post_recurring_charges.py
from django.core.management.base import BaseCommand
from django.utils import timezone
# adjust path as needed
from invoices.models import RecurringCharge, Invoice, InvoiceItem


class Command(BaseCommand):
    help = "Posts recurring charges as invoices/items (runs daily)."

    def handle(self, *args, **kwargs):
        today = timezone.localdate()
        # Only post on the day-of-month that matches
        due = RecurringCharge.objects.filter(
            active=True).select_related('lease', 'category')
        due = [rc for rc in due if rc.is_due_on(today)]

        for rc in due:
            inv = Invoice.objects.create(
                lease=rc.lease,
                issue_date=today,
                due_date=today,  # or lease default terms
                amount=0,  # will be recalculated by signals
                status='sent',  # or 'draft' if you prefer review first
                description=f"Auto recurring charges for {today:%b %Y}",
            )
            InvoiceItem.objects.create(
                invoice=inv,
                category=rc.lease.default_categoryif hasattr(
                    rc.lease, 'default_category') else ChargeType.objects.first(),
                category=rc.category,
                description=rc.description or f"{rc.category.name} (recurring)",
                amount=rc.amount,
                is_recurring=True,
            )
        self.stdout.write(self.style.SUCCESS(
            f"Posted {len(due)} recurring charges for {today}"))
