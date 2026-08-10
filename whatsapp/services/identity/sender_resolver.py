from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from handyman.models import HandymanProfile
from leases.models import Lease
from properties.models import Property
from tenants.models import Tenant
from whatsapp.models import WhatsAppStaffActionLog, WhatsAppStaffPropertyAccess

from .phone_normalizer import normalize_phone_number, phone_matches, searchable_suffix


TENANT_IDENTITY_PHONE_FIELDS = ("phone", "phone2", "phone3")


@dataclass
class SenderContext:
    phone_number: str
    tenant_matches: list = field(default_factory=list)
    staff_matches: list = field(default_factory=list)
    handyman_matches: list = field(default_factory=list)
    owner_matches: list = field(default_factory=list)
    guest_profile: object = None
    available_modes: list = field(default_factory=list)
    active_mode: str = ""
    active_leases: list = field(default_factory=list)
    property_permissions: dict = field(default_factory=dict)
    ambiguous: bool = False

    @property
    def tenant(self):
        return self.tenant_matches[0] if len(self.tenant_matches) == 1 else None

    @property
    def staff_user(self):
        return self.staff_matches[0] if len(self.staff_matches) == 1 else None

    @property
    def handyman(self):
        return self.handyman_matches[0] if len(self.handyman_matches) == 1 else None

    @property
    def has_tenant(self):
        return bool(len(self.tenant_matches) == 1 and self.active_leases)

    @property
    def has_staff(self):
        return len(self.staff_matches) == 1

    @property
    def has_active_tenant(self):
        return self.has_tenant

    @property
    def has_handyman(self):
        return len(self.handyman_matches) == 1


def resolve_sender(phone_number, conversation=None, log_ambiguity=True):
    normalized = normalize_phone_number(phone_number)
    suffix = searchable_suffix(normalized)
    tenants = _matching_tenants(normalized, suffix)
    staff = _matching_staff(normalized, suffix)
    handymen = _matching_handymen(normalized, suffix)
    owners = _matching_owners(normalized, suffix)
    simulator_identity = (conversation.context or {}).get("simulator_identity") if conversation else None
    staff_tenant_simulation = (conversation.context or {}).get("staff_tenant_simulation") if conversation else None
    if simulator_identity:
        role = simulator_identity.get("role")
        object_id = simulator_identity.get("object_id")
        tenants = list(Tenant.objects.filter(pk=object_id, is_active=True)) if role == "tenant" else []
        staff = list(get_user_model().objects.filter(pk=object_id, is_active=True, is_staff=True)) if role == "staff" else []
        handymen = list(HandymanProfile.objects.filter(pk=object_id, is_active=True)) if role == "handyman" else []
        owners = []
    elif staff_tenant_simulation:
        tenants = list(Tenant.objects.filter(pk=staff_tenant_simulation.get("tenant_id"), is_active=True))
        handymen = []
        owners = []
    selected_tenant_id = None
    if conversation:
        selected_tenant_id = (conversation.context or {}).get("selected_tenant_identity_id")
    if selected_tenant_id:
        selected = [item for item in tenants if item.pk == selected_tenant_id]
        if selected:
            tenants = selected
        else:
            conversation.context.pop("selected_tenant_identity_id", None)

    tenant_ids = [item.pk for item in tenants]
    today = timezone.localdate()
    active_leases = list(
        Lease.objects.select_related("tenant", "unit__property")
        .prefetch_related("family_members", "legacy_family_members")
        .filter(
            Q(tenant_id__in=tenant_ids)
            | Q(family_members__family_member_id__in=tenant_ids)
            | Q(legacy_family_members__tenant_id__in=tenant_ids),
            status="active",
            start_date__lte=today,
            end_date__gte=today,
        )
        .distinct()
        .order_by("unit__property__property_name", "unit__unit_number", "id")
    )
    tenant_lease_ids = {}
    for tenant in tenants:
        accessible_lease_ids = {
            lease.pk
            for lease in active_leases
            if lease.tenant_id == tenant.pk
            or any(member.family_member_id == tenant.pk for member in lease.family_members.all())
            or any(member.tenant_id == tenant.pk for member in lease.legacy_family_members.all())
        }
        if accessible_lease_ids:
            tenant_lease_ids[tenant.pk] = accessible_lease_ids

    # A reused phone number on an old tenant record must not create a phantom
    # account choice. Multiple Tenant rows that all grant access to the exact
    # same active lease(s) are one WhatsApp account choice, not repeated copies
    # of the same property/unit. Genuinely different lease sets remain separate.
    tenants = [tenant for tenant in tenants if tenant.pk in tenant_lease_ids]
    tenants = _collapse_duplicate_tenant_access(tenants, tenant_lease_ids, active_leases)
    permissions = {
        user.pk: list(
            WhatsAppStaffPropertyAccess.objects.filter(staff_user=user, is_active=True)
            .values_list("property_id", flat=True)
        )
        for user in staff
    }
    modes = []
    if active_leases:
        modes.append("tenant")
    if staff:
        modes.append("staff")
    if handymen:
        modes.append("handyman")
    if not modes:
        modes.append("guest")
    active_mode = ""
    if conversation and conversation.selected_mode_is_valid and conversation.selected_mode in modes:
        active_mode = conversation.selected_mode
    ambiguous = len(tenants) > 1 or len(staff) > 1
    context = SenderContext(
        phone_number=normalized,
        tenant_matches=tenants,
        staff_matches=staff,
        handyman_matches=handymen,
        owner_matches=owners,
        available_modes=modes,
        active_mode=active_mode,
        active_leases=active_leases,
        property_permissions=permissions,
        ambiguous=ambiguous,
    )
    if ambiguous and log_ambiguity:
        WhatsAppStaffActionLog.objects.create(
            phone_number=normalized,
            action="ambiguous_sender_identity",
            status=WhatsAppStaffActionLog.ACTION_STATUS_BLOCKED,
            details={
                "tenant_match_ids": [item.pk for item in tenants],
                "staff_match_ids": [item.pk for item in staff],
                "available_modes": modes,
            },
        )
    return context



