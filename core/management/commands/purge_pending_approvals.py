from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.pending_approval_purge import hard_delete_pending_objects


def _specs():
    from leases.models import (
        PendingAgreementApproval,
        PendingLeaseFamilyMemberSubmission,
        PendingPoliceVerificationSubmission,
    )
    from tenants.models import TenantRegistrationSubmission
    from whatsapp.models import (
        PendingWhatsAppMaintenance,
        PendingWhatsAppMedia,
        PendingWhatsAppPayment,
    )

    return (
        ("agreement", PendingAgreementApproval, "created_at"),
        ("payment", PendingWhatsAppPayment, "created_at"),
        ("media", PendingWhatsAppMedia, "created_at"),
        ("maintenance", PendingWhatsAppMaintenance, "created_at"),
        ("family", PendingLeaseFamilyMemberSubmission, "created_at"),
        ("police", PendingPoliceVerificationSubmission, "submitted_at"),
        ("registration", TenantRegistrationSubmission, "submitted_at"),
    )


class Command(BaseCommand):
    help = (
        "Hard-delete transient approval records older than the cutoff and remove "
        "their physical files when no retained model still references those files. "
        "Lease records are intentionally excluded. Dry-run is the default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument(
            "--before",
            help="Optional exclusive cutoff date in YYYY-MM-DD format.",
        )
        parser.add_argument(
            "--status",
            choices=("all", "pending", "reviewed"),
            default="all",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Perform deletion. Without this flag the command only reports counts.",
        )

    def handle(self, *args, **options):
        if options["before"]:
            cutoff_date = parse_date(options["before"])
            if not cutoff_date:
                raise CommandError("--before must use YYYY-MM-DD format.")
            cutoff = timezone.make_aware(
                datetime.combine(cutoff_date, time.min),
                timezone.get_current_timezone(),
            )
        else:
            days = options["days"]
            if days < 1:
                raise CommandError("--days must be at least 1.")
            cutoff = timezone.now() - timedelta(days=days)

        total_objects = 0
        total_rows = 0
        total_files = 0
        for label, model, date_field in _specs():
            queryset = model._default_manager.filter(
                **{f"{date_field}__lt": cutoff}
            )
            status_mode = options["status"]
            if status_mode == "pending":
                pending_statuses = (
                    getattr(model, "EDITABLE_STATUSES", None)
                    or {"pending", "pending_approval", "confirmed"}
                )
                queryset = queryset.filter(status__in=pending_statuses)
            elif status_mode == "reviewed":
                pending_statuses = (
                    getattr(model, "EDITABLE_STATUSES", None)
                    or {"pending", "pending_approval", "confirmed"}
                )
                queryset = queryset.exclude(status__in=pending_statuses)

            count = queryset.count()
            total_objects += count
            self.stdout.write(f"{label}: {count}")
            if options["execute"] and count:
                result = hard_delete_pending_objects(queryset.iterator())
                total_rows += result["database_rows"]
                total_files += result["files"]

        if not options["execute"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run only: {total_objects} approval object(s) matched. "
                    "Re-run with --execute to hard-delete them."
                )
            )
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {total_objects} approval object(s), {total_rows} database "
                f"row(s), and evaluated {total_files} file reference(s)."
            )
        )
