import hashlib
import re
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from smart_meter.forms_reconciliation import (
    EnergySystemReassignmentForm,
    EnergySystemSetupForm,
    InverterPeriodStatementForm,
    InverterReadingForm,
    UtilityBillCycleForm,
    UtilityBillPaymentForm,
    energy_system_output_meter_queryset,
)
from smart_meter.forms import MeterReadingProfileForm
from smart_meter.models import (
    EnergySystem,
    EnergySystemMeterLink,
    Inverter,
    InverterReading,
    InverterPeriodStatement,
    LiveReading,
    Meter,
    MeterReading,
    MeterCheckGroup,
    UtilityBillCycle,
    UtilityBillPayment,
    UtilityConnection,
)
from smart_meter.services.reconciliation import (
    _aware_midnight,
    _iesco_invoice_bill,
    build_check2_breakdown,
    build_energy_reconciliation,
    build_inverter_breakdown,
    build_latest_linked_meter_snapshot,
    confirm_bill,
    finalize_bill,
    iesco_bill_history,
    iesco_display_bill,
    log_audit,
    reopen_record,
)
from smart_meter.services.utility_bill_parser import UtilityBillParseError, parse_utility_bill


def _tolerance_status(diff, base):
    """ok / warn / bad badge for a Check card, from the diff as a % of the base figure.
    Unknown (missing data) reads as 'warn' rather than a false 'ok'."""
    if diff is None or base is None or base == 0:
        return "warn"
    pct = abs(diff) / abs(base) * 100
    if pct <= 1:
        return "ok"
    if pct <= 3:
        return "warn"
    return "bad"


def _scoreboard_period_boundaries(system, start_date, end_date):
    """Calendar-month and IESCO-reading boundaries used by every detail table."""
    cut_dates = {start_date, end_date + timedelta(days=1)}
    cursor = start_date.replace(day=1)
    while cursor <= end_date:
        next_month = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
        )
        if start_date < next_month <= end_date:
            cut_dates.add(next_month)
        cursor = next_month
    for bill in iesco_bill_history(system, limit=24):
        reading_date = None
        for date_format in ("%d %b %y", "%d %B %y", "%Y-%m-%d", "%d-%b-%Y"):
            try:
                reading_date = datetime.strptime(
                    (bill.reading_date or "").strip(), date_format
                ).date()
                break
            except ValueError:
                continue
        if reading_date and start_date < reading_date < end_date:
            cut_dates.add(reading_date + timedelta(days=1))
    return sorted(cut_dates)


def _average_rate(amount, units):
    if amount is None or units is None or units == 0:
        return None
    return (Decimal(amount) / Decimal(units)).quantize(Decimal("0.01"))


def _parse_period(request):
    today = timezone.localdate()
    start = today.replace(day=1)
    end = today if today > start else today + timedelta(days=1)
    try:
        if request.GET.get("start"):
            start = date.fromisoformat(request.GET["start"])
        if request.GET.get("end"):
            end = date.fromisoformat(request.GET["end"])
    except ValueError:
        raise ValidationError("Enter valid ISO dates.")
    if end <= start:
        raise ValidationError("The period end must be after the period start.")
    return start, end


@login_required
@permission_required("smart_meter.view_energysystem", raise_exception=True)
def energy_system_list(request):
    # Energy Systems and Check Groups now share one consolidated list/template.
    from smart_meter.views import meter_check_group_list
    return meter_check_group_list(request)


@login_required
@permission_required("smart_meter.add_energysystem", raise_exception=True)
def energy_system_setup(request, group_id):
    group = get_object_or_404(
        MeterCheckGroup.objects.select_related("check_meter"), pk=group_id
    )
    if request.method == "GET":
        return redirect("smart_meter:meter_check_group_edit", pk=group.pk)
    if hasattr(group, "energy_system"):
        return redirect("smart_meter:energy_system_detail", pk=group.energy_system.pk)
    form = EnergySystemSetupForm(request.POST or None, group=group)
    if request.method == "POST" and form.is_valid():
        export_path = form.cleaned_data["output_meter_includes_grid_export"]
        with transaction.atomic():
            group.name = form.cleaned_data["name"]
            group.save(update_fields=["name"])
            system = EnergySystem.objects.create(
                name=group.name,
                output_group=group,
                output_meter_includes_grid_export=(
                    None if export_path == "" else export_path == "true"
                ),
            )
            EnergySystemMeterLink.objects.bulk_create([
                EnergySystemMeterLink(energy_system=system, meter=meter, side=EnergySystemMeterLink.SIDE_INPUT)
                for meter in form.cleaned_data["input_meters"]
            ] + [
                EnergySystemMeterLink(energy_system=system, meter=meter, side=EnergySystemMeterLink.SIDE_OUTPUT)
                for meter in form.cleaned_data["output_meters"]
            ])
            if form.cleaned_data["output_reverse_capability"]:
                form.cleaned_data["output_meters"].update(reverse_energy_capability=form.cleaned_data["output_reverse_capability"])
        messages.success(request, "Energy System created with linked input and output meters.")
        return redirect("smart_meter:energy_system_detail", pk=system.pk)
    return render(request, "smart_meter/energy_system_setup.html", {"form": form, "group": group})


@login_required
@permission_required("smart_meter.change_energysystem", raise_exception=True)
def energy_system_edit(request, pk):
    system = get_object_or_404(
        EnergySystem.objects.select_related("output_group", "output_group__check_meter"), pk=pk
    )
    group = system.output_group
    if request.method == "GET":
        return redirect("smart_meter:meter_check_group_edit", pk=group.pk)
    form = EnergySystemSetupForm(request.POST or None, group=group, energy_system=system)
    if request.method == "POST" and form.is_valid():
        export_path = form.cleaned_data["output_meter_includes_grid_export"]
        with transaction.atomic():
            group.name = form.cleaned_data["name"]
            group.save(update_fields=["name"])
            system.name = group.name
            system.output_meter_includes_grid_export = None if export_path == "" else export_path == "true"
            system.save(update_fields=["name", "output_meter_includes_grid_export"])
            system.meter_links.all().delete()
            EnergySystemMeterLink.objects.bulk_create([
                EnergySystemMeterLink(energy_system=system, meter=meter, side=EnergySystemMeterLink.SIDE_INPUT)
                for meter in form.cleaned_data["input_meters"]
            ] + [
                EnergySystemMeterLink(energy_system=system, meter=meter, side=EnergySystemMeterLink.SIDE_OUTPUT)
                for meter in form.cleaned_data["output_meters"]
            ])
            if form.cleaned_data["output_reverse_capability"]:
                form.cleaned_data["output_meters"].update(reverse_energy_capability=form.cleaned_data["output_reverse_capability"])
        messages.success(request, "Energy System meter links updated.")
        return redirect("smart_meter:energy_system_detail", pk=system.pk)
    return render(request, "smart_meter/energy_system_setup.html", {"form": form, "group": group, "system": system, "is_edit": True})


