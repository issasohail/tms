import re

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection

from core.model_fields import NormalizedCNICField, NormalizedPhoneField
from core.utils.identity import normalize_cnic, normalize_phone


LEGACY_BLANK_CNIC_RE = re.compile(r"^(?:NEW|FM)[0-9a-f]{10,11}$", re.IGNORECASE)


class Command(BaseCommand):
    help = "Normalize stored CNIC and phone strings and report malformed legacy values."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Persist safe normalization changes.")
        parser.add_argument(
            "--phones-only",
            action="store_true",
            help="Normalize phone fields without inspecting or changing CNIC fields.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        phones_only = options["phones_only"]
        changed = malformed_cnic = legacy_blank_cnic = unusual_phone = skipped_models = 0
        existing_tables = set(connection.introspection.table_names())
        for model in apps.get_models():
            identity_fields = [
                field for field in model._meta.concrete_fields
                if isinstance(field, (NormalizedCNICField, NormalizedPhoneField))
                and (not phones_only or isinstance(field, NormalizedPhoneField))
            ]
            if not identity_fields:
                continue
            if model._meta.db_table not in existing_tables:
                skipped_models += 1
                self.stdout.write(self.style.WARNING(
                    f"SKIPPED {model._meta.label}: database table {model._meta.db_table!r} does not exist."
                ))
                continue
            field_names = [field.attname for field in identity_fields]
            rows = model._default_manager.values_list("pk", *field_names).iterator()
            for row in rows:
                object_pk, *current_values = row
                updates = {}
                for field, current in zip(identity_fields, current_values):
                    if isinstance(field, NormalizedCNICField):
                        raw_cnic = str(current or "").strip()
                        if LEGACY_BLANK_CNIC_RE.fullmatch(raw_cnic):
                            normalized = ""
                            legacy_blank_cnic += 1
                            self.stdout.write(self.style.WARNING(
                                f"LEGACY BLANK CNIC {model._meta.label} pk={object_pk} field={field.name} value={current!r} will be cleared"
                            ))
                        else:
                            normalized = normalize_cnic(current)
                        if normalized and len(normalized) != 13:
                            malformed_cnic += 1
                            self.stdout.write(self.style.WARNING(
                                f"MALFORMED CNIC {model._meta.label} pk={object_pk} field={field.name} value={current!r} normalized={normalized!r}"
                            ))
                            continue
                    else:
                        normalized = normalize_phone(current)
                        digit_count = len(normalized.lstrip("+"))
                        if normalized and (digit_count < 7 or digit_count > 15):
                            unusual_phone += 1
                            self.stdout.write(self.style.WARNING(
                                f"UNUSUAL PHONE {model._meta.label} pk={object_pk} field={field.name} value={current!r} normalized={normalized!r}"
                            ))
                    if current not in (None, "") and normalized != current:
                        updates[field.attname] = normalized
                        if field.name == "cnic":
                            try:
                                shadow_field = model._meta.get_field("cnic_digits")
                            except Exception:
                                shadow_field = None
                            if shadow_field is not None:
                                updates[shadow_field.attname] = (
                                    normalized or (None if shadow_field.null else "")
                                )
                if updates:
                    changed += sum(
                        1 for field in identity_fields if field.attname in updates
                    )
                    if apply_changes:
                        model._default_manager.filter(pk=object_pk).update(**updates)
        mode = "applied" if apply_changes else "dry-run"
        self.stdout.write(self.style.SUCCESS(
            f"Identity normalization {mode}: {changed} field change(s), {legacy_blank_cnic} legacy blank-CNIC placeholder(s), {malformed_cnic} malformed CNIC value(s), {unusual_phone} unusual phone value(s), {skipped_models} model(s) skipped because their tables do not exist."
        ))
