import hashlib
import os
import posixpath
import shutil
from dataclasses import dataclass

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from properties.models import UnitMedia

COPY_REQUIRED = "COPY_REQUIRED"
DUPLICATE_MAPPING = "DUPLICATE_MAPPING"
ALREADY_PREPARED = "ALREADY_PREPARED"
ALREADY_CANONICAL = "ALREADY_CANONICAL"
MISSING_SOURCE = "MISSING_SOURCE"
COLLISION = "COLLISION"
ERROR = "ERROR"

FIELD_SUBFOLDERS = {
    "file": "original",
    "stamped_file": "stamped",
    "thumbnail": "thumbs",
}


@dataclass
class PlanItem:
    media_pk: int
    unit_pk: int
    property_name: str
    unit_number: str
    field_name: str
    old_name: str
    new_name: str
    old_path: str
    new_path: str
    source_exists: bool = False
    destination_exists: bool = False
    source_sha256: str = ""
    destination_sha256: str = ""
    source_size: int = 0
    action: str = ERROR
    error: str = ""
    note: str = ""


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_name(media, field_name, old_name):
    filename = posixpath.basename((old_name or "").replace("\\", "/"))
    if not filename:
        raise ValueError("The stored path does not contain a filename.")
    return posixpath.join(
        "properties",
        media.storage_folder,
        FIELD_SUBFOLDERS[field_name],
        filename,
    )


def _physical_path(storage_name):
    try:
        return os.path.abspath(default_storage.path(storage_name))
    except NotImplementedError as exc:
        raise ValueError(
            "The configured storage does not expose local physical paths."
        ) from exc


def _build_item(media, field_name, old_name):
    item = PlanItem(
        media_pk=media.pk,
        unit_pk=media.unit_id,
        property_name=media.unit.property.property_name,
        unit_number=media.unit.unit_number,
        field_name=field_name,
        old_name=old_name,
        new_name="",
        old_path="",
        new_path="",
    )
    try:
        item.new_name = _canonical_name(media, field_name, old_name)
        item.old_path = _physical_path(old_name)
        item.new_path = _physical_path(item.new_name)
        item.source_exists = os.path.isfile(item.old_path)
        item.destination_exists = os.path.isfile(item.new_path)

        if item.source_exists:
            item.source_sha256 = _sha256(item.old_path)
            item.source_size = os.path.getsize(item.old_path)
        if item.destination_exists:
            item.destination_sha256 = _sha256(item.new_path)

        if old_name == item.new_name:
            item.action = ALREADY_CANONICAL
        elif not item.source_exists:
            item.action = MISSING_SOURCE
        elif item.destination_exists:
            item.action = (
                ALREADY_PREPARED
                if item.source_sha256 == item.destination_sha256
                else COLLISION
            )
        else:
            item.action = COPY_REQUIRED
    except Exception as exc:
        item.action = ERROR
        item.error = str(exc)
    return item


def _analyze_plan_destinations(items):
    """Resolve duplicate planned destinations before any filesystem mutation."""
    destination_groups = {}
    for item in items:
        if not item.new_path:
            continue
        destination_key = os.path.normcase(os.path.abspath(item.new_path))
        destination_groups.setdefault(destination_key, []).append(item)

    for group in destination_groups.values():
        if len(group) < 2:
            continue

        ordered = sorted(
            group,
            key=lambda item: (
                item.media_pk,
                item.field_name,
                item.old_name,
            ),
        )
        if any(item.action in {MISSING_SOURCE, ERROR} for item in ordered):
            continue

        source_hashes = {item.source_sha256 for item in ordered}
        if len(source_hashes) != 1 or "" in source_hashes:
            identities = ", ".join(
                f"UnitMedia #{item.media_pk} {item.field_name} "
                f"({item.source_sha256 or 'no SHA256'})"
                for item in ordered
            )
            for item in ordered:
                item.action = COLLISION
                item.note = (
                    "Multiple plan items target this canonical destination with "
                    f"different source content: {identities}"
                )
            continue

        copy_items = [item for item in ordered if item.action == COPY_REQUIRED]
        if copy_items:
            primary = copy_items[0]
            primary.note = (
                "Primary deterministic copy for identical-content duplicate "
                f"destination ({len(ordered)} mappings)."
            )
            for item in ordered:
                if item is primary:
                    continue
                if item.action == COPY_REQUIRED:
                    item.action = DUPLICATE_MAPPING
                    item.note = (
                        "Identical-content duplicate mapping; destination will be "
                        f"prepared by UnitMedia #{primary.media_pk} "
                        f"{primary.field_name}."
                    )


