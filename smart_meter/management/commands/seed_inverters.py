from django.core.management.base import BaseCommand

from smart_meter.models import EnergySystem, Inverter

# Energy System name (matched case-insensitively, by "contains") -> how many inverters it has.
# Edit this mapping if your System names differ from what's guessed here, then re-run —
# the command is safe to run more than once (existing inverters are left alone).
INVERTER_COUNTS = {
    "photon": 2,
    "tesla": 2,
    "h9": 3,
}


class Command(BaseCommand):
    help = (
        "Create Inverter rows for existing Energy Systems (Photon x2, Tesla x2, H9 x3 by "
        "default — edit INVERTER_COUNTS in this file if your system names differ). "
        "Safe to re-run: existing inverters are never duplicated or removed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be created without saving anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        matched_system_ids = set()

        for keyword, count in INVERTER_COUNTS.items():
            systems = EnergySystem.objects.filter(name__icontains=keyword)
            if not systems.exists():
                self.stdout.write(self.style.WARNING(
                    f"No Energy System found with '{keyword}' in its name — skipped."
                ))
                continue
            for system in systems:
                matched_system_ids.add(system.pk)
                existing_names = set(
                    system.inverters.values_list("name", flat=True)
                )
                for i in range(1, count + 1):
                    name = f"{system.name} Inverter {i}"
                    if name in existing_names:
                        continue
                    self.stdout.write(f"  + {name}  (system id {system.pk})")
                    if not dry_run:
                        Inverter.objects.get_or_create(
                            energy_system=system, name=name,
                        )

        unmatched = EnergySystem.objects.exclude(pk__in=matched_system_ids)
        if unmatched.exists():
            self.stdout.write(self.style.WARNING(
                "These Energy Systems didn't match any keyword in INVERTER_COUNTS and were "
                "left untouched — add them to the mapping if they need inverters too:"
            ))
            for system in unmatched:
                self.stdout.write(f"    id {system.pk}: {system.name}")

        if dry_run:
            self.stdout.write(self.style.NOTICE("Dry run — nothing was saved."))
        else:
            self.stdout.write(self.style.SUCCESS("Done."))
