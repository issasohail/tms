# maintenance/management/commands/backfill_agreements.py

from django.core.management.base import BaseCommand
from leases.models import Lease, AgreementVersion, AgreementClause


class Command(BaseCommand):
    help = "Backfill AgreementVersion v1 from existing lease clauses"

    def handle(self, *args, **kwargs):
        for lease in Lease.objects.all():
            if lease.agreement_versions.exists():
                continue

            # Create version 1
            v1 = AgreementVersion.objects.create(
                lease=lease,
                version=1,
                type="NEW",
                agreement_date=lease.agreement_date,
                term_start_date=lease.start_date,
                term_end_date=lease.end_date,
                is_current=True,
            )

            for clause in lease.clauses.all():
                AgreementClause.objects.create(
                    agreement_version=v1,
                    clause_number=clause.clause_number,
                    template_text=clause.template_text,
                )

            self.stdout.write(f"Created Agreement v1 for Lease {lease.id}")
