"""Prepaid Parameter 1 UI: builds locally and queues one durable write."""
import hashlib
import json
import logging
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from smart_meter.forms import MeterPrepaidSettingsForm, MeterSettingsForm
from smart_meter.dlt645 import build_read_price_param_frame, parse_070104ff_prices
from smart_meter.models import (
    Meter, MeterCommand, MeterPrepaidParameterBatch, MeterPrepaidPilot,
    MeterPrepaidSettings, MeterPrepaidWriteAttempt, MeterSettings,
)
from smart_meter.services.prepaid_parameters import (
    build_parameter_frame, classify_write_response, normalise_config,
)
from smart_meter.services.prepaid_pilot import (
    prepaid_allowlisted, prepaid_reads_enabled, prepaid_writes_enabled,
)
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

PREPAID_PARAMETER_FIELDS = tuple(
    field_name
    for _group_label, field_names in PREPAID_PARAMETER_GROUPS
    for field_name in field_names
)

PREPAID_PARAMETER_HELP = {
    "alarm_amount_1": "First low-balance warning amount in rupees. Use 0.00 to disable this threshold. Allowed protocol range: 0.00 to 999999.99.",
    "alarm_amount_2": "Second low-balance warning amount in rupees. Use 0.00 to disable this threshold. Allowed protocol range: 0.00 to 999999.99.",
    "overdraft_limit": "Credit/overdraft amount the meter is allowed to use, in rupees. Allowed protocol range: 0.00 to 999999.99.",
    "maximum_balance": "Maximum stored prepaid balance, in rupees. Allowed protocol range: 0.00 to 999999.99.",
    "reconnect_amount": "Balance/reconnect threshold used by the meter after credit control, in rupees. Allowed protocol range: 0.00 to 999999.99.",
    "max_load": "Maximum permitted load in kW. 0.0000 means no configured limit where supported. Allowed protocol range: 0.0000 to 99.9999.",
    "load_delay": "Meter load-control delay value. Enter a whole number from 0 to 255; use 0 when no delay is required.",
    "rate1_price_1": "Rate set 1, price slot 1 in Rs/kWh. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "rate1_price_2": "Rate set 1, price slot 2 in Rs/kWh. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "rate1_price_3": "Rate set 1, price slot 3 in Rs/kWh. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "rate1_price_4": "Rate set 1, price slot 4 in Rs/kWh. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "rate2_price_1": "Rate set 2, price slot 1 in Rs/kWh. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "rate2_price_2": "Rate set 2, price slot 2 in Rs/kWh. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "rate2_price_3": "Rate set 2, price slot 3 in Rs/kWh. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "rate2_price_4": "Rate set 2, price slot 4 in Rs/kWh. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "step_count": "Number of active stepped/tier levels. Enter 0 to 3.",
    "step1_value_1": "Rate-set 1, first tier breakpoint/energy value. Use 0 when stepped pricing is not used.",
    "step1_value_2": "Rate-set 1, second tier breakpoint/energy value. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "step1_value_3": "Rate-set 1, third tier breakpoint/energy value. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "step1_price_1": "Rate-set 1, tier price 1 in Rs/kWh. Up to 4 decimal places.",
    "step1_price_2": "Rate-set 1, tier price 2 in Rs/kWh. Up to 4 decimal places.",
    "step1_price_3": "Rate-set 1, tier price 3 in Rs/kWh. Up to 4 decimal places.",
    "step1_price_4": "Rate-set 1, tier price 4 in Rs/kWh. Up to 4 decimal places.",
    "step2_value_1": "Rate-set 2, first tier breakpoint/energy value. Use 0 when stepped pricing is not used.",
    "step2_value_2": "Rate-set 2, second tier breakpoint/energy value. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "step2_value_3": "Rate-set 2, third tier breakpoint/energy value. Up to 4 decimal places; protocol range 0.0000 to 9999.9999.",
    "step2_price_1": "Rate-set 2, tier price 1 in Rs/kWh. Up to 4 decimal places.",
    "step2_price_2": "Rate-set 2, tier price 2 in Rs/kWh. Up to 4 decimal places.",
    "step2_price_3": "Rate-set 2, tier price 3 in Rs/kWh. Up to 4 decimal places.",
    "step2_price_4": "Rate-set 2, tier price 4 in Rs/kWh. Up to 4 decimal places.",
    "timezone_count": "Number of configured time zones/areas. Enter a whole number from 0 to 2.",
    "schedule_count": "Number of configured time tables/schedules. Enter a whole number from 0 to 2.",
    "time_period_count": "Number of configured time periods. Enter a whole number from 0 to 8.",
    "rate_count": "Number of configured tariff prices/rates. Enter a whole number from 0 to 4.",
    "voltage_ratio": "PT/voltage transformer ratio used by the meter. Enter a whole number from 0 to 999999.",
    "current_ratio": "CT/current transformer ratio used by the meter. Enter a whole number from 0 to 999999.",
    "rate_switch_time": "When the meter should switch to the next rate set. Enter 0 for no scheduled switch, or YYMMDDHHMM (10 digits), e.g. 2610010000.",
    "step_switch_time": "When the meter should switch to the next stepped-price set. Enter 0 or YYMMDDHHMM (10 digits).",
    "timezone_switch_time": "When the meter should switch its time-zone/area configuration. Enter 0 or YYMMDDHHMM (10 digits).",
    "schedule_switch_time": "When the meter should switch its time-table/schedule configuration. Enter 0 or YYMMDDHHMM (10 digits).",
}


