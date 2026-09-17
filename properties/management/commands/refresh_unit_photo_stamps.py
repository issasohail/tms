from django.core.management.base import BaseCommand, CommandError

from properties.models import Unit, UnitMedia


class Command(BaseCommand):
    help = "Rebuild stamped images and thumbnails for an existing unit's photos."

    def add_arguments(self, parser):
        parser.add_argument("unit_id", type=int)

    def handle(self, *args, **options):
        unit_id = options["unit_id"]
        if not Unit.objects.filter(pk=unit_id).exists():
            raise CommandError(f"Unit {unit_id} does not exist.")

        refreshed = 0
        for media in UnitMedia.objects.filter(
            unit_id=unit_id, is_active=True, file_type="image"
        ).iterator():
            if not media.file or not media.file.storage.exists(media.file.name):
                self.stderr.write(f"Skipped photo {media.pk}: original file is missing.")
                continue
            media.refresh_image_derivatives()
            refreshed += 1

        self.stdout.write(self.style.SUCCESS(f"Refreshed {refreshed} photo stamp(s) for unit {unit_id}."))
