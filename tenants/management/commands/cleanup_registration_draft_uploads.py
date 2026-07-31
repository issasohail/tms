from django.core.management.base import BaseCommand
from django.utils import timezone

from tenants.models import TemporaryRegistrationUpload
from tenants.services.registration_drafts import cleanup_expired_temporary_uploads


class Command(BaseCommand):
    help = "Delete private tenant-registration draft uploads after their 48-hour expiry."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the number of expired records without deleting files.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        expired = TemporaryRegistrationUpload.objects.filter(expires_at__lte=now)
        count = expired.count()
        if options["dry_run"]:
            self.stdout.write(f"{count} expired temporary upload(s) would be deleted.")
            return
        deleted = cleanup_expired_temporary_uploads(now=now)
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} expired temporary upload(s)."))