def _copy_and_verify(item):
    if not os.path.isfile(item.old_path):
        raise CommandError(f"Source disappeared before copy: {item.old_path}")

    if os.path.isfile(item.new_path):
        destination_sha256 = _sha256(item.new_path)
        if destination_sha256 == item.source_sha256:
            return 0
        raise CommandError(
            f"Destination collision appeared before copy: {item.new_path}"
        )

    os.makedirs(os.path.dirname(item.new_path), exist_ok=True)
    try:
        with open(item.old_path, "rb") as source, open(item.new_path, "xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        shutil.copystat(item.old_path, item.new_path)
    except FileExistsError as exc:
        destination_sha256 = _sha256(item.new_path)
        if destination_sha256 == item.source_sha256:
            return 0
        raise CommandError(
            f"Destination collision appeared before copy: {item.new_path}"
        ) from exc

    if not os.path.isfile(item.new_path):
        raise CommandError(f"Destination was not created: {item.new_path}")
    destination_sha256 = _sha256(item.new_path)
    if destination_sha256 != item.source_sha256:
        raise CommandError(f"SHA256 verification failed: {item.new_path}")
    return item.source_size


class Command(BaseCommand):
    help = (
        "Preflight and optionally copy UnitMedia files into canonical property/unit "
        "directories without changing database paths or deleting source files."
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the plan without copying files (the default).",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Copy files only when the complete preflight has no blockers.",
        )

    def _write_item(self, item):
        value = lambda text: text if text not in (None, "") else "-"
        self.stdout.write(f"UnitMedia PK: {item.media_pk}")
        self.stdout.write(f"Unit PK: {item.unit_pk}")
        self.stdout.write(f"Property: {value(item.property_name)}")
        self.stdout.write(f"Unit: {value(item.unit_number)}")
        self.stdout.write(f"Field: {item.field_name}")
        self.stdout.write(f"Old DB-relative path: {value(item.old_name)}")
        self.stdout.write(f"New DB-relative path: {value(item.new_name)}")
        self.stdout.write(f"Old physical path: {value(item.old_path)}")
        self.stdout.write(f"New physical path: {value(item.new_path)}")
        self.stdout.write(f"Source exists: {'YES' if item.source_exists else 'NO'}")
        self.stdout.write(
            f"Destination exists: {'YES' if item.destination_exists else 'NO'}"
        )
        self.stdout.write(f"Source SHA256: {value(item.source_sha256)}")
        self.stdout.write(f"Destination SHA256: {value(item.destination_sha256)}")
        self.stdout.write(f"Proposed action: {item.action}")
        if item.note:
            self.stdout.write(f"Plan note: {item.note}")
        if item.error:
            self.stdout.write(f"Error: {item.error}")
        self.stdout.write("")

    def _write_summary(self, records_examined, items, bytes_copied):
        counts = {
            status: sum(1 for item in items if item.action == status)
            for status in (
                ALREADY_CANONICAL,
                COPY_REQUIRED,
                DUPLICATE_MAPPING,
                ALREADY_PREPARED,
                MISSING_SOURCE,
                COLLISION,
                ERROR,
            )
        }
        bytes_to_copy = sum(
            item.source_size for item in items if item.action == COPY_REQUIRED
        )
        self.stdout.write("Summary")
        self.stdout.write(f"UnitMedia records examined: {records_examined}")
        self.stdout.write(f"Non-empty file fields examined: {len(items)}")
        self.stdout.write(f"Already canonical: {counts[ALREADY_CANONICAL]}")
        self.stdout.write(f"Copy required: {counts[COPY_REQUIRED]}")
        self.stdout.write(f"Duplicate mappings: {counts[DUPLICATE_MAPPING]}")
        self.stdout.write(f"Already prepared: {counts[ALREADY_PREPARED]}")
        self.stdout.write(f"Missing sources: {counts[MISSING_SOURCE]}")
        self.stdout.write(f"Collisions: {counts[COLLISION]}")
        self.stdout.write(f"Errors: {counts[ERROR]}")
        self.stdout.write(f"Bytes to copy: {bytes_to_copy}")
        self.stdout.write(f"Bytes copied: {bytes_copied}")
        return counts

    def handle(self, *args, **options):
        apply = options["apply"]
        queryset = (
            UnitMedia.objects.select_related("unit__property")
            .only(
                "pk",
                "unit_id",
                "file",
                "stamped_file",
                "thumbnail",
                "unit__id",
                "unit__unit_number",
                "unit__property_id",
                "unit__property__id",
                "unit__property__property_name",
            )
            .order_by("pk")
        )
        records_examined = queryset.count()
        items = []

        for media in queryset.iterator():
            for field_name in FIELD_SUBFOLDERS:
                old_name = getattr(getattr(media, field_name), "name", "")
                if old_name:
                    items.append(_build_item(media, field_name, old_name))

        _analyze_plan_destinations(items)

        self.stdout.write("UnitMedia reorganization preflight")
        self.stdout.write(f"Mode: {'APPLY' if apply else 'DRY RUN'}")
        self.stdout.write("")
        for item in items:
            self._write_item(item)

        if not apply:
            self._write_summary(records_examined, items, bytes_copied=0)
            self.stdout.write(self.style.WARNING("DRY RUN - NO FILES CHANGED"))
            return

        blockers = {
            MISSING_SOURCE: sum(1 for item in items if item.action == MISSING_SOURCE),
            COLLISION: sum(1 for item in items if item.action == COLLISION),
            ERROR: sum(1 for item in items if item.action == ERROR),
        }
        if any(blockers.values()):
            self._write_summary(records_examined, items, bytes_copied=0)
            self.stdout.write("DATABASE CHANGED: NO")
            self.stdout.write("SOURCE FILES DELETED: NO")
            raise CommandError(
                "Apply refused because preflight found missing sources, "
                "collisions, or errors."
            )

        bytes_copied = 0
        try:
            for item in items:
                if item.action == COPY_REQUIRED:
                    bytes_copied += _copy_and_verify(item)
        except Exception as exc:
            item.action = ERROR
            item.error = str(exc)
            self.stderr.write(self.style.ERROR(f"Copy failed: {exc}"))
            self._write_summary(records_examined, items, bytes_copied)
            self.stdout.write("DATABASE CHANGED: NO")
            self.stdout.write("SOURCE FILES DELETED: NO")
            raise CommandError(
                "Apply stopped after a copy or verification error."
            ) from exc

        self._write_summary(records_examined, items, bytes_copied)
        self.stdout.write(self.style.SUCCESS("DATABASE CHANGED: NO"))
        self.stdout.write(self.style.SUCCESS("SOURCE FILES DELETED: NO"))
