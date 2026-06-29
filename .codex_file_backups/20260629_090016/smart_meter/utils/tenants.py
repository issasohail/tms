from django.utils import timezone

from leases.models import Lease


def tenant_display_name(tenant):
    if not tenant:
        return "Vacant"
    if hasattr(tenant, "get_full_name"):
        name = (tenant.get_full_name() or "").strip()
    else:
        first_name = (getattr(tenant, "first_name", "") or "").strip()
        last_name = (getattr(tenant, "last_name", "") or "").strip()
        name = f"{first_name} {last_name}".strip()
    return name or getattr(tenant, "name", "") or str(tenant) or "Vacant"


def active_tenant_names_for_units(unit_ids, today=None):
    names = {}
    unit_ids = [unit_id for unit_id in set(unit_ids) if unit_id]
    if not unit_ids:
        return names

    today = today or timezone.localdate()
    leases = (
        Lease.objects.filter(
            unit_id__in=unit_ids,
            status="active",
            start_date__lte=today,
            end_date__gte=today,
        )
        .select_related("tenant")
        .order_by("unit_id", "-start_date", "-id")
    )
    for lease in leases:
        if lease.unit_id not in names:
            names[lease.unit_id] = tenant_display_name(lease.tenant)
    return names


def attach_active_tenant_names(objects, unit_id_getter, attr_name="tenant_name"):
    unit_ids = [unit_id_getter(obj) for obj in objects]
    names = active_tenant_names_for_units(unit_ids)
    for obj in objects:
        setattr(obj, attr_name, names.get(unit_id_getter(obj), "Vacant"))
    return objects
