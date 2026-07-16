import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


BACKFILL_REASON = "Initial backfill - all meters set to Billing"


def backfill_meter_roles(apps, schema_editor):
    Meter = apps.get_model("smart_meter", "Meter")
    MeterRoleHistory = apps.get_model("smart_meter", "MeterRoleHistory")
    today = timezone.now().date()

    Meter.objects.all().update(meter_role="billing")
    histories = []
    for meter in Meter.objects.all().iterator():
        start_date = meter.installed_at.date() if meter.installed_at else today
        histories.append(MeterRoleHistory(
            meter_id=meter.id,
            role="billing",
            start_date=start_date,
            is_active=True,
            active_role_key=meter.id,
            reason=BACKFILL_REASON,
        ))
    MeterRoleHistory.objects.bulk_create(histories)


def reverse_backfill_meter_roles(apps, schema_editor):
    MeterRoleHistory = apps.get_model("smart_meter", "MeterRoleHistory")
    MeterRoleHistory.objects.filter(reason=BACKFILL_REASON).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0024_backfill_unit_building_types"),
        ("smart_meter", "0014_meter_role"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MeterCheckGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("check_meter", models.OneToOneField(limit_choices_to={"meter_role": "check"}, on_delete=django.db.models.deletion.CASCADE, related_name="check_group", to="smart_meter.meter")),
                ("property", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="meter_check_groups", to="properties.property")),
            ],
        ),
        migrations.CreateModel(
            name="MeterCheckGroupMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_date", models.DateField()),
                ("end_date", models.DateField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
                ("billing_meter", models.ForeignKey(limit_choices_to={"meter_role": "billing"}, on_delete=django.db.models.deletion.CASCADE, related_name="check_group_memberships", to="smart_meter.meter")),
                ("group", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="smart_meter.metercheckgroup")),
            ],
            options={
                "ordering": ["-start_date", "-id"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("end_date__isnull", True), ("end_date__gte", models.F("start_date")), _connector="OR"),
                        name="meter_check_group_membership_end_after_start",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="MeterRoleHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("billing", "Billing"), ("check", "Check / Audit")], max_length=10)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("active_role_key", models.PositiveIntegerField(blank=True, editable=False, help_text="Internal DB guard: meter id while this role record is active, NULL when closed.", null=True, unique=True)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("meter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="role_history", to="smart_meter.meter")),
            ],
            options={
                "ordering": ["-start_date", "-id"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("end_date__isnull", True), ("end_date__gte", models.F("start_date")), _connector="OR"),
                        name="meter_role_history_end_after_start",
                    ),
                ],
            },
        ),
        migrations.RunPython(backfill_meter_roles, reverse_backfill_meter_roles),
    ]
