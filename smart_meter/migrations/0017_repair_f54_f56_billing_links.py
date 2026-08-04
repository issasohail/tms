from datetime import date
from decimal import Decimal

from django.db import migrations


REPAIR_REASON = "repair_f54_f56_billing_links_0017"


OWNER_BILLED_LEASES = (
    ("F54", "F54-FLAT# 01", date(2026, 7, 1)),
    ("F54", "F54-FLAT# 05", date(2026, 6, 1)),
    ("F56", "F56-FLAT# 03", date(2025, 1, 1)),
    ("F56 Basement", "F56-ROOM# 06", date(2025, 12, 1)),
    ("F56 Basement", "F56-ROOM# 10", date(2025, 12, 1)),
    ("F56 Basement", "F56-ROOM# 11", date(2026, 1, 1)),
)


def _unit(Unit, property_name, unit_number):
    return Unit.objects.filter(
        property__property_name=property_name,
        unit_number=unit_number,
    ).first()


def _lease(Lease, unit, start_date):
    if not unit:
        return None
    return Lease.objects.filter(unit=unit, start_date=start_date).order_by("id").first()


def _reading_value(MeterReading, meter, reading_date, *, after):
    readings = MeterReading.objects.filter(meter=meter)
    if after:
        reading = readings.filter(ts__date__gte=reading_date).order_by("ts", "id").first()
        if not reading:
            reading = readings.filter(ts__date__lte=reading_date).order_by("-ts", "-id").first()
    else:
        reading = readings.filter(ts__date__lte=reading_date).order_by("-ts", "-id").first()
    return getattr(reading, "total_energy", None)


def _close_installation(
    MeterInstallation,
    MeterReading,
    *,
    meter,
    unit,
    lease,
    start_date,
    end_date,
):
    if not all((meter, unit, lease)):
        return
    installation = MeterInstallation.objects.filter(
        meter=meter,
        unit=unit,
        lease=lease,
        start_date=start_date,
        end_date__isnull=True,
    ).first()
    if not installation:
        return
    installation.end_date = end_date
    installation.end_reading = _reading_value(
        MeterReading, meter, end_date, after=False
    )
    installation.is_active = False
    installation.active_meter_key = None
    installation.notes = (
        (installation.notes or "")
        + f"\nClosed by {REPAIR_REASON}."
    ).strip()
    installation.save(
        update_fields=[
            "end_date",
            "end_reading",
            "is_active",
            "active_meter_key",
            "notes",
            "updated_at",
        ]
    )


def _ensure_installation(
    MeterInstallation,
    MeterReading,
    *,
    meter,
    unit,
    lease,
    start_date,
    end_date=None,
):
    if not all((meter, unit, lease)):
        return
    defaults = {
        "start_reading": _reading_value(
            MeterReading, meter, start_date, after=True
        ) or Decimal("0"),
        "end_date": end_date,
        "end_reading": (
            _reading_value(MeterReading, meter, end_date, after=False)
            if end_date
            else None
        ),
        "is_active": end_date is None,
        "active_meter_key": meter.pk if end_date is None else None,
        "reason": REPAIR_REASON,
        "notes": "Corrected unit/lease meter history for monthly billing.",
    }
    MeterInstallation.objects.update_or_create(
        meter=meter,
        unit=unit,
        lease=lease,
        start_date=start_date,
        defaults=defaults,
    )


def _ensure_occupancy(LeaseUnitOccupancy, *, lease, unit, move_in_date):
    if not lease or not unit:
        return
    LeaseUnitOccupancy.objects.get_or_create(
        lease=lease,
        unit=unit,
        move_in_date=move_in_date,
        defaults={
            "move_out_date": None,
            "active_lease_key": lease.pk,
            "notes": REPAIR_REASON,
        },
    )


