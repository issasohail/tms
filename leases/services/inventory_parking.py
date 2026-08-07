from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from core.models import GlobalSettings
from invoices.models import ItemCategory, RecurringCharge
from leases.models_parking_inventory import (
    InventoryItemDefinition,
    LeaseInventoryItem,
    ParkingPolicy,
    PropertyInventoryItem,
    UnitInventoryItem,
)


ZERO = Decimal("0.00")

LEASE_INVENTORY_FIELD_BY_CODE = {
    "ceiling_fan": "inventory_ceiling_fans",
    "exhaust_fan": "inventory_exhaust_fans",
    "ceiling_light": "inventory_ceiling_lights",
    "stove": "inventory_stove",
    "wardrobe": "inventory_wardrobes",
    "keys": "inventory_keys",
}


def effective_parking_policy(lease=None, unit=None, property_obj=None):
    if lease is not None:
        unit = lease.unit
        property_obj = unit.property
        policy = ParkingPolicy.objects.filter(lease=lease).first()
        if policy:
            return policy
    if unit is not None:
        property_obj = unit.property
        policy = ParkingPolicy.objects.filter(unit=unit).first()
        if policy:
            return policy
    if property_obj is not None:
        policy = ParkingPolicy.objects.filter(property=property_obj).first()
        if policy:
            return policy
    settings_obj = GlobalSettings.get_solo()
    return {
        "enabled": settings_obj.default_parking_enabled,
        "monthly_rate": settings_obj.default_motorcycle_parking_rate,
        "unauthorized_parking_penalty": settings_obj.default_unauthorized_parking_penalty,
        "scope_label": "Global default",
    }


def policy_value(policy, field):
    return policy[field] if isinstance(policy, dict) else getattr(policy, field)


def policy_scope_label(policy):
    return policy["scope_label"] if isinstance(policy, dict) else policy.scope_label


def _definition_defaults():
    return {
        item.id: {
            "item": item,
            "quantity": item.default_quantity,
            "condition": item.default_condition,
            "is_included": item.is_active,
            "source": "Global",
            "override": None,
        }
        for item in InventoryItemDefinition.objects.filter(is_active=True)
    }


def effective_inventory(property_obj=None, unit=None, lease=None):
    if lease is not None:
        unit = lease.unit
        rows = list(lease.inventory_items.select_related("item").all())
        if rows:
            return [{
                "item": row.item, "quantity": row.quantity,
                "condition": row.condition or row.item.default_condition,
                "is_included": row.is_included, "source": "Lease", "override": row,
            } for row in rows]
    if unit is not None:
        property_obj = unit.property
    values = _definition_defaults()
    if property_obj is not None:
        for row in property_obj.inventory_items.select_related("item").all():
            values[row.item_id].update({
                "quantity": row.quantity, "condition": row.condition,
                "is_included": row.is_included, "source": "Property", "override": row,
            })
    if unit is not None:
        for row in unit.inventory_items.select_related("item").all():
            values[row.item_id].update({
                "quantity": row.quantity, "condition": row.condition,
                "is_included": row.is_included, "source": "Unit", "override": row,
            })
    return list(values.values())


@transaction.atomic
def copy_inventory_defaults(scope_obj, item_id=None, *, overwrite=True, replace=False):
    if hasattr(scope_obj, "tenant_id") and hasattr(scope_obj, "unit_id"):
        model, target_field = LeaseInventoryItem, "lease"
        defaults = effective_inventory(unit=scope_obj.unit)
        source = "unit"
    elif hasattr(scope_obj, "property_id"):
        model, target_field = UnitInventoryItem, "unit"
        defaults = effective_inventory(property_obj=scope_obj.property)
        source = None
    else:
        model, target_field = PropertyInventoryItem, "property"
        defaults = list(_definition_defaults().values())
        source = None
    if item_id:
        defaults = [row for row in defaults if row["item"].id == int(item_id)]
    if replace:
        model.objects.filter(**{target_field: scope_obj}).exclude(
            item_id__in=[row["item"].id for row in defaults]
        ).delete()
    copied = 0
    for row in defaults:
        values = {
            "quantity": row["quantity"], "condition": row["condition"],
            "is_included": row["is_included"],
        }
        if model is LeaseInventoryItem:
            values["snapshot_source"] = source
        lookup = {target_field: scope_obj, "item": row["item"]}
        created = False
        if overwrite:
            model.objects.update_or_create(**lookup, defaults=values)
            copied += 1
        else:
            _, created = model.objects.get_or_create(**lookup, defaults=values)
            copied += int(created)
        if model is LeaseInventoryItem and (overwrite or created):
            sync_lease_field_from_inventory_item(
                scope_obj, row["item"], values["quantity"]
            )
    return copied


