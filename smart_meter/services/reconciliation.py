from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from smart_meter.models import (
    EnergyReconciliationAuditEvent,
    EnergySystem,
    EnergySystemMeterLink,
    InverterPeriodStatement,
    Meter,
    MeterReading,
    UtilityBillCycle,
    UtilityBillPayment,
    UtilityConnection,
)


ZERO = Decimal("0")
UNCONFIRMED_EXPORT_REASON = "Output meter's export path is not confirmed"
NO_EXACT_BILL_REASON = "No confirmed utility bill exactly matches this period; export is not prorated"
PV_RESIDUAL_LABEL = "PV/Storage Residual — battery movement unavailable"


def calculate_variance(check_kwh, billing_kwh, rate):
    """Pure, request-independent arithmetic used by the legacy Check Group page."""
    check_kwh = Decimal(str(check_kwh or 0))
    billing_kwh = Decimal(str(billing_kwh or 0))
    variance_kwh = check_kwh - billing_kwh
    return {
        "check_kwh": check_kwh,
        "billing_kwh": billing_kwh,
        "variance_kwh": variance_kwh,
        "variance_rs": variance_kwh * Decimal(str(rate or 0)),
        "leakage_percent": (
            variance_kwh / check_kwh * Decimal("100") if check_kwh else ZERO
        ),
    }


def calculate_check_group_period(group, start_date, end_date):
    """Return the existing Check Group readings and totals without request/view state."""
    from smart_meter.views_dashboard import _per_meter_series

    check_labels, check_datasets, check_rows, check_totals = _per_meter_series(
        Meter.objects.filter(pk=group.check_meter_id), start_date, end_date, "daily"
    )
    effective_memberships = list(
        group.memberships.filter(start_date__lte=end_date)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=start_date))
        .select_related("billing_meter", "billing_meter__unit", "billing_meter__unit__property")
        .order_by("start_date", "billing_meter__meter_number")
    )
    billing_rows = []
    billing_total_kwh = ZERO
    for membership in effective_memberships:
        segment_start = max(start_date, membership.start_date)
        segment_end = min(end_date, membership.end_date or end_date)
        _labels, _datasets, segment_rows, segment_totals = _per_meter_series(
            Meter.objects.filter(pk=membership.billing_meter_id),
            segment_start,
            segment_end,
            "daily",
        )
        billing_rows.extend(segment_rows)
        billing_total_kwh += Decimal(str(segment_totals["total_kwh"]))
    billing_rows.sort(
        key=lambda row: (
            row["period_key"],
            row["unit_number"],
            row["property_name"],
            row["meter_number"],
        )
    )
    totals = calculate_variance(
        check_totals["total_kwh"],
        billing_total_kwh,
        group.check_meter.effective_unit_rate,
    )
    return {
        **totals,
        "check_labels": check_labels,
        "check_datasets": check_datasets,
        "check_rows": check_rows,
        "check_totals": check_totals,
        "billing_rows": billing_rows,
        "effective_memberships": effective_memberships,
        "audit_summary_start_kwh": check_rows[0]["start_kwh"] if check_rows else None,
        "audit_summary_end_kwh": check_rows[-1]["end_kwh"] if check_rows else None,
    }


@dataclass(frozen=True)
class BoundaryReading:
    value: Decimal | None
    timestamp: datetime | None
    distance: timedelta | None
    status: str


def tolerance_status(distance):
    if distance is None or distance > timedelta(hours=24):
        return "invalid"
    if distance <= timedelta(minutes=15):
        return "green"
    if distance <= timedelta(minutes=60):
        return "acceptable"
    return "warning"


def _aware_midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())


def _register_value(reading, field_name, fallback_field=None):
    value = getattr(reading, field_name, None)
    if value is None and fallback_field:
        value = getattr(reading, fallback_field, None)
    return Decimal(str(value)) if value is not None else None


def closest_boundary_reading(meter, target_at, field_name="total_energy", fallback_field=None):
    available = Q(**{f"{field_name}__isnull": False})
    if fallback_field:
        available |= Q(**{f"{fallback_field}__isnull": False})
    before = (
        MeterReading.objects.filter(meter=meter, ts__lte=target_at).filter(available)
        .order_by("-ts", "-id")
        .first()
    )
    after = (
        MeterReading.objects.filter(meter=meter, ts__gt=target_at).filter(available)
        .order_by("ts", "id")
        .first()
    )
    candidates = [reading for reading in (before, after) if reading is not None]
    if not candidates:
        return BoundaryReading(None, None, None, "invalid")
    reading = min(candidates, key=lambda row: abs(row.ts - target_at))
    distance = abs(reading.ts - target_at)
    return BoundaryReading(
        _register_value(reading, field_name, fallback_field),
        reading.ts,
        distance,
        tolerance_status(distance),
    )