@require_POST
@login_required
@permission_required("smart_meter.change_energysystem", raise_exception=True)
def energy_system_output_meters_update(request, pk):
    raw_meter_ids = request.POST.getlist("output_meters")
    try:
        meter_ids = list(dict.fromkeys(int(value) for value in raw_meter_ids))
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "error": "Select valid output meters."},
            status=400,
        )
    if not meter_ids:
        return JsonResponse(
            {"success": False, "error": "At least one output meter is required."},
            status=400,
        )

    meters = list(energy_system_output_meter_queryset().filter(pk__in=meter_ids))
    if len(meters) != len(meter_ids):
        return JsonResponse(
            {"success": False, "error": "One or more selected meters are unavailable."},
            status=400,
        )

    with transaction.atomic():
        system = get_object_or_404(EnergySystem.objects.select_for_update(), pk=pk)
        system.meter_links.filter(side=EnergySystemMeterLink.SIDE_OUTPUT).delete()
        EnergySystemMeterLink.objects.bulk_create([
            EnergySystemMeterLink(
                energy_system=system,
                meter=meter,
                side=EnergySystemMeterLink.SIDE_OUTPUT,
            )
            for meter in meters
        ])

    return JsonResponse({
        "success": True,
        "output_meter_ids": [meter.pk for meter in meters],
        "message": f"Saved {len(meters)} output meter(s).",
    })


def build_energy_system_detail_context(system, start, end):
    """Build the energy/IESCO context embedded in the combined Check Group page."""
    report = build_energy_reconciliation(system, start, end)
    iesco_bill_latest = _iesco_invoice_bill(system)
    latest_snapshot = build_latest_linked_meter_snapshot(system)
    grid_reading = None
    if system.grid_interface_meter_id:
        grid_reading = LiveReading.objects.filter(
            meter_id=system.grid_interface_meter_id
        ).first()
        if grid_reading is None:
            grid_reading = MeterReading.objects.filter(
                meter_id=system.grid_interface_meter_id
            ).order_by("-ts", "-id").first()
    grid_forward_total = (
        getattr(grid_reading, "forward_active_energy_kwh", None)
        if grid_reading else None
    )
    if grid_forward_total is None and grid_reading:
        grid_forward_total = grid_reading.total_energy
    grid_reverse_total = (
        getattr(grid_reading, "reverse_active_energy_kwh", None)
        if grid_reading else None
    )
    grid_net_total = (
        grid_forward_total - grid_reverse_total
        if grid_forward_total is not None and grid_reverse_total is not None
        else None
    )
    return {
        "system": system,
        "report": report,
        "iesco_bill_latest": iesco_bill_latest,
        "latest_snapshot": latest_snapshot,
        "reassign_form": EnergySystemReassignmentForm(
            energy_system=system,
            initial={"effective_date": timezone.localdate()},
        ),
        "grid_reading": grid_reading,
        "grid_forward_total": grid_forward_total,
        "grid_reverse_total": grid_reverse_total,
        "grid_net_total": grid_net_total,
        "grid_profile_form": (
            MeterReadingProfileForm(instance=system.grid_interface_meter)
            if system.grid_interface_meter_id else None
        ),
    }


