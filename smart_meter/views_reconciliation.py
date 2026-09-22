import hashlib
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

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
    InverterPeriodStatement,
    LiveReading,
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
    iesco_import_kwh = iesco_bill.import_units if iesco_bill else None
    iesco_export_kwh = iesco_bill.export_units if iesco_bill else None
    import_diff_kwh = (
        abs(Decimal(iesco_import_kwh) - meter_import_kwh)
        if iesco_import_kwh is not None and meter_import_kwh is not None
        else None
    )
    export_diff_kwh = (
        abs(Decimal(iesco_export_kwh) - meter_export_kwh)
        if iesco_export_kwh is not None and meter_export_kwh is not None
        else None
    )
    check1 = {
        "iesco_bill": iesco_bill,
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
        ).order_by("-is_active", "billing_meter__meter_number")
    )

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

    inverter_breakdown = build_inverter_breakdown(system, start_date, end_date + timedelta(days=1))
    iesco_display = iesco_display_bill(system, start_date, end_date + timedelta(days=1))

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
        "memberships": memberships,
        "output_readings": output_readings,
        "daily_rollup": daily_rollup,
        "inverter_statements": inverter_statements,
        "start_date": start_date,
        "end_date": end_date,
        "quick_range": quick_range,
        "other_groups": other_groups,
        "grid_forward_total": detail_context["grid_forward_total"],
        "grid_reverse_total": detail_context["grid_reverse_total"],
    }
    return render(request, "smart_meter/energy_group_scoreboard.html", context)


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
@permission_required("smart_meter.add_inverterperiodstatement", raise_exception=True)
def inverter_statement_add(request, system_id):
    system = get_object_or_404(EnergySystem, pk=system_id)
    form = InverterPeriodStatementForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        statement = form.save(commit=False)
        statement.energy_system = system
        statement.created_by = request.user
        statement.updated_by = request.user
        statement.save()
        log_audit(statement, "created", request.user)
        messages.success(request, "Inverter statement saved as unconfirmed.")
        return redirect("smart_meter:energy_system_detail", pk=system.pk)
    return render(request, "smart_meter/reconciliation_form.html", {
        "form": form, "title": "Add inverter statement", "cancel_url": system.get_absolute_url() if hasattr(system, "get_absolute_url") else None,
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

