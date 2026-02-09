# management/commands/check_lease_renewals.py
from django.core.management.base import BaseCommand
from leases.models import Lease
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = 'Check for leases that need auto-renewal'

    def handle(self, *args, **options):
        leases = Lease.objects.filter(
            status='active',
            end_date__lte=timezone.now().date() + timedelta(days=30)
        )

        for lease in leases:
            if lease.should_auto_renew():
                renewed_lease = lease.auto_renew_if_needed()
                if renewed_lease:
                    self.stdout.write(
                        f"Renewed lease {lease.id} as {renewed_lease.id}")
                else:
                    self.stdout.write(f"Failed to renew lease {lease.id}")
            else:
                self.stdout.write(
                    f"Lease {lease.id} not marked for auto-renewal")
