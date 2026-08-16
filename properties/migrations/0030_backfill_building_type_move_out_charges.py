from decimal import Decimal

from django.db import migrations


def backfill_move_out_charge_defaults(apps, schema_editor):
    BuildingType = apps.get_model("properties", "BuildingType")
    BuildingType.objects.filter(inspection_incomplete_charge__lte=0).update(
        inspection_incomplete_charge=Decimal("5000.00")
    )
    BuildingType.objects.filter(key_card_not_returned_charge__lte=0).update(
        key_card_not_returned_charge=Decimal("1000.00")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("properties", "0029_property_electricity_unit_rate_and_more"),
    ]

    operations = [
        migrations.RunPython(
            backfill_move_out_charge_defaults,
            migrations.RunPython.noop,
        ),
    ]
