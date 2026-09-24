from datetime import date, datetime, timezone

from django.db import migrations
from django.db.models import Q


SCOREBOARD_START = date(2026, 1, 1)
SCOREBOARD_START_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Audit/output meter number -> (IESCO consumer ID, IESCO reference number).
SYSTEMS = {
    "260305510018": ("1143243650", "17146151548928"),
    "260305510004": ("1143090754", "17146151548921"),
    "260305510021": ("1143791551", "17146151547844"),
}


def backdate_scoreboard_billing_coverage(apps, schema_editor):
    """Expose existing billing history without inventing audit-meter readings.

    Billing meters were installed before the Energy System scoreboard groups were
    configured.  Move only memberships that have actual 2026 readings (on the
    assigned meter or a replacement meter cached against the same unit).  Audit
    meter dates and readings are deliberately left unchanged because those meters
    were physically installed later.
    """
    MeterCheckGroup = apps.get_model("smart_meter", "MeterCheckGroup")
    MeterCheckGroupMembership = apps.get_model(
        "smart_meter", "MeterCheckGroupMembership"
    )
    MeterReading = apps.get_model("smart_meter", "MeterReading")
    UtilityConnection = apps.get_model("smart_meter", "UtilityConnection")

    for audit_meter_number, (consumer_id, reference_no) in SYSTEMS.items():
        group = (
            MeterCheckGroup.objects.filter(
                check_meter__meter_number=audit_meter_number
            )
            .values("id", "energy_system__id")
            .first()
        )
        if not group:
            continue

        memberships = MeterCheckGroupMembership.objects.filter(
            group_id=group["id"],
            start_date__gt=SCOREBOARD_START,
        ).select_related("billing_meter")

        for membership in memberships:
            before_current_membership = datetime.combine(
                membership.start_date, datetime.min.time(), tzinfo=timezone.utc
            )
            available_meter_readings = MeterReading.objects.filter(
                meter__meter_role="billing",
                meter__meter_type="electric",
                ts__gte=SCOREBOARD_START_AT,
                ts__lt=before_current_membership,
            )
            if membership.billing_meter.unit_id:
                available_meter_readings = available_meter_readings.filter(
                    Q(meter_id=membership.billing_meter_id)
                    | Q(meter__unit_id=membership.billing_meter.unit_id)
                )
            else:
                available_meter_readings = available_meter_readings.filter(
                    meter_id=membership.billing_meter_id
                )

            if available_meter_readings.exists():
                MeterCheckGroupMembership.objects.filter(pk=membership.pk).update(
                    start_date=SCOREBOARD_START
                )

        # Consumer ID matching already works, but storing the reference explicitly
        # makes scoreboard-to-IESCO matching deterministic in production.
        if group["energy_system__id"]:
            UtilityConnection.objects.filter(
                energy_system_id=group["energy_system__id"],
                consumer_id=consumer_id,
                reference_no="",
            ).update(reference_no=reference_no)


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0047_inverter_and_inverter_reading"),
    ]

    operations = [
        migrations.RunPython(
            backdate_scoreboard_billing_coverage,
            migrations.RunPython.noop,
        ),
    ]
