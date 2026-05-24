from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from leases.models import Lease
from leases.models_renewal import LeaseRenewal


class Command(BaseCommand):
    help = "Backfill LeaseRenewal rows from duplicate Lease rows grouped by tenant + unit."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Apply changes. Without this flag the command is a dry-run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change. This is the default.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        if options["dry_run"] and commit:
            raise CommandError("Use either --dry-run or --commit, not both.")

        groups = {}
        for lease in Lease.objects.select_related("tenant", "unit").order_by(
            "tenant_id", "unit_id", "start_date", "id"
        ):
            groups.setdefault((lease.tenant_id, lease.unit_id), []).append(lease)

        duplicate_groups = {key: leases for key, leases in groups.items() if len(leases) > 1}
        self.stdout.write(
            self.style.WARNING(
                f"{'COMMIT' if commit else 'DRY-RUN'}: found {len(duplicate_groups)} tenant/unit groups with multiple leases."
            )
        )

        total_renewals = 0
        for (_tenant_id, _unit_id), leases in duplicate_groups.items():
            master = leases[0]
            children = leases[1:]
            latest = leases[-1]

            self.stdout.write("")
            self.stdout.write(
                f"Master Lease #{master.pk}: tenant={master.tenant_id}, unit={master.unit_id}, "
                f"{master.start_date} to {master.end_date}"
            )

            for idx, child in enumerate(children, start=1):
                exists = LeaseRenewal.objects.filter(
                    lease=master,
                    start_date=child.start_date,
                    end_date=child.end_date,
                ).exists()
                marker = "exists" if exists else "create"
                self.stdout.write(
                    f"  {marker}: renewal #{idx} from child Lease #{child.pk}, "
                    f"{child.start_date} to {child.end_date}, rent={child.monthly_rent}"
                )
                if not exists:
                    total_renewals += 1

            self.stdout.write(
                f"  master current values would become Lease #{latest.pk}: "
                f"{latest.start_date} to {latest.end_date}, rent={latest.monthly_rent}"
            )

            if commit:
                with transaction.atomic():
                    master = Lease.objects.select_for_update().get(pk=master.pk)
                    if not master.original_start_date:
                        master.original_start_date = master.start_date
                    master.is_master = True

                    for idx, child in enumerate(children, start=1):
                        renewal, created = LeaseRenewal.objects.get_or_create(
                            lease=master,
                            renewal_number=idx,
                            defaults={
                                "start_date": child.start_date,
                                "end_date": child.end_date,
                                "agreement_date": child.agreement_date,
                                "monthly_rent": child.monthly_rent,
                                "society_maintenance": child.society_maintenance,
                                "water_charges": child.water_charges or Decimal("0.00"),
                                "internet_charges": child.internet_charges or Decimal("0.00"),
                                "rent_increase_percent": child.rent_increase_percent or Decimal("10.00"),
                                "generated_agreement_pdf": child.generated_agreement_pdf,
                                "generated_agreement_docx": child.generated_agreement_docx,
                                "signed_copy": child.signed_copy,
                                "is_agreement_signed": child.is_agreement_signed,
                                "notes": f"Backfilled from Lease #{child.pk}. {child.notes or ''}".strip(),
                            },
                        )
                        if created:
                            child.is_master = False
                            child.superseded_by = master
                            child.status = "ended"
                            child.save(update_fields=["is_master", "superseded_by", "status", "updated_at"])

                    master.start_date = latest.start_date
                    master.end_date = latest.end_date
                    master.agreement_date = latest.agreement_date
                    master.monthly_rent = latest.monthly_rent
                    master.society_maintenance = latest.society_maintenance
                    master.water_charges = latest.water_charges
                    master.internet_charges = latest.internet_charges
                    master.rent_increase_percent = latest.rent_increase_percent
                    master.status = latest.status
                    master.save(update_fields=[
                        "original_start_date",
                        "is_master",
                        "start_date",
                        "end_date",
                        "agreement_date",
                        "monthly_rent",
                        "society_maintenance",
                        "water_charges",
                        "internet_charges",
                        "rent_increase_percent",
                        "status",
                        "updated_at",
                    ])

        self.stdout.write("")
        if commit:
            self.stdout.write(self.style.SUCCESS("Backfill committed."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry-run complete. {total_renewals} renewal rows would be created. Re-run with --commit to apply."
                )
            )
