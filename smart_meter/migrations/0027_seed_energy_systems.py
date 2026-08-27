from datetime import date

from django.db import migrations


EFFECTIVE_DATE = date(2026, 8, 27)


def seed_energy_systems(apps, schema_editor):
    Meter = apps.get_model("smart_meter", "Meter")
    MeterCheckGroup = apps.get_model("smart_meter", "MeterCheckGroup")
    EnergySystem = apps.get_model("smart_meter", "EnergySystem")
    EnergySystemMeterAssignment = apps.get_model("smart_meter", "EnergySystemMeterAssignment")
    UtilityConnection = apps.get_model("smart_meter", "UtilityConnection")

    def meter(number):
        return Meter.objects.filter(meter_number=number).first()

    def group(number):
        check_meter = meter(number)
        if not check_meter:
            return None
        return MeterCheckGroup.objects.filter(check_meter=check_meter).first()

    seeds = (
        {
            "name": "Photon",
            "output": "260305510018",
            "grid": "260305510019",
            "includes_export": False,
            "consumer_id": "1143243650",
            "property_label": "F56",
        },
        {
            "name": "Tesla",
            "output": "260305510004",
            "grid": "260305510020",
            "includes_export": False,
            "consumer_id": "1143090754",
            "property_label": "F54",
        },
        # H9/G10 is intentionally not seeded. Meter 260305510021 is identified
        # as "G10 Three Phase IN", but its physical Energy System role has not
        # been confirmed. A reading profile may be assigned independently in
        # migration 0028; topology must never be inferred from that profile.
    )

    for seed in seeds:
        output_group = group(seed["output"])
        if not output_group:
            continue
        output_meter = meter(seed["output"])
        if output_meter.meter_role == "check" and output_meter.measurement_point != "inverter_output":
            output_meter.measurement_point = "inverter_output"
            output_meter.save(update_fields=["measurement_point"])

        system, _created = EnergySystem.objects.get_or_create(
            output_group=output_group,
            defaults={
                "name": seed["name"],
                "output_meter_includes_grid_export": seed["includes_export"],
            },
        )
        EnergySystemMeterAssignment.objects.get_or_create(
            energy_system=system,
            role="output",
            end_date__isnull=True,
            defaults={"meter": output_meter, "start_date": EFFECTIVE_DATE},
        )

        grid_meter = meter(seed["grid"]) if seed["grid"] else None
        if grid_meter and grid_meter.meter_role == "check":
            if grid_meter.measurement_point != "grid_interface":
                grid_meter.measurement_point = "grid_interface"
                grid_meter.save(update_fields=["measurement_point"])
            if system.grid_interface_meter_id != grid_meter.pk:
                system.grid_interface_meter = grid_meter
                system.save(update_fields=["grid_interface_meter", "updated_at"])
            EnergySystemMeterAssignment.objects.get_or_create(
                energy_system=system,
                role="grid_interface",
                end_date__isnull=True,
                defaults={"meter": grid_meter, "start_date": EFFECTIVE_DATE},
            )

        if (
            not UtilityConnection.objects.filter(energy_system=system).exists()
            and not UtilityConnection.objects.filter(consumer_id=seed["consumer_id"]).exists()
        ):
            UtilityConnection.objects.create(
                energy_system=system,
                consumer_id=seed["consumer_id"],
                property_label=seed["property_label"],
            )

        if grid_meter:
            old_input_group = MeterCheckGroup.objects.filter(check_meter=grid_meter).first()
            if old_input_group and old_input_group.superseded_by_energy_system_id is None:
                old_input_group.superseded_by_energy_system = system
                old_input_group.save(update_fields=["superseded_by_energy_system"])


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0026_energy_reconciliation_models"),
    ]

    operations = [
        migrations.RunPython(seed_energy_systems, migrations.RunPython.noop),
    ]
