"""Prepaid Parameter 1 UI: builds locally and queues one durable write."""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from smart_meter.forms import MeterPrepaidSettingsForm, MeterSettingsForm
from smart_meter.models import MeterPrepaidSettings, MeterSettings
from smart_meter.services.prepaid_parameters import build_parameter_frame, classify_write_response
from smart_meter.services.prepaid_pilot import prepaid_allowlisted, prepaid_writes_enabled
from smart_meter.utils.db_send import send_via_db

logger = logging.getLogger(__name__)


def _result_message(result):
    """Never call a queued Parameter 1 write successful without a 0x83 reply."""
    reply = result.get("reply", "")
    if reply:
        try:
            response = classify_write_response(bytes.fromhex(reply))
        except ValueError:
            response = {"state": "ambiguous", "error": "unparseable meter response"}
        if response["state"] == "accepted":
            return "success", "Meter accepted Parameter 1 (0x83)."
        if response["state"] == "rejected":
            detail = "" if response.get("error_byte") is None else f" Error byte: {response['error_byte']:02X}."
            return "error", "Meter rejected Parameter 1 (0xC3)." + detail
        return "warning", "Parameter write may have reached the meter, but its response is ambiguous. Do not retry automatically."
    if result.get("queued"):
        return "warning", "Meter is offline; the single Parameter 1 write is queued. It will not be retried after a send attempt."
    return "warning", "Parameter write has no confirmed 0x83 response. Treat it as ambiguous; do not retry automatically."


@require_http_methods(["GET", "POST"])
@login_required
@permission_required("smart_meter.write_prepaid_parameters", raise_exception=True)
def prepaid_params(request):
    settings_row, _ = MeterSettings.objects.get_or_create(pk=1)
    if request.method == "POST" and request.POST.get("action") == "save_general":
        general_form = MeterSettingsForm(request.POST, instance=settings_row)
        form = MeterPrepaidSettingsForm()
        if general_form.is_valid():
            general_form.save()
            messages.success(request, "General meter settings updated.")
            return redirect("smart_meter:prepaid_params")
        return render(request, "smart_meter/prepaid_params.html", {
            "form": form,
            "general_form": general_form,
        })

    instance = None
    if request.method == "POST" and request.POST.get("meter"):
        instance = MeterPrepaidSettings.objects.filter(meter_id=request.POST["meter"]).first()
    form = MeterPrepaidSettingsForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        settings_row = form.save(commit=False)
        if not prepaid_writes_enabled():
            form.add_error(None, "Prepaid writes are disabled in Prepaid Controls.")
        elif not prepaid_allowlisted(settings_row.meter):
            form.add_error(None, "This meter is not enabled for prepaid operations.")
        else:
            try:
                built = build_parameter_frame(settings_row.meter.meter_number, settings_row.to_vendor_parameters())
            except (TypeError, ValueError) as exc:
                form.add_error(None, f"Parameter validation failed: {exc}")
            else:
                settings_row.save()
                # Parameter 1 is non-idempotent: one listener transmission only.
                result = send_via_db(
                    meter_number=settings_row.meter.meter_number,
                    frame_hex=built["frame"].hex().upper(), timeout=32.0,
                    expect_di="070104FF", initiated_by=request.user.get_username(),
                    reason="manual prepaid Parameter 1 update", command_type="prepaid_write",
                    source="prepaid", max_attempts=1,
                )
                level, message = _result_message(result)
                getattr(messages, level)(request, message)
                logger.info("Parameter 1 queued meter=%s command=%s status=%s", settings_row.meter.meter_number, result.get("command_id"), result.get("status"))
                return redirect("smart_meter:prepaid_params")
    return render(request, "smart_meter/prepaid_params.html", {
        "form": form,
        "general_form": MeterSettingsForm(instance=settings_row),
    })
