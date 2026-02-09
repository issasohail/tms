from django.core.management.base import BaseCommand
from django.utils import timezone
from tenants.models import Tenant
from notifications.utils import send_balance_notification

class Command(BaseCommand):
    help = 'Sends balance reminders to tenants with overdue invoices'

    def handle(self, *args, **options):
        today = timezone.now().date()
        tenants = Tenant.objects.filter(
            is_active=True,
            invoices__due_date__lt=today,
            invoices__status__in=['unpaid', 'partially_paid']
        ).distinct()
        
        notifications_sent = 0
        for tenant in tenants:
            if tenant.current_balance > 0:
                send_balance_notification(tenant)
                notifications_sent += 1
        
        self.stdout.write(self.style.SUCCESS(f'Sent {notifications_sent} balance reminders.'))