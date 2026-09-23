from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
import re
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from smart_meter.models import (
    EnergyReconciliationAuditEvent,
    EnergySystem,
    EnergySystemMeterLink,
    Inverter,
    InverterPeriodStatement,
    InverterReading,
    LiveReading,
    Meter,
    MeterReading,
    MeterRawFrame,
    UtilityBillCycle,
    UtilityBillPayment,
    UtilityConnection,
)


ZERO = Decimal("0")
UNCONFIRMED_EXPORT_REASON = "Output meter's export path is not confirmed"
NO_EXACT_BILL_REASON = "No confirmed utility bill exactly matches this period; export is not prorated"
PV_RESIDUAL_LABEL = "PV/Storage Residual — battery movement unavailable"
PROVISIONAL_SYNC_TOLERANCE = timedelta(minutes=15)


def _natural_text_key(value):
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", value or "")
        if part
    )


def _latest_reading_for_meter(meter):
    """Return the newest live or stored reading without using it in confirmed totals."""
    live = LiveReading.objects.filter(meter=meter).order_by("-ts", "-id").first()
    stored = MeterReading.objects.filter(meter=meter).order_by("-ts", "-id").first()
    candidates = [reading for reading in (live, stored) if reading is not None]
    return max(candidates, key=lambda reading: reading.ts) if candidates else None


def _timing_difference_label(delta):
    total_seconds = int(delta.total_seconds())
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return " ".join(parts)


def build_latest_linked_meter_snapshot(system):
    """Latest configured source readings for display only; never feeds confirmed reconciliation."""
    links = list(
        system.meter_links.select_related("meter").order_by("side", "meter__meter_number")
    )
    if not links:
        return {"inputs": [], "outputs": [], "status": "incomplete", "provisional_balance": None}

    rows = {EnergySystemMeterLink.SIDE_INPUT: [], EnergySystemMeterLink.SIDE_OUTPUT: []}
    for link in links:
        reading = _latest_reading_for_meter(link.meter)
        forward = (
            _register_value(reading, "forward_active_energy_kwh", "total_energy")
            if reading else None
        )
        reverse = _register_value(reading, "reverse_active_energy_kwh") if reading else None
        rows[link.side].append({
            "meter": link.meter,
            "reading": reading,
            "forward": forward,
            "reverse": reverse,
            "net": forward - reverse if forward is not None and reverse is not None else None,
            "timing_difference": None,
            "timing_difference_label": "",
        })

    inputs, outputs = rows[EnergySystemMeterLink.SIDE_INPUT], rows[EnergySystemMeterLink.SIDE_OUTPUT]
    reference = next((row for row in inputs if row["reading"] is not None), None)
    if reference:
        for row in inputs + outputs:
            if row["reading"] is not None:
                difference = abs(reference["reading"].ts - row["reading"].ts)
                row["timing_difference"] = difference
                row["timing_difference_label"] = _timing_difference_label(difference)

    all_rows = inputs + outputs
    all_have_forward = bool(inputs and outputs) and all(row["forward"] is not None for row in all_rows)
    all_in_sync = reference is not None and all(
        row["timing_difference"] is not None and row["timing_difference"] <= PROVISIONAL_SYNC_TOLERANCE
        for row in all_rows
    )
    provisional_balance = None
    if all_have_forward:
        provisional_balance = sum(row["forward"] for row in outputs) - sum(row["forward"] for row in inputs)
    return {
        "inputs": inputs,
        "outputs": outputs,
        "reference_meter": reference["meter"] if reference else None,
        "sync_tolerance_minutes": int(PROVISIONAL_SYNC_TOLERANCE.total_seconds() // 60),
        "status": "provisional" if provisional_balance is not None and all_in_sync else "out_of_sync" if provisional_balance is not None else "incomplete",
        "provisional_balance": provisional_balance,
    }


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

    assignments = list(group.audit_assignments.filter(
        start_date__lte=end_date,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gt=start_date)).order_by("start_date"))
    check_labels, check_datasets, check_rows = [], [], []
    check_totals = None
    if not assignments:
        check_labels, check_datasets, check_rows, check_totals = _per_meter_series(
            Meter.objects.filter(pk=group.check_meter_id), start_date, end_date, "daily"
        )
    else:
        for assignment in assignments:
            segment_start = max(start_date, assignment.start_date)
            segment_end = min(end_date, assignment.end_date - timedelta(days=1) if assignment.end_date else end_date)
            if segment_end < segment_start:
                continue
            labels, datasets, rows, totals = _per_meter_series(
                Meter.objects.filter(pk=assignment.meter_id), segment_start, segment_end, "daily"
            )
            previous_label_count = len(check_labels)
            for dataset in check_datasets:
                dataset["data"].extend([None] * len(labels))
                dataset["reverseData"].extend([None] * len(labels))
            for dataset in datasets:
                dataset["data"] = [None] * previous_label_count + dataset["data"]
                dataset["reverseData"] = [None] * previous_label_count + dataset["reverseData"]
            check_labels.extend(labels)
            check_datasets.extend(datasets)
            check_rows.extend(rows)
            if check_totals is None:
                check_totals = dict(totals)
            else:
                for key, value in totals.items():
                    check_totals[key] += value
    if check_totals is None:
        check_totals = {"total_kwh": ZERO}
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
    source: str = "historical_snapshot"


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