def _collapse_duplicate_tenant_access(tenants, tenant_lease_ids, active_leases):
    """Collapse identities that expose exactly the same active lease set."""
    grouped = {}
    for tenant in tenants:
        signature = tuple(sorted(tenant_lease_ids.get(tenant.pk, set())))
        grouped.setdefault(signature, []).append(tenant)

    lease_tenant_ids = {lease.pk: lease.tenant_id for lease in active_leases}
    collapsed = []
    for signature, group in grouped.items():
        primary_ids = {lease_tenant_ids.get(lease_id) for lease_id in signature}
        representative = next(
            (tenant for tenant in group if tenant.pk in primary_ids),
            None,
        )
        collapsed.append(representative or group[0])
    return collapsed

def _matching_tenants(normalized, suffix):
    if not suffix:
        return []
    query = Q()
    for field_name in TENANT_IDENTITY_PHONE_FIELDS:
        query |= Q(**{f"{field_name}__icontains": suffix})
    candidates = Tenant.objects.filter(query, is_active=True).order_by("id")
    return _dedupe(
        item for item in candidates
        if any(phone_matches(normalized, getattr(item, field_name, "")) for field_name in TENANT_IDENTITY_PHONE_FIELDS)
    )


def _matching_staff(normalized, suffix):
    if not suffix:
        return []
    User = get_user_model()
    candidates = User.objects.filter(
        is_active=True, is_staff=True, whatsapp_number__icontains=suffix
    ).order_by("id")
    return _dedupe(item for item in candidates if phone_matches(normalized, item.whatsapp_number))


def _matching_handymen(normalized, suffix):
    if not suffix:
        return []
    candidates = HandymanProfile.objects.filter(
        Q(phone__icontains=suffix) | Q(whatsapp_number__icontains=suffix), is_active=True
    ).order_by("id")
    return _dedupe(
        item for item in candidates
        if phone_matches(normalized, item.whatsapp_number) or phone_matches(normalized, item.phone)
    )


def _matching_owners(normalized, suffix):
    if not suffix:
        return []
    candidates = Property.objects.filter(owner_phone__icontains=suffix).order_by("id")
    return _dedupe(item for item in candidates if phone_matches(normalized, item.owner_phone))


def _dedupe(items):
    unique = {}
    for item in items:
        unique[item.pk] = item
    return list(unique.values())
