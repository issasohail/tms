# leases/management/commands/cleanup_agreements.py
from django.core.management.base import BaseCommand
from django.core.files.storage import default_storage
from leases.models import Lease
import os

class Command(BaseCommand):
    help = 'Cleans up orphaned agreement files'
    
    def handle(self, *args, **options):
        # Get all files in agreements directory
        agreements_dir = 'agreements/'
        existing_files = set()
        
        # Collect all valid file paths from leases
        for lease in Lease.objects.exclude(signed_agreement=''):
            if lease.signed_agreement:
                existing_files.add(lease.signed_agreement.name)
        
        # List all files in storage
        all_files = default_storage.listdir(agreements_dir)[1]
        
        # Delete orphaned files
        deleted_count = 0
        for filename in all_files:
            full_path = os.path.join(agreements_dir, filename)
            if full_path not in existing_files:
                default_storage.delete(full_path)
                deleted_count += 1
                self.stdout.write(f"Deleted orphaned file: {full_path}")
        
        self.stdout.write(f"Deleted {deleted_count} orphaned files")