@login_required
@permission_required("smart_meter.view_energysystem", raise_exception=True)
def energy_group_scoreboard(request, pk=None):
    """Check 1 / Check 2 / Check 3 side by side for one Energy Group, with a
    per-meter breakdown backing each number instead of a single opaque figure."""
    if pk is None:
        default_group = (
            MeterCheckGroup.objects.filter(energy_system__isnull=False, is_active=True)
            .order_by("name", "pk")
            .first()
        )
        if default_group is None:
            messages.info(request, "No Energy Group has an Energy System configured yet.")
            return redirect("smart_meter:meter_check_group_list")
        return redirect("smart_meter:energy_group_scoreboard", pk=default_group.pk)

    group = get_object_or_404(
        MeterCheckGroup.objects.select_related("check_meter"), pk=pk
    )
    system = getattr(group, "energy_system", None)
    if system is None:
        messages.info(request, "This Energy Group has no Energy System configured yet.")
        return redirect("smart_meter:meter_check_group_detail", pk=group.pk)

    today = timezone.localdate()
    start_date = today.replace(day=1)
    end_date = today
    quick_range = (request.GET.get("range") or "").strip().lower()
    if quick_range == "this_month":
        start_date = today.replace(day=1)
    elif quick_range == "last_month":
        end_date = today.replace(day=1) - timedelta(days=1)
        start_date = end_date.replace(day=1)
    elif quick_range == "selected_month":
        try:
            selected_year = int(request.GET.get("year") or today.year)
            selected_month = int(request.GET.get("month") or today.month)
            start_date = date(selected_year, selected_month, 1)
            end_date = date(
                selected_year,
                selected_month,
                monthrange(selected_year, selected_month)[1],
            )
        except (TypeError, ValueError):
            messages.warning(request, "Invalid month or year; the current month is shown.")
            start_date = today.replace(day=1)
            end_date = today
            quick_range = ""
    else:
        quick_range = ""
        try:
            if request.GET.get("start"):
                start_date = date.fromisoformat(request.GET["start"])
            if request.GET.get("end"):
                end_date = date.fromisoformat(request.GET["end"])
        except ValueError:
            messages.warning(request, "Invalid date range; the current month is shown.")
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    detail_context = build_energy_system_detail_context(
        system, start_date, end_date + timedelta(days=1)
    )
    report = detail_context["report"]
    iesco_bill = report["iesco_bill"]

    meter_import_kwh = report["grid_import_readings"]["kwh"]
    meter_export_kwh = report["grid_export_readings"]["kwh"]
    iesco_import_kwh = report.get("iesco_import_kwh")
    iesco_export_kwh = report.get("iesco_export_kwh")
    import_diff_kwh = (
        Decimal(iesco_import_kwh) - meter_import_kwh
        if iesco_import_kwh is not None and meter_import_kwh is not None
        else None
    )
    export_diff_kwh = (
        Decimal(iesco_export_kwh) - meter_export_kwh
        if iesco_export_kwh is not None and meter_export_kwh is not None
        else None
    )
    check1 = {
        "iesco_bill": iesco_bill,
        "iesco_bills": report.get("iesco_bills", []),
        "iesco_import_kwh": iesco_import_kwh,
        "iesco_export_kwh": iesco_export_kwh,
        "meter_import_kwh": meter_import_kwh,
        "meter_export_kwh": meter_export_kwh,
        "import_diff_kwh": import_diff_kwh,
        "export_diff_kwh": export_diff_kwh,
        "import_status": _tolerance_status(import_diff_kwh, iesco_import_kwh),
        "export_status": _tolerance_status(export_diff_kwh, iesco_export_kwh),
    }

    check2 = build_check2_breakdown(system, start_date, end_date + timedelta(days=1))
    check2["status"] = _tolerance_status(check2["variance_kwh"], check2["output_total_kwh"])

    other_groups = (
        MeterCheckGroup.objects.filter(energy_system__isnull=False, is_active=True)
        .select_related("check_meter")
        .order_by("name", "pk")
    )

    memberships = list(
        group.memberships.select_related(
            "billing_meter", "billing_meter__unit"
        )
    )
    membership_rows = [{
        "membership": membership,
        "meter": membership.billing_meter,
        "unit": membership.billing_meter.unit,
        "start_date": membership.start_date,
        "end_date": membership.end_date,
        "is_active": membership.is_active and membership.end_date is None,
        "status": "Active" if membership.is_active and membership.end_date is None else "Inactive",
        "inferred": False,
    } for membership in memberships]
    membership_meter_ids = {membership.billing_meter_id for membership in memberships}
    covered_unit_ids = {
        membership.billing_meter.unit_id
        for membership in memberships if membership.billing_meter.unit_id
    }
    replacement_candidates = Meter.objects.filter(
        unit_id__in=covered_unit_ids,
        meter_role=Meter.METER_ROLE_BILLING,
        meter_type=Meter.METER_TYPE_ELECTRIC,
    ).exclude(pk__in=membership_meter_ids).select_related("unit")
    for meter in replacement_candidates:
        first_reading = meter.readings.order_by("ts", "id").first()
        last_reading = meter.readings.order_by("-ts", "-id").first()
        if first_reading is None:
            continue
        membership_rows.append({
            "membership": None,
            "meter": meter,
            "unit": meter.unit,
            "start_date": timezone.localtime(first_reading.ts).date(),
            "end_date": timezone.localtime(last_reading.ts).date(),
            "is_active": False,
            "status": "Not assigned",
            "inferred": True,
        })

    def membership_sort_key(row):
        unit_name = getattr(row["unit"], "unit_number", "") or ""
        natural_unit = tuple(
            (0, int(part)) if part.isdigit() else (1, part.lower())
            for part in re.split(r"(\d+)", unit_name) if part
        )
        status_order = {"Active": 0, "Inactive": 1, "Not assigned": 2}
        return natural_unit, status_order.get(row["status"], 9), row["meter"].meter_number

    membership_rows.sort(key=membership_sort_key)

    period_start_at = _aware_midnight(start_date)
    period_end_at = _aware_midnight(end_date + timedelta(days=1))
    all_output_readings = list(
        MeterReading.objects.filter(
            meter_id=group.check_meter_id, ts__gte=period_start_at, ts__lt=period_end_at
        ).order_by("ts")
    )
    output_readings = all_output_readings[:60]

    daily_rollup = []
    by_day = {}
    for reading in all_output_readings:
        day = timezone.localtime(reading.ts).date()
        bucket = by_day.setdefault(day, [])
        bucket.append(reading)
    for day in sorted(by_day):
        readings_for_day = by_day[day]
        first_reading = readings_for_day[0]
        last_reading = readings_for_day[-1]
        kwh = (
            last_reading.total_energy - first_reading.total_energy
            if last_reading.total_energy is not None and first_reading.total_energy is not None
            else None
        )
        daily_rollup.append({
            "day": day,
            "meter_number": group.check_meter.meter_number,
            "begin": first_reading.total_energy,
            "end": last_reading.total_energy,
            "kwh": kwh,
            "reading_count": len(readings_for_day),
        })

    # Use the maintained dashboard calculation for the audit/output meter.
    from smart_meter.views_dashboard import _per_meter_series
    _labels, _datasets, output_rows, output_totals = _per_meter_series(
        Meter.objects.filter(pk=group.check_meter_id), start_date, end_date, "daily"
    )
    valid_output_rows = [row for row in output_rows if row.get("usage_valid", True)]
    output_period_total = Decimal(str(output_totals["total_kwh"]))
    output_billing_difference = (
        output_period_total - check2["billing_total_kwh"]
        if output_period_total is not None and check2["billing_total_kwh"] is not None
        else None
    )
    check2["variance_kwh"] = output_billing_difference
    check2["status"] = _tolerance_status(output_billing_difference, output_period_total)

    input_meter_ids = list(
        system.meter_links.filter(side=EnergySystemMeterLink.SIDE_INPUT)
        .values_list("meter_id", flat=True)
    )
    if not input_meter_ids and system.grid_interface_meter_id:
        input_meter_ids = [system.grid_interface_meter_id]
    has_separate_audit_meter = group.check_meter_id not in input_meter_ids
    input_meter_rows = []
    for input_meter in Meter.objects.filter(pk__in=input_meter_ids).select_related("unit").order_by("meter_number"):
        _input_labels, _input_datasets, input_rows, input_totals = _per_meter_series(
            Meter.objects.filter(pk=input_meter.pk), start_date, end_date, "daily"
        )
        valid_import_rows = [row for row in input_rows if row.get("usage_valid", True)]
        valid_export_rows = [
            row for row in input_rows
            if row.get("reverse_usage_valid", True) and row.get("reverse_usage") is not None
        ]
        input_meter_rows.append({
            "meter": input_meter,
            "begin": valid_import_rows[0].get("display_start_kwh") if valid_import_rows else None,
            "end": valid_import_rows[-1].get("display_end_kwh") if valid_import_rows else None,
            "import_kwh": (
                Decimal(str(input_totals["total_kwh"])) if valid_import_rows else None
            ),
            "reverse_begin": (
                valid_export_rows[0].get("display_start_reverse_kwh")
                if valid_export_rows else None
            ),
            "reverse_end": (
                valid_export_rows[-1].get("display_end_reverse_kwh")
                if valid_export_rows else None
            ),
            "export_kwh": (
                Decimal(str(input_totals["total_reverse_kwh"]))
                if valid_export_rows else None
            ),
        })
    input_import_values = [
        row["import_kwh"] for row in input_meter_rows if row["import_kwh"] is not None
    ]
    input_export_values = [
        row["export_kwh"] for row in input_meter_rows if row["export_kwh"] is not None
    ]
    input_import_kwh = sum(input_import_values, Decimal("0")) if input_import_values else None
    input_export_kwh = sum(input_export_values, Decimal("0")) if input_export_values else None
    solar_units_kwh = None
    if has_separate_audit_meter and output_period_total is not None and input_import_kwh is not None:
        if system.output_meter_includes_grid_export is True:
            solar_units_kwh = output_period_total - input_import_kwh
        elif system.output_meter_includes_grid_export is False and input_export_kwh is not None:
            solar_units_kwh = output_period_total + input_export_kwh - input_import_kwh
    audit_import_diff = (
        Decimal(iesco_import_kwh) - input_import_kwh
        if iesco_import_kwh is not None and input_import_kwh is not None else None
    )
    input_export_diff = (
        Decimal(iesco_export_kwh) - input_export_kwh
        if iesco_export_kwh is not None and input_export_kwh is not None else None
    )
    check1.update({
        "input_import_kwh": input_import_kwh,
        "input_export_kwh": input_export_kwh,
        "input_net_kwh": (
            input_import_kwh - input_export_kwh
            if input_import_kwh is not None and input_export_kwh is not None else None
        ),
        "solar_units_kwh": solar_units_kwh,
        "iesco_amount": report.get("current_cycle_utility_cost"),
        "billing_amount": report.get("tenant_energy_revenue"),
        "solar_income": report.get("operating_energy_margin"),
        "audit_import_kwh": output_period_total if has_separate_audit_meter else None,
        "audit_net_kwh": output_period_total if has_separate_audit_meter else None,
        "billing_import_kwh": check2["billing_total_kwh"],
        "billing_export_kwh": check2["billing_total_reverse_kwh"],
        "billing_net_kwh": (
            check2["billing_total_kwh"] - check2["billing_total_reverse_kwh"]
        ),
        "iesco_net_kwh": (
            Decimal(iesco_import_kwh) - Decimal(iesco_export_kwh)
            if iesco_import_kwh is not None and iesco_export_kwh is not None
            else None
        ),
        "import_diff_kwh": audit_import_diff,
        "export_diff_kwh": input_export_diff,
        "import_status": _tolerance_status(audit_import_diff, iesco_import_kwh),
        "export_status": _tolerance_status(input_export_diff, iesco_export_kwh),
    })

    inverter_breakdown = build_inverter_breakdown(system, start_date, end_date + timedelta(days=1))
    iesco_display = iesco_display_bill(system, start_date, end_date + timedelta(days=1))
    check1.update({
        "iesco_average_rate": _average_rate(
            report.get("current_cycle_utility_cost"), check1.get("iesco_import_kwh")
        ),
        "audit_average_rate": _average_rate(
            check2["billing_total_amount"], output_period_total
        ) if has_separate_audit_meter else None,
        "input_average_rate": _average_rate(
            report.get("current_cycle_utility_cost"), input_import_kwh
        ),
        "billing_average_rate": _average_rate(
            check2["billing_total_amount"], check2["billing_total_kwh"]
        ),
    })
    audit_summary = {
        "begin": (
            valid_output_rows[0].get("display_start_kwh")
            if valid_output_rows else None
        ),
        "end": (
            valid_output_rows[-1].get("display_end_kwh")
            if valid_output_rows else None
        ),
        "reverse_begin": (
            valid_output_rows[0].get("display_start_reverse_kwh")
            if valid_output_rows else None
        ),
        "reverse_end": (
            valid_output_rows[-1].get("display_end_reverse_kwh")
            if valid_output_rows else None
        ),
        "rate": check1["audit_average_rate"],
    }

    inverter_statements = list(
        system.inverter_statements.order_by("-period_end", "-id")[:6]
    )

    context = {
        "group": group,
        "system": system,
        "report": report,
        "check1": check1,
        "check2": check2,
        "bill_history": iesco_bill_history(system),
        "iesco_display": iesco_display,
        "inverter_breakdown": inverter_breakdown,
        "audit_summary": audit_summary,
        "input_meter_rows": input_meter_rows,
        "has_separate_audit_meter": has_separate_audit_meter,
        "memberships": membership_rows,
        "output_readings": output_readings,
        "daily_rollup": daily_rollup,
        "output_period_total": output_period_total,
        "output_billing_difference": output_billing_difference,
        "inverter_statements": inverter_statements,
        "start_date": start_date,
        "end_date": end_date,
        "quick_range": quick_range,
        "selected_month": start_date.month,
        "selected_year": start_date.year,
        "month_choices": [
            (number, date(2000, number, 1).strftime("%B"))
            for number in range(1, 13)
        ],
        "year_choices": range(
            min(today.year - 5, start_date.year),
            max(today.year + 1, start_date.year) + 1,
        ),
        "other_groups": other_groups,
        "grid_forward_total": detail_context["grid_forward_total"],
        "grid_reverse_total": detail_context["grid_reverse_total"],
    }
    return render(request, "smart_meter/energy_group_scoreboard.html", context)


