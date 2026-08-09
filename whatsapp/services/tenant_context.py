from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from leases.models import Lease
from payments.models import Payment
from tenants.models import Tenant
from whatsapp.services.whatsapp import WhatsAppService


TENANT_PHONE_FIELDS = (
    "phone",
    "phone2",
    "phone3",
    "employer_phone",
    "reference_phone_1",
    "reference_phone_2",
    "emergency_contact_phone",
)


@dataclass
class TenantLeaseResolution:
    tenant: Tenant | None
    lease: Lease | None
    lease_status: str = ""

    @property
    def lease_end_date(self):
        if not self.lease:
            return None
        move_out_dates = [
            item.move_out_date
            for item in self.lease.unit_occupancies.all()
            if item.move_out_date is not None
        ]
        return max(move_out_dates) if move_out_dates else self.lease.end_date

    def __iter__(self):
        # Backward-compatible tuple unpacking for Phase 3 callers.
        yield self.tenant
        yield self.lease
        yield self.lease_status


@dataclass
class LeaseContext:
    lease: Lease
    balance: Decimal
    recent_payments: list
    maintenance_requests: list

    @property
    def tenant(self):
        return self.lease.tenant

    @property
    def unit(self):
        return self.lease.unit

    @property
    def property(self):
        return self.lease.unit.property


def normalize_phone(value):
    return WhatsAppService.normalize_phone_number(value or "")


def active_leases():
    today = timezone.localdate()
    return Lease.objects.select_related("tenant", "unit", "unit__property").filter(
        status="active",
        start_date__lte=today,
        end_date__gte=today,
    )


def find_active_leases_for_phone(phone_number):
    normalized = normalize_phone(phone_number)
    digits = _digits(normalized)
    if not digits:
        return Lease.objects.none()

    suffix = digits[-10:]
    tenant_phone_query = _phone_q("tenant__", suffix)
    family_phone_query = _phone_q("family_members__family_member__", suffix)
    legacy_family_phone_query = _phone_q("legacy_family_members__tenant__", suffix)

    lease_ids = set(active_leases().filter(tenant_phone_query).values_list("id", flat=True))
    lease_ids.update(active_leases().filter(family_phone_query).values_list("id", flat=True))
    lease_ids.update(active_leases().filter(legacy_family_phone_query).values_list("id", flat=True))

    for lease in active_leases().prefetch_related("family_members__family_member", "legacy_family_members__tenant"):
        phones = []
        phones.extend(_tenant_phone_values(lease.tenant))
        for member in lease.family_members.all():
            phones.extend(_tenant_phone_values(member.family_member))
        for member in lease.legacy_family_members.all():
            phones.extend(_tenant_phone_values(member.tenant))
        if any(_phone_matches(digits, candidate) for candidate in phones):
            lease_ids.add(lease.pk)

    return active_leases().filter(id__in=lease_ids).order_by("unit__property__property_name", "unit__unit_number")


def resolve_tenant_and_last_lease(phone_number):
    """Resolve tenant identity independently from active-lease eligibility.

    Active tenancy is preferred. If none exists, return the most recent real
    tenancy (including an expired active row), excluding pending/rejected
    drafts. Duplicate phone matches are resolved deterministically by the most
    recently updated tenant record.
    """
    normalized = normalize_phone(phone_number)
    digits = _digits(normalized)
    if not digits:
        return TenantLeaseResolution(None, None, "")

    suffix = digits[-10:]
    direct_phone_fields = ("phone", "phone2", "phone3")
    query = Q()
    for field in direct_phone_fields:
        query |= Q(**{f"{field}__icontains": suffix})

    candidates = list(Tenant.objects.filter(query, is_active=True).order_by("-updated_at", "-id"))
    tenant = next(
        (
            item
            for item in candidates
            if any(_phone_matches(digits, getattr(item, field, "")) for field in direct_phone_fields)
        ),
        None,
    )
    if tenant is None:
        return TenantLeaseResolution(None, None, "")

    today = timezone.localdate()
    leases = (
        Lease.objects.filter(
            Q(tenant=tenant)
            | Q(family_members__family_member=tenant)
            | Q(legacy_family_members__tenant=tenant)
        )
        .exclude(status__in=("pending_approval", "rejected"))
        .select_related("tenant", "unit", "unit__property")
        .prefetch_related("unit_occupancies")
        .distinct()
    )

    active = (
        leases.filter(status="active", start_date__lte=today, end_date__gte=today)
        .order_by("-end_date", "-start_date", "-updated_at", "-id")
        .first()
    )
    if active:
        return TenantLeaseResolution(tenant, active, "active")

    historical = list(leases)
    if not historical:
        return TenantLeaseResolution(tenant, None, "")

    def historical_key(lease):
        move_out_dates = [
            item.move_out_date
            for item in lease.unit_occupancies.all()
            if item.move_out_date is not None
        ]
        actual_end = max(move_out_dates) if move_out_dates else lease.end_date
        return (actual_end, lease.start_date, lease.updated_at, lease.pk)

    latest = max(historical, key=historical_key)
    return TenantLeaseResolution(tenant, latest, "ended")


def build_lease_context(lease):
    # Keep WhatsApp in sync with the lease ledger.  The canonical model logic
    # excludes cancelled invoices and counts only the lease portion of split
    # payments (security-deposit allocations are not rent payments).
    balance = lease.get_balance
    recent_payments = list(
        Payment.objects.filter(lease=lease)
        .select_related("payment_method")
        .order_by("-payment_date", "-id")[:5]
    )
    maintenance_requests = list(
        lease.maintenance_requests.order_by("-reported_date", "-id")[:5]
    )
    return LeaseContext(
        lease=lease,
        balance=balance,
        recent_payments=recent_payments,
        maintenance_requests=maintenance_requests,
    )


def lease_option_lines(leases):
    lines = ["We found multiple active leases linked to this WhatsApp number.", "", "Please reply with the number:"]
    for index, lease in enumerate(leases, start=1):
        prop = lease.unit.property.property_name
        unit = lease.unit.unit_number
        lines.append(f"{index}. {prop} - Unit {unit}")
    lines.append(f"{len(leases) + 1}. Other")
    return "\n".join(lines)


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phone_q(prefix, suffix):
    query = Q()
    for field in TENANT_PHONE_FIELDS:
        query |= Q(**{f"{prefix}{field}__icontains": suffix})
    return query


def _tenant_phone_values(tenant):
    if tenant is None:
        return []
    return [getattr(tenant, field, "") for field in TENANT_PHONE_FIELDS]


def _phone_matches(target_digits, candidate):
    candidate_digits = _digits(candidate)
    if not candidate_digits:
        return False
    return candidate_digits.endswith(target_digits[-10:]) or target_digits.endswith(candidate_digits[-10:])
