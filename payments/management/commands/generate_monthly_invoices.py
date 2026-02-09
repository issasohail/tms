from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from tenants.models import Tenant
from payments.models import Invoice
from notifications.utils import send_balance_notification

class Command(BaseCommand):
    help = 'Generates monthly rent invoices for all active tenants'

    def handle(self, *args, **options):
        today = timezone.now().date()
        first_of_month = today.replace(day=1)
        next_month = first_of_month + timedelta(days=32)
        first_of_next_month = next_month.replace(day=1)
        
        active_tenants = Tenant.objects.filter(is_active=True, lease_end_date__gte=today)
        
        invoices_created = 0
        for tenant in active_tenants:
            # Check if invoice already exists for this month
            existing_invoice = Invoice.objects.filter(
                tenant=tenant,
                issue_date__month=first_of_next_month.month,
                issue_date__year=first_of_next_month.year,
                description__contains='Monthly Rent'
            ).exists()
            
            if not existing_invoice:
                invoice = Invoice.objects.create(
                    tenant=tenant,
                    issue_date=first_of_next_month,
                    due_date=first_of_next_month + timedelta(days=7),
                    amount=tenant.unit.monthly_rent,
                    description=f"Monthly Rent for {first_of_next_month.strftime('%B %Y')}"
                )
                invoices_created += 1
        
        self.stdout.write(self.style.SUCCESS(f'Successfully generated {invoices_created} monthly invoices.'))