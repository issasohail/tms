from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from punjab_estamp.models import PunjabEStampDistrict
from punjab_estamp.services.sync import PunjabEStampSyncError, sync_district_tehsils


class Command(BaseCommand):
    help = "Sync public Punjab e-Stamp Tehsil options for one District or all configured Districts."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--district", help="District DB id, portal value, or exact name.")
        target.add_argument("--all", action="store_true", help="Sync every active configured District.")
        parser.add_argument("--dry-run", action="store_true", help="Fetch/report without changing the database.")
        parser.add_argument(
            "--mark-missing-inactive",
            action="store_true",
            help="Mark cached Tehsils absent from the live list inactive. Never done by default.",
        )
        parser.add_argument("--timeout", type=int, default=8, help="Per-request timeout in seconds (default 8).")

    def handle(self, *args, **options):
        queryset = PunjabEStampDistrict.objects.filter(active=True).order_by("sort_order", "name")
        district_arg = options.get("district")
        if district_arg:
            query = Q(name__iexact=district_arg) | Q(portal_value=district_arg)
            if str(district_arg).isdigit():
                query |= Q(pk=int(district_arg))
            queryset = queryset.filter(query)
            if queryset.count() != 1:
                raise CommandError(f"Could not resolve exactly one active District from: {district_arg}")

        reports = []
        failures = []
        for district in queryset:
            self.stdout.write(f"Syncing {district.name} ({district.portal_value})...")
            try:
                report = sync_district_tehsils(
                    district,
                    dry_run=options["dry_run"],
                    mark_missing_inactive=options["mark_missing_inactive"],
                    timeout=max(2, options["timeout"]),
                )
            except PunjabEStampSyncError as exc:
                failures.append((district.name, str(exc)))
                self.stderr.write(self.style.ERROR(f"  FAILED: {exc}"))
                continue
            reports.append(report)
            self.stdout.write(
                self.style.SUCCESS(
                    "  found={found} created={created} updated={updated} "
                    "unchanged={unchanged} deactivated={deactivated}{suffix}".format(
                        **report,
                        suffix=" (dry run)" if report["dry_run"] else "",
                    )
                )
            )

        self.stdout.write(
            f"Completed: {len(reports)} District(s) succeeded; {len(failures)} failed."
        )
        if failures:
            self.stdout.write("Failed Districts:")
            for name, error in failures:
                self.stdout.write(f"- {name}: {error}")
            if len(reports) == 0:
                raise CommandError("No District could be synchronized.")