@require_GET
@login_required
@permission_required("smart_meter.view_energysystem", raise_exception=True)
def energy_group_meter_detail(request, pk, meter_id):
    """AJAX fragment containing grouped daily rows for an input, audit, or billing meter."""
    group = get_object_or_404(
        MeterCheckGroup.objects.select_related("energy_system"), pk=pk
    )
    input_meter_ids = set(
        group.energy_system.meter_links.filter(side=EnergySystemMeterLink.SIDE_INPUT)
        .values_list("meter_id", flat=True)
    )
    if not input_meter_ids and group.energy_system.grid_interface_meter_id:
        input_meter_ids.add(group.energy_system.grid_interface_meter_id)
    is_input_detail = request.GET.get("source") == "input" and meter_id in input_meter_ids
    is_audit_detail = not is_input_detail and meter_id == group.check_meter_id
    if is_input_detail or is_audit_detail:
        meter = get_object_or_404(
            Meter.objects.select_related("unit", "unit__property"), pk=meter_id
        )
    else:
        unit_ids = set(
            group.memberships.filter(billing_meter__unit_id__isnull=False)
            .values_list("billing_meter__unit_id", flat=True)
        )
        meter = get_object_or_404(
            Meter.objects.select_related("unit", "unit__property"),
            pk=meter_id,
            unit_id__in=unit_ids,
            meter_role=Meter.METER_ROLE_BILLING,
        )
    today = timezone.localdate()
    try:
        start_date = date.fromisoformat(request.GET.get("start") or today.replace(day=1).isoformat())
        end_date = date.fromisoformat(request.GET.get("end") or today.isoformat())
    except ValueError:
        return JsonResponse({"error": "Invalid date range."}, status=400)
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    detail_direction = (
        "export"
        if (is_input_detail or is_audit_detail) and request.GET.get("direction") == "export"
        else "import"
    )

    from smart_meter.views_dashboard import _per_meter_series
    _labels, _datasets, rows, totals = _per_meter_series(
        Meter.objects.filter(pk=meter.pk), start_date, end_date, "daily"
    )
    audit_meter = group.check_meter
    _audit_labels, _audit_datasets, audit_rows, audit_totals = _per_meter_series(
        Meter.objects.filter(pk=audit_meter.pk), start_date, end_date, "daily"
    )

    # Split reconciliation at calendar-month boundaries and immediately after
    # an IESCO reading date. This produces, for example, Aug 8–31 and Sep 1–8.
    boundaries = _scoreboard_period_boundaries(
        group.energy_system, start_date, end_date
    )

    def rows_for_period(source_rows, period_start, period_end_exclusive):
        return [
            row for row in source_rows
            if period_start <= row["period_key"] < period_end_exclusive
        ]

    period_groups = []
    for index in range(len(boundaries) - 1):
        period_start = boundaries[index]
        period_end_exclusive = boundaries[index + 1]
        billing_period_rows = rows_for_period(rows, period_start, period_end_exclusive)
        audit_period_rows = rows_for_period(audit_rows, period_start, period_end_exclusive)
        if is_input_detail or is_audit_detail:
            usage_key = "reverse_usage" if detail_direction == "export" else "usage"
            valid_key = (
                "reverse_usage_valid"
                if detail_direction == "export"
                else "usage_valid"
            )
            begin_key = (
                "display_start_reverse_kwh"
                if detail_direction == "export"
                else "display_start_kwh"
            )
            end_key = (
                "display_end_reverse_kwh"
                if detail_direction == "export"
                else "display_end_kwh"
            )
            valid_detail_rows = [
                row
                for row in billing_period_rows
                if row.get(valid_key, True) and row.get(usage_key) is not None
            ]
            daily_rows = [
                {
                    "day": row["period_key"],
                    "begin": row.get(begin_key) if row.get(valid_key, True) else None,
                    "end": row.get(end_key) if row.get(valid_key, True) else None,
                    "detail_kwh": (
                        row.get(usage_key) if row.get(valid_key, True) else None
                    ),
                    "valid": row.get(valid_key, True),
                    "reason": row.get("continuity_reason", ""),
                }
                for row in billing_period_rows
            ]
            period_groups.append({
                "start_date": period_start,
                "end_date": period_end_exclusive - timedelta(days=1),
                "detail_begin": (
                    valid_detail_rows[0].get(begin_key)
                    if valid_detail_rows else None
                ),
                "detail_end": (
                    valid_detail_rows[-1].get(end_key)
                    if valid_detail_rows else None
                ),
                "detail_kwh": sum(
                    (row[usage_key] for row in valid_detail_rows), Decimal("0")
                ),
                "daily_rows": daily_rows,
            })
            continue
        valid_billing_rows = [row for row in billing_period_rows if row.get("usage_valid", True)]
        valid_audit_rows = [row for row in audit_period_rows if row.get("usage_valid", True)]
        audit_by_day = {row["period_key"]: row for row in valid_audit_rows}
        daily_rows = []
        for billing_row in billing_period_rows:
            audit_row = audit_by_day.get(billing_row["period_key"])
            billing_usage = (
                billing_row["usage"] if billing_row.get("usage_valid", True) else None
            )
            audit_usage = (
                audit_row["usage"]
                if audit_row and audit_row.get("usage_valid", True) else None
            )
            daily_rows.append({
                "day": billing_row["period_key"],
                "begin": billing_row.get("display_start_kwh"),
                "end": billing_row.get("display_end_kwh"),
                "billing_kwh": billing_usage,
                "billing_reverse_kwh": billing_row.get("reverse_usage"),
                "unit_rate": billing_row.get("unit_rate"),
                "usage_amount": billing_row.get("usage_amount"),
                "audit_kwh": audit_usage,
                "difference_kwh": (
                    audit_usage - billing_usage
                    if audit_usage is not None and billing_usage is not None else None
                ),
                "valid": billing_row.get("usage_valid", True),
                "reason": billing_row.get("continuity_reason", ""),
            })
        billing_kwh = sum((row["usage"] for row in valid_billing_rows), Decimal("0"))
        audit_kwh = sum((row["usage"] for row in valid_audit_rows), Decimal("0"))
        billing_reverse = sum(
            (row["reverse_usage"] for row in valid_billing_rows if row["reverse_usage"] is not None),
            Decimal("0"),
        )
        usage_amount = sum((row["usage_amount"] for row in valid_billing_rows), Decimal("0"))
        period_groups.append({
            "start_date": period_start,
            "end_date": period_end_exclusive - timedelta(days=1),
            "begin": valid_billing_rows[0]["display_start_kwh"] if valid_billing_rows else None,
            "end": valid_billing_rows[-1]["display_end_kwh"] if valid_billing_rows else None,
            "billing_kwh": billing_kwh,
            "billing_reverse_kwh": billing_reverse,
            "audit_kwh": audit_kwh,
            "difference_kwh": audit_kwh - billing_kwh,
            "usage_amount": usage_amount,
            "daily_rows": daily_rows,
        })
    grouped_detail_total = sum(
        (row.get("detail_kwh", Decimal("0")) for row in period_groups),
        Decimal("0"),
    )
    grouped_billing_total = sum(
        (row.get("billing_kwh", Decimal("0")) for row in period_groups),
        Decimal("0"),
    )
    grouped_audit_total = sum(
        (row.get("audit_kwh", Decimal("0")) for row in period_groups),
        Decimal("0"),
    )
    tenant_names = list(dict.fromkeys(
        row["tenant_name"] for row in rows if row.get("tenant_name")
    ))
    # Use a relative application URL in the rendered fragment so /tms/ hosting
    # and local development both retain the configured script prefix.
    from django.urls import reverse
    full_dashboard_url = (
        f"{reverse('smart_meter:energy_dashboard')}?unit={meter.unit_id}"
        f"&meter={meter.pk}&role={'check' if is_input_detail or is_audit_detail else 'billing'}&report_type=daily"
        f"&start={start_date.isoformat()}&end={end_date.isoformat()}"
    )
    html = render_to_string("smart_meter/partials/scoreboard_meter_detail.html", {
        "meter": meter,
        "rows": rows,
        "totals": totals,
        "audit_meter": audit_meter,
        "audit_totals": audit_totals,
        "period_groups": period_groups,
        "grouped_billing_total": grouped_billing_total,
        "grouped_audit_total": grouped_audit_total,
        "grouped_difference_total": grouped_audit_total - grouped_billing_total,
        "grouped_detail_total": grouped_detail_total,
        "detail_direction": detail_direction,
        "tenant_names": tenant_names,
        "start_date": start_date,
        "end_date": end_date,
        "full_dashboard_url": full_dashboard_url,
        "is_audit_detail": is_audit_detail,
        "is_input_detail": is_input_detail,
    }, request=request)
    return JsonResponse({"html": html})