def _raw_register_spec(field_name):
    return {
        "forward_active_energy_kwh": ("00010000", "forward_active_energy_kwh"),
        "reverse_active_energy_kwh": ("00020000", "reverse_active_energy_kwh"),
    }.get(field_name)


def _raw_boundary_reading(meter, target_at, field_name):
    spec = _raw_register_spec(field_name)
    if not spec:
        return None
    di, decoded_key = spec
    qs = MeterRawFrame.objects.filter(
        meter=meter, data_identifier=di, trust_classification=MeterRawFrame.TRUST_AUTHORITATIVE
    )
    before = qs.filter(received_at__lte=target_at).order_by("-received_at", "-id").first()
    after = qs.filter(received_at__gt=target_at).order_by("received_at", "id").first()
    candidates = [row for row in (before, after) if row is not None]
    if not candidates:
        return None
    frame = min(candidates, key=lambda row: abs(row.received_at - target_at))
    raw_value = (frame.decoded_data or {}).get(decoded_key)
    if raw_value in (None, ""):
        return None
    try:
        value = Decimal(str(raw_value))
    except Exception:
        return None
    distance = abs(frame.received_at - target_at)
    return BoundaryReading(value, frame.received_at, distance, tolerance_status(distance), "authoritative_raw_frame")


def closest_boundary_reading(meter, target_at, field_name="total_energy", fallback_field=None):
    raw = _raw_boundary_reading(meter, target_at, field_name)
    if raw is not None:
        return raw
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
        "historical_snapshot",
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
        raw_spec = _raw_register_spec(field_name)
        if raw_spec and start.source == "authoritative_raw_frame" and end.source == "authoritative_raw_frame":
            di, decoded_key = raw_spec
            readings = MeterRawFrame.objects.filter(
                meter=meter, data_identifier=di,
                trust_classification=MeterRawFrame.TRUST_AUTHORITATIVE,
                received_at__gte=start.timestamp, received_at__lte=end.timestamp,
            ).order_by("received_at", "id")
            values = []
            for reading in readings:
                raw_value = (reading.decoded_data or {}).get(decoded_key)
                if raw_value not in (None, ""):
                    try:
                        values.append(Decimal(str(raw_value)))
                    except Exception:
                        pass
        else:
            readings = (
                MeterReading.objects.filter(
                    meter=meter, ts__gte=start.timestamp, ts__lte=end.timestamp,
                ).filter(available).order_by("ts", "id")
            )
            values = [_register_value(reading, field_name, fallback_field) for reading in readings]
        previous = None
        for current in values:
            if current is None:
                continue
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


