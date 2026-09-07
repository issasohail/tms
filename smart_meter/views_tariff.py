import json
import secrets

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.access import restrict_queryset_to_properties
from properties.models import Property, Unit
from smart_meter.forms_tariff import BulkTariffForm, TariffConfigurationForm
from smart_meter.models import (
    Meter,
    MeterTariffAudit,
    MeterTariffBulkItem,
    MeterTariffBulkRun,
    MeterTariffConfiguration,
)
from smart_meter.services.tariff_configuration import (
    configure_prices,
    read_current_configuration,
    save_schedule_draft,
)
from smart_meter.status import resolve_meter_online_statuses


def _allowed_meters(request):
    return restrict_queryset_to_properties(
        Meter.objects.select_related("unit", "unit__property"), request.user, "unit__property"
    )


def _new_token(request, key):
    token = secrets.token_urlsafe(24)
    request.session[key] = token
    return token


def _consume_token(request, key):
    supplied = request.POST.get("submission_token", "")
    expected = request.session.pop(key, None)
    return bool(supplied and expected and secrets.compare_digest(supplied, expected))


@permission_required("smart_meter.read_meter_tariff", raise_exception=True)
def tariff_configure(request, meter_id):
    meter = get_object_or_404(_allowed_meters(request), pk=meter_id)
    configuration, _ = MeterTariffConfiguration.objects.get_or_create(meter=meter)
    latest_read = meter.tariff_audits.filter(status="read").first()
    initial = {}
    if latest_read:
        live_prices = latest_read.values_after.get("prices", [])
        live_count = latest_read.values_after.get("active_rate_count") or 1
        initial.update(
            mode="flat" if meter.tariff_capability == "single_rate" or live_count <= 1 else "time_of_use",
            active_rate_count=max(1, live_count),
            flat_rate=live_prices[0] if live_prices and len(set(live_prices)) == 1 else None,
            schedule_json=json.dumps(configuration.schedule_draft or []),
        )
        for index, price in enumerate(live_prices, 1):
            initial[f"rate_{index}_price"] = price
        for index in range(1, 5):
            initial[f"rate_{index}_label"] = getattr(configuration, f"rate_{index}_label")
    form = TariffConfigurationForm(request.POST or None, initial=initial, meter=meter)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "read":
            audit = read_current_configuration(meter=meter, user=request.user)
            if audit.status == "read":
                messages.success(request, "Live meter tariff configuration read successfully.")
            else:
                messages.error(request, audit.error or audit.get_status_display())
            return redirect("smart_meter:tariff_configure", meter_id=meter.pk)
        if action in {"write_tariff", "save_draft"}:
            if not request.user.has_perm("smart_meter.write_meter_tariff"):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied
            if not _consume_token(request, f"tariff_submission_{meter.pk}"):
                messages.error(request, "This tariff submission was already used or expired. Review and confirm again.")
                return redirect("smart_meter:tariff_configure", meter_id=meter.pk)
            if form.is_valid() and request.POST.get("confirm_write") == "yes":
                if action == "save_draft":
                    audit = save_schedule_draft(
                        meter=meter, user=request.user, rows=form.cleaned_data["schedule"],
                        labels=form.cleaned_data["labels"],
                        active_rate_count=form.cleaned_data["active_rate_count"],
                    )
                    messages.success(request, "Time-of-use schedule saved as a draft. No schedule frame was sent.")
                else:
                    audit = configure_prices(
                        meter=meter, user=request.user, mode=form.cleaned_data["mode"],
                        prices=form.cleaned_data["prices"], labels=form.cleaned_data["labels"],
                        active_rate_count=form.cleaned_data["active_rate_count"],
                    )
                    if audit.status == "verified":
                        messages.success(request, "Tariff verified by immediate meter read-back.")
                    elif audit.status == "no_change":
                        messages.info(request, "Live values already matched; no write was sent.")
                    else:
                        messages.error(request, audit.error or audit.get_status_display())
                    return redirect("smart_meter:tariff_audit_detail", audit_id=audit.pk)
            elif request.POST.get("confirm_write") != "yes":
                form.add_error(None, "Confirm the operation before continuing.")

    token = _new_token(request, f"tariff_submission_{meter.pk}")
    live_values = latest_read.values_after if latest_read else None
    return render(request, "smart_meter/tariff_configure.html", {
        "meter": meter, "configuration": configuration, "form": form,
        "live_values": live_values, "latest_read": latest_read,
        "submission_token": token, "schedule_write_verified": False,
        "schedule_draft_json": json.dumps(configuration.schedule_draft or []),
    })