def repair_billing_links(apps, schema_editor):
    Lease = apps.get_model("leases", "Lease")
    LeaseUnitOccupancy = apps.get_model("leases", "LeaseUnitOccupancy")
    Unit = apps.get_model("properties", "Unit")
    Meter = apps.get_model("smart_meter", "Meter")
    MeterInstallation = apps.get_model("smart_meter", "MeterInstallation")
    MeterReading = apps.get_model("smart_meter", "MeterReading")

    units = {
        (property_name, unit_number): _unit(Unit, property_name, unit_number)
        for property_name, unit_number in {
            ("F35", "F35-FLAT# 03"),
            ("F54", "F54-FLAT# 01"),
            ("F54", "F54-FLAT# 05"),
            ("F56", "F56-FLAT# 02"),
            ("F56", "F56-FLAT# 03"),
            ("F56 Basement", "F56-ROOM# 06"),
            ("F56 Basement", "F56-ROOM# 09"),
            ("F56 Basement", "F56-ROOM# 10"),
            ("F56 Basement", "F56-ROOM# 11"),
        }
    }

    leases = {
        (property_name, unit_number, start_date): _lease(
            Lease, units.get((property_name, unit_number)), start_date
        )
        for property_name, unit_number, start_date in {
            ("F35", "F35-FLAT# 03", date(2024, 9, 1)),
            ("F54", "F54-FLAT# 01", date(2026, 5, 31)),
            ("F54", "F54-FLAT# 01", date(2026, 7, 1)),
            ("F54", "F54-FLAT# 05", date(2026, 6, 1)),
            ("F56", "F56-FLAT# 02", date(2026, 8, 1)),
            ("F56", "F56-FLAT# 03", date(2025, 1, 1)),
            ("F56 Basement", "F56-ROOM# 06", date(2025, 12, 1)),
            ("F56 Basement", "F56-ROOM# 09", date(2026, 7, 1)),
            ("F56 Basement", "F56-ROOM# 10", date(2025, 12, 1)),
            ("F56 Basement", "F56-ROOM# 10", date(2026, 1, 1)),
            ("F56 Basement", "F56-ROOM# 11", date(2026, 1, 1)),
        }
    }

    for property_name, unit_number, start_date in OWNER_BILLED_LEASES:
        lease = leases.get((property_name, unit_number, start_date))
        if lease and not lease.electricity_bill_by_owner:
            lease.electricity_bill_by_owner = True
            lease.save(update_fields=["electricity_bill_by_owner", "updated_at"])

    meters = {
        number: Meter.objects.filter(meter_number=number).first()
        for number in {
            "241203510003",
            "250619510006",
            "260305510016",
            "241203510008",
            "250619510018",
            "260305510005",
            "250619510010",
            "241203510005",
        }
    }

    # F54 Flat 1 changed tenants on July 1. Keep June on the prior lease and
    # begin the new installation on July 1 so the new tenant is not charged for June.
    _close_installation(
        MeterInstallation,
        MeterReading,
        meter=meters["241203510003"],
        unit=units[("F54", "F54-FLAT# 01")],
        lease=leases[("F54", "F54-FLAT# 01", date(2026, 5, 31))],
        start_date=date(2025, 9, 27),
        end_date=date(2026, 6, 30),
    )
    _ensure_installation(
        MeterInstallation,
        MeterReading,
        meter=meters["241203510003"],
        unit=units[("F54", "F54-FLAT# 01")],
        lease=leases[("F54", "F54-FLAT# 01", date(2026, 7, 1))],
        start_date=date(2026, 7, 1),
    )
    _ensure_occupancy(
        LeaseUnitOccupancy,
        lease=leases[("F54", "F54-FLAT# 01", date(2026, 7, 1))],
        unit=units[("F54", "F54-FLAT# 01")],
        move_in_date=date(2026, 7, 1),
    )

    # F54 Flat 5 received meter 260305510016 on June 7, but its installation
    # remained attached to F35 Flat 3 and the stopped meter remained active.
    _close_installation(
        MeterInstallation, MeterReading,
        meter=meters["250619510006"],
        unit=units[("F54", "F54-FLAT# 05")],
        lease=leases[("F54", "F54-FLAT# 05", date(2026, 6, 1))],
        start_date=date(2025, 8, 22), end_date=date(2026, 6, 6),
    )
    _close_installation(
        MeterInstallation, MeterReading,
        meter=meters["260305510016"],
        unit=units[("F35", "F35-FLAT# 03")],
        lease=leases[("F35", "F35-FLAT# 03", date(2024, 9, 1))],
        start_date=date(2026, 5, 18), end_date=date(2026, 6, 6),
    )
    _ensure_installation(
        MeterInstallation, MeterReading,
        meter=meters["260305510016"],
        unit=units[("F54", "F54-FLAT# 05")],
        lease=leases[("F54", "F54-FLAT# 05", date(2026, 6, 1))],
        start_date=date(2026, 6, 7),
    )

    # F56 Flat 3 received meter 250619510018 on June 10 and meter
    # 260305510005 on July 17; neither move was reflected in installations.
    _close_installation(
        MeterInstallation, MeterReading,
        meter=meters["241203510008"],
        unit=units[("F56", "F56-FLAT# 03")],
        lease=leases[("F56", "F56-FLAT# 03", date(2025, 1, 1))],
        start_date=date(2025, 8, 13), end_date=date(2026, 6, 9),
    )
    _close_installation(
        MeterInstallation, MeterReading,
        meter=meters["250619510018"],
        unit=units[("F56", "F56-FLAT# 02")],
        lease=leases[("F56", "F56-FLAT# 02", date(2026, 8, 1))],
        start_date=date(2025, 9, 27), end_date=date(2026, 6, 9),
    )
    _ensure_installation(
        MeterInstallation, MeterReading,
        meter=meters["250619510018"],
        unit=units[("F56", "F56-FLAT# 03")],
        lease=leases[("F56", "F56-FLAT# 03", date(2025, 1, 1))],
        start_date=date(2026, 6, 10), end_date=date(2026, 7, 16),
    )
    _ensure_installation(
        MeterInstallation, MeterReading,
        meter=meters["260305510005"],
        unit=units[("F56", "F56-FLAT# 03")],
        lease=leases[("F56", "F56-FLAT# 03", date(2025, 1, 1))],
        start_date=date(2026, 7, 17),
    )

    # Lease 120 moved from Room 9 to Room 10 on May 1. Move its billing
    # installation to Room 10's physical meter and preserve Room 9 for its next lease.
    _close_installation(
        MeterInstallation, MeterReading,
        meter=meters["250619510010"],
        unit=units[("F56 Basement", "F56-ROOM# 09")],
        lease=leases[("F56 Basement", "F56-ROOM# 10", date(2025, 12, 1))],
        start_date=date(2025, 8, 22), end_date=date(2026, 4, 30),
    )
    _ensure_installation(
        MeterInstallation, MeterReading,
        meter=meters["250619510010"],
        unit=units[("F56 Basement", "F56-ROOM# 09")],
        lease=leases[("F56 Basement", "F56-ROOM# 09", date(2026, 7, 1))],
        start_date=date(2026, 7, 1),
    )
    _ensure_occupancy(
        LeaseUnitOccupancy,
        lease=leases[("F56 Basement", "F56-ROOM# 09", date(2026, 7, 1))],
        unit=units[("F56 Basement", "F56-ROOM# 09")],
        move_in_date=date(2026, 7, 1),
    )
    _close_installation(
        MeterInstallation, MeterReading,
        meter=meters["241203510005"],
        unit=units[("F56 Basement", "F56-ROOM# 10")],
        lease=leases[("F56 Basement", "F56-ROOM# 10", date(2026, 1, 1))],
        start_date=date(2025, 8, 26), end_date=date(2026, 4, 30),
    )
    _ensure_installation(
        MeterInstallation, MeterReading,
        meter=meters["241203510005"],
        unit=units[("F56 Basement", "F56-ROOM# 10")],
        lease=leases[("F56 Basement", "F56-ROOM# 10", date(2025, 12, 1))],
        start_date=date(2026, 5, 1),
    )


