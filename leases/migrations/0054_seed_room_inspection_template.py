from django.db import migrations


def seed_room_template(apps, schema_editor):
    InspectionCategory = apps.get_model("leases", "InspectionCategory")
    InspectionItem = apps.get_model("leases", "InspectionItem")
    InspectionTemplate = apps.get_model("leases", "InspectionTemplate")

    category_names = ["Bedroom1", "Bathroom1", "Kitchen"]
    categories = list(
        InspectionCategory.objects.filter(name__in=category_names).order_by("display_order", "name")
    )
    items = list(
        InspectionItem.objects.filter(category__in=categories, active=True).order_by(
            "category__display_order", "display_order", "item_name"
        )
    )
    template, _ = InspectionTemplate.objects.get_or_create(
        name="Room",
        defaults={
            "description": "Room template with one bedroom, one bathroom, and kitchen.",
            "display_order": 20,
            "active": True,
        },
    )
    template.items.set(items)
    template.item_order = [item.pk for item in items]
    template.save(update_fields=["item_order"])


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0053_inspectiontemplate_item_order"),
    ]

    operations = [
        migrations.RunPython(seed_room_template, migrations.RunPython.noop),
    ]
