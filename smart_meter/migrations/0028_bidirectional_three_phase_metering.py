from django.db import migrations, models


THREE_PHASE_METER_NUMBERS = (
    "260305510019",
    "260305510020",
    "260305510021",
)


def configure_three_phase_profiles(apps, schema_editor):
    Meter = apps.get_model("smart_meter", "Meter")
    Meter.objects.filter(meter_number__in=THREE_PHASE_METER_NUMBERS).update(
        reading_profile="total_and_per_phase"
    )


def restore_auto_profiles(apps, schema_editor):
    Meter = apps.get_model("smart_meter", "Meter")
    Meter.objects.filter(
        meter_number__in=THREE_PHASE_METER_NUMBERS,
        reading_profile="total_and_per_phase",
    ).update(reading_profile="auto")


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0027_seed_energy_systems"),
    ]

    operations = [
        migrations.AddField(
            model_name="meter",
            name="reading_profile",
            field=models.CharField(
                choices=[
                    ("auto", "Auto"),
                    ("total_only", "Total only"),
                    ("total_and_per_phase", "Total + per-phase"),
                ],
                default="auto",
                help_text=(
                    "Controls whether direct polling requests only cumulative totals or also "
                    "retains phase A/B/C measurements. Auto preserves existing meter behavior."
                ),
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="livereading",
            name="forward_active_energy_kwh",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text=(
                    "Cumulative forward/import active-energy register; mirrors total_energy when read."
                ),
                max_digits=14,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="livereading",
            name="reverse_active_energy_kwh",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text=(
                    "Cumulative reverse/export active-energy register; never used for tenant billing."
                ),
                max_digits=14,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="meterreading",
            name="forward_active_energy_kwh",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text=(
                    "Cumulative forward/import active-energy register; mirrors total_energy when read."
                ),
                max_digits=14,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="meterreading",
            name="reverse_active_energy_kwh",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text=(
                    "Cumulative reverse/export active-energy register; never used for tenant billing."
                ),
                max_digits=14,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="meterreading",
            name="power_a",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name="meterreading",
            name="power_b",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name="meterreading",
            name="power_c",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=9, null=True),
        ),
        migrations.AlterField(
            model_name="metercommand",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("credit_control", "Credit Control"),
                    ("payment", "Payment"),
                    ("prepaid", "Prepaid Pilot"),
                    ("system", "System"),
                    ("schedule", "Timing Schedule"),
                    ("energy_probe", "Energy Probe (No Persistence)"),
                    ("energy_probe_persist", "Energy Probe (Persist)"),
                ],
                db_index=True,
                default="manual",
                max_length=24,
            ),
        ),
        migrations.RunPython(configure_three_phase_profiles, restore_auto_profiles),
    ]
