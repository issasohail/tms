from django.db import migrations, models


SEED_NOTE = (
    "Automatically created for an existing Audit meter by migration 0016. "
    "Assign its downstream Billing meters from the group detail page."
)


def seed_audit_groups(apps, schema_editor):
    Meter = apps.get_model("smart_meter", "Meter")
    MeterCheckGroup = apps.get_model("smart_meter", "MeterCheckGroup")
    Unit = apps.get_model("properties", "Unit")

    audit_meters = Meter.objects.filter(
        meter_role="check",
        unit_id__isnull=False,
    ).order_by("pk")

    for meter in audit_meters.iterator():
        unit = Unit.objects.filter(pk=meter.unit_id).select_related("property").first()
        if unit is None or unit.property_id is None:
            continue

        group_name = (
            f"{unit.property.property_name} - {unit.unit_number} - "
            f"Audit {meter.meter_number}"
        )[:100]
        MeterCheckGroup.objects.get_or_create(
            check_meter_id=meter.pk,
            defaults={
                "name": group_name,
                "property_id": unit.property_id,
                "notes": SEED_NOTE,
                "is_active": True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("smart_meter", "0015_meter_role_history_check_groups"),
    ]

    operations = [
        migrations.AlterField(
            model_name="meter",
            name="meter_role",
            field=models.CharField(
                choices=[("billing", "Billing"), ("check", "Audit")],
                default="billing",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="meterrolehistory",
            name="role",
            field=models.CharField(
                choices=[("billing", "Billing"), ("check", "Audit")],
                max_length=10,
            ),
        ),
        migrations.RunPython(seed_audit_groups, migrations.RunPython.noop),
    ]