@require_GET
@login_required
@permission_required("smart_meter.view_energysystem", raise_exception=True)
def energy_group_inverter_detail(request, pk, inverter_id):
    """AJAX fragment of saved cumulative readings for one inverter."""
    group = get_object_or_404(MeterCheckGroup, pk=pk)
    inverter = get_object_or_404(
        Inverter, pk=inverter_id, energy_system=group.energy_system
    )
    today = timezone.localdate()
    try:
        start_date = date.fromisoformat(
            request.GET.get("start") or today.replace(day=1).isoformat()
        )
        end_date = date.fromisoformat(request.GET.get("end") or today.isoformat())
    except ValueError:
        return JsonResponse({"error": "Invalid date range."}, status=400)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    start_at = _aware_midnight(start_date)
    end_at = _aware_midnight(end_date + timedelta(days=1))
    previous = inverter.readings.filter(recorded_at__lt=start_at).order_by(
        "-recorded_at", "-id"
    ).first()
    readings = list(
        inverter.readings.filter(recorded_at__gte=start_at, recorded_at__lt=end_at)
        .order_by("recorded_at", "id")
    )
    detail_rows = []
    prior = previous
    for reading in readings:
        detail_rows.append({
            "reading": reading,
            "begin": prior.reading_kwh if prior else None,
            "end": reading.reading_kwh,
            "generation": (
                reading.reading_kwh - prior.reading_kwh if prior else None
            ),
        })
        prior = reading

    boundaries = _scoreboard_period_boundaries(
        group.energy_system, start_date, end_date
    )
    period_groups = []
    for index in range(len(boundaries) - 1):
        period_start = boundaries[index]
        period_end_exclusive = boundaries[index + 1]
        period_rows = [
            row for row in detail_rows
            if period_start <= timezone.localtime(row["reading"].recorded_at).date()
            < period_end_exclusive
        ]
        period_groups.append({
            "start_date": period_start,
            "end_date": period_end_exclusive - timedelta(days=1),
            "rows": period_rows,
            "total_kwh": sum(
                (row["generation"] for row in period_rows if row["generation"] is not None),
                Decimal("0"),
            ),
        })
    total_kwh = sum(
        (row["generation"] for row in detail_rows if row["generation"] is not None),
        Decimal("0"),
    )
    html = render_to_string(
        "smart_meter/partials/scoreboard_inverter_detail.html",
        {
            "inverter": inverter,
            "period_groups": period_groups,
            "total_kwh": total_kwh,
            "start_date": start_date,
            "end_date": end_date,
        },
        request=request,
    )
    return JsonResponse({"html": html})


