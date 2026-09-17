"""Synchronize optional location rules with dated Check Group memberships."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from properties.models import Unit
from smart_meter.models import Meter, MeterCheckGroup, MeterCheckGroupMembership, MeterInstallation


def group_for_unit(unit):
    """An explicit unit rule takes precedence over a whole-property rule."""
    groups = MeterCheckGroup.objects.filter(
        automatic_coverage=True, is_active=True, superseded_by_energy_system__isnull=True,
    )
    explicit = list(groups.filter(
        coverage_mode=MeterCheckGroup.COVERAGE_UNITS, coverage_units=unit,
    ).distinct())
    matches = explicit or list(groups.filter(
        coverage_mode=MeterCheckGroup.COVERAGE_PROPERTY, property_id=unit.property_id,
    ))
    if len(matches) > 1:
        raise ValidationError("More than one automatic Check Group covers this unit.")
    return matches[0] if matches else None


@transaction.atomic
def sync_meter_coverage(meter, effective_date):
    """Apply the unit's current rule after an installation or role change."""
    meter = Meter.objects.select_for_update().get(pk=meter.pk)
    installation = MeterInstallation.objects.filter(
        meter=meter, is_active=True, end_date__isnull=True,
    ).select_related("unit__property").first()
    target = (
        group_for_unit(installation.unit)
        if installation and meter.is_active and meter.meter_role == Meter.METER_ROLE_BILLING
        and meter.meter_type == Meter.METER_TYPE_ELECTRIC else None
    )
    current = list(MeterCheckGroupMembership.objects.select_for_update().filter(
        billing_meter=meter, is_active=True, end_date__isnull=True,
    ))
    for membership in current:
        if target and membership.group_id == target.pk:
            return membership
        if not membership.assigned_automatically:
            if target:
                raise ValidationError(
                    f"Meter {meter.meter_number} is manually assigned to another Check Group."
                )
            return membership
        close_date = effective_date - timedelta(days=1)
        if close_date < membership.start_date:
            raise ValidationError(
                f"Meter {meter.meter_number} was assigned today; its coverage needs manual review."
            )
        membership.close(end_date=close_date, notes="Automatic coverage changed.")
    if target:
        return MeterCheckGroupMembership.objects.create(
            group=target, billing_meter=meter, start_date=effective_date,
            assigned_automatically=True,
            notes="Assigned by property/unit coverage rule.",
        )
    return None


@transaction.atomic
def sync_group_coverage(group, effective_date):
    """Apply a changed rule to existing installations and prior automatic members."""
    meter_ids = set(MeterCheckGroupMembership.objects.filter(
        group=group, assigned_automatically=True, is_active=True, end_date__isnull=True,
    ).values_list("billing_meter_id", flat=True))
    if group.automatic_coverage and group.is_active:
        if group.coverage_mode == MeterCheckGroup.COVERAGE_PROPERTY and group.property_id:
            unit_ids = Unit.objects.filter(property_id=group.property_id).values_list("pk", flat=True)
        else:
            unit_ids = group.coverage_units.values_list("pk", flat=True)
        meter_ids.update(MeterInstallation.objects.filter(
            unit_id__in=unit_ids, is_active=True, end_date__isnull=True,
        ).values_list("meter_id", flat=True))
    for meter in Meter.objects.filter(pk__in=meter_ids).order_by("pk"):
        sync_meter_coverage(meter, effective_date)