@transaction.atomic
def sync_lease_inventory_from_fields(
    lease,
    changed_fields=None,
    *,
    only_inherited=False,
):
    """Keep agreement inventory rows aligned with the legacy lease form fields."""
    field_filter = set(changed_fields or LEASE_INVENTORY_FIELD_BY_CODE.values())
    definitions = {
        item.code: item
        for item in InventoryItemDefinition.objects.filter(
            code__in=LEASE_INVENTORY_FIELD_BY_CODE
        )
    }
    existing = {
        row.item.code: row
        for row in lease.inventory_items.select_related("item").filter(
            item__code__in=LEASE_INVENTORY_FIELD_BY_CODE
        )
    }
    inherited = {
        row["item"].code: row
        for row in effective_inventory(unit=lease.unit)
        if row["item"].code in LEASE_INVENTORY_FIELD_BY_CODE
    }
    updated = 0

    for code, field_name in LEASE_INVENTORY_FIELD_BY_CODE.items():
        if field_name not in field_filter:
            continue
        item = definitions.get(code)
        if item is None:
            continue
        raw_quantity = getattr(lease, field_name, None)
        if raw_quantity is None:
            continue
        try:
            quantity = max(0, int(raw_quantity))
        except (TypeError, ValueError):
            quantity = 0

        row = existing.get(code)
        if row is not None:
            if only_inherited and row.snapshot_source == "lease":
                continue
            changed = []
            if row.quantity != quantity:
                row.quantity = quantity
                changed.append("quantity")
            if row.snapshot_source != "lease":
                row.snapshot_source = "lease"
                changed.append("snapshot_source")
            if changed:
                row.save(update_fields=changed + ["updated_at"])
                updated += 1
            continue

        inherited_row = inherited.get(code, {})
        LeaseInventoryItem.objects.create(
            lease=lease,
            item=item,
            quantity=quantity,
            condition=inherited_row.get("condition") or item.default_condition,
            is_included=inherited_row.get("is_included", item.is_active),
            snapshot_source="lease",
        )
        updated += 1

    return updated


def sync_lease_field_from_inventory_item(lease, item, quantity):
    """Keep lease-detail/form quantities aligned with Inventory Manager edits."""
    field_name = LEASE_INVENTORY_FIELD_BY_CODE.get(item.code)
    if not field_name:
        return False
    quantity = max(0, int(quantity or 0))
    if getattr(lease, field_name, None) == quantity:
        return False
    setattr(lease, field_name, quantity)
    lease.save(update_fields=[field_name, "updated_at"])
    return True


def ensure_lease_inventory_snapshot(lease):
    if not lease.inventory_items.exists():
        # Preserve any inventory counts explicitly set on the lease itself
        # (e.g. entered on the lease-create form) before falling back to
        # unit/global defaults for anything the lease didn't specify.
        # overwrite=False so this fallback never clobbers rows just written
        # from the lease's own fields.
        sync_lease_inventory_from_fields(lease)
        copy_inventory_defaults(lease, overwrite=False)