@login_required
@permission_required("smart_meter.add_inverterreading", raise_exception=True)
def inverter_reading_add(request, system_id):
    system = get_object_or_404(EnergySystem, pk=system_id)
    if not system.inverters.filter(is_active=True).exists():
        messages.info(
            request,
            "No inverters are set up for this Energy System yet — add them from the admin, "
            "or ask for the seed command to run for this system.",
        )
        return redirect("smart_meter:energy_group_scoreboard", pk=system.output_group_id)
    form = InverterReadingForm(request.POST or None, request.FILES or None, energy_system=system)
    if request.method == "POST" and form.is_valid():
        reading = form.save(commit=False)
        reading.created_by = request.user
        reading.save()
        messages.success(request, "Inverter reading saved.")
        return redirect("smart_meter:energy_group_scoreboard", pk=system.output_group_id)
    return render(request, "smart_meter/reconciliation_form.html", {
        "form": form,
        "title": f"Add inverter reading — {system.name}",
        "cancel_url": None,
    })


@login_required
@permission_required("smart_meter.view_energysystem", raise_exception=True)
def energy_system_detail(request, pk):
    # Keep the old URL working, but render the single combined reconciliation page.
    system = get_object_or_404(EnergySystem, pk=pk)
    from smart_meter.views import meter_check_group_detail
    return meter_check_group_detail(request, system.output_group_id)