@permission_required("smart_meter.view_meter_tariff_audit", raise_exception=True)
def tariff_audit_list(request, meter_id):
    meter = get_object_or_404(_allowed_meters(request), pk=meter_id)
    return render(request, "smart_meter/tariff_audit_list.html", {
        "meter": meter, "audits": meter.tariff_audits.select_related("initiating_user")[:200],
    })


@permission_required("smart_meter.view_meter_tariff_audit", raise_exception=True)
def tariff_audit_detail(request, audit_id):
    audit = get_object_or_404(
        MeterTariffAudit.objects.select_related("meter", "initiating_user"), pk=audit_id
    )
    if not _allowed_meters(request).filter(pk=audit.meter_id).exists():
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return render(request, "smart_meter/tariff_audit_detail.html", {"audit": audit})


@permission_required("smart_meter.bulk_write_meter_tariff", raise_exception=True)
def tariff_bulk_setup(request):
    meters = _allowed_meters(request).exclude(tariff_capability="unknown")
    capability = request.GET.get("capability", "")
    property_id, unit_id = request.GET.get("property", ""), request.GET.get("unit", "")
    online = request.GET.get("online", "")
    if capability in {"single_rate", "multi_rate"}:
        meters = meters.filter(tariff_capability=capability)
    if property_id.isdigit():
        meters = meters.filter(unit__property_id=property_id)
    if unit_id.isdigit():
        meters = meters.filter(unit_id=unit_id)
    meter_list = list(meters.order_by("unit__property__property_name", "unit__unit_number", "meter_number"))
    statuses = resolve_meter_online_statuses((meter, None) for meter in meter_list)
    for meter in meter_list:
        meter.is_online = statuses[meter.pk]["is_online"]
    if online in {"online", "offline"}:
        meter_list = [meter for meter in meter_list if meter.is_online == (online == "online")]

    form = BulkTariffForm(request.POST or None)
    if request.method == "POST" and form.is_valid() and request.POST.get("confirm_write") == "yes":
        selected = list(meters.filter(pk__in=form.cleaned_data["meter_ids"]))
        if len(selected) != len(form.cleaned_data["meter_ids"]):
            form.add_error("meter_ids", "One or more selected meters are outside your permitted filter scope.")
        else:
            with transaction.atomic():
                run = MeterTariffBulkRun.objects.create(
                    requested_price=form.cleaned_data["price"], status="running", created_by=request.user
                )
                MeterTariffBulkItem.objects.bulk_create([
                    MeterTariffBulkItem(run=run, meter=meter, capability=meter.tariff_capability)
                    for meter in selected
                ])
            return redirect("smart_meter:tariff_bulk_result", run_id=run.pk)
    return render(request, "smart_meter/tariff_bulk_setup.html", {
        "meters": meter_list, "form": form, "properties": Property.objects.order_by("property_name"),
        "units": Unit.objects.select_related("property").order_by("property__property_name", "unit_number"),
    })


@permission_required("smart_meter.bulk_write_meter_tariff", raise_exception=True)
def tariff_bulk_result(request, run_id):
    run = get_object_or_404(MeterTariffBulkRun.objects.prefetch_related("items__meter"), pk=run_id)
    if run.created_by_id != request.user.pk and not request.user.is_superuser:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return render(request, "smart_meter/tariff_bulk_result.html", {"run": run})


@require_POST
@permission_required("smart_meter.bulk_write_meter_tariff", raise_exception=True)
def tariff_bulk_process_item(request, run_id, item_id):
    item = get_object_or_404(
        MeterTariffBulkItem.objects.select_related("run", "meter"), pk=item_id, run_id=run_id
    )
    if item.run.created_by_id != request.user.pk and not request.user.is_superuser:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    if item.processed_at and request.POST.get("manual_retry") != "yes":
        return JsonResponse({"status": item.status, "error": item.error, "audit_id": item.audit_id})
    if item.processed_at and request.POST.get("confirm_write") != "yes":
        return JsonResponse({"status": "failed", "error": "Manual retry requires confirmation."}, status=400)
    audit = configure_prices(
        meter=item.meter, user=request.user, mode="flat", prices=[item.run.requested_price],
        labels=["Flat", "Rate 2", "Rate 3", "Rate 4"], active_rate_count=1,
    )
    item.status, item.error, item.audit = audit.status, audit.error, audit
    item.old_prices = audit.values_before.get("prices", [])
    item.processed_at = timezone.now()
    item.save()
    remaining = item.run.items.filter(processed_at__isnull=True).exists()
    if not remaining:
        item.run.status = "completed"
        item.run.completed_at = timezone.now()
        item.run.save(update_fields=["status", "completed_at"])
    return JsonResponse({
        "status": item.status, "error": item.error, "audit_id": audit.pk,
        "old_prices": item.old_prices,
    })
