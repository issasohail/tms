from django.db.models import Q
from django.utils import timezone

from leases.models import Lease, LeaseUnitOccupancy


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


def active_tenant_info_for_units(unit_ids, today=None):
    tenant_info = {}
    unit_ids = [unit_id for unit_id in set(unit_ids) if unit_id]
    if not unit_ids:
        return tenant_info

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
        if lease.unit_id not in tenant_info:
            tenant_info[lease.unit_id] = {
                "name": tenant_display_name(lease.tenant),
                "tenant_id": lease.tenant_id,
                "lease_id": lease.id,
            }
    return tenant_info


def attach_active_tenant_names(objects, unit_id_getter, attr_name="tenant_name"):
    unit_ids = [unit_id_getter(obj) for obj in objects]
    names = active_tenant_names_for_units(unit_ids)
    for obj in objects:
        setattr(obj, attr_name, names.get(unit_id_getter(obj), "Vacant"))
    return objects


def attach_tenant_names_for_dates(
    objects,
    unit_id_getter,
    date_getter,
    attr_name="tenant_name",
    lease_attr_name="tenant_lease_id",
):
    dated = []
    for obj in objects:
        unit_id = unit_id_getter(obj)
        used_date = date_getter(obj)
        if unit_id and used_date:
            dated.append((obj, unit_id, used_date))
        else:
            setattr(obj, attr_name, "Vacant")
            setattr(obj, lease_attr_name, None)

    if not dated:
        return objects

    unit_ids = {unit_id for _, unit_id, _ in dated}
    min_date = min(used_date for _, _, used_date in dated)
    max_date = max(used_date for _, _, used_date in dated)

    occupancies = list(
        LeaseUnitOccupancy.objects.filter(
            unit_id__in=unit_ids,
            move_in_date__lte=max_date,
        )
        .filter(Q(move_out_date__isnull=True) | Q(move_out_date__gte=min_date))
        .select_related("lease", "lease__tenant")
        .order_by("unit_id", "-move_in_date", "-id")
    )
    leases = list(
        Lease.objects.filter(
            unit_id__in=unit_ids,
            start_date__lte=max_date,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=min_date))
        .select_related("tenant")
        .order_by("unit_id", "-start_date", "-id")
    )

    for obj, unit_id, used_date in dated:
        name = "Vacant"
        lease_id = None
        for occupancy in occupancies:
            if occupancy.unit_id != unit_id:
                continue
            if occupancy.move_in_date <= used_date and (
                occupancy.move_out_date is None or occupancy.move_out_date >= used_date
            ):
                name = tenant_display_name(occupancy.lease.tenant)
                lease_id = occupancy.lease_id
                break
        if name == "Vacant":
            for lease in leases:
                if lease.unit_id != unit_id:
                    continue
                if lease.start_date <= used_date and (
                    lease.end_date is None or lease.end_date >= used_date
                ):
                    name = tenant_display_name(lease.tenant)
                    lease_id = lease.id
                    break
        setattr(obj, attr_name, name)
        setattr(obj, lease_attr_name, lease_id)

    return objects