def build_check2_breakdown(system, start_date, end_date):
    """Meter-by-meter rows backing Check 2 (Output Distribution): begin/end reading,
    units, and dashboard usage charge for the output meter(s) and every billing meter, so the
    on-screen variance can be traced back to its source readings instead of just a
    single number."""
    output_meter_ids = list(
        EnergySystemMeterLink.objects.filter(
            energy_system=system, side=EnergySystemMeterLink.SIDE_OUTPUT
        ).values_list("meter_id", flat=True)
    )
    output_meters = (
        list(Meter.objects.filter(pk__in=output_meter_ids).order_by("meter_number"))
        if output_meter_ids
        else [system.output_group.check_meter]
    )

    def dashboard_summary(meter, segment_start, segment_end):
        """Use the same chained readings shown by the main dashboard.

        The reconciliation report remains strict about 24-hour boundary readings,
        but Check 2 is an operational list and should mirror the dashboard figures
        the user can already inspect for each billing meter.
        """
        from smart_meter.views_dashboard import _per_meter_series

        period_start_at = _aware_midnight(segment_start)
        period_end_at = _aware_midnight(segment_end)
        has_period_readings = MeterReading.objects.filter(
            meter=meter,
            ts__gte=period_start_at,
            ts__lt=period_end_at,
        ).exists()
        inclusive_end = max(segment_start, segment_end - timedelta(days=1))
        _labels, _datasets, rows, totals = _per_meter_series(
            Meter.objects.filter(pk=meter.pk), segment_start, inclusive_end, "daily"
        )
        valid_rows = [row for row in rows if row.get("usage_valid", True)]
        if not valid_rows:
            return {
                "begin": None,
                "end": None,
                "kwh": None,
                "valid": False,
                "reason": "No dashboard readings are available for this period",
                "has_period_readings": has_period_readings,
                "amount": ZERO,
                "reverse_kwh": ZERO,
            }
        first = valid_rows[0]
        last = valid_rows[-1]
        return {
            "begin": first.get("display_start_kwh", first.get("start_kwh")),
            "end": last.get("display_end_kwh", last.get("end_kwh")),
            "kwh": Decimal(str(totals["total_kwh"])),
            "valid": len(valid_rows) == len(rows),
            "has_period_readings": has_period_readings,
            "amount": Decimal(str(totals["usage_charges"])),
            "reverse_kwh": Decimal(str(totals["total_reverse_kwh"])),
            "reason": "; ".join(
                dict.fromkeys(
                    row.get("continuity_reason", "")
                    for row in rows
                    if not row.get("usage_valid", True) and row.get("continuity_reason")
                )
            ),
        }

    output_rows = []
    for meter in output_meters:
        delta = dashboard_summary(meter, start_date, end_date)
        output_rows.append({
            "meter": meter,
            "begin": delta["begin"],
            "end": delta["end"],
            "kwh": delta["kwh"],
            "reverse_kwh": delta["reverse_kwh"],
            "valid": delta["valid"],
            "reason": delta["reason"],
        })
    output_valid = bool(output_rows) and all(row["valid"] for row in output_rows)
    output_total = sum(
        (row["kwh"] for row in output_rows if row["kwh"] is not None), ZERO
    ) if output_rows else None

    billing_rows = []
    billing_total = ZERO
    billing_reverse_total = ZERO
    billing_valid = True
    billing_amount_total = ZERO
    processed_meter_ids = set()
    memberships = list(
        system.output_group.memberships.filter(start_date__lt=end_date).filter(
            Q(end_date__isnull=True) | Q(end_date__gt=start_date)
        ).select_related(
            "billing_meter", "billing_meter__unit", "billing_meter__unit__property"
        ).order_by(
            "billing_meter__unit__unit_number", "-is_active", "-start_date", "pk"
        )
    )
    membership_meter_ids = {membership.billing_meter_id for membership in memberships}
    membership_by_meter_id = {
        membership.billing_meter_id: membership for membership in memberships
    }
    for membership in memberships:
        segment_start = max(start_date, membership.start_date)
        segment_end = min(end_date, membership.end_date or end_date)
        unit_id = membership.billing_meter.unit_id
        candidate_ids = [membership.billing_meter_id]
        if unit_id:
            reading_meter_ids = MeterReading.objects.filter(
                meter__unit_id=unit_id,
                meter__meter_role=Meter.METER_ROLE_BILLING,
                meter__meter_type=Meter.METER_TYPE_ELECTRIC,
                ts__gte=_aware_midnight(segment_start),
                ts__lt=_aware_midnight(segment_end),
            ).values_list("meter_id", flat=True).distinct()
            candidate_ids.extend(reading_meter_ids)
        candidates = list(
            Meter.objects.filter(pk__in=dict.fromkeys(candidate_ids))
            .select_related("unit", "unit__property")
            .order_by("is_active", "meter_number")
        )
        for meter in candidates:
            if meter.pk in processed_meter_ids:
                continue
            delta = dashboard_summary(meter, segment_start, segment_end)
            meter_amount = delta["amount"]
            if (
                delta["kwh"] is None
                and not delta["has_period_readings"]
                and not meter_amount
            ):
                continue
            processed_meter_ids.add(meter.pk)
            if delta["kwh"] is not None:
                billing_total += delta["kwh"]
            billing_reverse_total += delta["reverse_kwh"]
            billing_amount_total += meter_amount
            if not delta["valid"]:
                billing_valid = False
            billing_rows.append({
                "meter": meter,
                "unit": meter.unit or membership.billing_meter.unit,
                "begin": delta["begin"],
                "end": delta["end"],
                "kwh": delta["kwh"],
                "reverse_kwh": delta["reverse_kwh"],
                "amount": meter_amount,
                "unit_rate": Decimal(str(meter.effective_unit_rate or ZERO)),
                "valid": delta["valid"],
                "reason": delta["reason"],
                "is_replacement": meter.pk not in membership_meter_ids,
                "membership": membership_by_meter_id.get(meter.pk),
            })

    billing_rows.sort(key=lambda row: (
        _natural_text_key(getattr(row["unit"], "unit_number", "") or ""),
        row["meter"].meter_number,
    ))
    unit_counts = {}
    for row in billing_rows:
        unit_id = getattr(row["unit"], "pk", None)
        unit_counts[unit_id] = unit_counts.get(unit_id, 0) + 1
    unit_groups_by_id = {}
    for sequence, row in enumerate(billing_rows, start=1):
        row["sequence"] = sequence
        unit_id = getattr(row["unit"], "pk", None)
        multiple = unit_counts.get(unit_id, 0) > 1
        if multiple:
            row["display_name"] = row["meter"].name or row["meter"].meter_number
        else:
            property_name = getattr(
                getattr(row["unit"], "property", None), "property_name", ""
            )
            property_name = property_name[:8]
            unit_name = getattr(row["unit"], "unit_number", "")
            row["display_name"] = " - ".join(
                part for part in (property_name, unit_name) if part
            )
        group = unit_groups_by_id.setdefault(unit_id, {
            "unit": row["unit"],
            "rows": [],
            "total_kwh": ZERO,
            "total_reverse_kwh": ZERO,
            "total_amount": ZERO,
        })
        group["rows"].append(row)
        group["total_kwh"] += row["kwh"] or ZERO
        group["total_reverse_kwh"] += row["reverse_kwh"] or ZERO
        group["total_amount"] += row["amount"] or ZERO
    unit_groups = list(unit_groups_by_id.values())
    for unit_group in unit_groups:
        unit_group["average_rate"] = (
            (unit_group["total_amount"] / unit_group["total_kwh"]).quantize(
                Decimal("0.01")
            )
            if unit_group["total_kwh"] else None
        )

    variance = (
        output_total - billing_total
        if output_valid and billing_valid
        else None
    )

    return {
        "output_rows": output_rows,
        "output_total_kwh": output_total,
        "billing_rows": billing_rows,
        "unit_groups": unit_groups,
        "billing_total_kwh": billing_total,
        "billing_total_reverse_kwh": billing_reverse_total,
        "billing_total_amount": billing_amount_total,
        "billing_incomplete": not billing_valid,
        "variance_kwh": variance,
    }


