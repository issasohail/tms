import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from punjab_estamp.models import PunjabEStampDistrict, PunjabEStampTehsil


class Command(BaseCommand):
    help = "Import a verified Punjab District/Tehsil JSON export without contacting the live portal."

    def add_arguments(self, parser):
        parser.add_argument("json_file")
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["json_file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Could not read JSON: {exc}") from exc

        districts = payload.get("districts") if isinstance(payload, dict) else payload
        if not isinstance(districts, list):
            raise CommandError("Expected a list or an object containing a 'districts' list.")

        created = updated = 0
        for item in districts:
            if not isinstance(item, dict):
                continue
            district_value = str(item.get("portal_value") or "").strip()
            district_name = str(item.get("name") or "").strip()
            if not district_value:
                raise CommandError("Every District entry must contain portal_value.")
            district = PunjabEStampDistrict.objects.filter(portal_value=district_value).first()
            if not district:
                raise CommandError(
                    f"District portal value {district_value} ({district_name or 'unnamed'}) is not configured in TMS."
                )
            for index, tehsil in enumerate(item.get("tehsils") or [], start=1):
                name = str(tehsil.get("name") or "").strip()
                portal_value = str(tehsil.get("portal_value") or "").strip()
                if not name or not portal_value:
                    raise CommandError(f"Invalid Tehsil entry under {district.name}.")
                current = PunjabEStampTehsil.objects.filter(
                    district=district, portal_value=portal_value
                ).first()
                if current is None:
                    created += 1
                    if not options["dry_run"]:
                        PunjabEStampTehsil.objects.create(
                            district=district,
                            name=name,
                            portal_value=portal_value,
                            active=True,
                            sort_order=index,
                        )
                else:
                    changes = current.name != name or not current.active or current.sort_order != index
                    if changes:
                        updated += 1
                        if not options["dry_run"]:
                            current.name = name
                            current.active = True
                            current.sort_order = index
                            current.save(update_fields=["name", "active", "sort_order"])

        if options["dry_run"]:
            transaction.set_rollback(True)
        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete: created={created}, updated={updated}"
                + (" (dry run)" if options["dry_run"] else "")
            )
        )
