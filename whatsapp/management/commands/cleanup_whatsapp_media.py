from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import GlobalSettings
from whatsapp.models import PendingWhatsAppMedia, PendingWhatsAppPayment


class Command(BaseCommand):
    help = "Delete stored WhatsApp pending media files older than the configured retention days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            help="Override GlobalSettings.whatsapp_media_retention_days for this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be removed without deleting files or changing records.",
        )

    def handle(self, *args, **options):
        if options["days"] is not None:
            days = options["days"]
        else:
            days = GlobalSettings.get_solo().whatsapp_media_retention_days or 90
        if days < 1:
            raise SystemExit("Retention days must be 1 or higher.")

        cutoff = timezone.now() - timedelta(days=days)
        queryset = (
            PendingWhatsAppMedia.objects.exclude(file="")
            .filter(created_at__lt=cutoff)
            .order_by("created_at")
        )

        dry_run = options["dry_run"]
        scanned = queryset.count()
        removed = 0
        missing = 0

        for media in queryset.iterator():
            file_name = media.file.name
            exists = False
            if file_name:
                try:
                    exists = media.file.storage.exists(file_name)
                except Exception:
                    exists = False

            self.stdout.write(
                f"{'[dry-run] ' if dry_run else ''}PendingWhatsAppMedia #{media.pk}: {file_name or '-'}"
            )

            if dry_run:
                continue

            if exists:
                media.file.delete(save=False)
                removed += 1
            else:
                missing += 1

            PendingWhatsAppPayment.objects.filter(screenshot=file_name).update(screenshot="")
            note = f"Media file removed by retention cleanup after {days} days on {timezone.localdate()}."
            media.file = ""
            media.ai_notes = f"{media.ai_notes}\n{note}".strip()
            media.save(update_fields=["file", "ai_notes", "updated_at"])

        action = "would be processed" if dry_run else "processed"
        self.stdout.write(
            self.style.SUCCESS(
                f"{scanned} media record(s) {action}. Deleted files: {removed}. Missing files marked clean: {missing}."
            )
        )
