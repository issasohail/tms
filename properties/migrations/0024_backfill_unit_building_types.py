from django.db import migrations


def backfill_unit_building_types(apps, schema_editor):
    BuildingType = apps.get_model("properties", "BuildingType")
    Unit = apps.get_model("properties", "Unit")

    active_types = BuildingType.objects.filter(is_active=True)
    single_room = active_types.filter(code__in=(
        "single_room",
        "single_room_attached_bath_kitchen",
    )).order_by("sort_order", "id").first()
    two_room = active_types.filter(code="two_room_flat").first()
    fallback = active_types.order_by("sort_order", "id").first()

    for unit in Unit.objects.filter(building_type_id__isnull=True).select_related(
        "property"
    ):
        property_name = (unit.property.property_name or "").lower()
        if "f56" in property_name and "basement" in property_name:
            building_type = single_room or two_room or fallback
        else:
            building_type = two_room or fallback
        if building_type:
            unit.building_type_id = building_type.pk
            unit.save(update_fields=["building_type"])


class Migration(migrations.Migration):
    dependencies = [
        ("properties", "0023_building_type_and_unit_assignment"),
    ]

    operations = [
        migrations.RunPython(
            backfill_unit_building_types,
            migrations.RunPython.noop,
        ),
    ]
