"""Prepaid Parameter 1 UI: builds locally and queues one durable write."""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from smart_meter.forms import MeterPrepaidSettingsForm, MeterSettingsForm
from smart_meter.models import Meter, MeterPrepaidSettings, MeterSettings
from smart_meter.services.prepaid_parameters import build_parameter_frame, classify_write_response
from smart_meter.services.prepaid_pilot import prepaid_allowlisted, prepaid_writes_enabled
from smart_meter.utils.db_send import send_via_db

logger = logging.getLogger(__name__)


PREPAID_PARAMETER_GROUPS = (
    ("Alerts, credit and limits (Rs)", (
        "alarm_amount_1", "alarm_amount_2", "overdraft_limit",
        "maximum_balance", "reconnect_amount", "max_load", "load_delay",
    )),
    ("Tariffs (Rs/kWh)", (
        "rate1_price_1", "rate1_price_2", "rate1_price_3", "rate1_price_4",
        "rate2_price_1", "rate2_price_2", "rate2_price_3", "rate2_price_4",
    )),
    ("Tier values and prices", (
        "step_count", "step1_value_1", "step1_value_2", "step1_value_3",
        "step1_price_1", "step1_price_2", "step1_price_3", "step1_price_4",
        "step2_value_1", "step2_value_2", "step2_value_3",
        "step2_price_1", "step2_price_2", "step2_price_3", "step2_price_4",
    )),
    ("Counts, ratios and switch dates", (
        "timezone_count", "schedule_count", "time_period_count", "rate_count",
        "voltage_ratio", "current_ratio", "rate_switch_time", "step_switch_time",
        "timezone_switch_time", "schedule_switch_time",
    )),
)


def _style_general_form(form):
    for field in form.fields.values():
        field.widget.attrs["class"] = "form-control form-control-sm"
    return form


def _parameter_table_rows(submitted_form=None, submitted_meter_id=None):
    meters = list(
        Meter.objects.all()
        .select_related("unit", "unit__property")
        .order_by("unit__property__property_name", "unit__unit_number", "meter_number")
    )
    settings_by_meter = {
        setting.meter_id: setting
        for setting in MeterPrepaidSettings.objects.filter(
            meter_id__in=[meter.pk for meter in meters]
        )
    }
    rows = []
    for meter in meters:
        form_id = f"prepaid-parameter-form-{meter.pk}"
        if submitted_form is not None and str(meter.pk) == str(submitted_meter_id):
            row_form = submitted_form
        else:
            row_form = MeterPrepaidSettingsForm(
                instance=settings_by_meter.get(meter.pk),
                initial={"meter": meter},
            )
        for name, field in row_form.fields.items():
            if name == "meter":
                continue
            css_classes = field.widget.attrs.get("class", "")
            field.widget.attrs.update({
                "class": f"{css_classes} form-control form-control-sm prepaid-inline-input".strip(),
                "form": form_id,
                "readonly": True,
                "title": field.label,
            })
        groups = [
            {
                "label": label,
                "fields": [
                    {"name": name, "label": row_form.fields[name].label, "field": row_form[name]}
                    for name in field_names
                ],
            }
            for label, field_names in PREPAID_PARAMETER_GROUPS
        ]
        rows.append({
            "meter": meter,
            "form": row_form,
            "form_id": form_id,
            "groups": groups,
            "has_settings": meter.pk in settings_by_meter,
            "can_update": meter.is_active and meter.billing_mode == "prepaid_pilot",
            "is_submitted": submitted_form is not None and str(meter.pk) == str(submitted_meter_id),
        })
    return rows


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
    action = request.POST.get("action") if request.method == "POST" else ""
    general_form = _style_general_form(MeterSettingsForm(
        request.POST if action == "save_general" else None,
        instance=settings_row,
    ))
    submitted_form = None
    submitted_meter_id = None

    if action == "save_general":
        if general_form.is_valid():
            general_form.save()
            messages.success(request, "General meter settings updated.")
            return redirect("smart_meter:prepaid_params")

    if action == "save_prepaid_parameters":
        submitted_meter_id = request.POST.get("meter")
        instance = MeterPrepaidSettings.objects.filter(
            meter_id=submitted_meter_id
        ).first() if submitted_meter_id else None
        submitted_form = MeterPrepaidSettingsForm(request.POST, instance=instance)

    if submitted_form is not None and submitted_form.is_valid():
        meter_settings = submitted_form.save(commit=False)
        if not prepaid_writes_enabled():
            submitted_form.add_error(None, "Prepaid writes are disabled in Prepaid Controls.")
        elif not prepaid_allowlisted(meter_settings.meter):
            submitted_form.add_error(None, "This meter is not enabled for prepaid operations.")
        else:
            try:
                built = build_parameter_frame(
                    meter_settings.meter.meter_number,
                    meter_settings.to_vendor_parameters(),
                )
            except (TypeError, ValueError) as exc:
                submitted_form.add_error(None, f"Parameter validation failed: {exc}")
            else:
                meter_settings.save()
                # Parameter 1 is non-idempotent: one listener transmission only.
                result = send_via_db(
                    meter_number=meter_settings.meter.meter_number,
                    frame_hex=built["frame"].hex().upper(), timeout=32.0,
                    expect_di="070104FF", initiated_by=request.user.get_username(),
                    reason="manual prepaid Parameter 1 update", command_type="prepaid_write",
                    source="prepaid", max_attempts=1,
                )
                level, message = _result_message(result)
                getattr(messages, level)(request, message)
                logger.info("Parameter 1 queued meter=%s command=%s status=%s", meter_settings.meter.meter_number, result.get("command_id"), result.get("status"))
                return redirect("smart_meter:prepaid_params")

    parameter_rows = _parameter_table_rows(submitted_form, submitted_meter_id)
    header_form = MeterPrepaidSettingsForm()
    parameter_groups = [
        {
            "label": label,
            "fields": [
                {"name": name, "label": header_form.fields[name].label}
                for name in field_names
            ],
        }
        for label, field_names in PREPAID_PARAMETER_GROUPS
    ]
    return render(request, "smart_meter/prepaid_params.html", {
        "form": submitted_form or MeterPrepaidSettingsForm(),
        "general_form": general_form,
        "parameter_rows": parameter_rows,
        "parameter_groups": parameter_groups,
        "submitted_form": submitted_form,
    })