def _style_general_form(form):
    for field in form.fields.values():
        field.widget.attrs["class"] = "form-control form-control-sm"
    return form


MODEL_TO_VENDOR = {
    "rate_switch_time": "priceChgDate", "step_switch_time": "stepChgDate",
    "timezone_switch_time": "timeAreaChgDate", "schedule_switch_time": "timeSecChgDate",
    "timezone_count": "qtyarea", "schedule_count": "qtytimertable",
    "time_period_count": "qtytimer", "rate_count": "qtyprice", "step_count": "qtystep",
    "voltage_ratio": "pt", "current_ratio": "ct",
    "alarm_amount_1": "warnlowbala1", "alarm_amount_2": "warnlowbala2",
    "overdraft_limit": "creditVal", "maximum_balance": "balancemax",
    "reconnect_amount": "remainPowerOn", "max_load": "kwMax", "load_delay": "sleepKw",
    **{f"rate1_price_{i}": f"set1Price{i}" for i in range(1, 5)},
    **{f"rate2_price_{i}": f"set2Price{i}" for i in range(1, 5)},
    **{f"step1_value_{i}": f"set1Step{i}" for i in range(1, 4)},
    **{f"step2_value_{i}": f"set2Step{i}" for i in range(1, 4)},
    **{f"step1_price_{i}": f"set1StepPrice{i}" for i in range(1, 5)},
    **{f"step2_price_{i}": f"set2StepPrice{i}" for i in range(1, 5)},
}


def _jsonable_config(config):
    normalized = normalise_config(config)
    return {key: str(value) for key, value in sorted(normalized.items())}