def meter_period_delta(
    meter,
    start_date,
    end_date,
    *,
    field_name="total_energy",
    fallback_field=None,
):
    start = closest_boundary_reading(
        meter, _aware_midnight(start_date), field_name, fallback_field
    )
    end = closest_boundary_reading(
        meter, _aware_midnight(end_date), field_name, fallback_field
    )
    valid = start.status != "invalid" and end.status != "invalid"
    reason = ""
    if not valid:
        reason = "A period-boundary reading is missing or more than 24 hours away"
    elif end.timestamp <= start.timestamp:
        valid = False
        reason = "Period boundary readings are not in chronological order"
    else:
        available = Q(**{f"{field_name}__isnull": False})
        if fallback_field:
            available |= Q(**{f"{fallback_field}__isnull": False})
        readings = (
            MeterReading.objects.filter(
                meter=meter,
                ts__gte=start.timestamp,
                ts__lte=end.timestamp,
            )
            .filter(available)
            .order_by("ts", "id")
        )
        previous = None
        for reading in readings:
            current = _register_value(reading, field_name, fallback_field)
            if previous is not None and current < previous:
                valid = False
                reason = (
                    f"{field_name} decreased between period boundaries; "
                    "rollover/reset continuity is unconfirmed"
                )
                break
            previous = current
    return {
        "start": start,
        "end": end,
        "kwh": end.value - start.value if valid else None,
        "valid": valid,
        "reason": reason,
    }


def linked_meter_period_delta(system, side, start_date, end_date, *, field_name="total_energy", fallback_field=None):
    """Sum a configured side of an Energy System; every source needs valid boundaries."""
    meter_ids = list(
        EnergySystemMeterLink.objects.filter(energy_system=system, side=side)
        .values_list("meter_id", flat=True)
    )
    if not meter_ids:
        return None
    total = ZERO
    reasons = []
    for meter in Meter.objects.filter(pk__in=meter_ids).order_by("meter_number"):
        result = meter_period_delta(
            meter, start_date, end_date, field_name=field_name, fallback_field=fallback_field
        )
        if result["valid"]:
            total += result["kwh"]
        else:
            reasons.append(f"{meter.meter_number}: {result['reason']}")
    return {
        "kwh": total if not reasons else None,
        "valid": not reasons,
        "reason": "; ".join(reasons),
    }


def _exact_bill(system, start_date, end_date):
    try:
        connection = system.utility_connection
    except UtilityConnection.DoesNotExist:
        return None
    return (
        connection.bill_cycles.filter(
            period_start=start_date,
            period_end=end_date,
            confirmed_at__isnull=False,
        )
        .order_by("-confirmed_at", "-id")
        .first()
    )


def _tenant_financials(system, start_date, end_date):
    from invoices.models import InvoiceItem
    from payments.models import PaymentDetail

    meter_ids = list(
        system.output_group.memberships.filter(start_date__lt=end_date)
        .filter(Q(end_date__isnull=True) | Q(end_date__gt=start_date))
        .values_list("billing_meter_id", flat=True)
        .distinct()
    )
    unit_ids = list(
        Meter.objects.filter(pk__in=meter_ids, unit_id__isnull=False)
        .values_list("unit_id", flat=True)
        .distinct()
    )
    energy_items = InvoiceItem.objects.filter(
        invoice__issue_date__gte=start_date,
        invoice__issue_date__lt=end_date,
        invoice__lease__unit_id__in=unit_ids,
    ).exclude(
        invoice__lifecycle_status__in=("cancelled", "void")
    ).filter(
        Q(category__name__iexact="Electric")
        | Q(category__name__iexact="Electricity")
        | Q(description__icontains="electric")
    )
    revenue = energy_items.aggregate(total=Sum("amount"))["total"] or ZERO
    collections = (
        PaymentDetail.objects.filter(
            payment__payment_date__gte=start_date,
            payment__payment_date__lt=end_date,
            electricity_meter_id__in=meter_ids,
        ).aggregate(total=Sum("electricity_amount"))["total"]
        or ZERO
    )
    return Decimal(revenue), Decimal(collections)


