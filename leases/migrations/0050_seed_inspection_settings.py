from django.db import migrations


def seed_inspection_settings(apps, schema_editor):
    InspectionType = apps.get_model("leases", "InspectionType")
    InspectionCategory = apps.get_model("leases", "InspectionCategory")
    InspectionStatus = apps.get_model("leases", "InspectionStatus")
    InspectionItem = apps.get_model("leases", "InspectionItem")
    InspectionTemplate = apps.get_model("leases", "InspectionTemplate")

    for order, name in enumerate(
        [
            "Move In",
            "Move Out",
            "Routine Inspection",
            "Maintenance Inspection",
            "Annual Inspection",
        ],
        start=10,
    ):
        InspectionType.objects.get_or_create(
            name=name,
            defaults={"display_order": order, "active": True},
        )

    statuses = [
        ("Excellent", "success"),
        ("Good", "primary"),
        ("Fair", "warning"),
        ("Needs Repair", "info"),
        ("Damaged", "danger"),
        ("Missing", "dark"),
        ("Not Applicable", "secondary"),
    ]
    for order, (name, color) in enumerate(statuses, start=10):
        InspectionStatus.objects.get_or_create(
            name=name,
            defaults={"badge_color": color, "display_order": order, "active": True},
        )

    category_items = {
        "Entrance": ["Door", "Lock", "Light"],
        "Living Room": ["Paint", "Ceiling Fan", "Light", "Switch", "Window", "Door"],
        "Bedroom": ["Paint", "Light", "Window", "Door", "Wardrobe"],
        "Kitchen": ["Sink", "Faucet", "Stove", "Exhaust Fan", "Cabinet", "Counter Top"],
        "Bathroom": [
            "Shower",
            "Wash Basin",
            "Toilet Seat",
            "Flush Tank",
            "Mirror",
            "Exhaust Fan",
            "Light",
        ],
        "Balcony": ["Floor", "Railing", "Drain"],
        "Electrical": ["Main Breaker", "Sockets", "Switches"],
        "Plumbing": ["Water Lines", "Drainage", "Leaks"],
    }

    template_items = []
    cat_order = 10
    for category_name, items in category_items.items():
        category, _ = InspectionCategory.objects.get_or_create(
            name=category_name,
            defaults={"display_order": cat_order, "active": True},
        )
        item_order = 10
        for item_name in items:
            item, _ = InspectionItem.objects.get_or_create(
                category=category,
                item_name=item_name,
                defaults={
                    "display_order": item_order,
                    "required": True,
                    "allow_photos": True,
                    "allow_damage_cost": True,
                    "allow_notes": True,
                    "active": True,
                },
            )
            template_items.append(item)
            item_order += 10
        cat_order += 10

    template, _ = InspectionTemplate.objects.get_or_create(
        name="Apartment Standard",
        defaults={
            "description": "Starter configurable apartment inspection template.",
            "display_order": 10,
            "active": True,
        },
    )
    if not template.items.exists():
        template.items.set(template_items)


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0049_leaseinspection_inspector_name"),
    ]

    operations = [
        migrations.RunPython(seed_inspection_settings, migrations.RunPython.noop),
    ]