def _config_hash(config):
    payload = json.dumps(_jsonable_config(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reply_bytes(reply):
    if isinstance(reply, (bytes, bytearray)):
        return bytes(reply)
    return bytes.fromhex(str(reply or "").replace(" ", ""))


def _cancel_deferred(command_id, reason):
    if command_id:
        MeterCommand.objects.filter(
            pk=command_id, status__in=("pending", "waiting_online", "retry")
        ).update(status="cancelled", cancelled_at=timezone.now(), cancelled_reason=reason)


def _read_parameter_1(meter, user):
    if not prepaid_reads_enabled():
        raise ValueError("Prepaid reads are disabled in Prepaid Controls.")
    frame = build_read_price_param_frame(meter.meter_number)
    result = send_via_db(
        meter_number=meter.meter_number,
        frame_hex=frame.hex().upper(),
        timeout=12.0,
        expect_di="070104FF",
        initiated_by=user.get_username(),
        reason="Prepaid Parameter 1 live verification read",
        command_type="prepaid_read",
        source="prepaid",
        max_attempts=1,
    )
    command_id = result.get("command_id")
    if result.get("status") in {"waiting_online", "pending"} or not result.get("reply"):
        _cancel_deferred(command_id, "Parameter 1 verification requires a fresh foreground response")
        raise ValueError(result.get("error") or "Meter is offline or no live Parameter 1 reply was received.")
    if not result.get("ok"):
        raise ValueError(result.get("error") or "Parameter 1 read failed.")
    raw = _reply_bytes(result.get("reply"))
    decoded = parse_070104ff_prices(raw)
    return decoded, raw.hex().upper(), command_id


def _apply_decoded(settings_obj, decoded):
    for model_field, vendor_field in MODEL_TO_VENDOR.items():
        if vendor_field in decoded:
            setattr(settings_obj, model_field, decoded[vendor_field])


def _verify_and_sync_meter(meter, user):
    decoded, raw_hex, command_id = _read_parameter_1(meter, user)
    settings_obj, _ = MeterPrepaidSettings.objects.get_or_create(meter=meter)
    _apply_decoded(settings_obj, decoded)
    settings_obj.baseline_status = "verified"
    settings_obj.baseline_verified_at = timezone.now()
    settings_obj.baseline_raw_read_hex = raw_hex
    settings_obj.baseline_command_id = command_id
    settings_obj.baseline_hash = _config_hash(settings_obj.to_vendor_parameters())
    settings_obj.save()
    return settings_obj


def _parameter_table_rows(submitted_forms=None):
    submitted_forms = submitted_forms or {}
    meters = list(
        Meter.objects.all().select_related("unit", "unit__property")
        .order_by("unit__property__property_name", "unit__unit_number", "meter_number")
    )
    settings_by_meter = {
        setting.meter_id: setting
        for setting in MeterPrepaidSettings.objects.filter(meter_id__in=[meter.pk for meter in meters])
    }
    rows = []
    for meter in meters:
        form_id = f"prepaid-parameter-form-{meter.pk}"
        row_form = submitted_forms.get(str(meter.pk)) or submitted_forms.get(meter.pk)
        if row_form is None:
            row_form = MeterPrepaidSettingsForm(instance=settings_by_meter.get(meter.pk), initial={"meter": meter})
        can_update = meter.is_active and meter.billing_mode == "prepaid_pilot"
        settings_obj = settings_by_meter.get(meter.pk)
        for name, field in row_form.fields.items():
            if name == "meter":
                continue
            css_classes = field.widget.attrs.get("class", "")
            field.widget.attrs.update({
                "class": f"{css_classes} form-control form-control-sm prepaid-inline-input".strip(),
                "form": form_id, "title": field.label, "data-prepaid-field": name, "autocomplete": "off",
            })
            field.widget.attrs.pop("readonly", None)
            if not can_update:
                field.widget.attrs["readonly"] = True
                field.widget.attrs["tabindex"] = "-1"
        groups = [{
            "label": label,
            "fields": [{"name": name, "label": row_form.fields[name].label, "field": row_form[name]} for name in field_names],
        } for label, field_names in PREPAID_PARAMETER_GROUPS]
        rows.append({
            "meter": meter, "form": row_form, "form_id": form_id, "groups": groups,
            "has_settings": settings_obj is not None, "can_update": can_update,
            "baseline_status": getattr(settings_obj, "baseline_status", "local_only") if settings_obj else "local_only",
            "baseline_verified_at": getattr(settings_obj, "baseline_verified_at", None) if settings_obj else None,
            "is_submitted": str(meter.pk) in {str(key) for key in submitted_forms},
        })
    return rows


def _result_message(result):
    reply = result.get("reply", "")
    if reply:
        try:
            response = classify_write_response(_reply_bytes(reply))
        except ValueError:
            response = {"state": "ambiguous", "error": "unparseable meter response"}
        if response["state"] == "accepted":
            return "success", "Meter accepted Parameter 1 (0x83)."
        if response["state"] == "rejected":
            detail = "" if response.get("error_byte") is None else f" Error byte: {response['error_byte']:02X}."
            return "error", "Meter rejected Parameter 1 (0xC3)." + detail
        return "warning", "Parameter write may have reached the meter, but its response is ambiguous. Do not retry automatically."
    if result.get("queued"):
        return "warning", "Meter is offline; the write was not allowed to proceed as a verified foreground change."
    return "warning", "Parameter write has no confirmed 0x83 response. Treat it as ambiguous; do not retry automatically."


def _ensure_fresh_baseline(persisted, user):
    if not persisted or persisted.baseline_status != "verified" or not persisted.baseline_hash:
        raise ValueError("Read / Verify this meter first. Parameter 1 writes require a verified physical baseline.")
    decoded, raw_hex, command_id = _read_parameter_1(persisted.meter, user)
    live_holder = MeterPrepaidSettings(meter=persisted.meter)
    _apply_decoded(live_holder, decoded)
    live_hash = _config_hash(live_holder.to_vendor_parameters())
    if live_hash != persisted.baseline_hash:
        persisted.baseline_status = "mismatch"
        persisted.baseline_verified_at = timezone.now()
        persisted.baseline_raw_read_hex = raw_hex
        persisted.baseline_command_id = command_id
        persisted.save(update_fields=["baseline_status", "baseline_verified_at", "baseline_raw_read_hex", "baseline_command_id", "updated_at"])
        raise ValueError("Physical Parameter 1 no longer matches the verified database baseline. Read / Verify again before writing.")
    return live_hash


def _send_parameter_write(desired, user, *, batch=None):
    persisted = MeterPrepaidSettings.objects.select_related("meter").filter(meter=desired.meter).first()
    before_hash = _ensure_fresh_baseline(persisted, user)
    desired_hash = _config_hash(desired.to_vendor_parameters())
    pilot, _ = MeterPrepaidPilot.objects.get_or_create(meter=desired.meter)
    attempt = MeterPrepaidWriteAttempt.objects.create(
        pilot=pilot, batch=batch, parameter="070104FF Parameter 1",
        requested_value=f"sha256:{desired_hash}",
        original_value=f"sha256:{before_hash}",
        before_hash=before_hash, desired_hash=desired_hash, status="pending",
        reason="manual prepaid Parameter 1 update", user=user,
        read_before_hex=persisted.baseline_raw_read_hex,
    )
    built = build_parameter_frame(desired.meter.meter_number, desired.to_vendor_parameters())
    result = send_via_db(
        meter_number=desired.meter.meter_number, frame_hex=built["frame"].hex().upper(), timeout=32.0,
        expect_di="070104FF", initiated_by=user.get_username(), reason="manual prepaid Parameter 1 update",
        command_type="prepaid_write", source="prepaid", max_attempts=1,
    )
    attempt.command_hex = built["frame"].hex().upper()
    attempt.command_id = result.get("command_id")
    attempt.ack_hex = str(result.get("reply") or "")
    level, message = _result_message(result)
    attempt.result_state = level
    if level != "success":
        attempt.status = "failed" if level == "error" else "uncertain"
        attempt.save()
        persisted.baseline_status = "stale" if level == "warning" else persisted.baseline_status
        persisted.save(update_fields=["baseline_status", "updated_at"])
        return level, message, attempt

    # A 0x83 acknowledgement is not enough: read the complete Parameter 1 back.
    try:
        decoded, raw_hex, command_id = _read_parameter_1(desired.meter, user)
        readback = MeterPrepaidSettings(meter=desired.meter)
        _apply_decoded(readback, decoded)
        actual_hash = _config_hash(readback.to_vendor_parameters())
    except Exception as exc:
        attempt.status = "uncertain"
        attempt.result_state = "accepted_readback_failed"
        attempt.actual_value = str(exc)[:128]
        attempt.save()
        persisted.baseline_status = "stale"
        persisted.save(update_fields=["baseline_status", "updated_at"])
        return "warning", "Meter accepted the write, but read-back verification failed. Do not retry; review this meter.", attempt
    if actual_hash != desired_hash:
        attempt.status = "uncertain"
        attempt.result_state = "readback_mismatch"
        attempt.read_back_hex = raw_hex
        attempt.actual_value = actual_hash
        attempt.save()
        persisted.baseline_status = "mismatch"
        persisted.baseline_raw_read_hex = raw_hex
        persisted.baseline_command_id = command_id
        persisted.save(update_fields=["baseline_status", "baseline_raw_read_hex", "baseline_command_id", "updated_at"])
        return "warning", "Meter accepted the write, but the read-back differs from the requested configuration. Do not retry automatically.", attempt

    # Only a verified read-back is allowed to become the new local baseline.
    desired.pk = persisted.pk
    desired.baseline_status = "verified"
    desired.baseline_hash = desired_hash
    desired.baseline_verified_at = timezone.now()
    desired.baseline_raw_read_hex = raw_hex
    desired.baseline_command_id = command_id
    desired.last_write_verified_at = desired.baseline_verified_at
    desired.save()
    attempt.status = "verified"
    attempt.result_state = "verified"
    attempt.read_back_hex = raw_hex
    attempt.actual_value = desired_hash
    attempt.verified_at = timezone.now()
    attempt.save()
    logger.info("Parameter 1 verified meter=%s command=%s", desired.meter.meter_number, result.get("command_id"))
    return "success", "Meter accepted Parameter 1 and the complete read-back matched.", attempt


def _build_save_all_forms(post_data):
    forms, seen = {}, set()
    for meter_id in post_data.getlist("meter_ids"):
        meter_id = str(meter_id).strip()
        if not meter_id or meter_id in seen:
            continue
        seen.add(meter_id)
        instance = MeterPrepaidSettings.objects.filter(meter_id=meter_id).first()
        data = {"meter": meter_id}
        for field_name in PREPAID_PARAMETER_FIELDS:
            data[field_name] = post_data.get(f"meter_{meter_id}__{field_name}", "")
        forms[meter_id] = MeterPrepaidSettingsForm(data, instance=instance)
    return forms


def _preview_summary(forms):
    summary = {}
    for meter_id, form in forms.items():
        desired = form.save(commit=False)
        current = MeterPrepaidSettings.objects.filter(meter_id=meter_id).first()
        for field in PREPAID_PARAMETER_FIELDS:
            before = getattr(current, field, None) if current else None
            after = getattr(desired, field, None)
            if str(before) != str(after):
                item = summary.setdefault(field, {"label": form.fields[field].label, "meters": 0, "values": set()})
                item["meters"] += 1
                item["values"].add(str(after))
    return [{"field": key, "label": value["label"], "meters": value["meters"], "values": sorted(value["values"])} for key, value in summary.items()]


def _save_preview_session(request, forms):
    token = uuid.uuid4().hex
    payload = {"meter_ids": list(forms)}
    for meter_id, form in forms.items():
        payload[meter_id] = {field: form.data.get(field, "") for field in PREPAID_PARAMETER_FIELDS}
    previews = request.session.setdefault("prepaid_parameter_previews", {})
    previews[token] = payload
    # Keep only a few recent previews per browser session.
    while len(previews) > 5:
        previews.pop(next(iter(previews)))
    request.session.modified = True
    return token


def _forms_from_preview(request, token):
    payload = (request.session.get("prepaid_parameter_previews") or {}).pop(token, None)
    request.session.modified = True
    if not payload:
        return {}
    forms = {}
    for meter_id in payload.get("meter_ids", []):
        values = payload.get(str(meter_id), payload.get(meter_id, {}))
        data = {"meter": meter_id, **values}
        forms[str(meter_id)] = MeterPrepaidSettingsForm(data, instance=MeterPrepaidSettings.objects.filter(meter_id=meter_id).first())
    return forms


@require_http_methods(["GET", "POST"])
@login_required
@permission_required("smart_meter.write_prepaid_parameters", raise_exception=True)
def prepaid_params(request):
    settings_row, _ = MeterSettings.objects.get_or_create(pk=1)
    action = request.POST.get("action") if request.method == "POST" else ""
    general_form = _style_general_form(MeterSettingsForm(request.POST if action == "save_general" else None, instance=settings_row))
    submitted_forms = {}
    save_all_preview = None

    if action == "save_general" and general_form.is_valid():
        general_form.save()
        messages.success(request, "General meter settings updated.")
        return redirect("smart_meter:prepaid_params")

    if action == "verify_prepaid_parameters":
        meter = Meter.objects.filter(pk=request.POST.get("meter")).first()
        if not meter or not prepaid_allowlisted(meter):
            messages.error(request, "This meter is not enabled for prepaid operations.")
        else:
            try:
                _verify_and_sync_meter(meter, request.user)
            except Exception as exc:
                messages.error(request, f"Read / Verify failed for {meter.meter_number}: {exc}")
            else:
                messages.success(request, f"Parameter 1 read from {meter.meter_number}; local values now match the physical meter baseline.")
        return redirect("smart_meter:prepaid_params")

    if action == "save_prepaid_parameters":
        meter_id = request.POST.get("meter")
        instance = MeterPrepaidSettings.objects.filter(meter_id=meter_id).first() if meter_id else None
        if meter_id:
            submitted_forms[str(meter_id)] = MeterPrepaidSettingsForm(request.POST, instance=instance)
    elif action in {"preview_save_all_prepaid_parameters", "save_all_prepaid_parameters"}:
        submitted_forms = _build_save_all_forms(request.POST)
    elif action == "confirm_save_all_prepaid_parameters":
        submitted_forms = _forms_from_preview(request, request.POST.get("preview_token", ""))

    if submitted_forms:
        all_valid = all(form.is_valid() for form in submitted_forms.values())
        if all_valid and not prepaid_writes_enabled():
            for form in submitted_forms.values():
                form.add_error(None, "Prepaid writes are disabled in Prepaid Controls.")
            all_valid = False
        prepared = []
        if all_valid:
            for meter_id, form in submitted_forms.items():
                desired = form.save(commit=False)
                if not prepaid_allowlisted(desired.meter):
                    form.add_error(None, "This meter is not enabled for prepaid operations.")
                    all_valid = False
                    continue
                persisted = MeterPrepaidSettings.objects.filter(meter=desired.meter).first()
                if not persisted or persisted.baseline_status != "verified":
                    form.add_error(None, "Read / Verify this meter first; its physical Parameter 1 baseline is not verified.")
                    all_valid = False
                    continue
                try:
                    build_parameter_frame(desired.meter.meter_number, desired.to_vendor_parameters())
                except (TypeError, ValueError) as exc:
                    form.add_error(None, f"Parameter validation failed: {exc}")
                    all_valid = False
                else:
                    prepared.append((meter_id, desired))

        if all_valid and action in {"preview_save_all_prepaid_parameters", "save_all_prepaid_parameters"}:
            token = _save_preview_session(request, submitted_forms)
            save_all_preview = {"token": token, "target_count": len(prepared), "changes": _preview_summary(submitted_forms)}
        elif all_valid and action == "save_prepaid_parameters":
            _meter_id, desired = prepared[0]
            try:
                level, message, _attempt = _send_parameter_write(desired, request.user)
            except Exception as exc:
                messages.error(request, str(exc))
            else:
                getattr(messages, level)(request, message)
            return redirect("smart_meter:prepaid_params")
        elif all_valid and action == "confirm_save_all_prepaid_parameters":
            batch = MeterPrepaidParameterBatch.objects.create(
                status="processing", target_count=len(prepared), created_by=request.user,
                preview_changes={item["field"]: {"label": item["label"], "meters": item["meters"], "values": item["values"]} for item in _preview_summary(submitted_forms)},
            )
            counts = {"success": 0, "warning": 0, "error": 0}
            details = []
            for _meter_id, desired in prepared:
                try:
                    level, message, _attempt = _send_parameter_write(desired, request.user, batch=batch)
                except Exception as exc:
                    level, message = "error", str(exc)
                counts[level] += 1
                if level != "success":
                    details.append(f"{desired.meter.meter_number}: {message}")
            batch.accepted_count = counts["success"]
            batch.warning_count = counts["warning"]
            batch.rejected_count = counts["error"]
            batch.status = "completed" if counts["warning"] == counts["error"] == 0 else "partial" if counts["success"] else "failed"
            batch.completed_at = timezone.now()
            batch.save(update_fields=["accepted_count", "warning_count", "rejected_count", "status", "completed_at"])
            summary = f"Batch {batch.batch_key}: {counts['success']} verified, {counts['warning']} uncertain, {counts['error']} rejected/blocked."
            getattr(messages, "success" if batch.status == "completed" else "warning")(request, summary)
            for detail in details[:10]:
                messages.warning(request, detail)
            return redirect("smart_meter:prepaid_params")

    parameter_rows = _parameter_table_rows(submitted_forms)
    header_form = MeterPrepaidSettingsForm()
    parameter_groups = [{
        "label": label,
        "fields": [{
            "name": name, "label": header_form.fields[name].label,
            "help": PREPAID_PARAMETER_HELP.get(name, header_form.fields[name].help_text or "Enter the value required by the meter for this Parameter 1 field."),
        } for name in field_names],
    } for label, field_names in PREPAID_PARAMETER_GROUPS]
    return render(request, "smart_meter/prepaid_params.html", {
        "form": next(iter(submitted_forms.values()), MeterPrepaidSettingsForm()),
        "general_form": general_form, "parameter_rows": parameter_rows,
        "parameter_groups": parameter_groups, "submitted_form": next(iter(submitted_forms.values()), None),
        "submitted_forms": submitted_forms, "save_all_preview": save_all_preview,
    })