def iesco_bill_history(system, limit=6):
    """Recent saved IESCO bills for this system's connection, from the Invoices app."""
    bills = list(_iesco_invoice_queryset(system))
    bills.sort(
        key=lambda bill: (
            _parse_iesco_date(bill.issue_date)
            or _parse_iesco_date(bill.reading_date)
            or datetime.min.date(),
            bill.pk,
        ),
        reverse=True,
    )
    return bills[:limit]


def inverter_reading_period_delta(inverter, start_date, end_date):
    """Generation for one inverter over a period: the closest logged reading at/before the
    period end minus the closest logged reading at/before the period start. Mirrors the
    smart-meter delta pattern but against the simpler InverterReading log."""
    start_at = _aware_midnight(start_date)
    end_at = _aware_midnight(end_date)

    def closest(target_at):
        before = (
            InverterReading.objects.filter(inverter=inverter, recorded_at__lte=target_at)
            .order_by("-recorded_at", "-id").first()
        )
        after = (
            InverterReading.objects.filter(inverter=inverter, recorded_at__gt=target_at)
            .order_by("recorded_at", "id").first()
        )
        candidates = [row for row in (before, after) if row is not None]
        if not candidates:
            return None
        return min(candidates, key=lambda row: abs(row.recorded_at - target_at))

    start_reading = closest(start_at)
    end_reading = closest(end_at)
    if start_reading is None or end_reading is None:
        return {"kwh": None, "valid": False, "reason": "Missing a logged reading near this boundary", "start": start_reading, "end": end_reading}
    if end_reading.recorded_at <= start_reading.recorded_at:
        return {"kwh": None, "valid": False, "reason": "Only one reading is logged for this inverter in range", "start": start_reading, "end": end_reading}
    return {
        "kwh": end_reading.reading_kwh - start_reading.reading_kwh,
        "valid": True,
        "reason": "",
        "start": start_reading,
        "end": end_reading,
    }


