import os
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from leases.models_lease_photos import LeaseMedia, _base_dir, _cap_path, _lease_media_stem, _thumbs_path
from properties.models import PropertyMedia, UnitMedia, _media_date, _name_part


def _copy_then_delete(old_name, new_name, apply):
    if not old_name or old_name == new_name:
        return old_name, "unchanged"
    if not default_storage.exists(old_name):
        return old_name, "missing"

    final_name = new_name
    if default_storage.exists(final_name):
        root, ext = os.path.splitext(final_name)
        final_name = f"{root}-r{uuid.uuid4().hex[:6]}{ext}"

    if apply:
        with default_storage.open(old_name, "rb") as src:
            default_storage.save(final_name, ContentFile(src.read()))
        if default_storage.exists(final_name):
            default_storage.delete(old_name)
    return final_name, "renamed"


def _property_media_stem(media):
    if isinstance(media, PropertyMedia):
        return f"{_name_part(media.property.property_name, 'property')}_{_media_date(media)}_{media.pk}"
    unit = media.unit
    return (
        f"{_name_part(unit.property.property_name, 'property')}_"
        f"{_name_part(unit.unit_number, 'unit')}_"
        f"{_media_date(media)}_{media.pk}"
    )


def _property_media_targets(media):
    stem = _property_media_stem(media)
    ext = os.path.splitext(media.file.name or "")[1].lower() or ".bin"
    folder = f"properties/{media.storage_folder}"
    targets = {"file": f"{folder}/original/{stem}{ext}"}
    if media.stamped_file:
        targets["stamped_file"] = f"{folder}/stamped/{stem}-stamped.jpg"
    if media.thumbnail:
        targets["thumbnail"] = f"{folder}/thumbs/{stem}-thumb.jpg"
    return targets


def _lease_media_targets(media):
    ext = os.path.splitext(media.file.name or "")[1].lower() or ".bin"
    media_type = media.media_type or "file"
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        media_type = "image"
    elif ext in {".mp4", ".mov", ".avi", ".mkv"}:
        media_type = "video"
    folder = "photos" if media_type == "image" else ("videos" if media_type == "video" else "files")
    base = _base_dir(media)
    stem = _lease_media_stem(media)
    targets = {"file": _cap_path(f"{base}/{folder}/{stem}", ext)}
    if media.thumbnail:
        targets["thumbnail"] = _thumbs_path(media)
    return targets


class Command(BaseCommand):
    help = (
        "Rename property, unit, and lease media files to meaningful names and "
        "update FileField values. Tenant and expense media are not touched."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually copy files, delete old files, and update model fields. Without this, only prints a dry run.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        totals = {"renamed": 0, "unchanged": 0, "missing": 0}

        def handle_model(model, target_builder, label):
            qs = model.objects.all().order_by("pk")
            self.stdout.write(f"{label}: checking {qs.count()} rows")
            for media in qs:
                updates = {}
                for field_name, target in target_builder(media).items():
                    old_name = getattr(getattr(media, field_name), "name", "")
                    new_name, status = _copy_then_delete(old_name, target, apply)
                    totals[status] = totals.get(status, 0) + 1
                    if status == "renamed" and new_name != old_name:
                        updates[field_name] = new_name
                        self.stdout.write(f"  {label} #{media.pk} {field_name}: {old_name} -> {new_name}")
                if apply and updates:
                    updates["updated_at"] = timezone.now()
                    with transaction.atomic():
                        model.objects.filter(pk=media.pk).update(**updates)

        handle_model(PropertyMedia, _property_media_targets, "PropertyMedia")
        handle_model(UnitMedia, _property_media_targets, "UnitMedia")
        handle_model(LeaseMedia, _lease_media_targets, "LeaseMedia")

        if apply:
            for media in LeaseMedia.objects.all().only("pk", "file", "media_type"):
                ext = os.path.splitext(media.file.name or "")[1].lower()
                media_type = "video" if ext in {".mp4", ".mov", ".avi", ".mkv"} else (
                    "image" if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else "file"
                )
                if media.media_type != media_type:
                    LeaseMedia.objects.filter(pk=media.pk).update(media_type=media_type, updated_at=timezone.now())

        mode = "APPLIED" if apply else "DRY RUN"
        self.stdout.write(self.style.SUCCESS(
            f"{mode}: renamed={totals.get('renamed', 0)}, unchanged={totals.get('unchanged', 0)}, missing={totals.get('missing', 0)}"
        ))
