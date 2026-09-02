from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from invoices.models import Invoice
from leases.models import Lease
from leases.utils.billing import (
    MOVE_IN_PRORATION_MARKER,
    reconcile_move_in_proration,
)


class Command(BaseCommand):
    help = (
        "Audit marker move-in proration invoices. The default is dry-run; "
        "pass --apply to reconcile editable invoices."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply proposed changes to editable invoices.",
        )
        parser.add_argument(
            "--lease-id",
            type=int,
            help="Audit only this lease ID.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        lease_id = options.get("lease_id")
        marker_rows = Invoice.objects.filter(
            description__startswith=MOVE_IN_PRORATION_MARKER
        )
        if lease_id is not None:
            if not Lease.objects.filter(pk=lease_id).exists():
                raise CommandError(f"Lease #{lease_id} does not exist.")
            marker_rows = marker_rows.filter(lease_id=lease_id)

        lease_ids = list(
            marker_rows.order_by("lease_id")
            .values_list("lease_id", flat=True)
            .distinct()
        )
        mode_label = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(f"Move-in proration audit mode: {mode_label}")
        if not lease_ids:
            self.stdout.write("No [MOVE_IN_PRORATION] invoices found for this scope.")
            self.stdout.write("Summary: leases=0 invoices=0 actions={}")
            return

        counts = Counter()
        invoice_count = marker_rows.count()
        for current_lease_id in lease_ids:
            with transaction.atomic():
                lease = Lease.objects.select_for_update().get(pk=current_lease_id)
                current = list(
                    Invoice.objects.filter(
                        lease=lease,
                        description__startswith=MOVE_IN_PRORATION_MARKER,
                    ).order_by("id")
                )
                result = reconcile_move_in_proration(
                    lease,
                    enabled=None,
                    apply=apply_changes,
                )
                counts[result["action"]] += 1
                occupancy = (
                    lease.unit_occupancies.filter(move_out_date__isnull=True)
                    .order_by("-move_in_date", "-id")
                    .first()
                )
                stored = "; ".join(
                    (
                        f"invoice={invoice.pk} status={invoice.status}/"
                        f"{invoice.lifecycle_status} window={invoice.issue_date} "
                        f"description={invoice.description!r} amount={invoice.amount}"
                    )
                    for invoice in current
                )
                expected_window = (
                    f"{result['move_in_date']} to {result['period_end']}"
                    if result.get("applicable")
                    else "none"
                )
                expected_amount = (
                    result.get("amount") if result.get("applicable") else "0.00"
                )
                self.stdout.write(
                    f"lease={lease.pk} lease_start={lease.start_date} "
                    f"occupancy_move_in={getattr(occupancy, 'move_in_date', None)} "
                    f"stored=[{stored}] expected_window={expected_window} "
                    f"expected_amount={expected_amount} action={result['action']} "
                    f"reason={result['warning'] or result['reason']}"
                )

        actions = ", ".join(
            f"{action}={count}" for action, count in sorted(counts.items())
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Summary: leases={len(lease_ids)} invoices={invoice_count} "
                f"actions={{{actions}}}"
            )
        )