@require_POST
@login_required
@permission_required("smart_meter.change_energysystem", raise_exception=True)
def energy_system_reassign_meter(request, pk):
    system = get_object_or_404(EnergySystem, pk=pk)
    form = EnergySystemReassignmentForm(request.POST, energy_system=system)
    if not form.is_valid():
        messages.error(request, "The meter assignment was not changed: " + "; ".join(
            error for errors in form.errors.values() for error in errors
        ))
        return redirect("smart_meter:energy_system_detail", pk=system.pk)
    try:
        assignment = system.reassign_meter(
            form.cleaned_data["role"],
            form.cleaned_data["meter"],
            request.user,
            form.cleaned_data["effective_date"],
        )
        if form.cleaned_data["notes"]:
            assignment.notes = form.cleaned_data["notes"]
            assignment.save(update_fields=["notes"])
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Energy System meter assignment updated.")
    return redirect("smart_meter:energy_system_detail", pk=system.pk)


@login_required
@permission_required("smart_meter.add_inverterreading", raise_exception=True)
def inverter_statement_add(request, system_id):
    system = get_object_or_404(EnergySystem, pk=system_id)
    inverters = list(system.inverters.filter(is_active=True).order_by("name", "pk"))
    selected_date = timezone.localdate()
    errors = []
    if request.method == "POST":
        try:
            selected_date = date.fromisoformat((request.POST.get("reading_date") or "").strip())
        except ValueError:
            errors.append("Enter a valid reading date.")

        entries = []
        for inverter in inverters:
            raw_value = (request.POST.get(f"reading_{inverter.pk}") or "").strip()
            if not raw_value:
                continue
            try:
                value = Decimal(raw_value)
                if value < 0:
                    raise ValueError
            except Exception:
                errors.append(f"Enter a valid current unit count for {inverter.name}.")
                continue
            entries.append((inverter, value))

        if not entries:
            errors.append("Enter a current unit count for at least one inverter.")

        if not errors:
            recorded_at = timezone.make_aware(
                datetime.combine(selected_date, time(hour=12)),
                timezone.get_current_timezone(),
            )
            with transaction.atomic():
                for inverter, value in entries:
                    InverterReading.objects.update_or_create(
                        inverter=inverter,
                        recorded_at=recorded_at,
                        defaults={"reading_kwh": value, "created_by": request.user},
                    )
            messages.success(
                request,
                f"Saved {len(entries)} inverter reading{'s' if len(entries) != 1 else ''} for {selected_date:%d %b %Y}.",
            )
            return redirect("smart_meter:energy_group_scoreboard", pk=system.output_group_id)

    return render(request, "smart_meter/inverter_reading_bulk_form.html", {
        "system": system,
        "inverters": inverters,
        "selected_date": selected_date,
        "errors": errors,
    })


@login_required
@permission_required("smart_meter.change_inverterperiodstatement", raise_exception=True)
def inverter_statement_edit(request, pk):
    statement = get_object_or_404(InverterPeriodStatement, pk=pk)
    if statement.confirmed_at:
        messages.error(request, "Reopen this statement before editing it.")
        return redirect("smart_meter:energy_system_detail", pk=statement.energy_system_id)
    form = InverterPeriodStatementForm(request.POST or None, request.FILES or None, instance=statement)
    if request.method == "POST" and form.is_valid():
        statement = form.save(commit=False)
        statement.updated_by = request.user
        statement.save()
        log_audit(statement, "edited", request.user)
        messages.success(request, "Inverter statement updated.")
        return redirect("smart_meter:energy_system_detail", pk=statement.energy_system_id)
    return render(request, "smart_meter/reconciliation_form.html", {"form": form, "title": "Edit inverter statement"})


@require_POST
@login_required
@permission_required("smart_meter.change_inverterperiodstatement", raise_exception=True)
@transaction.atomic
def inverter_statement_confirm(request, pk):
    statement = get_object_or_404(InverterPeriodStatement.objects.select_for_update(), pk=pk)
    if statement.confirmed_at is not None:
        return HttpResponseBadRequest("This inverter statement is already confirmed.")
    statement.confirmed_at = timezone.now()
    statement.updated_by = request.user
    statement.save(update_fields=["confirmed_at", "updated_by", "updated_at"])
    log_audit(statement, "confirmed", request.user)
    messages.success(request, "Inverter statement confirmed.")
    return redirect("smart_meter:energy_system_detail", pk=statement.energy_system_id)


@require_POST
@login_required
@permission_required("smart_meter.change_inverterperiodstatement", raise_exception=True)
def inverter_statement_reopen(request, pk):
    statement = get_object_or_404(InverterPeriodStatement, pk=pk)
    try:
        reopen_record(statement, request.user, request.POST.get("reason"))
    except ValidationError as exc:
        return HttpResponseBadRequest("; ".join(exc.messages))
    messages.success(request, "Inverter statement reopened.")
    return redirect("smart_meter:energy_system_detail", pk=statement.energy_system_id)


