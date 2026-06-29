from django.db import migrations


def rename_and_duplicate_rooms(apps, schema_editor):
    InspectionCategory = apps.get_model("leases", "InspectionCategory")
    InspectionItem = apps.get_model("leases", "InspectionItem")
    InspectionTemplate = apps.get_model("leases", "InspectionTemplate")

    renames = {
        "Bedroom": "Bedroom1",
        "Bedrrom": "Bedroom1",
        "Bathroom": "Bathroom1",
        "Bathrrom": "Bathroom1",
    }
    for old_name, new_name in renames.items():
        old = InspectionCategory.objects.filter(name__iexact=old_name).first()
        if old and not InspectionCategory.objects.filter(name__iexact=new_name).exclude(pk=old.pk).exists():
            old.name = new_name
            old.save(update_fields=["name"])

    def clone_room(source_name, target_name, order):
        source = InspectionCategory.objects.filter(name__iexact=source_name).first()
        if not source:
            return
        target, _ = InspectionCategory.objects.get_or_create(
            name=target_name,
            defaults={"display_order": order, "active": True},
        )
        copied_items = []
        for item in InspectionItem.objects.filter(category=source).order_by("display_order", "item_name"):
            new_item, _ = InspectionItem.objects.get_or_create(
                category=target,
                item_name=item.item_name,
                defaults={
                    "display_order": item.display_order,
                    "required": item.required,
                    "allow_photos": item.allow_photos,
                    "allow_damage_cost": item.allow_damage_cost,
                    "allow_notes": item.allow_notes,
                    "active": item.active,
                },
            )
            copied_items.append(new_item)
        for template in InspectionTemplate.objects.filter(active=True):
            existing_source_items = template.items.filter(category=source)
            if existing_source_items.exists():
                template.items.add(*copied_items)

    clone_room("Bedroom1", "Bedroom2", 35)
    clone_room("Bathroom1", "Bathroom2", 55)


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0051_alter_inspectionphoto_image"),
    ]

    operations = [
        migrations.RunPython(rename_and_duplicate_rooms, migrations.RunPython.noop),
    ]