def reverse_repair(apps, schema_editor):
    Lease = apps.get_model("leases", "Lease")
    LeaseUnitOccupancy = apps.get_model("leases", "LeaseUnitOccupancy")
    Unit = apps.get_model("properties", "Unit")
    Meter = apps.get_model("smart_meter", "Meter")
    MeterInstallation = apps.get_model("smart_meter", "MeterInstallation")

    MeterInstallation.objects.filter(reason=REPAIR_REASON).delete()
    LeaseUnitOccupancy.objects.filter(notes=REPAIR_REASON).delete()

    for property_name, unit_number, start_date in OWNER_BILLED_LEASES:
        unit = _unit(Unit, property_name, unit_number)
        lease = _lease(Lease, unit, start_date)
        if lease:
            lease.electricity_bill_by_owner = (
                property_name == "F56 Basement" and unit_number == "F56-ROOM# 11"
            )
            lease.save(update_fields=["electricity_bill_by_owner", "updated_at"])

    originals = (
        ("241203510003", "F54", "F54-FLAT# 01", "F54", "F54-FLAT# 01", date(2026, 5, 31), date(2025, 9, 27)),
        ("250619510006", "F54", "F54-FLAT# 05", "F54", "F54-FLAT# 05", date(2026, 6, 1), date(2025, 8, 22)),
        ("260305510016", "F35", "F35-FLAT# 03", "F35", "F35-FLAT# 03", date(2024, 9, 1), date(2026, 5, 18)),
        ("241203510008", "F56", "F56-FLAT# 03", "F56", "F56-FLAT# 03", date(2025, 1, 1), date(2025, 8, 13)),
        ("250619510018", "F56", "F56-FLAT# 02", "F56", "F56-FLAT# 02", date(2026, 8, 1), date(2025, 9, 27)),
        ("250619510010", "F56 Basement", "F56-ROOM# 09", "F56 Basement", "F56-ROOM# 10", date(2025, 12, 1), date(2025, 8, 22)),
        ("241203510005", "F56 Basement", "F56-ROOM# 10", "F56 Basement", "F56-ROOM# 10", date(2026, 1, 1), date(2025, 8, 26)),
    )
    for (
        meter_number,
        install_property,
        install_unit_number,
        lease_property,
        lease_unit_number,
        lease_start,
        install_start,
    ) in originals:
        meter = Meter.objects.filter(meter_number=meter_number).first()
        installation_unit = _unit(Unit, install_property, install_unit_number)
        lease_unit = _unit(Unit, lease_property, lease_unit_number)
        lease = _lease(Lease, lease_unit, lease_start)
        if not all((meter, installation_unit, lease)):
            continue
        MeterInstallation.objects.filter(
            meter=meter,
            unit=installation_unit,
            lease=lease,
            start_date=install_start,
        ).update(
            end_date=None,
            end_reading=None,
            is_active=True,
            active_meter_key=meter.pk,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("leases", "0099_leaserenewal_photo_settings"),
        ("smart_meter", "0016_rename_check_role_and_seed_audit_groups"),
    ]

    operations = [
        migrations.RunPython(repair_billing_links, reverse_repair),
    ]
