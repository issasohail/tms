import hashlib
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from smart_meter.forms_reconciliation import (
    EnergySystemReassignmentForm,
    InverterPeriodStatementForm,
    UtilityBillCycleForm,
    UtilityBillPaymentForm,
)
from smart_meter.models import (
    EnergySystem,
    InverterPeriodStatement,
    UtilityBillCycle,
    UtilityBillPayment,
    UtilityConnection,
)
from smart_meter.services.reconciliation import (
    build_energy_reconciliation,
    confirm_bill,
    finalize_bill,
    log_audit,
    reopen_record,
)
from smart_meter.services.utility_bill_parser import UtilityBillParseError, parse_utility_bill


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
    systems = EnergySystem.objects.select_related(
        "output_group", "output_group__check_meter", "grid_interface_meter"
    ).order_by("name")
    return render(request, "smart_meter/energy_system_list.html", {"systems": systems})


@login_required
@permission_required("smart_meter.view_energysystem", raise_exception=True)
def energy_system_detail(request, pk):
    system = get_object_or_404(
        EnergySystem.objects.select_related(
            "output_group", "output_group__check_meter", "grid_interface_meter"
        ),
        pk=pk,
    )
    try:
        start, end = _parse_period(request)
    except ValidationError as exc:
        messages.warning(request, exc.message)
        today = timezone.localdate()
        start, end = today.replace(day=1), today
    report = build_energy_reconciliation(system, start, end)
    reassign_form = EnergySystemReassignmentForm(
        energy_system=system,
        initial={"effective_date": timezone.localdate()},
    )
    return render(
        request,
        "smart_meter/energy_system_detail.html",
        {"system": system, "report": report, "reassign_form": reassign_form},
    )


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
    payment.confirmed_at = timezone.now()
    payment.updated_by = request.user
    payment.save(update_fields=["confirmed_at", "updated_by", "updated_at"])
    log_audit(payment, "confirmed", request.user)
    messages.success(request, "Utility payment confirmed.")
    return redirect("smart_meter:utility_bill_detail", pk=payment.bill_cycle_id)
