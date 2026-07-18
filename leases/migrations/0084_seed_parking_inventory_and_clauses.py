from decimal import Decimal

from django.db import migrations


CLAUSE_11_ADDITION = (
    " No music or loud noise is allowed at any time. No person other than those "
    "named in the lease may stay in the premises; the Tenant must meet all other "
    "visitors outside the building. Only shoes may be kept outside the Tenant's "
    "entrance door; all other belongings must remain inside the premises. Tenant "
    "agrees that failure to make payment on time may result in interruption of "
    "utility services, including electricity, water, and internet."
)

CLAUSE_13 = (
    "That the said premises has been handed over in working order, including "
    "[INVENTORY_LIST]. The Tenant acknowledges receipt of these items and shall "
    "return them in the same condition, subject to reasonable wear and tear."
)

CLAUSE_29 = "[PARKING_CLAUSE]"

CLAUSE_30 = (
    "Blankets may not be washed using the premises' water supply. The Tenant must "
    "use dry cleaning or obtain prior permission from Building Maintenance; "
    "otherwise, a penalty of Rs. [WATER_ABUSE_PENALTY]/- per item may be charged."
)


def _append_once(value, addition):
    value = (value or "").rstrip()
    marker = "No music or loud noise is allowed at any time."
    return value if marker in value else f"{value}{addition}"


