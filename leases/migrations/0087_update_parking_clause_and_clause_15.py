from django.db import migrations


PARKING_CLAUSE = (
    "Motorcycles and other vehicles shall not be brought into or parked in any "
    "residential, hallway, stairway, or other non-designated area of the building. "
    "[PARKING_ASSIGNMENT_TERMS] Reserved motorcycle parking is charged at Rs. "
    "[PARKING_MONTHLY_RATE]/- per space per month and included in monthly billing. "
    "Any vehicle found parked without authorization or outside its assigned space "
    "may incur a penalty of Rs. [UNAUTHORIZED_PARKING_PENALTY]/- for each violation."
)

CLAUSE_15 = (
    "The Owner may terminate this Agreement and require the Tenant to vacate the "
    "premises at any time by giving thirty (30) days' written notice. The Tenant may "
    "terminate this Agreement only after completing the minimum occupancy period "
    "stated in Clause 6 and must give thirty (30) days' written notice. If the Tenant "
    "vacates before completing the minimum occupancy period, the early-termination "
    "obligations and penalties stated in Clause 6 shall apply."
)


def update_parking_clause_and_clause_15(apps, schema_editor):
    AgreementPlaceholder = apps.get_model("leases", "AgreementPlaceholder")
    DefaultClause = apps.get_model("leases", "DefaultClause")

    DefaultClause.objects.update_or_create(
        clause_number=29,
        is_active=True,
        defaults={"category": "parking", "body": PARKING_CLAUSE},
    )
    DefaultClause.objects.update_or_create(
        clause_number=15,
        is_active=True,
        defaults={"category": "general", "body": CLAUSE_15},
    )

    placeholders = (
        ("PARKING_CLAUSE", "Complete Parking Clause", "Complete effective parking clause for backward compatibility.", 220),
        ("PARKING_ENABLED", "Parking Enabled", "Whether the effective parking policy is enabled for the lease.", 221),
        ("PARKING_SPACE", "Assigned Parking Space", "Assigned parking-space label, or Not assigned.", 222),
        ("PARKING_ASSIGNMENT_TERMS", "Parking Assignment Terms", "Assignment wording based on the lease's active parking allocation.", 223),
        ("PARKING_MONTHLY_RATE", "Parking Monthly Rate", "Effective monthly rate for the assigned or available parking space.", 224),
        ("UNAUTHORIZED_PARKING_PENALTY", "Unauthorized Parking Penalty", "Effective penalty for each unauthorized parking violation.", 225),
    )
    for key, label, description, sort_order in placeholders:
        AgreementPlaceholder.objects.update_or_create(
            key=key,
            defaults={
                "label": label,
                "description": description,
                "category": "Parking Clause",
                "source_type": "system",
                "resolver_key": key.lower(),
                "default_value": "",
                "is_active": True,
                "sort_order": sort_order,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("leases", "0086_alter_defaultclause_category")]
    operations = [
        migrations.RunPython(
            update_parking_clause_and_clause_15,
            migrations.RunPython.noop,
        )
    ]
