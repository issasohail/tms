from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from invoices.models import Invoice
from leases.models import Lease
from payments.models import Payment
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


def build_lease_context(lease):
    invoices_total = (
        Invoice.objects.filter(lease=lease).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    payments_total = (
        Payment.objects.filter(lease=lease).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    balance = invoices_total - payments_total
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
