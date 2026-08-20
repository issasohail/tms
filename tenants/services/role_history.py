from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Case, DecimalField, F, Q, Sum, When
from django.db.models.functions import Coalesce
from django.urls import reverse

from invoices.models import Invoice
from leases.models import Lease, LeaseFamilyMember, LeaseRenewal
from payments.models import Payment


@dataclass
class RoleHistoryRow:
    role_type: str
    lease: object
    renewal: object
    related_tenant: object
    property: object
    unit: object
    lease_start: object
    lease_end: object
    balance: object
    status: str
    status_date: object
    detail_url: str

    @property
    def lease_label(self):
        return f"Lease #{self.lease.pk}" + (
            f" — Renewal #{self.renewal.renewal_number}" if self.renewal else ""
        )


def _balance(lease):
    for name in ("annotated_balance", "balance", "current_balance"):
        value = getattr(lease, name, None)
        if value is not None:
            return value
    try:
        return lease.get_balance
    except Exception:
        return Decimal("0.00")


def _prime_lease_balances(leases):
    """Attach canonical lease balances in two grouped queries.

    Role-history rows can point at leases owned by other tenants.  Those lease
    objects do not share the TenantDetailView's invoice/payment prefetch cache,
    so calling ``get_balance`` on every row would otherwise create an N+1.
    """
    by_id = {}
    for lease in leases:
        if lease is not None and lease.pk and not hasattr(lease, "_cached_get_balance"):
            by_id.setdefault(lease.pk, lease)
    if not by_id:
        return

    lease_ids = list(by_id)
    zero = Decimal("0.00")
    decimal_field = DecimalField(max_digits=12, decimal_places=2)

    invoice_totals = {
        row["lease_id"]: row["total"] or zero
        for row in (
            Invoice.objects.filter(lease_id__in=lease_ids)
            .exclude(status="cancelled")
            .values("lease_id")
            .annotate(total=Coalesce(Sum("amount"), zero, output_field=decimal_field))
        )
    }
    payment_totals = {
        row["lease_id"]: row["total"] or zero
        for row in (
            Payment.objects.filter(lease_id__in=lease_ids)
            .values("lease_id")
            .annotate(
                total=Coalesce(
                    Sum(
                        Case(
                            When(detail__isnull=False, then=F("detail__lease_amount")),
                            default=F("amount"),
                            output_field=decimal_field,
                        )
                    ),
                    zero,
                    output_field=decimal_field,
                )
            )
        )
    }
    for lease_id, lease in by_id.items():
        lease._cached_get_balance = invoice_totals.get(lease_id, zero) - payment_totals.get(
            lease_id, zero
        )


def tenant_role_history(
    tenant,
    role=None,
    *,
    primary_leases=None,
    family_links=None,
):
    """Return role history while batching role lookups.

    ``primary_leases`` and ``family_links`` allow TenantDetailView to reuse rows
    it already loaded.  Other callers keep the same public API and behavior.
    """
    rows = []

    if primary_leases is None:
        primary_leases = list(
            Lease.objects.filter(tenant=tenant).select_related("tenant", "unit__property")
        )
    else:
        primary_leases = list(primary_leases)

    if role in (None, "family_member"):
        if family_links is None:
            family_links = list(
                LeaseFamilyMember.objects.filter(family_member=tenant).select_related(
                    "lease__tenant", "lease__unit__property"
                )
            )
        else:
            family_links = list(family_links)
    else:
        family_links = []

    role_specs = (
        ("proposer", "Proposer", "proposer_id"),
        ("seconder", "Seconder", "seconder_id"),
        ("witness", "Witness", "witness1_tenant_id"),
        ("witness", "Witness", "witness2_tenant_id"),
    )
    wanted_specs = [spec for spec in role_specs if role in (None, spec[0])]

    related_leases = []
    if wanted_specs:
        related_filter = Q()
        for _key, _label, field_name in wanted_specs:
            related_filter |= Q(**{field_name: tenant.pk})
        related_leases = list(
            Lease.objects.filter(related_filter)
            .select_related("tenant", "unit__property")
            .distinct()
        )

    witness_renewals = []
    if role in (None, "witness"):
        witness_renewals = list(
            LeaseRenewal.objects.filter(
                Q(witness1_tenant=tenant) | Q(witness2_tenant=tenant)
            )
            .select_related("lease__tenant", "lease__unit__property")
            .distinct()
        )

    _prime_lease_balances(
        [link.lease for link in family_links]
        + related_leases
        + [renewal.lease for renewal in witness_renewals]
    )

    if not role:
        for lease in primary_leases:
            rows.append(
                RoleHistoryRow(
                    "Primary Tenant",
                    lease,
                    None,
                    lease.tenant,
                    lease.unit.property,
                    lease.unit,
                    lease.start_date,
                    lease.end_date,
                    _balance(lease),
                    lease.get_status_display(),
                    lease.updated_at,
                    reverse("leases:lease_detail", args=[lease.pk]),
                )
            )

    if role in (None, "family_member"):
        for link in family_links:
            lease = link.lease
            rows.append(
                RoleHistoryRow(
                    "Family Member",
                    lease,
                    None,
                    lease.tenant,
                    lease.unit.property,
                    lease.unit,
                    lease.start_date,
                    lease.end_date,
                    _balance(lease),
                    lease.get_status_display(),
                    link.updated_at,
                    reverse("leases:lease_detail", args=[lease.pk]),
                )
            )

    # Preserve the old block ordering (proposer, seconder, witness1, witness2)
    # while evaluating the Lease queryset only once.
    for key, label, field_name in wanted_specs:
        for lease in related_leases:
            if getattr(lease, field_name) != tenant.pk:
                continue
            rows.append(
                RoleHistoryRow(
                    label,
                    lease,
                    None,
                    lease.tenant,
                    lease.unit.property,
                    lease.unit,
                    lease.start_date,
                    lease.end_date,
                    _balance(lease),
                    lease.get_status_display(),
                    lease.updated_at,
                    reverse("leases:lease_detail", args=[lease.pk]),
                )
            )

    if role in (None, "witness"):
        for field_name in ("witness1_tenant_id", "witness2_tenant_id"):
            for renewal in witness_renewals:
                if getattr(renewal, field_name) != tenant.pk:
                    continue
                lease = renewal.lease
                rows.append(
                    RoleHistoryRow(
                        "Witness",
                        lease,
                        renewal,
                        lease.tenant,
                        lease.unit.property,
                        lease.unit,
                        renewal.start_date,
                        renewal.end_date,
                        _balance(lease),
                        "Renewal",
                        renewal.updated_at,
                        reverse(
                            "leases:lease_history_detail", args=[lease.pk, renewal.pk]
                        ),
                    )
                )
    return rows


def role_counts(tenant):
    rows = tenant_role_history(tenant)
    counts = {"family_member": 0, "proposer": 0, "seconder": 0, "witness": 0}
    for row in rows:
        key = row.role_type.lower().replace(" ", "_")
        if key in counts:
            counts[key] += 1
    return counts