def build_energy_reconciliation(system, start_date, end_date):
    output = linked_meter_period_delta(
        system, EnergySystemMeterLink.SIDE_OUTPUT, start_date, end_date
    ) or meter_period_delta(system.output_group.check_meter, start_date, end_date)
    billing_total = ZERO
    billing_valid = True
    billing_reasons = []
    for membership in system.output_group.memberships.filter(start_date__lt=end_date).filter(
        Q(end_date__isnull=True) | Q(end_date__gt=start_date)
    ).select_related("billing_meter"):
        segment_start = max(start_date, membership.start_date)
        segment_end = min(end_date, membership.end_date or end_date)
        result = meter_period_delta(membership.billing_meter, segment_start, segment_end)
        if result["valid"]:
            billing_total += result["kwh"]
        else:
            billing_valid = False
            billing_reasons.append(membership.billing_meter.meter_number)

    linked_grid_import = linked_meter_period_delta(
        system,
        EnergySystemMeterLink.SIDE_INPUT,
        start_date,
        end_date,
        field_name="forward_active_energy_kwh",
        fallback_field="total_energy",
    )
    grid_import = linked_grid_import or (
        meter_period_delta(
            system.grid_interface_meter,
            start_date,
            end_date,
            field_name="forward_active_energy_kwh",
            fallback_field="total_energy",
        )
        if system.grid_interface_meter_id
        else {"kwh": None, "valid": False, "reason": "No grid-interface meter is assigned"}
    )
    linked_grid_export = linked_meter_period_delta(
        system,
        EnergySystemMeterLink.SIDE_INPUT,
        start_date,
        end_date,
        field_name="reverse_active_energy_kwh",
    )
    grid_export = linked_grid_export or (
        meter_period_delta(
            system.grid_interface_meter,
            start_date,
            end_date,
            field_name="reverse_active_energy_kwh",
        )
        if system.grid_interface_meter_id
        else {"kwh": None, "valid": False, "reason": "No grid-interface meter is assigned"}
    )
    bill = _exact_bill(system, start_date, end_date)
    export_kwh = Decimal(bill.export_kwh) if bill else None
    output_kwh = output["kwh"]
    import_kwh = grid_import["kwh"]
    grid_export_kwh = grid_export["kwh"]
    net_grid_kwh = (
        import_kwh - grid_export_kwh
        if import_kwh is not None and grid_export_kwh is not None
        else None
    )
    topology = system.output_meter_includes_grid_export

    building_consumption = None
    distribution_variance = None
    raw_difference = None
    net_non_grid = None
    withheld = []
    if not output["valid"]:
        withheld.append(output["reason"])
    if not billing_valid:
        withheld.append("Invalid boundary readings for billing meters: " + ", ".join(billing_reasons))
    if system.grid_interface_meter_id and not grid_import["valid"]:
        withheld.append("Grid forward energy: " + grid_import["reason"])
    if system.grid_interface_meter_id and not grid_export["valid"]:
        withheld.append("Grid reverse energy: " + grid_export["reason"])

    if output_kwh is not None and billing_valid:
        if topology is True:
            if export_kwh is not None:
                building_consumption = output_kwh - export_kwh
                distribution_variance = building_consumption - billing_total
            else:
                withheld.append(NO_EXACT_BILL_REASON)
            if import_kwh is not None:
                net_non_grid = output_kwh - import_kwh
        elif topology is False:
            building_consumption = output_kwh
            distribution_variance = output_kwh - billing_total
            if export_kwh is not None and import_kwh is not None:
                net_non_grid = output_kwh + export_kwh - import_kwh
            elif export_kwh is None:
                withheld.append(NO_EXACT_BILL_REASON)
        else:
            raw_difference = output_kwh - billing_total
            withheld.append(UNCONFIRMED_EXPORT_REASON)

    statement = (
        system.inverter_statements.filter(
            period_start=start_date,
            period_end=end_date,
            confirmed_at__isnull=False,
        )
        .order_by("-confirmed_at", "-id")
        .first()
    )
    pv_generation = Decimal(statement.pv_generation_kwh) if statement else None
    pv_residual = (
        pv_generation - net_non_grid
        if pv_generation is not None and net_non_grid is not None
        else None
    )

    tenant_revenue, tenant_collections = _tenant_financials(system, start_date, end_date)
    utility_cost = Decimal(bill.current_cycle_utility_cost) if bill else None
    operating_margin = tenant_revenue - utility_cost if utility_cost is not None else None
    utility_paid = None
    cash_position = None
    utility_payable_credit = None
    if bill:
        confirmed_payments = bill.payments.filter(confirmed_at__isnull=False)
        utility_paid = Decimal(
            confirmed_payments.aggregate(total=Sum("amount"))["total"] or ZERO
        )
        if bill.grand_total is not None:
            utility_payable_credit = Decimal(bill.grand_total) - utility_paid
        if confirmed_payments.exists():
            cash_position = tenant_collections - utility_paid
        else:
            utility_paid = None

    return {
        "system": system,
        "period_start": start_date,
        "period_end": end_date,
        "output_kwh": output_kwh,
        "output_readings": output,
        "billing_total_kwh": billing_total if billing_valid else None,
        "grid_import_kwh": import_kwh,
        "grid_import_readings": grid_import,
        "grid_export_kwh": grid_export_kwh,
        "grid_export_readings": grid_export,
        "net_grid_energy_kwh": net_grid_kwh,
        "export_kwh": export_kwh,
        "exact_bill": bill,
        "building_consumption_kwh": building_consumption,
        "distribution_variance_kwh": distribution_variance,
        "raw_output_to_billing_difference_kwh": raw_difference,
        "net_non_grid_contribution_kwh": net_non_grid,
        "withheld_reasons": list(dict.fromkeys(withheld)),
        "pv_statement": statement,
        "pv_generation_kwh": pv_generation,
        "pv_storage_residual_kwh": pv_residual,
        "pv_storage_residual_label": PV_RESIDUAL_LABEL,
        "tenant_energy_revenue": tenant_revenue,
        "tenant_electricity_collections": tenant_collections,
        "current_cycle_utility_cost": utility_cost,
        "operating_energy_margin": operating_margin,
        "utility_amount_paid": utility_paid,
        "cash_position": cash_position,
        "utility_payable_credit": utility_payable_credit,
        "tenant_outstanding": tenant_revenue - tenant_collections,
    }