def inventory_list_html(lease):
    # Older leases received inventory snapshots during migration. Lease-form
    # edits made afterward updated only the legacy fields, leaving those
    # inherited rows stale. Reconcile them once when the agreement resolves
    # [INVENTORY_LIST], but preserve rows explicitly managed at lease scope.
    sync_lease_inventory_from_fields(lease, only_inherited=True)
    ensure_lease_inventory_snapshot(lease)
    parts = []
    for row in effective_inventory(lease=lease):
        item = row["item"]
        if not row["is_included"] or not item.include_in_clause or row["quantity"] <= 0:
            continue
        condition = f" ({escape(row['condition'])})" if row["condition"] else ""
        parts.append(
            f"<strong>{row['quantity']} {escape(item.name)}</strong>{condition}"
        )
    if not parts:
        return "<strong>no inventory items recorded</strong>"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def parking_clause_html(lease):
    policy = effective_parking_policy(lease=lease)
    if not policy_value(policy, "enabled"):
        return ""
    allocation = (
        lease.parking_allocations.filter(is_active=True, end_date__isnull=True)
        .select_related("parking_space").order_by("parking_space__label").first()
    )
    rate = allocation.agreed_monthly_rate if allocation else policy_value(policy, "monthly_rate")
    penalty = policy_value(policy, "unauthorized_parking_penalty")
    allocation_text = (
        f"The Tenant is assigned reserved parking space <strong>{escape(allocation.parking_space.label)}</strong> "
        if allocation else
        "A vehicle may be parked only in a reserved parking space assigned by the Owner and recorded in this Agreement. "
    )
    return (
        "Motorcycles and other vehicles shall not be brought into or parked in any residential, "
        "hallway, stairway, or other non-designated area of the building. "
        f"{allocation_text}Reserved motorcycle parking is charged at Rs. "
        f"<strong>{rate:,.0f}</strong>/- per space per month and included in monthly billing. "
        "Any vehicle found parked without authorization or outside its assigned space may incur "
        f"a penalty of Rs. <strong>{penalty:,.0f}</strong>/- for each violation."
    )


def _active_parking_allocation(lease):
    return (
        lease.parking_allocations.filter(is_active=True, end_date__isnull=True)
        .select_related("parking_space").order_by("parking_space__label").first()
    )


def parking_space_label(lease):
    allocation = _active_parking_allocation(lease)
    return allocation.parking_space.label if allocation else "Not assigned"


def effective_parking_monthly_rate(lease):
    allocation = _active_parking_allocation(lease)
    if allocation:
        return allocation.agreed_monthly_rate
    return policy_value(effective_parking_policy(lease=lease), "monthly_rate")


def effective_unauthorized_parking_penalty(lease):
    return policy_value(
        effective_parking_policy(lease=lease),
        "unauthorized_parking_penalty",
    )


def parking_assignment_terms_html(lease):
    allocation = _active_parking_allocation(lease)
    if allocation:
        return (
            "The Tenant is assigned reserved parking space "
            f"<strong>{escape(allocation.parking_space.label)}</strong>."
        )
    return (
        "A vehicle may be parked only in a reserved parking space assigned by "
        "the Owner and recorded in this Agreement."
    )


@transaction.atomic
def sync_parking_recurring_charge(allocation):
    category, _ = ItemCategory.objects.get_or_create(
        name="Motorcycle Parking", defaults={"is_active": True}
    )
    charge = allocation.recurring_charge
    if charge is None:
        charge = RecurringCharge(lease=allocation.lease, scope="LEASE", kind="FIXED")
    charge.category = category
    charge.description = f"Reserved motorcycle parking {allocation.parking_space.label}"
    charge.amount = allocation.agreed_monthly_rate
    charge.day_of_month = 1
    charge.start_date = allocation.start_date
    charge.end_date = allocation.end_date
    charge.active = allocation.is_active and allocation.end_date is None
    charge.combine_with_rent = True
    charge.save()
    if allocation.recurring_charge_id != charge.id:
        allocation.recurring_charge = charge
        allocation.save(update_fields=["recurring_charge"])
    return charge
