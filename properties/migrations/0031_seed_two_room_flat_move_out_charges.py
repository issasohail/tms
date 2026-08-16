from decimal import Decimal

from django.db import migrations


def seed_two_room_flat_move_out_charges(apps, schema_editor):
    BuildingType = apps.get_model("properties", "BuildingType")
    two_room = (
        BuildingType.objects.filter(code="two_room_flat").first()
        or BuildingType.objects.filter(name__iexact="Two Room Flat").first()
    )
    if two_room is None:
        BuildingType.objects.create(
            code="two_room_flat",
            name="Two Room Flat",
            inspection_incomplete_charge=Decimal("5000.00"),
            key_card_not_returned_charge=Decimal("1000.00"),
            is_active=True,
        )
        return
    two_room.code = "two_room_flat"
    two_room.name = "Two Room Flat"
    two_room.inspection_incomplete_charge = Decimal("5000.00")
    two_room.key_card_not_returned_charge = Decimal("1000.00")
    two_room.is_active = True
    two_room.save(update_fields=[
        "code", "name", "inspection_incomplete_charge",
        "key_card_not_returned_charge", "is_active",
    ])


class Migration(migrations.Migration):
    dependencies = [
        ("properties", "0030_backfill_building_type_move_out_charges"),
    ]

    operations = [
        migrations.RunPython(
            seed_two_room_flat_move_out_charges,
            migrations.RunPython.noop,
        ),
    ]
