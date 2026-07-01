import re

from django.db import migrations


def mark_smart_meter_units(apps, schema_editor):
    Unit = apps.get_model("properties", "Unit")
    qs = Unit.objects.select_related("property")
    for unit in qs.iterator():
        property_name = (getattr(unit.property, "property_name", "") or "").strip().lower()
        unit_number = (unit.unit_number or "").strip().lower()
        should_mark = False
        if property_name == "f54":
            should_mark = True
        if property_name == "f56" and "basement" in unit_number:
            should_mark = True
        if property_name == "f56" and re.search(r"flat\s*#?\s*0?[123]\b", unit_number):
            should_mark = True
        if should_mark and not unit.is_smart_meter:
            unit.is_smart_meter = True
            unit.save(update_fields=["is_smart_meter"])


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0019_property_colony_property_covered_area_type_and_more"),
    ]

    operations = [
        migrations.RunPython(mark_smart_meter_units, migrations.RunPython.noop),
    ]