def _snapshot(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        value = getattr(instance, field.attname)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        elif field.get_internal_type() in {"FileField", "ImageField"}:
            value = str(getattr(instance, field.name) or "")
        data[field.name] = value
    return data


def log_audit(instance, action, user=None, reason=""):
    targets = {
        UtilityBillCycle: "utility_bill_cycle",
        InverterPeriodStatement: "inverter_statement",
        UtilityBillPayment: "utility_bill_payment",
    }
    target_name = next(name for cls, name in targets.items() if isinstance(instance, cls))
    return EnergyReconciliationAuditEvent.objects.create(
        **{target_name: instance},
        action=action,
        reason=reason,
        changed_by=user,
        snapshot=_snapshot(instance),
    )


def validate_bill_confirmation(cycle):
    if not cycle.period_start or not cycle.period_end:
        raise ValidationError("A confirmed bill requires both period dates.")
    overlap = UtilityBillCycle.objects.filter(
        utility_connection=cycle.utility_connection,
        confirmed_at__isnull=False,
        period_start__lt=cycle.period_end,
        period_end__gt=cycle.period_start,
    )
    if cycle.pk:
        overlap = overlap.exclude(pk=cycle.pk)
    if overlap.exists():
        raise ValidationError("This bill period overlaps another confirmed bill for the connection.")


@transaction.atomic
def confirm_bill(cycle, user=None):
    cycle = UtilityBillCycle.objects.select_for_update().get(pk=cycle.pk)
    if cycle.finalized_at is not None or cycle.status == "final":
        raise ValidationError("A finalized bill must be reopened before it can transition again.")
    if cycle.confirmed_at is not None:
        raise ValidationError("This utility bill is already confirmed.")
    validate_bill_confirmation(cycle)
    cycle.confirmed_at = timezone.now()
    if cycle.status == "draft":
        cycle.status = "incomplete"
    cycle.updated_by = user
    cycle.save(update_fields=["confirmed_at", "status", "updated_by", "updated_at"])
    log_audit(cycle, "confirmed", user)
    return cycle


@transaction.atomic
def finalize_bill(cycle, user=None):
    cycle = UtilityBillCycle.objects.select_for_update().get(pk=cycle.pk)
    if cycle.finalized_at is not None or cycle.status == "final":
        raise ValidationError("This utility bill is already final.")
    if cycle.confirmed_at is None:
        raise ValidationError("Confirm the utility bill before finalizing it.")
    validate_bill_confirmation(cycle)
    required = (
        cycle.import_off_peak_kwh,
        cycle.import_peak_kwh,
        cycle.export_off_peak_kwh,
        cycle.export_peak_kwh,
        cycle.grand_total,
    )
    if any(value is None for value in required):
        raise ValidationError("A final bill requires all four energy totals and Grand Total.")
    cycle.finalized_at = timezone.now()
    cycle.status = "final"
    cycle.updated_by = user
    cycle.save(update_fields=["confirmed_at", "finalized_at", "status", "updated_by", "updated_at"])
    log_audit(cycle, "finalized", user)
    return cycle


@transaction.atomic
def reopen_record(instance, user, reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to reopen this record.")
    instance = instance.__class__.objects.select_for_update().get(pk=instance.pk)
    if getattr(instance, "confirmed_at", None) is None and getattr(
        instance, "finalized_at", None
    ) is None:
        raise ValidationError("Only a confirmed or finalized record can be reopened.")
    if isinstance(instance, UtilityBillCycle):
        instance.finalized_at = None
        instance.confirmed_at = None
        instance.status = "draft"
        instance.updated_by = user
        fields = ["finalized_at", "confirmed_at", "status", "updated_by", "updated_at"]
    else:
        instance.confirmed_at = None
        instance.updated_by = user
        fields = ["confirmed_at", "updated_by", "updated_at"]
    instance.save(update_fields=fields)
    log_audit(instance, "reopened", user, reason)
    return instance