def build_inverter_breakdown(system, start_date, end_date):
    """Per-inverter generation rows for Check 3, plus the total (the manual cross-check
    figure) across every active inverter on this Energy System."""
    rows = []
    total = ZERO
    all_valid = True
    for inverter in system.inverters.filter(is_active=True).order_by("name"):
        delta = inverter_reading_period_delta(inverter, start_date, end_date)
        latest = inverter.readings.order_by("-recorded_at").first()
        if delta["valid"]:
            total += delta["kwh"]
        else:
            all_valid = False
        rows.append({
            "inverter": inverter,
            "kwh": delta["kwh"],
            "valid": delta["valid"],
            "reason": delta["reason"],
            "latest_reading": latest,
        })
    return {
        "rows": rows,
        "total_kwh": total if (rows and all_valid) else None,
        "has_inverters": bool(rows),
    }


def iesco_display_bill(system, start_date, end_date):
    """Bill details, aggregate values, and reading interval for scoreboard display."""
    selected = _iesco_invoice_bills(system, start_date, end_date)
    all_bills = list(_iesco_invoice_queryset(system))
    all_bills.sort(key=lambda bill: (
        _parse_iesco_date(bill.reading_date) or datetime.min.date(), bill.pk
    ))
    shown = selected or (all_bills[-1:] if all_bills else [])
    if not shown:
        return {"bill": None, "bills": [], "is_exact_match": False}

    shown_ids = {bill.pk for bill in shown}
    first_index = next(
        (index for index, bill in enumerate(all_bills) if bill.pk in shown_ids), 0
    )
    reading_dates = [
        parsed for parsed in (_parse_iesco_date(bill.reading_date) for bill in shown)
        if parsed is not None
    ]
    period_start = (
        _parse_iesco_date(all_bills[first_index - 1].reading_date)
        if first_index > 0 else (min(reading_dates) if reading_dates else None)
    )
    period_end = max(reading_dates) if reading_dates else None
    return {
        "bill": shown[-1],
        "bills": shown,
        "is_exact_match": bool(selected),
        "period_start": period_start,
        "period_end": period_end,
        "import_kwh": sum(
            (Decimal(bill.import_units) for bill in shown if bill.import_units is not None), ZERO
        ),
        "export_kwh": sum(
            (Decimal(bill.export_units) for bill in shown if bill.export_units is not None), ZERO
        ),
        "bill_cost": sum(
            (bill.current_bill_amount for bill in shown if bill.current_bill_amount is not None), ZERO
        ),
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


def _parse_iesco_date(value):
    value = (value or "").strip()
    for date_format in ("%d %b %y", "%d %B %y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def _iesco_invoice_queryset(system):
    """All saved IESCO records for the linked connection, regardless of review state."""
    from invoices.models import IescoBillReading

    try:
        connection = system.utility_connection
    except UtilityConnection.DoesNotExist:
        return IescoBillReading.objects.none()
    lookup = Q()
    if connection.reference_no:
        lookup |= Q(reference_no=connection.reference_no)
    if connection.consumer_id:
        lookup |= Q(consumer_id=connection.consumer_id)
    if not lookup:
        return IescoBillReading.objects.none()
    return IescoBillReading.objects.filter(lookup).order_by("received_at", "pk")


def _iesco_invoice_bills(system, start_date, end_date):
    """Return saved bills whose issue date falls inside the requested period."""
    source_bills = list(_iesco_invoice_queryset(system))
    source_bills.sort(key=lambda bill: (
        _parse_iesco_date(bill.reading_date) or datetime.min.date(), bill.pk
    ))

    # "Update Period" changes the screen to the meter-reading interval. Prefer
    # the bill whose previous/current reading dates exactly define that interval,
    # even when an older bill's issue date also happens to sit inside it.
    exact_period = []
    selected_end = end_date - timedelta(days=1)
    for index, bill in enumerate(source_bills):
        current_reading = _parse_iesco_date(bill.reading_date)
        previous_reading = (
            _parse_iesco_date(source_bills[index - 1].reading_date)
            if index > 0 else current_reading
        )
        if previous_reading == start_date and current_reading == selected_end:
            exact_period.append(bill)
    if exact_period:
        return exact_period

    bills = []
    for bill in source_bills:
        # Older imported records may predate issue-date extraction; keep their
        # established reading-date behavior as a compatibility fallback.
        issue_date = _parse_iesco_date(bill.issue_date) or _parse_iesco_date(bill.reading_date)
        if issue_date is not None and start_date <= issue_date < end_date:
            bills.append(bill)
    bills.sort(key=lambda bill: (_parse_iesco_date(bill.issue_date), bill.pk))
    return bills


def _iesco_invoice_bill(system, start_date=None, end_date=None):
    """Latest saved bill, or the latest bill issued inside a selected period."""
    bills = list(_iesco_invoice_queryset(system))
    if start_date is None or end_date is None:
        bills.sort(key=lambda bill: (
            _parse_iesco_date(bill.issue_date)
            or _parse_iesco_date(bill.reading_date)
            or datetime.min.date(),
            bill.pk,
        ))
        return bills[-1] if bills else None
    selected = _iesco_invoice_bills(system, start_date, end_date)
    return selected[-1] if selected else None


def _tenant_financials(system, start_date, end_date):
    from payments.models import PaymentDetail

    meter_ids = list(
        system.output_group.memberships.filter(start_date__lt=end_date)
        .filter(Q(end_date__isnull=True) | Q(end_date__gt=start_date))
        .values_list("billing_meter_id", flat=True)
        .distinct()
    )
    # Revenue is the Energy Dashboard usage charge (valid metered units x the
    # meter's configured rate), never a manually editable invoice amount.
    revenue = build_check2_breakdown(system, start_date, end_date)["billing_total_amount"]
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
    iesco_bills = _iesco_invoice_bills(system, start_date, end_date)
    iesco_bill = iesco_bills[-1] if iesco_bills else None
    iesco_import_kwh = (
        sum(
            (Decimal(item.import_units) for item in iesco_bills if item.import_units is not None),
            ZERO,
        )
        if iesco_bills else None
    )
    iesco_export_kwh = (
        sum(
            (Decimal(item.export_units) for item in iesco_bills if item.export_units is not None),
            ZERO,
        )
        if iesco_bills else None
    )
    export_kwh = (
        iesco_export_kwh
        if iesco_export_kwh is not None
        else Decimal(bill.export_kwh) if bill else None
    )
    output_kwh = output["kwh"]
    import_kwh = (
        iesco_import_kwh
        if iesco_import_kwh is not None
        else grid_import["kwh"]
    )
    grid_export_kwh = (
        iesco_export_kwh
        if iesco_export_kwh is not None
        else grid_export["kwh"]
    )
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
    if not iesco_bill and system.grid_interface_meter_id and not grid_import["valid"]:
        withheld.append("Grid forward energy: " + grid_import["reason"])
    if not iesco_bill and system.grid_interface_meter_id and not grid_export["valid"]:
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
    utility_cost = (
        sum(
            (item.current_bill_amount for item in iesco_bills if item.current_bill_amount is not None),
            ZERO,
        )
        if iesco_bills
        else Decimal(bill.current_cycle_utility_cost) if bill else None
    )
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
        "iesco_bill": iesco_bill,
        "iesco_bills": iesco_bills,
        "iesco_import_kwh": iesco_import_kwh,
        "iesco_export_kwh": iesco_export_kwh,
        "grid_source": "IESCO invoice" if iesco_bill else "Smart meter",
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
