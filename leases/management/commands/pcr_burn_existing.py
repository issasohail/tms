from django.core.management.base import BaseCommand
from leases.models_pcr import PCRPhoto, _burn_datestamp, _infer_taken_at, _make_thumbnail
from django.core.files.base import ContentFile
import uuid
import os


class Command(BaseCommand):
    help = "Burn datestamp into existing PCR photos and (re)generate thumbnails."

    def handle(self, *args, **kwargs):
        for p in PCRPhoto.objects.all():
            try:
                if not p.taken_at:
                    p.taken_at = _infer_taken_at(p.image)
                stamped = _burn_datestamp(
                    p.image, p.taken_at, p.room, enabled=p.show_datestamp)
                p.image.save(os.path.basename(p.image.name),
                             ContentFile(stamped), save=False)
                thumb = _make_thumbnail(p.image, 512)
                p.thumbnail.save(
                    f"thumb-{uuid.uuid4()}.jpg", ContentFile(thumb), save=False)
                p.save(update_fields=["image", "thumbnail", "taken_at"])
                self.stdout.write(self.style.SUCCESS(
                    f"Processed photo {p.id}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error on {p.id}: {e}"))
