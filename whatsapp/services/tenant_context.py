from dataclasses import dataclass
from decimal import Decimal
import re

from django.db.models import Q, Sum

from invoices.models import Invoice
from leases.models import Lease
from payments.models import Payment
from whatsapp.services.whatsapp import WhatsAppService


PHONE_FIELDS = ("phone", "phone2", "phone3", "emergency_contact_phone")


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
    return Lease.objects.select_related("tenant", "unit", "unit__property").filter(status="active")


def find_active_leases_for_phone(phone_number):
    normalized = normalize_phone(phone_number)
    digits = _digits(normalized)
    if not digits:
        return Lease.objects.none()

    tenant_phone_query = Q()
    for field in PHONE_FIELDS:
        tenant_phone_query |= Q(**{f"tenant__{field}__icontains": digits[-10:]})

    lease_ids = set(
        active_leases()
        .filter(tenant_phone_query)
        .values_list("id", flat=True)
    )

    lease_ids.update(
        Lease.objects.filter(
            status="active",
            family_members__family_member__phone__icontains=digits[-10:],
        ).values_list("id", flat=True)
    )
    lease_ids.update(
        Lease.objects.filter(
            status="active",
            legacy_family_members__tenant__phone__icontains=digits[-10:],
        ).values_list("id", flat=True)
    )

    for lease in active_leases().prefetch_related("family_members__family_member", "legacy_family_members__tenant"):
        phones = []
        tenant = lease.tenant
        for field in PHONE_FIELDS:
            phones.append(getattr(tenant, field, ""))
        for member in lease.family_members.all():
            phones.append(getattr(member.family_member, "phone", ""))
        for member in lease.legacy_family_members.all():
            phones.append(getattr(member.tenant, "phone", ""))
        if any(_phone_matches(digits, candidate) for candidate in phones):
            lease_ids.add(lease.pk)

    return active_leases().filter(id__in=lease_ids).order_by("unit__property__property_name", "unit__unit_number")


def find_active_leases_for_text(text, phone_number=""):
    normalized_text = _compact(text)
    text_digits = _digits(text)
    text_number_groups = _number_groups(text)
    phone_digits = _digits(normalize_phone(phone_number))
    candidates = []

    if not normalized_text and not text_digits:
        return Lease.objects.none()

    for lease in active_leases().prefetch_related("family_members__family_member", "legacy_family_members__tenant"):
        tenant = lease.tenant
        unit = lease.unit
        prop = unit.property
        score = 0

        unit_key = _compact(getattr(unit, "unit_number", ""))
        prop_key = _compact(getattr(prop, "property_name", ""))
        tenant_key = _compact(f"{getattr(tenant, 'first_name', '')} {getattr(tenant, 'last_name', '')}")
        unit_digits = _digits(getattr(unit, "unit_number", ""))
        unit_number_groups = _number_groups(getattr(unit, "unit_number", ""))
        prop_number_groups = set(_number_groups(getattr(prop, "property_name", "")))
        unit_only_number_groups = [group for group in unit_number_groups if group not in prop_number_groups]

        if normalized_text:
            if unit_key and (unit_key in normalized_text or normalized_text in unit_key):
                score += 70
            if prop_key and (prop_key in normalized_text or normalized_text in prop_key):
                score += 35
            if tenant_key and any(part and len(part) >= 3 and part in tenant_key for part in normalized_text.split()):
                score += 65
            if text_digits and len(text_digits) <= 4 and unit_digits and _same_small_number(text_digits, unit_digits):
                score += 45
            elif _has_matching_number_group(text_number_groups, unit_only_number_groups):
                score += 45

        phones = []
        for field in PHONE_FIELDS:
            phones.append(getattr(tenant, field, ""))
        for member in lease.family_members.all():
            phones.append(getattr(member.family_member, "phone", ""))
        for member in lease.legacy_family_members.all():
            phones.append(getattr(member.tenant, "phone", ""))

        if text_digits and any(_phone_matches(text_digits, candidate) for candidate in phones):
            score += 80
        if phone_digits and any(_phone_matches(phone_digits, candidate) for candidate in phones):
            score += 25

        if score >= 60:
            candidates.append((score, lease.pk))

    candidates.sort(reverse=True)
    lease_ids = []
    for _, lease_id in candidates[:5]:
        if lease_id not in lease_ids:
            lease_ids.append(lease_id)
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


def _compact(value):
    return "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or "")).strip()


def _phone_matches(target_digits, candidate):
    candidate_digits = _digits(candidate)
    if not candidate_digits:
        return False
    return candidate_digits.endswith(target_digits[-10:]) or target_digits.endswith(candidate_digits[-10:])


def _same_small_number(left, right):
    try:
        return int(left[-3:]) == int(right[-3:])
    except (TypeError, ValueError):
        return False


def _number_groups(value):
    return [int(part) for part in re.findall(r"\d+", str(value or ""))]


def _has_matching_number_group(left_groups, right_groups):
    if not left_groups or not right_groups:
        return False
    return bool(set(left_groups) & set(right_groups))
