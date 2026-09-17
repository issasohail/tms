"""Archive old MeterRawFrame rows to gzip JSONL before optional deletion."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from smart_meter.models import MeterRawFrame


class Command(BaseCommand):
    help = (
        "Archive MeterRawFrame rows older than N days to a gzip JSONL file. "
        "Dry-run by default; --confirm writes and verifies the archive before deletion. "
        "MeterReading and LiveReading rows are never deleted by this command."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=180)
        parser.add_argument("--archive-dir", default="meter_raw_frame_archive")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--batch-size", type=int, default=2000)

    def handle(self, *args, **options):
        days = options["days"]
        if days < 30:
            raise CommandError("Refusing retention shorter than 30 days.")
        cutoff = timezone.now() - timezone.timedelta(days=days)
        qs = MeterRawFrame.objects.filter(received_at__lt=cutoff).order_by("id")
        count = qs.count()
        self.stdout.write(f"Raw frames older than {days} days: {count} (cutoff {cutoff.isoformat()})")
        if not options["confirm"] or not count:
            self.stdout.write(self.style.WARNING("DRY RUN: no archive written and no rows deleted."))
            return

        archive_dir = Path(options["archive_dir"]).expanduser().resolve()
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        path = archive_dir / f"meter_raw_frames_before_{cutoff:%Y%m%d}_{stamp}.jsonl.gz"
        archived_ids = []
        fields = (
            "id", "meter_id", "received_at", "source_ip", "source_port", "control_code",
            "data_identifier", "data_length", "raw_frame_hex", "checksum_style", "decoded_data",
            "trust_classification", "parser_version",
        )
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            for row in qs.values(*fields).iterator(chunk_size=options["batch_size"]):
                row["received_at"] = row["received_at"].isoformat()
                handle.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
                archived_ids.append(row["id"])

        if len(archived_ids) != count:
            path.unlink(missing_ok=True)
            raise CommandError("Archive row count did not match query count; database was not changed.")

        verified_count = 0
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    json.loads(line)
                    verified_count += 1
        except Exception as exc:
            raise CommandError(
                f"Archive verification failed; database was not changed: {exc}"
            ) from exc
        if verified_count != count:
            raise CommandError(
                f"Archive verification count {verified_count} != expected {count}; "
                "database was not changed."
            )

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksum_path = path.with_suffix(path.suffix + ".sha256")
        checksum_path.write_text(f"{digest}  {path.name}\n", encoding="ascii")

        with transaction.atomic():
            deleted, _detail = MeterRawFrame.objects.filter(id__in=archived_ids).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Archived and verified {count} frames to {path}; "
                f"sha256={digest}; delete result={deleted}."
            )
        )
        self.stdout.write(
            "Historical MeterReading/LiveReading telemetry was intentionally left unchanged."
        )