@login_required
@permission_required("smart_meter.add_utilitybillcycle", raise_exception=True)
def utility_bill_upload(request):
    post_data = request.POST.copy() if request.method == "POST" else None
    parsed = None
    upload = request.FILES.get("attachment") if request.method == "POST" else None
    if upload and not post_data.get("bill_month"):
        try:
            parsed = parse_utility_bill(upload)
            connection = UtilityConnection.objects.filter(consumer_id=parsed.data["consumer_id"]).first()
            if connection:
                post_data["utility_connection"] = str(connection.pk)
            else:
                messages.error(request, "No Utility Connection matches the parsed Consumer ID.")
            for field, value in parsed.data.items():
                if field in UtilityBillCycleForm.base_fields and value is not None:
                    post_data[field] = value.isoformat() if hasattr(value, "isoformat") else str(value)
            for warning in parsed.warnings:
                messages.warning(request, warning)
        except UtilityBillParseError as exc:
            messages.error(request, str(exc))
    form = UtilityBillCycleForm(post_data, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        cycle = form.save(commit=False)
        cycle.created_by = request.user
        cycle.updated_by = request.user
        if parsed:
            upload.seek(0)
            cycle.extracted_raw = {
                "sha256": hashlib.sha256(upload.read()).hexdigest(),
                "parser_warnings": parsed.warnings,
                "parsed": {key: str(value) if value is not None else None for key, value in parsed.data.items()},
            }
            upload.seek(0)
        cycle.save()
        log_audit(cycle, "created", request.user)
        messages.success(request, "Utility bill saved as an unconfirmed draft.")
        return redirect("smart_meter:utility_bill_detail", pk=cycle.pk)
    return render(request, "smart_meter/utility_bill_form.html", {"form": form})


@login_required
@permission_required("smart_meter.view_utilitybillcycle", raise_exception=True)
def utility_bill_detail(request, pk):
    cycle = get_object_or_404(
        UtilityBillCycle.objects.select_related("utility_connection", "utility_connection__energy_system"),
        pk=pk,
    )
    return render(request, "smart_meter/utility_bill_detail.html", {"cycle": cycle})


@login_required
@permission_required("smart_meter.change_utilitybillcycle", raise_exception=True)
def utility_bill_edit(request, pk):
    cycle = get_object_or_404(UtilityBillCycle, pk=pk)
    if cycle.finalized_at:
        messages.error(request, "Reopen this finalized bill before editing it.")
        return redirect("smart_meter:utility_bill_detail", pk=cycle.pk)
    form = UtilityBillCycleForm(request.POST or None, request.FILES or None, instance=cycle)
    if request.method == "POST" and form.is_valid():
        cycle = form.save(commit=False)
        cycle.updated_by = request.user
        # Any edited source values must be reviewed and explicitly confirmed again.
        cycle.confirmed_at = None
        cycle.status = "draft"
        cycle.save()
        log_audit(cycle, "edited", request.user)
        messages.success(request, "Utility bill updated as an unconfirmed draft.")
        return redirect("smart_meter:utility_bill_detail", pk=cycle.pk)
    return render(request, "smart_meter/utility_bill_form.html", {"form": form, "cycle": cycle})


@require_POST
@login_required
@permission_required("smart_meter.change_utilitybillcycle", raise_exception=True)
def utility_bill_confirm(request, pk):
    cycle = get_object_or_404(UtilityBillCycle, pk=pk)
    try:
        confirm_bill(cycle, request.user)
    except ValidationError as exc:
        return HttpResponseBadRequest("; ".join(exc.messages))
    messages.success(request, "Utility bill confirmed for calculation use.")
    return redirect("smart_meter:utility_bill_detail", pk=pk)


@require_POST
@login_required
@permission_required("smart_meter.change_utilitybillcycle", raise_exception=True)
def utility_bill_finalize(request, pk):
    cycle = get_object_or_404(UtilityBillCycle, pk=pk)
    try:
        finalize_bill(cycle, request.user)
    except ValidationError as exc:
        return HttpResponseBadRequest("; ".join(exc.messages))
    messages.success(request, "Utility bill finalized.")
    return redirect("smart_meter:utility_bill_detail", pk=pk)


@require_POST
@login_required
@permission_required("smart_meter.change_utilitybillcycle", raise_exception=True)
def utility_bill_reopen(request, pk):
    cycle = get_object_or_404(UtilityBillCycle, pk=pk)
    try:
        reopen_record(cycle, request.user, request.POST.get("reason"))
    except ValidationError as exc:
        return HttpResponseBadRequest("; ".join(exc.messages))
    messages.success(request, "Utility bill reopened as a draft.")
    return redirect("smart_meter:utility_bill_detail", pk=pk)


@login_required
@permission_required("smart_meter.add_utilitybillpayment", raise_exception=True)
def utility_bill_payment_add(request, bill_id):
    cycle = get_object_or_404(UtilityBillCycle, pk=bill_id)
    form = UtilityBillPaymentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        payment = form.save(commit=False)
        payment.bill_cycle = cycle
        payment.created_by = request.user
        payment.updated_by = request.user
        payment.save()
        log_audit(payment, "created", request.user)
        messages.success(request, "Utility payment saved as unconfirmed.")
        return redirect("smart_meter:utility_bill_detail", pk=cycle.pk)
    return render(request, "smart_meter/reconciliation_form.html", {"form": form, "title": "Record utility payment"})


@login_required
@permission_required("smart_meter.change_utilitybillpayment", raise_exception=True)
def utility_bill_payment_edit(request, pk):
    payment = get_object_or_404(UtilityBillPayment, pk=pk)
    if payment.confirmed_at:
        messages.error(request, "A confirmed utility payment cannot be edited.")
        return redirect("smart_meter:utility_bill_detail", pk=payment.bill_cycle_id)
    form = UtilityBillPaymentForm(request.POST or None, request.FILES or None, instance=payment)
    if request.method == "POST" and form.is_valid():
        payment = form.save(commit=False)
        payment.updated_by = request.user
        payment.save()
        log_audit(payment, "edited", request.user)
        messages.success(request, "Utility payment updated.")
        return redirect("smart_meter:utility_bill_detail", pk=payment.bill_cycle_id)
    return render(request, "smart_meter/reconciliation_form.html", {"form": form, "title": "Edit utility payment"})


@require_POST
@login_required
@permission_required("smart_meter.change_utilitybillpayment", raise_exception=True)
@transaction.atomic
def utility_bill_payment_confirm(request, pk):
    payment = get_object_or_404(UtilityBillPayment.objects.select_for_update(), pk=pk)
    if payment.confirmed_at is not None:
        return HttpResponseBadRequest("This utility payment is already confirmed.")
    payment.confirmed_at = timezone.now()
    payment.updated_by = request.user
    payment.save(update_fields=["confirmed_at", "updated_by", "updated_at"])
    log_audit(payment, "confirmed", request.user)
    messages.success(request, "Utility payment confirmed.")
    return redirect("smart_meter:utility_bill_detail", pk=payment.bill_cycle_id)

