from decimal import Decimal
from copy import copy

from django.db import transaction
from django.db.models import Max

from leases.models_renewal import LeaseRenewal, LeaseRenewalClause


ZERO = Decimal("0.00")


LEASE_TO_HISTORY_FIELDS = [
    "start_date",
    "end_date",
    "lease_months",
    "agreement_date",
    "monthly_rent",
    "society_maintenance",
    "water_charges",
    "internet_charges",
    "agreement_charges",
    "security_deposit",
    "witness1_tenant",
    "witness2_tenant",
    "terms",
]

HISTORY_TO_LEASE_FIELDS = LEASE_TO_HISTORY_FIELDS + ["rent_increase_percent"]


def latest_history(lease):
    return lease.renewals.order_by("-renewal_number", "-id").first()


def is_active_history(history):
    latest = latest_history(history.lease)
    return bool(latest and latest.pk == history.pk)


def next_history_number(lease):
    return (lease.renewals.aggregate(last=Max("renewal_number"))["last"] or 0) + 1


def copy_master_lease_to_history(lease, history):
    for field in LEASE_TO_HISTORY_FIELDS:
        setattr(history, field, getattr(lease, field, None))
    history.agreement_date = lease.agreement_date or lease.start_date
    history.rent_increase_percent = lease.rent_increase_percent or ZERO
    return history


def lease_with_history_values(lease, history):
    lease_copy = copy(lease)
    for field in HISTORY_TO_LEASE_FIELDS:
        if hasattr(history, field):
            setattr(lease_copy, field, getattr(history, field))
    return lease_copy


def sync_history_to_master_lease(history, *, user=None):
    lease = history.lease
    if history.start_date and not history.agreement_date:
        history.agreement_date = history.start_date
        history.save(update_fields=["agreement_date", "updated_at"])
    for field in HISTORY_TO_LEASE_FIELDS:
        setattr(lease, field, getattr(history, field, None))
    if lease.status != "active":
        lease.status = "active"
    lease.save(update_fields=HISTORY_TO_LEASE_FIELDS + ["status", "updated_at"])
    return lease


def copy_lease_clauses_to_history(lease, history):
    if history.clauses.exists():
        return
    lease.initialize_clauses()
    LeaseRenewalClause.objects.bulk_create([
        LeaseRenewalClause(
            renewal=history,
            clause_number=clause.clause_number,
            template_text=clause.template_text,
            is_customized=clause.is_customized,
        )
        for clause in lease.clauses.all().order_by("clause_number")
    ])


def copy_previous_history_clauses(lease, history):
    if history.clauses.exists():
        return

    previous = (
        lease.renewals.exclude(pk=history.pk)
        .filter(renewal_number__lt=history.renewal_number)
        .order_by("-renewal_number", "-id")
        .first()
    )
    if previous and previous.clauses.exists():
        LeaseRenewalClause.objects.bulk_create([
            LeaseRenewalClause(
                renewal=history,
                clause_number=clause.clause_number,
                template_text=clause.template_text,
                is_customized=clause.is_customized,
            )
            for clause in previous.clauses.all().order_by("clause_number")
        ])
        return

    copy_lease_clauses_to_history(lease, history)


@transaction.atomic
def ensure_original_history(lease, *, user=None):
    original = lease.renewals.filter(renewal_number=1).first()
    if original:
        if original.is_original:
            return original

        # Existing databases may already have Renewal #1 from the old design.
        # Move those real renewals forward so #1 can permanently mean original lease.
        for row in lease.renewals.order_by("-renewal_number", "-id"):
            row.renewal_number = row.renewal_number + 1
            row.save(update_fields=["renewal_number", "updated_at"])

    history = LeaseRenewal(lease=lease, renewal_number=1, is_original=True)
    copy_master_lease_to_history(lease, history)
    history.created_by = user if getattr(user, "is_authenticated", False) else None
    history.updated_by = history.created_by
    history.save()
    copy_lease_clauses_to_history(lease, history)
    return history
