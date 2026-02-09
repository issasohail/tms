# smart_meter/views_prepaid.py
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
import time
import logging
from django.conf import settings
from .models import Meter


logger = logging.getLogger(__name__)


# --- Vendor prepaid frame builder (support both locations) ---
try:
    # preferred package location
    from smart_meter.vendor.prepaid import DLT645_2007_Prepaid
except Exception:
    # fall back to a top-level prepaid.py sitting on PYTHONPATH
    from prepaid import DLT645_2007_Prepaid  # type: ignore

# --- Sender (same listener used elsewhere) ---
try:
    from smart_meter.utils.control_client import send_via_listener
except Exception:
    send_via_listener = None  # render frame even if sender not wired


class PrepaidParamsForm(forms.Form):
    meter = forms.ModelChoiceField(
        queryset=Meter.objects.order_by("meter_number"),
        label="Meter",
        widget=forms.Select(attrs={"class": "form-select"})
    )
    rate1_price_1 = forms.DecimalField(
        label="Rate 1 price (Rs/kWh)", decimal_places=4, max_digits=10, initial=Decimal("0.0000")
    )
    mirror_across_rates = forms.BooleanField(
        label="Mirror price across rate1 slots (1–4)", required=False, initial=True
    )


def _full_param_block(rate1_price_1: Decimal, mirror: bool = True) -> dict:
    """
    Build a complete prepaid parameter dict matching the vendor structure.
    Any fields not entered are given safe-zero defaults.
    Prices are floats with 4 decimals (the vendor packs them as BCD * 10^4).
    """
    p = float(Decimal(rate1_price_1).quantize(Decimal("0.0001")))

    params = {
        # --- 5-byte BCD times (yymmddhhmm). 0 = no scheduled switch ---
        "rate_switch_time": 0,
        "step_switch_time": 0,
        "timezone_switch_time": 0,
        "schedule_switch_time": 0,

        # --- counts (1 byte) ---
        "timezone_count": 0,
        "schedule_count": 0,
        "time_period_count": 0,
        "rate_count": 1,   # enable one rate table
        "step_count": 0,   # no step tariffs

        # --- ratios (3 bytes, direct-connected = 1) ---
        "voltage_ratio": 1,
        "current_ratio": 1,

        # --- money thresholds (4-byte ints, fen/cents) ---
        "alarm_amount_1": 0,
        "alarm_amount_2": 0,
        "overdraft_limit": 0,
        "area_amount_limit": 0,
        "contract_amount_limit": 0,

        # --- load limiting (optional) ---
        "max_load_power_limit": 0,
        "load_power_delay": 0,

        # --- Rate 1 table (4 slots available) ---
        "rate1_price_1": p,
        "rate1_price_2": 0.0000,
        "rate1_price_3": 0.0000,
        "rate1_price_4": 0.0000,

        # --- Rate 2 table (unused) ---
        "rate2_price_1": 0.0000,
        "rate2_price_2": 0.0000,
        "rate2_price_3": 0.0000,
        "rate2_price_4": 0.0000,

        # --- All step bands disabled ---
        "step1_value_1": 0, "step1_value_2": 0, "step1_value_3": 0,
        "step1_price_1": 0.0000, "step1_price_2": 0.0000, "step1_price_3": 0.0000, "step1_price_4": 0.0000,

        "step2_value_1": 0, "step2_value_2": 0, "step2_value_3": 0,
        "step2_price_1": 0.0000, "step2_price_2": 0.0000, "step2_price_3": 0.0000, "step2_price_4": 0.0000,

        "step3_value_1": 0, "step3_value_2": 0, "step3_value_3": 0,
        "step3_price_1": 0.0000, "step3_price_2": 0.0000, "step3_price_3": 0.0000, "step3_price_4": 0.0000,
    }

    if mirror:
        # Some firmwares require all 4 rate1 slots populated.
        params["rate1_price_2"] = p
        params["rate1_price_3"] = p
        params["rate1_price_4"] = p

    return params


# Put these near the top (fallbacks if not in settings.py)
LISTENER_HOST = getattr(settings, "CONTROL_LISTENER_HOST", None)
LISTENER_PORT = getattr(settings, "CONTROL_LISTENER_PORT", None)


@require_http_methods(["GET", "POST"])
def prepaid_params(request):
    form = PrepaidParamsForm(request.POST or None)
    ctx = {
        "form": form,
        "frame_hex": None,
        "params_sent": None,
        "result": None,
        "listener_host": LISTENER_HOST,
        "listener_port": LISTENER_PORT,
    }

    if request.method == "POST" and form.is_valid():
        meter = form.cleaned_data["meter"]
        p = form.cleaned_data["rate1_price_1"]
        mirror = bool(form.cleaned_data.get("mirror_across_rates"))

        # 1) Build
        params = _full_param_block(p, mirror=mirror)
        prepaid = DLT645_2007_Prepaid()
        frame = prepaid.build_frame(meter.meter_number, params)
        frame_hex = frame.hex().upper()

        # Log everything before we try to send
        logger.info("PREPAID SEND build: meter=%s params=%s frame_hex=%s",
                    meter.meter_number, params, frame_hex)

        # 2) Send with retries
        res = {"ok": False, "error": "send_via_listener not available"}
        if send_via_listener is not None:
            attempts = [(1, 5.0), (2, 10.0), (3, 15.0)]
            for attempt, to in attempts:
                t0 = time.time()
                try:
                    logger.info("PREPAID SEND attempt #%d → %s:%s timeout=%.1fs",
                                attempt, LISTENER_HOST, LISTENER_PORT, to)
                    res = send_via_listener(
                        meter.meter_number, frame, timeout=to)
                    dt = (time.time() - t0) * 1000
                    logger.info(
                        "PREPAID SEND attempt #%d result in %.0f ms: %s", attempt, dt, res)
                    if res.get("ok"):
                        break
                except Exception as e:
                    dt = (time.time() - t0) * 1000
                    logger.exception(
                        "PREPAID SEND attempt #%d raised after %.0f ms", attempt, dt)
                    res = {"ok": False, "error": str(e)}
        else:
            logger.error("PREPAID SEND aborted: send_via_listener is None")

        # 3) Messages + context
        if res.get("ok"):
            messages.success(
                request, f"Prepaid parameters sent to {meter.meter_number}.")
        else:
            messages.error(
                request, f"Send failed: {res.get('error', 'no reply')}")

        ctx.update({"frame_hex": frame_hex,
                   "params_sent": params, "result": res})

    return render(request, "smart_meter/prepaid_params.html", ctx)