def seed_parking_inventory_and_clauses(apps, schema_editor):
    Property = apps.get_model("properties", "Property")
    Unit = apps.get_model("properties", "Unit")
    Lease = apps.get_model("leases", "Lease")
    LeaseRenewal = apps.get_model("leases", "LeaseRenewal")
    DefaultClause = apps.get_model("leases", "DefaultClause")
    LeaseAgreementClause = apps.get_model("leases", "LeaseAgreementClause")
    LeaseRenewalClause = apps.get_model("leases", "LeaseRenewalClause")
    AgreementPlaceholder = apps.get_model("leases", "AgreementPlaceholder")
    LeaseVehicleType = apps.get_model("leases", "LeaseVehicleType")
    InventoryItemDefinition = apps.get_model("leases", "InventoryItemDefinition")
    UnitInventoryItem = apps.get_model("leases", "UnitInventoryItem")
    LeaseInventoryItem = apps.get_model("leases", "LeaseInventoryItem")
    ParkingPolicy = apps.get_model("leases", "ParkingPolicy")
    ParkingSpace = apps.get_model("leases", "ParkingSpace")
    ItemCategory = apps.get_model("invoices", "ItemCategory")

    definitions = {}
    for code, name, quantity, include, order in (
        ("ceiling_fan", "Ceiling Fan", 3, True, 10),
        ("exhaust_fan", "Exhaust Fan", 2, True, 20),
        ("ceiling_light", "Ceiling Light", 16, True, 30),
        ("stove", "Stove", 0, True, 40),
        ("wardrobe", "Wardrobe", 2, True, 50),
        ("keys", "Keys", 2, False, 60),
    ):
        item, _ = InventoryItemDefinition.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "unit_label": "item",
                "default_quantity": quantity,
                "default_condition": "Working order",
                "include_in_clause": include,
                "sort_order": order,
                "is_active": True,
            },
        )
        definitions[code] = item

    unit_fields = {
        "ceiling_fan": "ceiling_fan",
        "exhaust_fan": "exhaust_fan",
        "ceiling_light": "ceiling_lights",
        "stove": "stove",
        "wardrobe": "wardrobes",
        "keys": "keys",
    }
    for unit in Unit.objects.all().iterator():
        for code, field in unit_fields.items():
            value = getattr(unit, field, None)
            if value is None:
                value = definitions[code].default_quantity
            UnitInventoryItem.objects.update_or_create(
                unit_id=unit.pk,
                item_id=definitions[code].pk,
                defaults={
                    "quantity": max(0, int(value)),
                    "condition": "Working order",
                    "is_included": True,
                },
            )

    lease_fields = {
        "ceiling_fan": "inventory_ceiling_fans",
        "exhaust_fan": "inventory_exhaust_fans",
        "ceiling_light": "inventory_ceiling_lights",
        "stove": "inventory_stove",
        "wardrobe": "inventory_wardrobes",
        "keys": "inventory_keys",
    }
    for lease in Lease.objects.select_related("unit").all().iterator():
        for code, lease_field in lease_fields.items():
            value = getattr(lease, lease_field, None)
            if code in {"ceiling_fan", "exhaust_fan", "ceiling_light", "stove"}:
                value = getattr(lease.unit, unit_fields[code], value)
            if value is None:
                value = getattr(lease.unit, unit_fields[code], None)
            if value is None:
                value = definitions[code].default_quantity
            LeaseInventoryItem.objects.update_or_create(
                lease_id=lease.pk,
                item_id=definitions[code].pk,
                defaults={
                    "quantity": max(0, int(value)),
                    "condition": "Working order",
                    "is_included": True,
                    "snapshot_source": "migration",
                },
            )

    basement = Property.objects.filter(property_name__icontains="F56").filter(
        property_name__icontains="Basement"
    ).first()
    if basement is None:
        basement = Property.objects.filter(pk=8).first()
    motorcycle_type, _ = LeaseVehicleType.objects.get_or_create(
        code="motorcycle",
        defaults={"name": "Motorcycle", "is_active": True, "sort_order": 20},
    )
    ItemCategory.objects.get_or_create(name="Motorcycle Parking")
    if basement:
        ParkingPolicy.objects.update_or_create(
            property_id=basement.pk,
            defaults={
                "enabled": True,
                "monthly_rate": Decimal("2000.00"),
                "unauthorized_parking_penalty": Decimal("5000.00"),
            },
        )
        for number in range(1, 7):
            ParkingSpace.objects.update_or_create(
                property_id=basement.pk,
                label=f"P-{number:02d}",
                defaults={
                    "vehicle_type_id": motorcycle_type.pk,
                    "monthly_rate_override": Decimal("2000.00"),
                    "is_active": True,
                },
            )

    for key, label, description, order in (
        ("INVENTORY_LIST", "Inventory List", "Effective lease inventory list.", 210),
        ("PARKING_CLAUSE", "Parking Clause", "Effective reserved parking terms.", 220),
        ("WATER_ABUSE_PENALTY", "Water Abuse Penalty", "Global per-item water abuse penalty.", 230),
    ):
        AgreementPlaceholder.objects.update_or_create(
            key=key,
            defaults={
                "label": label,
                "description": description,
                "category": "Lease Terms",
                "source_type": "system",
                "resolver_key": key.lower(),
                "default_value": "",
                "is_active": True,
                "sort_order": order,
            },
        )

    default_11 = DefaultClause.objects.filter(clause_number=11, is_active=True).first()
    if default_11:
        default_11.body = _append_once(default_11.body, CLAUSE_11_ADDITION)
        default_11.save(update_fields=["body", "updated_at"])
    else:
        DefaultClause.objects.create(
            clause_number=11, category="general",
            body=CLAUSE_11_ADDITION.strip(), is_active=True,
        )
    for number, body in ((13, CLAUSE_13), (29, CLAUSE_29), (30, CLAUSE_30)):
        DefaultClause.objects.update_or_create(
            clause_number=number,
            is_active=True,
            defaults={"category": "general", "body": body},
        )

    active_leases = Lease.objects.filter(status="active").select_related("unit")
    for lease in active_leases.iterator():
        clause_11, _ = LeaseAgreementClause.objects.get_or_create(
            lease_id=lease.pk,
            clause_number=11,
            defaults={"template_text": CLAUSE_11_ADDITION.strip()},
        )
        new_11 = _append_once(clause_11.template_text, CLAUSE_11_ADDITION)
        if new_11 != clause_11.template_text:
            clause_11.template_text = new_11
            clause_11.save(update_fields=["template_text", "updated_at"])
        for number, body in ((13, CLAUSE_13), (30, CLAUSE_30)):
            LeaseAgreementClause.objects.update_or_create(
                lease_id=lease.pk,
                clause_number=number,
                defaults={"template_text": body, "is_customized": False},
            )
        if basement and lease.unit.property_id == basement.pk:
            LeaseAgreementClause.objects.update_or_create(
                lease_id=lease.pk,
                clause_number=29,
                defaults={"template_text": CLAUSE_29, "is_customized": False},
            )

    renewals = LeaseRenewal.objects.filter(lease__status="active").select_related(
        "lease__unit"
    )
    for renewal in renewals.iterator():
        clause_11, _ = LeaseRenewalClause.objects.get_or_create(
            renewal_id=renewal.pk,
            clause_number=11,
            defaults={"template_text": CLAUSE_11_ADDITION.strip()},
        )
        new_11 = _append_once(clause_11.template_text, CLAUSE_11_ADDITION)
        if new_11 != clause_11.template_text:
            clause_11.template_text = new_11
            clause_11.save(update_fields=["template_text"])
        for number, body in ((13, CLAUSE_13), (30, CLAUSE_30)):
            LeaseRenewalClause.objects.update_or_create(
                renewal_id=renewal.pk,
                clause_number=number,
                defaults={"template_text": body, "is_customized": False},
            )
        if basement and renewal.lease.unit.property_id == basement.pk:
            LeaseRenewalClause.objects.update_or_create(
                renewal_id=renewal.pk,
                clause_number=29,
                defaults={"template_text": CLAUSE_29, "is_customized": False},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_globalsettings_default_motorcycle_parking_rate_and_more"),
        ("invoices", "0021_rename_allocation_to_payment_detail"),
        ("properties", "0024_backfill_unit_building_types"),
        ("leases", "0083_inventoryitemdefinition_and_more"),
    ]

    operations = [migrations.RunPython(seed_parking_inventory_and_clauses, migrations.RunPython.noop)]
