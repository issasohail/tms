# same helper you use for Cut/Restore

import inspect
from django.conf import settings as dj_settings  # at top of file
import logging
from smart_meter.dlt645_money import build_amount_init_frame
from datetime import datetime, date, time, timedelta
from django.db.models import Q  # keep if you still use search elsewhere
import calendar
from datetime import date, datetime, timedelta
from datetime import datetime
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from .forms import ReadingManualForm
from urllib.parse import urlencode
from django.shortcuts import redirect, render
from .models import Meter, LiveReading
from django.conf import settings
from django.http import JsonResponse, Http404
from django.db.models.functions import Lower, Cast
from django.db.models import F, OuterRef, Subquery, DecimalField, DateTimeField
from smart_meter.forms import MeterPrepaidSettingsForm
from smart_meter.models import MeterPrepaidSettings, Meter
from .forms import SwitchLabForm
from smart_meter.utils.commands import refresh_live
from smart_meter.models import Meter, LiveReading
from smart_meter.utils.commands import send_cutoff_command, send_restore_command
from django.shortcuts import redirect
from smart_meter.vendor.prepaid import DLT645_2007_Prepaid
from smart_meter.vendor.switch_OnOff import frame_command as build_switch_frame

from smart_meter.models import Meter
from django.shortcuts import render, redirect, get_object_or_404
from django import forms
from smart_meter.utils import send_cutoff_command, send_restore_command

from collections import OrderedDict
from smart_meter.models import Meter, MeterReading
from collections import defaultdict, OrderedDict
from datetime import date, datetime, time, timedelta
from smart_meter.models import Meter, MeterReading, LiveReading, MeterBalance
from django.db.models import Min, Max, Q
from collections import defaultdict
from smart_meter.models import Meter, MeterReading, MeterBalance, LiveReading
from smart_meter.models import Meter, MeterBalance, LiveReading  # 👈 import LiveReading
from smart_meter.models import Meter, MeterReading, MeterBalance
from django.db.models import Q, F, Value, BooleanField, Case, When, OuterRef, Subquery
from smart_meter.models import Meter, MeterReading       # or LiveReading
from properties.models import Property, Unit             # adjust if different
from openpyxl.utils import get_column_letter
from openpyxl import Workbook
import csv
from io import BytesIO
from django.core.paginator import Paginator
from properties.models import Property, Unit        # adjust import paths
from smart_meter.models import Meter, MeterReading  # or LiveReading
from properties.models import Property, Unit   # adjust paths
from django.db.models import Q
from .models import MeterReading, Meter
from properties.models import Property, Unit
from django.db.models import OuterRef, Subquery, F
from django.db.models import Q, OuterRef, Subquery
from .models import Meter, MeterReading
from django.db.models import OuterRef, Subquery
from .models import Unit, Meter, MeterReading, MeterBalance, Lease
from django.utils import timezone
from .models import Unit, Meter, MeterReading
from django.db.models import Min, Max, Sum
from smart_meter.utils.messaging import build_whatsapp_url
from leases.models import Lease  # adjust if lease is in another app
from smart_meter.models import MeterReading, MeterBalance, MeterEvent
from .forms import RechargeForm
from smart_meter.models import MeterBalance
from django.http import HttpResponseRedirect
from decimal import Decimal
from datetime import date
from django.urls import reverse
from django.shortcuts import render, get_object_or_404
from django.utils.timezone import now
from datetime import datetime, timedelta
from smart_meter.models import MeterReading, Bill
from django.shortcuts import render, redirect
from .forms import AssignMeterForm
from properties.models import Unit
from smart_meter.models import MeterBalance, MeterEvent
from smart_meter.meter_client import send_restore_command  # ✅ we'll add this below
# make sure this is imported
from smart_meter.models import MeterReading, Bill, MeterSettings
from decimal import Decimal
from smart_meter.models import MeterReading, MeterBalance
from properties.models import Unit
from django.utils.timezone import now
from datetime import timedelta
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
# You will write these
from smart_meter.utils import send_cutoff_command, send_restore_command
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
# You will write these
from smart_meter.utils import send_cutoff_command, send_restore_command
from properties.models import Property
from smart_meter.models import MeterBalance
from django.contrib import messages
from decimal import Decimal
from django.views.decorators.csrf import csrf_exempt
from smart_meter.models import Meter, MeterEvent
from smart_meter.utils import send_cutoff_command, send_restore_command
from django.shortcuts import render
from .models import Meter
from django.views.decorators.http import require_POST
from .protocol import build_power_frame
import socket
from django.shortcuts import render, redirect
from .models import Meter
from .forms import MeterForm
from django.shortcuts import get_object_or_404
from django.shortcuts import get_object_or_404, redirect
from .models import MeterReading
from .forms import MeterReadingForm
from .forms import MeterSettingsForm
from django.shortcuts import render, get_object_or_404, redirect
from .models import Meter
from .tasks import poll_all_meters
from django.http import JsonResponse
from datetime import date
from smart_meter.services.billing import generate_bill_for_unit
from properties.models import Unit
from django.db.models.functions import TruncDate
from django.db.models import Min, Max, F, DecimalField, ExpressionWrapper
from smart_meter.models import MeterReading
from django.db.models.functions import TruncMonth
# smart_meter/views.py
from datetime import date, timedelta
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.db.models.functions import TruncDate, TruncMonth
from django.db.models import Min, Max
from properties.models import Unit
from .models import LiveReading, MeterReading, Meter
from .services.billing import generate_bill_for_unit
from .models import Bill
# smart_meter/views.py
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.contrib import messages
# smart_meter/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction
from .models import UnknownMeter, Meter
from .forms import UnknownToMeterForm
from .models import Meter, LiveReading, MeterReading, MeterBalance, MeterEvent
from django.contrib import messages
from django.db import transaction, IntegrityError
from django.shortcuts import render, get_object_or_404, redirect
from .models import UnknownMeter, Meter
from django.contrib.auth.decorators import login_required, permission_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.core.paginator import Paginator
from smart_meter.models import Meter, LiveReading
from smart_meter.vendor.switch_OnOff import frame_command as build_switch_frame
from smart_meter.utils.vpn import vpn_connected, public_ip
# vendor helper
# vendor frame builder
from smart_meter.vendor.switch_OnOff import frame_command as build_switch_frame
from django.db.models import F
from django.db.models.functions import Lower
from django.conf import settings

from datetime import datetime, timedelta

from smart_meter.dlt645_money import build_amount_init_frame
from smart_meter.utils.db_send import send_via_db as _db_send  # fallback sender
from django.conf import settings

DISABLE_CUTOFFS = getattr(settings, "DISABLE_CUTOFFS", False)

# optional sender (if you have the listener); we fall back gracefully if missing
try:
    # Prefer the control client if it exists and imports cleanly
    from smart_meter.utils.control_client import send_via_db as _control_send
except Exception:
    _control_send = None


send_via_db = _control_send or _db_send
logger = logging.getLogger("meter_control")

if not callable(send_via_db):
    # You can log and set a no-op that returns a clear error for callers
    import logging
    logger = logging.getLogger(__name__)
    logger.error("send_via_db is not callable; control sender unavailable")

    def send_via_db(*args, **kwargs):
        return {"ok": False, "error": "control sender unavailable", "payload": None}
    # --- END robust sender import ---

try:
    logger.info("Active send_via_db line 201: %s.%s",
                getattr(send_via_db, "__module__", "?"),
                getattr(send_via_db, "__name__", "?"))
except Exception:
    pass


# Detect which parameter the active sender supports
try:
    _SEND_SIG = inspect.signature(send_via_db)
    _SUPPORTS_FRAME = "frame" in _SEND_SIG.parameters
    _SUPPORTS_FRAME_HEX = "frame_hex" in _SEND_SIG.parameters
except Exception:
    _SUPPORTS_FRAME = False
    _SUPPORTS_FRAME_HEX = True  # fall back to hex path


def _as_hex(frame):
    try:
        return frame.hex().upper()
    except AttributeError:
        return str(frame).upper()


# universal caller that adapts to the available parameters
_SIG = None
try:
    _SIG = inspect.signature(send_via_db)
except Exception:
    _SIG = None

# Inspect active sender once
try:
    _SEND_SIG = inspect.signature(send_via_db)
    _PARAMS = set(_SEND_SIG.parameters.keys())
except Exception:
    _SEND_SIG = None
    _PARAMS = set()


def _call_send(*, meter_number, frame=None, frame_hex=None, **kwargs):
    """
    Universal sender:
    - Accepts either `frame` (bytes) or `frame_hex` (str).
    - Maps to the active send_via_db signature.
    - Drops unknown kwargs (like allow_switch) safely.
    """
    # Decide which payload param to use
    if "frame" in _PARAMS and frame is not None:
        kwargs["frame"] = frame
    else:
        # We must use hex
        if frame_hex is None:
            if frame is None:
                raise ValueError("Provide either frame or frame_hex")
            frame_hex = _as_hex(frame)
        kwargs["frame_hex"] = frame_hex

    # Keep only kwargs the sender supports
    if _PARAMS:
        kwargs = {k: v for k, v in kwargs.items(
        ) if k in _PARAMS or k == "meter_number"}

    return send_via_db(meter_number=meter_number, **kwargs)


def _send_switch(meter_number, frame, **kwargs):
    """Call send_via_db regardless of whether it wants bytes or hex."""
    if _SUPPORTS_FRAME:
        return send_via_db(meter_number=meter_number, frame=frame, **kwargs)
    # default to hex
    hex_str = frame.hex().upper() if hasattr(frame, "hex") else str(frame)
    # some backends may not accept extra flags; drop ones they likely don't know about
    kwargs = {k: v for k, v in kwargs.items() if k in _SEND_SIG.parameters}
    return send_via_db(meter_number=meter_number, frame_hex=hex_str, **kwargs)


# If you have these helpers; otherwise we’ll just log the event
try:
    from smart_meter.meter_client import send_cutoff_command, send_restore_command
except Exception:
    send_cutoff_command = None
    send_restore_command = None


def assign_meter(request):
    if request.method == "POST":
        form = AssignMeterForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("assign_meter")
    else:
        form = AssignMeterForm()

    return render(request, "smart_meter/assign_meter.html", {"form": form})


# views.py (replace daily_report)


def daily_report(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id)
    meter = getattr(unit, "meter", None)
    if not meter:
        return render(request, "smart_meter/daily.html", {
            "unit": unit, "rows": [], "chart_labels": [], "chart_data": [],
            "start": None, "end": None
        })

    # date range (default last 7 days)
    try:
        start_str = request.GET.get("start")
        end_str = request.GET.get("end")
        if start_str and end_str:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
        else:
            end = timezone.localdate()
            start = end - timedelta(days=6)
    except Exception:
        end = timezone.localdate()
        start = end - timedelta(days=6)

    # Build aware datetimes for the range [start, end+1day)
    tz = timezone.get_current_timezone()
    sdt = timezone.make_aware(datetime.combine(start, time.min), tz)
    edt = timezone.make_aware(datetime.combine(
        end + timedelta(days=1), time.min), tz)

    # Pull readings sorted by ts
    qs = (MeterReading.objects
          .filter(meter=meter, ts__gte=sdt, ts__lt=edt)
          .order_by("ts")
          .values("ts", "total_energy"))

    # Group by LOCAL date and keep min/max per day
    by_day = OrderedDict()  # {date: {"min": Decimal, "max": Decimal}}
    for r in qs:
        ts_local = timezone.localtime(r["ts"], tz)
        d = ts_local.date()
        val = Decimal(str(r["total_energy"] or "0"))
        if d not in by_day:
            by_day[d] = {"min": val, "max": val}
        else:
            by_day[d]["max"] = val

    rows = []
    chart_labels = []
    chart_data = []

    if by_day:
        # Stitch continuity: start of first day = that day's min; subsequent starts = previous day's end
        days = list(by_day.keys())
        first_day = days[0]
        prev_end = by_day[first_day]["min"] or Decimal("0")

        for d in days:
            end_kwh = by_day[d]["max"] if by_day[d]["max"] is not None else prev_end
            start_kwh = prev_end
            usage = end_kwh - start_kwh
            if usage < 0:
                usage = Decimal("0")

            rows.append({
                "date": d,
                "start_kwh": start_kwh,
                "end_kwh": end_kwh,
                "units": usage,
            })

            chart_labels.append(d.strftime("%b %d, %Y"))
            chart_data.append(float(usage))
            prev_end = end_kwh

    return render(request, "smart_meter/daily.html", {
        "unit": unit,
        "rows": rows,
        "chart_labels": chart_labels,
        "chart_data": chart_data,
        "start": start,
        "end": end,
    })


def monthly_report(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id)
    meter = getattr(unit, "meter", None)
    if not meter:
        return render(request, "smart_meter/monthly.html", {"unit": unit, "rows": []})

    qs = (MeterReading.objects.filter(meter=meter)
          .annotate(month=TruncMonth('ts'))
          .values('month')
          .annotate(start_kwh=Min('total_energy'), end_kwh=Max('total_energy'))
          .order_by('month'))

    rows = []
    for r in qs:
        if r['start_kwh'] is None or r['end_kwh'] is None:
            continue
        used = Decimal(r['end_kwh']) - Decimal(r['start_kwh'])
        rows.append({
            "month": r['month'],
            "start_kwh": r['start_kwh'],
            "end_kwh": r['end_kwh'],
            "units": max(used, Decimal('0.000')),
        })
    return render(request, "smart_meter/monthly.html", {"unit": unit, "rows": rows})


def generate_bill_view(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id)
    if request.method == "POST":
        from datetime import date
        month = request.POST.get("month")  # YYYY-MM
        y, m = map(int, month.split("-"))
        # simple month end
        from calendar import monthrange
        period_start = date(y, m, 1)
        period_end = date(y, m, monthrange(y, m)[1])
        bill = generate_bill_for_unit(unit, period_start, period_end)
        return redirect("admin:smart_meter_bill_change", bill.id)
    return render(request, "smart_meter/generate_bill.html", {"unit": unit})


def view_bills(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id)
    bills = Bill.objects.filter(unit=unit).order_by('-period_start')
    return render(request, "smart_meter/bills.html", {"unit": unit, "bills": bills})


def meter_dashboard(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id, is_smart_meter=True)

    # Get latest reading
    latest = MeterReading.objects.filter(
        unit=unit).order_by("-timestamp").first()

    # Get balance
    balance, _ = MeterBalance.objects.get_or_create(unit=unit)

    # Get tenant phone number (via lease)
    try:
        lease = Lease.objects.filter(unit=unit).latest("start_date")
        tenant = lease.tenant
        phone = tenant.phone  # assumes your tenant model has this
    except:
        tenant = None
        phone = None

    # If balance is low, build WhatsApp alert URL
    wa_url = None
    if balance.balance <= 100 and phone:
        message = f"⚠️ Dear {tenant.name}, your electricity meter balance is ₹{balance.balance}. Please recharge soon to avoid disconnection."
        wa_url = build_whatsapp_url(phone, message)

    # Last 7 days usage for chart
    start_date = now() - timedelta(days=7)
    readings = (
        MeterReading.objects.filter(unit=unit, timestamp__gte=start_date)
        .order_by("timestamp")
        .values("timestamp", "total_energy")
    )

    labels = [r["timestamp"].strftime("%d %b %H:%M") for r in readings]
    values = [float(r["total_energy"] or 0) for r in readings]

    # Monthly total for billing
    current_month = now().replace(day=1)
    month_readings = MeterReading.objects.filter(
        unit=unit, timestamp__gte=current_month
    ).order_by("timestamp")

    start_kwh = month_readings.first().total_energy if month_readings.exists() else 0
    end_kwh = month_readings.last().total_energy if month_readings.exists() else 0
    total_kwh = round((end_kwh or 0) - (start_kwh or 0), 2)

    context = {
        "unit": unit,
        "latest": latest,
        "labels": labels,
        "values": values,
        "total_kwh": total_kwh,
        "peak": latest.peak_hour if latest else False,
        "wa_url": wa_url,  # ✅ pass WhatsApp link to template
        "balance": balance,
    }

    return render(request, "smart_meter/dashboard.html", context)


BILLING_RATE = Decimal("7.50")  # ₹7.50 per kWh


def view_bills(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id)
    bills = Bill.objects.filter(unit=unit).order_by("-month")
    return render(request, "smart_meter/bill_list.html", {"unit": unit, "bills": bills})


# smart_meter/views.py


def meter_status(request, meter_id: int):
    """
    Returns whether the meter is 'online' based on last LiveReading.ts.
    Online if ts within ONLINE_MINUTES (default 10) or ?minutes= override.
    """
    try:
        meter = Meter.objects.get(pk=meter_id)
    except Meter.DoesNotExist:
        raise Http404("Meter not found")

    # allow ?minutes= override; else from settings; else 10
    try:
        minutes = int(request.GET.get("minutes") or getattr(
            settings, "ONLINE_MINUTES", 10))
    except ValueError:
        minutes = getattr(settings, "ONLINE_MINUTES", 10)

    # prefer OneToOne 'live' row if present
    lr = getattr(meter, "live", None)
    ts = lr.ts if isinstance(lr, LiveReading) else None

    online = False
    if ts:
        online = (timezone.now() - ts) <= timedelta(minutes=minutes)

    return JsonResponse({
        "online": online,
        "last_reading_ts": ts.isoformat() if ts else None,
        "minutes_window": minutes,
    })


def meter_settings(request):
    from .forms import MeterSettingsForm  # Lazy import inside the function
    settings, _ = MeterSettings.objects.get_or_create(id=1)

    if request.method == "POST":
        form = MeterSettingsForm(request.POST, instance=settings)
        if form.is_valid():
            form.save()
            messages.success(request, "Settings updated.")
    else:
        form = MeterSettingsForm(instance=settings)

    return render(request, "smart_meter/settings.html", {"form": form})


# views.py

# views.py


# smart_meter/views.py (relevant parts)


# smart_meter/views.py


def _meters_annotated_qs(request, online_minutes: int = 10):
    prop_id = (request.GET.get("property") or "").strip()
    unit_id = (request.GET.get("unit") or "").strip()
    meter_id = (request.GET.get("meter") or "").strip()
    q = (request.GET.get("q") or "").strip()

    qs = Meter.objects.select_related("unit", "unit__property")

    if prop_id:
        qs = qs.filter(unit__property_id=prop_id)
    if unit_id:
        qs = qs.filter(unit_id=unit_id)
    if meter_id:
        qs = qs.filter(id=meter_id)
    if q:
        qs = qs.filter(
            Q(meter_number__icontains=q) |
            Q(name__icontains=q) |
            Q(unit__unit_number__icontains=q) |
            Q(unit__property__property_name__icontains=q)
        )

    # Live row per meter (if duplicates exist, use the newest)
    live_qs = (LiveReading.objects
               .filter(meter=OuterRef("pk"))
               .order_by("-ts"))  # if you use ts, change to "-ts"

    # Current balance by unit
    bal_qs = (MeterBalance.objects
              .filter(unit=OuterRef("unit_id"))
              .values("balance")[:1])

    # 👇 annotate from **LiveReading** instead of MeterReading
    qs = qs.annotate(
        balance=Subquery(bal_qs),

        last_ts=Subquery(live_qs.values("ts")[:1]),         # or "ts"
        last_voltage_a=Subquery(live_qs.values("voltage_a")[:1]),
        last_current_a=Subquery(live_qs.values("current_a")[:1]),
        last_total_energy=Subquery(live_qs.values("total_energy")[:1]),
    )

    # Online/Offline from **live** timestamp
    cutoff_dt = timezone.now() - timedelta(minutes=online_minutes)
    qs = qs.annotate(
        is_online=Case(
            When(last_ts__gte=cutoff_dt, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        is_cutoff_flag=Case(
            When(Q(balance__isnull=False) & Q(balance__lte=F("min_balance_cutoff")),
                 then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        is_low=Case(
            When(Q(balance__isnull=False) & Q(balance__lt=F("min_balance_alert")),
                 then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )

    return qs.order_by("-last_ts", "meter_number")


# smart_meter/views.py


def meter_list(request):
    ONLINE_MINUTES = 10

    # Your existing helper builds the base queryset with flags/filters
    meters_qs = _meters_annotated_qs(request, online_minutes=ONLINE_MINUTES)

    # ✅ Make it efficient for template access (no N+1)
    meters_qs = meters_qs.select_related(
        'unit', 'unit__property', 'live')
    # 'live' is OneToOne related_name

    # (Optional) ensure each row has a balance value for scripts like "auto-select negative"
    latest_balance = (LiveReading.objects
                      .filter(meter=OuterRef('pk'))
                      .values('balance')[:1])
    meters_qs = meters_qs.annotate(balance=Subquery(latest_balance))

    # ---- last LiveReading per meter (no fragile reverse join) ----
    latest_lr = LiveReading.objects.filter(
        meter_id=OuterRef('pk')).order_by('-id')

    meters_qs = meters_qs.annotate(
        # fields for default & tiebreak ordering
        prop_name=Lower('unit__property__property_name'),
        unit_num=F('unit__unit_number'),
        meter_num=F('meter_number'),
        power_val=Subquery(latest_lr.values('total_power')[:1]),
        last_read_at=Subquery(latest_lr.values('ts')[:1]),

    )

    # ---- determine sort target from query string; default: property → unit ----
    sort = (request.GET.get('sort') or 'property').lower()
    dir_ = (request.GET.get('dir') or 'asc').lower()
    sort_map = {
        'property': 'prop_name',
        'unit':     'unit_num',
        'meter':    'meter_num',
        'power':    'power_status',
        'last':     'last_ts',
    }
    order_field = sort_map.get(sort, 'prop_name')
    if dir_ == 'desc':
        order_field = '-' + order_field

    # main order + stable tie-breakers
    meters_qs = meters_qs.order_by(
        order_field, 'prop_name', 'unit_num', 'meter_num')

    # ---- build header links here (so template doesn’t need parentheses/logic) ----
    qd = request.GET.copy()
    for k in ['sort', 'dir', 'page']:
        qd.pop(k, None)
    base_qs = qd.urlencode()

    def link_for(col):
        next_dir = 'desc' if (sort == col and dir_ == 'asc') else 'asc'
        return f'?{base_qs}&sort={col}&dir={next_dir}' if base_qs else f'?sort={col}&dir={next_dir}'

    header_links = {
        'meter':    link_for('meter'),
        'property': link_for('property'),
        'unit':     link_for('unit'),
        'power':    link_for('power'),
        'last':     link_for('last'),
    }
    paginator = Paginator(meters_qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    total_count = paginator.count

    ctx = {
        "meters": page_obj,
        "page_obj": page_obj,
        "paginator": paginator,
        "total_count": total_count,
        # ...your existing filters...
        "online_minutes": ONLINE_MINUTES, }

    # add to context (before render)
    ctx.update({
        'current_sort': sort,
        'current_dir': dir_,
        'header_links': header_links,
        'base_qs': base_qs,
    })

    try:
        (selected_meters,
         all_properties, filtered_units, filtered_meters,
         prop_id, unit_id, meter_param) = _filtered_meter_sets(request)
    except NameError:
        # Fallback (if helper not present in your repo)
        prop_id = (request.GET.get("property") or "").strip()
        unit_id = (request.GET.get("unit") or "").strip()
        meter_param = (request.GET.get("meter") or "").strip()

        from properties.models import Property, Unit
        all_properties = Property.objects.all().order_by("property_name")

        units_qs = Unit.objects.all()
        if prop_id:
            units_qs = units_qs.filter(property_id=prop_id)
        filtered_units = units_qs.order_by("unit_number")

        meters_qs = Meter.objects.select_related("unit", "unit__property")
        if unit_id:
            meters_qs = meters_qs.filter(unit_id=unit_id)
        elif prop_id:
            meters_qs = meters_qs.filter(unit__property_id=prop_id)
        filtered_meters = meters_qs.order_by("meter_number")

    # Keep the search box value in the UI
    q = (request.GET.get("q") or "").strip()

    # Expose everything the filter partial needs
    ctx.update({
        "all_properties": all_properties,
        "filtered_units": filtered_units,
        "filtered_meters": filtered_meters,
        "current_property": prop_id,
        "current_unit": unit_id,
        "current_meter": meter_param,
        "q": q,

        # Backward-compat alias if the partial uses a different key
        "properties": all_properties,
    })

    return render(request, "smart_meter/meter_list.html", ctx)


def add_meter(request):
    if request.method == "POST":
        form = MeterForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("smart_meter:meter_list")
    else:
        form = MeterForm()

    return render(request, "smart_meter/meter_form.html", {"form": form})


def meter_edit(request, pk):
    meter = get_object_or_404(Meter, pk=pk)
    if request.method == "POST":
        form = MeterForm(request.POST, instance=meter)
        if form.is_valid():
            form.save()
            return redirect('smart_meter:meter_detail', pk=meter.pk)
    else:
        form = MeterForm(instance=meter)
    return render(request, "smart_meter/meter_form.html", {"form": form, "edit": True})


def meter_delete(request, pk):
    meter = get_object_or_404(Meter, pk=pk)
    if request.method == "POST":
        meter.delete()
        return redirect('smart_meter:meter_list')
    return render(request, "smart_meter/meter_confirm_delete.html", {"meter": meter})


def meter_detail(request, pk):
    meter = get_object_or_404(Meter, pk=pk)
    return render(request, 'smart_meter/meter_detail.html', {'meter': meter})


def edit_reading(request, pk):
    reading = get_object_or_404(MeterReading, pk=pk)
    if request.method == 'POST':
        form = MeterReadingForm(request.POST, instance=reading)
        if form.is_valid():
            form.save()
            return redirect('reading_list')
    else:
        form = MeterReadingForm(instance=reading)
    return render(request, 'smart_meter/reading_form.html', {'form': form, 'edit': True})


def delete_reading(request, pk):
    reading = get_object_or_404(MeterReading, pk=pk)
    if request.method == 'POST':
        reading.delete()
        return redirect('reading_list')
    return render(request, 'smart_meter/reading_confirm_delete.html', {'reading': reading})


def meter_readings(request, meter_id):
    meter = get_object_or_404(Meter, id=meter_id)
    readings = MeterReading.objects.filter(meter=meter).order_by('-timestamp')
    return render(request, 'smart_meter/meter_readings.html', {
        'meter': meter,
        'readings': readings
    })


def fetch_meter_data(request):
    # Fetch data for all meters
    poll_all_meters()  # Call your polling function here to fetch the data
    return JsonResponse({'status': 'success'})


def toggle_power(request, meter_id):
    meter = get_object_or_404(Meter, pk=meter_id)
    # Logic to toggle power, e.g., sending a TCP command to turn off the meter
    # send_tcp_command_to_toggle_power(meter)
    meter.is_active = not meter.is_active  # Simulate power toggling
    meter.save()
    return redirect('smart_meter:meter_list')


def recharge_balance(request, meter_id):
    meter = get_object_or_404(Meter, pk=meter_id)
    if request.method == 'POST':
        amount = float(request.POST.get('amount', 0))
        meter.balance += amount
        meter.save()
    return redirect('smart_meter:meter_list')


def refund_balance(request, meter_id):
    meter = get_object_or_404(Meter, pk=meter_id)
    if request.method == 'POST':
        amount = float(request.POST.get('amount', 0))
        meter.balance -= amount
        meter.save()
    return redirect('smart_meter:meter_list')


# views.py

# ... keep your other imports

ONLINE_MINUTES = 10  # consider 'online' if ts within this many minutes


def live_custom(request):
    # 1) Pull the filter values from query string
    q = (request.GET.get("q") or "").strip()
    offline_only = (request.GET.get("offline") == "1")

    # 2) Reuse the same cascading filter sets as meter list
    #    selected_meters = the final meter set based on property/unit/meter GET params
    (selected_meters,
     all_properties, filtered_units, filtered_meters,
     prop_id, unit_id, meter_id) = _filtered_meter_sets(request)

    # 3) Base queryset: only readings for the selected meters
    qs = (
        LiveReading.objects
        .select_related("meter", "meter__unit", "meter__unit__property")
        .filter(meter__in=selected_meters)
        .order_by("meter__unit__property__property_name",
                  "meter__unit__unit_number",
                  "meter__meter_number")
    )

    # 4) Optional free-text search across property / unit / meter
    if q:
        qs = qs.filter(
            Q(meter__unit__unit_number__icontains=q) |
            Q(meter__meter_number__icontains=q) |
            Q(meter__unit__property__property_name__icontains=q)
        )

    # 5) Compute 'is_online' and apply offline-only filter if requested
    cutoff = timezone.now() - timedelta(minutes=ONLINE_MINUTES)
    rows = []
    for r in qs:
        r.is_online = bool(r.ts and r.ts >= cutoff)
        if offline_only and r.is_online:
            continue
        rows.append(r)

    # Mark selected flags (NO template comparison needed)
    # formatter-proof flags (avoid template comparisons)
    cp = str(prop_id or "")
    cu = str(unit_id or "")
    cm = str(meter_id or "")

    for p in all_properties:
        p.is_selected = (str(p.id) == cp)

    for u in filtered_units:
        u.is_selected = (str(u.id) == cu)

    for m in filtered_meters:
        m.is_selected = (str(m.id) == cm)

    offline_checked = (request.GET.get("offline") == "1")



    # 6) Render with everything the filter bar needs
    return render(request, "smart_meter/live_custom.html", {
        "rows": rows,
        "online_minutes": ONLINE_MINUTES,
        "q": q,
        "offline_only": offline_only,

        # dropdown data (same as meter list)
        "all_properties": all_properties,
        "filtered_units": filtered_units,
        "filtered_meters": filtered_meters,
        "current_property": prop_id,
        "current_unit": unit_id,
        "current_meter": meter_id,
        "vpn_connected": vpn_connected(),
        "public_ip": public_ip(),
    })


@require_POST
def recharge_meter(request, meter_id):
    meter = get_object_or_404(Meter, id=meter_id)
    try:
        amt = Decimal(request.POST.get("amount", "0") or "0")
    except Exception:
        messages.error(request, "Invalid amount.")
        return redirect("smart_meter_live_custom")

    if amt <= 0:
        messages.error(request, "Amount must be greater than 0.")
        return redirect("smart_meter_live_custom")

    bal, _ = MeterBalance.objects.get_or_create(unit=meter.unit)
    # Add credit to balance
    bal.balance = (bal.balance or Decimal("0.00")) + amt
    bal.save()

    MeterEvent.objects.create(unit=meter.unit, event_type="recharge",
                              note=f"Recharge via live page: +₹{amt}")
    messages.success(
        request, f"Recharged meter {meter.meter_number} by ₹{amt}.")
    return redirect("smart_meter_live_custom")


# smart_meter/views.py


def _redirect_back(request, fallback_name="smart_meter:meter_list"):
    return redirect(request.META.get("HTTP_REFERER") or reverse(fallback_name))


@require_POST
def cutoff_meter(request, meter_id):
    """Cut OFF (open relay) for a single meter — meter-number based, no IP needed."""
    meter = get_object_or_404(Meter, pk=meter_id)

    byCmd = 0x1A  # OFF
    frame = build_switch_frame(meter.meter_number, byCmd)
    frame_hex = _as_hex(frame)
    cmd_name = "OFF"

    # Audit: exactly what the user requested

    try:
        frame_hex = frame.hex().upper()
    except AttributeError:
        frame_hex = str(frame)

    # Entry audit (this is specifically what you asked for when pressing the ON/OFF button)
    logger.info(
        "REQUEST TX CUT-OFF from cutoff_meter user=%s path=%s meter=%s "
        "cmd=%s(0x%02X) frame=%s disable_cutoffs=%s sender=%s.%s",
        getattr(request.user, "username", "anonymous"),      # user=%s
        getattr(request, "path", ""),                        # path=%s
        meter.meter_number,                                  # meter=%s
        cmd_name,                                            # cmd=%s
        byCmd,                                               # 0x%02X  (int!)
        frame_hex,                                           # frame=%s
        DISABLE_CUTOFFS,                                     # disable_cutoffs=%s
        getattr(send_via_db, "__module__", "?"),             # sender=%s
        getattr(send_via_db, "__name__", "?"),               # .%s
    )


    from smart_meter.models import MeterEvent
    MeterEvent.objects.create(
        unit=meter.unit,
        event_type="cutoff_tx",
        note=f"frame={frame_hex} by={getattr(request.user, 'username', 'anonymous')}",
    )
    # blank line separator
    logger.info("-------------------------------------")

    # Send (honor feature flag if you kept it)
    if DISABLE_CUTOFFS:
        res = {"ok": True, "error": None, "payload": "skipped:DISABLE_CUTOFFS"}
        logger.info("RESPONSE meter=%s cmd=%s ok=%s error=%s payload=%s",
                    meter.meter_number, cmd_name, res.get("ok"), res.get("error"), res.get("payload"))
    else:
        try:
            secret = getattr(settings, "METER_CTRL_SECRET", None)  # optional

            res = _call_send(
                meter_number=meter.meter_number,
                frame=frame,
                timeout=32.0,
                expect_di=None,
                allow_switch=True,                                 # <-- explicit
                initiated_by=request.user.get_username(),          # <-- who clicked
                reason="manual switch from UI",                    # <-- audit
                auth=secret,                                       # <-- optional shared secret
            )
            logger.info("RESPONSE RX CUT-OFF meter=%s cmd=%s ok=%s error=%s payload=%s",
                        meter.meter_number, cmd_name, res.get("ok"), res.get("error"), res.get("payload"))

            MeterEvent.objects.create(
                unit=meter.unit,
                event_type="cutoff_rx",
                note=f"ok={res.get('ok')} error={res.get('error')} payload={res.get('payload')}",
            )

            import time
            if res.get("ok"):
                time.sleep(5)  # brief settle
                try:
                    refresh_live(meter.meter_number)
                    lr = (LiveReading.objects
                          .filter(meter=meter).order_by("-ts").first())
                    amps = float(getattr(lr, "current_a", 0) or 0)
                    watts = float(getattr(lr, "total_power", 0) or 0)
                    logger.info("POST-CUTOFF VERIFY meter=%s I=%.3fA P=%.3fW ts=%s",
                                meter.meter_number, amps, watts, getattr(lr, "ts", None))
                    if amps > 0.02 or watts > 5:
                        messages.warning(request,
                                         f"Cutoff sent, but current is {amps:.3f}A / {watts:.0f}W — relay may be closed, bypassed, or stuck.")
                except Exception:
                    pass

        except Exception as e:
            logger.exception("SEND_FAILED meter=%s cmd=%s error=%s",
                             meter.meter_number, cmd_name, e)
            # For AJAX callers, return JSON error
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                # blank line separator

                return JsonResponse({"success": False, "error": str(e)}, status=500)
            messages.error(
                request, f"Cut off failed for {meter.meter_number}: {e}")
            # blank line separator

            return _redirect_back(request)

    # Update UI state
    success = bool(res.get("ok"))
    if success:
        Meter.objects.filter(pk=meter.pk).update(power_status="off")
        try:
            refresh_live(meter.meter_number)
        except Exception:
            pass

    # Respond depending on caller
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        # blank line separator

        return JsonResponse({"success": success, "error": res.get("error")})
    else:
        if success:
            messages.success(request, f"Cut off sent to {meter.meter_number}.")
        else:
            messages.error(
                request, f"Cut off failed for {meter.meter_number}: {res.get('error', 'no reply')}")

        return _redirect_back(request)


@require_POST
def restore_meter(request, meter_id):
    """Restore (close relay) for a single meter — meter-number based, no IP needed."""
    meter = get_object_or_404(Meter, pk=meter_id)

    byCmd = 0x1C  # ON
    frame = build_switch_frame(meter.meter_number, byCmd)
    frame_hex = _as_hex(frame)
    cmd_name = "ON"

    try:
        frame_hex = frame.hex().upper()
    except AttributeError:
        frame_hex = str(frame)

    logger.info(
        "REQUEST from RESTORE_METER user=%s path=%s meter=%s cmd=%s(0x%02X) frame=%s",
        getattr(request.user, "username", "anonymous"),
        getattr(request, "path", ""),
        meter.meter_number, cmd_name, byCmd, frame_hex
    )

    logger.info("-------------------------------------")

    if DISABLE_CUTOFFS:
        res = {"ok": True, "error": None, "payload": "skipped:DISABLE_CUTOFFS"}
        logger.info("RESPONSE meter=%s cmd=%s ok=%s error=%s payload=%s",
                    meter.meter_number, cmd_name, res.get("ok"), res.get("error"), res.get("payload"))
    else:
        try:
            secret = getattr(settings, "METER_CTRL_SECRET", None)  # optional
            res = _call_send(
                meter_number=meter.meter_number,
                frame=frame,
                timeout=32.0,
                expect_di=None,
                allow_switch=True,                                 # <-- explicit
                initiated_by=request.user.get_username(),          # <-- who clicked
                reason="manual switch from UI",                    # <-- audit
                auth=secret,                                       # <-- optional shared secret
            )
            logger.info("RESPONSE meter=%s cmd=%s ok=%s error=%s payload=%s",
                        meter.meter_number, cmd_name, res.get("ok"), res.get("error"), res.get("payload"))
        except Exception as e:
            logger.exception("SEND_FAILED meter=%s cmd=%s error=%s",
                             meter.meter_number, cmd_name, e)
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                # blank line separator

                return JsonResponse({"success": False, "error": str(e)}, status=500)
            messages.error(
                request, f"Restore failed for {meter.meter_number}: {e}")
            # blank line separator

            return _redirect_back(request)

    success = bool(res.get("ok"))
    if success:
        Meter.objects.filter(pk=meter.pk).update(power_status="on")
        try:
            refresh_live(meter.meter_number)
        except Exception:
            pass

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":

        return JsonResponse({"success": success, "error": res.get("error")})
    else:
        if success:
            messages.success(request, f"Restore sent to {meter.meter_number}.")
        else:
            messages.error(
                request, f"Restore failed for {meter.meter_number}: {res.get('error', 'no reply')}")

        return _redirect_back(request)


def unknown_meter_list(request):
    q = request.GET.get("q", "").strip()
    qs = UnknownMeter.objects.filter(status="new").order_by("-last_seen")
    if q:
        qs = qs.filter(meter_number__icontains=q)
    return render(request, "smart_meter/unknown_meter_list.html", {"unknown_meters": qs, "q": q})


@transaction.atomic
def unknown_meter_convert(request, pk):
    um = get_object_or_404(UnknownMeter, pk=pk)
    if request.method == "POST":
        form = UnknownToMeterForm(request.POST, initial={
            "meter_number": um.meter_number})
        if form.is_valid():
            meter = form.save(commit=False)
            meter.meter_number = um.meter_number  # enforce
            meter.save()
            # unit is not a field on Meter in some schemas; if your Meter has FK unit, then save above already covered it
            um.status = "added"
            um.save(update_fields=["status"])
            messages.success(request, f"Meter {meter.meter_number} created.")
            return redirect("smart_meter:unknown_meter_list")
    else:
        form = UnknownToMeterForm(initial={"meter_number": um.meter_number})
    return render(request, "smart_meter/unknown_meter_convert.html", {"um": um, "form": form})


def unknown_meter_ignore(request, pk):
    um = get_object_or_404(UnknownMeter, pk=pk)
    um.status = "ignored"
    um.save(update_fields=["status"])
    messages.info(request, f"Ignored {um.meter_number}.")
    return redirect("smart_meter:unknown_meter_list")

# smart_meter/views.py


@transaction.atomic
def unknown_meter_quick_add(request, pk):
    um = get_object_or_404(UnknownMeter, pk=pk)
    # Create Meter with just the number if it doesn't exist
    meter, created = Meter.objects.get_or_create(
        meter_number=um.meter_number,
        defaults={
            # optional defaults—adjust to your Meter fields


            "power_status": "on",  # or your model’s default/choice
        }
    )
    # mark unknown as added
    um.status = "added"
    um.save(update_fields=["status"])
    if created:
        messages.success(request, f"✅ Meter {meter.meter_number} created.")
    else:
        messages.info(
            request, f"ℹ️ Meter {meter.meter_number} already existed; marked as added.")
    return redirect("smart_meter:unknown_meter_list")


# smart_meter/views.py

try:
    from leases.models import Lease
except Exception:
    Lease = None

try:
    from smart_meter.utils import build_whatsapp_url
except Exception:
    build_whatsapp_url = None


# smart_meter/views.py


try:
    from leases.models import Lease
except Exception:
    Lease = None

try:
    from smart_meter.utils import build_whatsapp_url
except Exception:
    build_whatsapp_url = None


BILLING_RATE = Decimal("7.50")
ONLINE_MINUTES = 10


def _filtered_meter_sets(request):
    """Return (meters_qs, all_properties, filtered_units, filtered_meters, current ids) for meter_filters.html."""
    prop_id = (request.GET.get("property") or "").strip()
    unit_id = (request.GET.get("unit") or "").strip()
    meter_id = (request.GET.get("meter") or "").strip()

    all_properties = Property.objects.all().order_by("property_name")

    units_qs = Unit.objects.all()
    if prop_id:
        units_qs = units_qs.filter(property_id=prop_id)
    filtered_units = units_qs.order_by("unit_number")

    meters_qs = Meter.objects.select_related("unit", "unit__property")
    if unit_id:
        meters_qs = meters_qs.filter(unit_id=unit_id)
    elif prop_id:
        meters_qs = meters_qs.filter(unit__property_id=prop_id)
    filtered_meters = meters_qs.order_by("meter_number")

    # Final meter set (what charts/tables use)
    selected_meters = filtered_meters
    if meter_id:
        selected_meters = selected_meters.filter(id=meter_id)

    return (
        selected_meters,
        all_properties, filtered_units, filtered_meters,
        prop_id, unit_id, meter_id
    )


# smart_meter/views.py — replace energy_dashboard with this version


# smart_meter/views.py


try:
    from leases.models import Lease
except Exception:
    Lease = None

try:
    from smart_meter.utils import build_whatsapp_url
except Exception:
    build_whatsapp_url = None

ONLINE_MINUTES = 10  # consider live online if LiveReading.ts within this window


def _aware_midnight(d: date):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(d, time.min), tz)


def _meter_q(param: str) -> Q:
    """Allow meter dropdown to use either ID or meter_number."""
    if not param:
        return Q()
    if param.isdigit():
        return Q(id=int(param)) | Q(meter_number=param)
    return Q(meter_number=param)


def energy_dashboard(request):
    """
    - Default: ALL meters, current month, daily usage lines (one line per meter).
    - If monthly + ALL meters: grouped bars (one bar per meter per month).
    - If ONE meter + hourly: hourly usage line for the chosen range.
    - Data labels are shown on the chart (values on points/bars).
    """
    # ---------- date window (default = current month) ----------
    today = date.today()
    start_date = date(today.year, today.month, 1)
    end_date = today
    if request.GET.get("start") and request.GET.get("end"):
        try:
            start_date = date.fromisoformat(request.GET["start"])
            end_date = date.fromisoformat(request.GET["end"])
        except Exception:
            pass

    dt_start = _aware_midnight(start_date)                       # inclusive
    dt_end_excl = _aware_midnight(end_date + timedelta(days=1))  # exclusive

    report_type = request.GET.get(
        "report_type", "daily")  # daily | monthly | hourly

    # ---------- filters (same keys as meter_filters.html) ----------
    prop_id = (request.GET.get("property") or "").strip()
    unit_id = (request.GET.get("unit") or "").strip()
    meter_param = (request.GET.get("meter")
                   or "").strip()  # id or meter_number

    all_properties = Property.objects.all().order_by("property_name")

    units_qs = Unit.objects.all()
    if prop_id:
        units_qs = units_qs.filter(property_id=prop_id)
    filtered_units = units_qs.order_by("unit_number")

    meters_qs = Meter.objects.select_related("unit", "unit__property")
    if unit_id:
        meters_qs = meters_qs.filter(unit_id=unit_id)
    elif prop_id:
        meters_qs = meters_qs.filter(unit__property_id=prop_id)
    filtered_meters = meters_qs.order_by("meter_number")

    if meter_param:
        filtered_meters = filtered_meters.filter(_meter_q(meter_param))

    selected_meters = filtered_meters
    per_meter_mode = bool(meter_param)

    # ---------- live/per-meter status card ----------
    selected_meter = None
    unit = None
    latest_ts = latest_voltage_a = latest_current_a = latest_total_energy = None
    balance_obj = None
    wa_url = None

    if per_meter_mode:
        selected_meter = (
            Meter.objects.select_related("unit", "unit__property")
            .filter(_meter_q(meter_param)).first()
        )
        if selected_meter:
            unit = selected_meter.unit
            # Prefer live
            live = (LiveReading.objects
                    .filter(meter=selected_meter)
                    .order_by("-ts").first())
            if live:
                latest_ts = live.ts
                latest_voltage_a = live.voltage_a
                latest_current_a = live.current_a
                latest_total_energy = live.total_energy
            else:
                snap = (MeterReading.objects
                        .filter(meter=selected_meter)
                        .order_by("-ts").first())
                if snap:
                    latest_ts = snap.ts
                    latest_voltage_a = snap.voltage_a
                    latest_current_a = snap.current_a
                    latest_total_energy = snap.total_energy

            # Balance + optional WA alert link
            balance_obj, _ = MeterBalance.objects.get_or_create(unit=unit)
            if (balance_obj and balance_obj.balance is not None
                    and Lease and build_whatsapp_url
                    and balance_obj.balance <= selected_meter.min_balance_alert):
                try:
                    lease = Lease.objects.filter(
                        unit=unit).latest("start_date")
                    if getattr(lease, "tenant", None) and lease.tenant.phone:
                        msg = (f"⚠️ Dear {lease.tenant.get_full_name()}, your meter balance is "
                               f"Rs. {balance_obj.balance}. Please recharge soon to avoid disconnection.")
                        wa_url = build_whatsapp_url(lease.tenant.phone, msg)
                except Exception:
                    pass

    # ---------- pull snapshots once. bucket in Python (no DB tz deps) ----------
    tz = timezone.get_current_timezone()

    # Special case: hourly buckets only make sense for ONE meter;
    # if hourly with ALL meters, we’ll return empty series and the template will nudge to pick a meter.
    hourly_mode = (report_type == "hourly")

    base_qs = (MeterReading.objects
               .filter(meter__in=selected_meters, ts__gte=dt_start, ts__lt=dt_end_excl)
               .values("meter_id", "ts", "total_energy")
               .order_by("meter_id", "ts"))

    debug_snapshot_count_in_window = base_qs.count()

    # Fallback: if window empty, try the last 7 days with data
    if debug_snapshot_count_in_window == 0:
        latest = (MeterReading.objects
                  .filter(meter__in=selected_meters)
                  .order_by("-ts").values_list("ts", flat=True).first())
        if latest:
            last_day = latest.astimezone(tz).date()
            fb_start = _aware_midnight(last_day - timedelta(days=6))
            fb_end = _aware_midnight(last_day + timedelta(days=1))
            base_qs = (MeterReading.objects
                       .filter(meter__in=selected_meters, ts__gte=fb_start, ts__lt=fb_end)
                       .values("meter_id", "ts", "total_energy")
                       .order_by("meter_id", "ts"))
            debug_snapshot_count_in_window = base_qs.count()

    # ---------- Python bucketing ----------
    # Per meter per period min/max => usage
    per_meter_period_minmax = defaultdict(lambda: {"min": None, "max": None})
    # Per meter whole window min/max for totals
    per_meter_window_minmax = defaultdict(lambda: {"min": None, "max": None})
    # For chart datasets: usage per meter per period (preserve insertion order of periods)
    per_meter_usage = defaultdict(lambda: OrderedDict())

    for row in base_qs:
        mid = row["meter_id"]
        ts = row["ts"].astimezone(tz)
        val = row["total_energy"]
        if val is None:
            continue

        if hourly_mode and per_meter_mode:
            # bucket to exact hour
            period_key = ts.replace(minute=0, second=0, microsecond=0)
        elif report_type == "monthly":
            period_key = date(ts.year, ts.month, 1)
        else:  # daily (default)
            period_key = ts.date()

        # update min/max for that (meter, period)
        key = (mid, period_key)
        mm = per_meter_period_minmax[key]
        mm["min"] = val if mm["min"] is None or val < mm["min"] else mm["min"]
        mm["max"] = val if mm["max"] is None or val > mm["max"] else mm["max"]

        # whole-window min/max
        mw = per_meter_window_minmax[mid]
        mw["min"] = val if mw["min"] is None or val < mw["min"] else mw["min"]
        mw["max"] = val if mw["max"] is None or val > mw["max"] else mw["max"]

    # Build per meter usage map & a sorted list of periods
    # (we want consistent x-axis across datasets; fill missing with 0)
    period_set = set()
    for (mid, p), mm in per_meter_period_minmax.items():
        if mm["min"] is None or mm["max"] is None:
            continue
        use = Decimal(mm["max"]) - Decimal(mm["min"])
        if use < 0:
            use = Decimal("0")
        per_meter_usage[mid][p] = use
        period_set.add(p)

    periods_sorted = sorted(period_set)
    if hourly_mode and per_meter_mode:
        x_labels = [p.strftime("%d %b %H:00") for p in periods_sorted]
    elif report_type == "monthly":
        x_labels = [p.strftime("%b %Y") for p in periods_sorted]
    else:
        x_labels = [p.strftime("%b %d") for p in periods_sorted]

    # Chart datasets:
    id_to_number = dict(selected_meters.values_list("id", "meter_number"))
    series_datasets = []
    for mid in selected_meters.values_list("id", flat=True):
        # keep series order by current queryset order
        data = [float(per_meter_usage[mid].get(p, Decimal("0")))
                for p in periods_sorted]
        series_datasets.append({
            "label": id_to_number.get(mid, f"Meter {mid}"),
            "data": data,
            # No explicit colors; Chart.js picks defaults. (User asked for values displayed; we do via datalabels plugin.)
        })

    # Totals & cost (use each meter's unit_rate in Rs.)
    monthly_total = Decimal("0")
    monthly_cost = Decimal("0")
    rate_map = dict(selected_meters.values_list("id", "unit_rate"))
    for mid, mm in per_meter_window_minmax.items():
        if mm["min"] is None or mm["max"] is None:
            continue
        use = Decimal(mm["max"]) - Decimal(mm["min"])
        if use < 0:
            use = Decimal("0")
        monthly_total += use
        monthly_cost += use * Decimal(rate_map.get(mid) or 0)

    # “billing_rate” display: single meter => that meter’s rate; all meters => only show if all rates are same
    if per_meter_mode and selected_meter:
        billing_rate = Decimal(selected_meter.unit_rate or 0)
    else:
        distinct_rates = list(selected_meters.values_list(
            "unit_rate", flat=True).distinct())
        billing_rate = Decimal(distinct_rates[0]) if len(
            distinct_rates) == 1 else None

    # Fleet online/offline counts
    online_count = offline_count = 0
    if not per_meter_mode:
        cutoff = timezone.now() - timedelta(minutes=ONLINE_MINUTES)
        latest_live = {lr.meter_id: lr.ts for lr in LiveReading.objects.filter(
            meter__in=selected_meters)}
        for mid in selected_meters.values_list("id", flat=True):
            ts_live = latest_live.get(mid)
            if ts_live and ts_live >= cutoff:
                online_count += 1
            else:
                offline_count += 1

    context = {
        # filters & choices
        "all_properties": all_properties,
        "filtered_units": filtered_units,
        "filtered_meters": filtered_meters,  # used by template dropdown
        "meters": filtered_meters,           # alias for backward‐compat
        "current_property": prop_id,
        "current_unit": unit_id,
        "current_meter": meter_param,

        # live/per-meter card
        "selected_meter": selected_meter,
        "unit": unit,
        "latest_ts": latest_ts,
        "latest_voltage_a": latest_voltage_a,
        "latest_current_a": latest_current_a,
        "latest_total_energy": latest_total_energy,
        "balance": balance_obj,
        "wa_url": wa_url,

        # chart series
        "report_type": report_type,
        "start_date": start_date,
        "end_date": end_date,
        "series_labels": x_labels,
        "series_datasets": series_datasets,

        # summaries
        "monthly_total": monthly_total,
        "monthly_cost": monthly_cost,
        "billing_rate": billing_rate,  # Rs./kWh (None if mixed)
        "online_count": online_count,
        "offline_count": offline_count,
        "online_minutes": ONLINE_MINUTES,

        "selected_property_id": selected_property_id,
        "selected_unit_id": selected_unit_id,
        "selected_meter_id": selected_meter_id,

        # debug (optional)
        "debug_selected_meters_count": selected_meters.count(),
        "debug_snapshot_count_in_window": debug_snapshot_count_in_window,
        "debug_meter_param": meter_param or "(all)",
    }
    return render(request, "smart_meter/dashboard.html", context)


def _filtered_meter_sets(request):
    """
    Reuse the exact same GET keys your meter filters use: ?property=, ?unit=, ?meter=
    Returns (selected_meters, all_properties, filtered_units,
             filtered_meters, prop_id, unit_id, meter_id)
    """
    prop_id = (request.GET.get("property") or "").strip()
    unit_id = (request.GET.get("unit") or "").strip()
    meter_id = (request.GET.get("meter") or "").strip()

    all_properties = Property.objects.all().order_by("property_name")

    units_qs = Unit.objects.all()
    if prop_id:
        units_qs = units_qs.filter(property_id=prop_id)
    filtered_units = units_qs.order_by("unit_number")

    meters_qs = Meter.objects.select_related("unit", "unit__property")
    if unit_id:
        meters_qs = meters_qs.filter(unit_id=unit_id)
    elif prop_id:
        meters_qs = meters_qs.filter(unit__property_id=prop_id)
    filtered_meters = meters_qs.order_by("meter_number")

    # Final meter set for charts/tables:
    selected_meters = filtered_meters
    if meter_id:
        selected_meters = selected_meters.filter(id=meter_id)

    return (
        selected_meters,
        all_properties, filtered_units, filtered_meters,
        prop_id, unit_id, meter_id
    )


def _parse_meter_param(meter_param: str):
    """
    Accept both a numeric meter PK (id) or a meter_number string.
    Return a Q object to filter meters accordingly.
    """
    if not meter_param:
        return Q()
    # If it looks like an integer PK
    if meter_param.isdigit():
        return Q(id=int(meter_param)) | Q(meter_number=meter_param)
    # Otherwise treat it as meter_number
    return Q(meter_number=meter_param)


@csrf_exempt
def fetch_meter_data(request):
    if request.method == "POST":
        try:
            # Simulate data fetching - in real app, this would call your API
            # Update last_updated timestamp for all meters
            from .models import Meter
            Meter.objects.update(last_updated=timezone.now())

            return JsonResponse({
                "status": "success",
                "message": "Meter data refreshed successfully"
            })
        except Exception as e:
            return JsonResponse({
                "status": "error",
                "message": f"Failed to fetch data: {str(e)}"
            }, status=500)
    return JsonResponse({
        "status": "error",
        "message": "Invalid request method"
    }, status=400)


# views.py

QUICK_RANGES = {"today", "yesterday", "this_week",
                "last_week", "this_month", "last_month", "custom", ""}


def _range_to_dates(range_key: str):
    t = timezone.localdate()
    if range_key == "today":
        return t, t
    if range_key == "yesterday":
        y = t-timedelta(days=1)
        return y, y
    if range_key == "this_week":
        s = t - timedelta(days=t.weekday())
        e = s + timedelta(days=6)
        return s, e
    if range_key == "last_week":
        s = t - timedelta(days=t.weekday()+7)
        e = s + timedelta(days=6)
        return s, e
    if range_key == "this_month":
        s = t.replace(day=1)
        e = t.replace(day=calendar.monthrange(t.year, t.month)[1])
        return s, e
    if range_key == "last_month":
        y, m = (t.year-1, 12) if t.month == 1 else (t.year, t.month-1)
        s = date(y, m, 1)
        e = date(y, m, calendar.monthrange(y, m)[1])
        return s, e
    return None, None


def reading_list(request):
    prop_id = request.GET.get("property") or ""
    unit_id = request.GET.get("unit") or ""
    meter_id = request.GET.get("meter") or ""

    range_key = (request.GET.get("range") or "").strip()
    if range_key not in QUICK_RANGES:
        range_key = ""  # treat unknown as "All time"

    start_str = (request.GET.get("start") or "").strip()
    end_str = (request.GET.get("end") or "").strip()

    # ---------- Build date window as DATES first ----------
    # Priority:
    #   1) If range_key is a known preset (and not "custom"): use it
    #   2) else parse start/end strings (custom/manual)
    start_date = end_date = None
    if range_key and range_key != "custom":
        # must return date objects (not datetimes)
        start_date, end_date = _range_to_dates(range_key)
    else:
        try:
            if start_str:
                start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            if end_str:
                end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
        except ValueError:
            start_date = end_date = None

    # Normalize: if only one side provided, allow open-ended
    #   (leave the other as None and we’ll only apply the side we have)
    # ---------- Convert to TZ-AWARE DATETIMES ----------
    tz = timezone.get_current_timezone()

    def aware_start(d: date | None):
        if not d:
            return None
        return timezone.make_aware(datetime.combine(d, time.min), tz)

    def aware_end_exclusive(d: date | None):
        if not d:
            return None
        # end is inclusive by date -> exclusive at next day's midnight
        next_day = d + timedelta(days=1)
        return timezone.make_aware(datetime.combine(next_day, time.min), tz)

    start_dt = aware_start(start_date)
    end_dt_excl = aware_end_exclusive(end_date)

    # ---------- Base queryset ----------
    readings = MeterReading.objects.select_related(
        "meter", "meter__unit", "meter__unit__property"
    )

    if meter_id:
        readings = readings.filter(meter_id=meter_id)
    elif unit_id:
        readings = readings.filter(meter__unit_id=unit_id)
    elif prop_id:
        readings = readings.filter(meter__unit__property_id=prop_id)

    # ---------- Date filters (use datetime bounds; robust across time zones) ----------
    if start_dt:
        readings = readings.filter(ts__gte=start_dt)
    if end_dt_excl:
        readings = readings.filter(ts__lt=end_dt_excl)

    total_count = readings.count()
    readings = readings.order_by("-ts")

    paginator = Paginator(readings, 100)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_items = page_obj.object_list
    rows = [(r, ReadingManualForm(instance=r, request=request, prefix=f"r{r.pk}"))
            for r in page_items]

    qs = request.GET.copy()
    qs.pop("page", None)

    ctx = dict(
        all_properties=Property.objects.order_by("property_name"),
        filtered_units=(Unit.objects.filter(property_id=prop_id)
                        if prop_id else Unit.objects.all()).order_by("unit_number"),
        filtered_meters=(Meter.objects.filter(unit_id=unit_id) if unit_id else
                         Meter.objects.filter(unit__property_id=prop_id) if prop_id else
                         Meter.objects.all()).order_by("meter_number"),
        current_property=prop_id,
        current_unit=unit_id,
        current_meter=meter_id,
        rows=rows,
        page_obj=page_obj, paginator=paginator, total_count=total_count,
        range=range_key,          # keeps the dropdown state
        # still dates for the template's value="{{ start|date:'Y-m-d' }}"
        start=start_date,
        end=end_date,
        qs=qs.urlencode(),
    )
    return render(request, "smart_meter/reading_list.html", ctx)

# --- Reuse the exact same filtering logic for both list & exports ---


def _filtered_readings_qs(request):
    prop_id = request.GET.get("property") or ""
    unit_id = request.GET.get("unit") or ""
    meter_id = request.GET.get("meter") or ""
    q = request.GET.get("q") or ""

    qs = (MeterReading.objects
          .select_related("meter", "meter__unit", "meter__unit__property"))

    if meter_id:
        qs = qs.filter(meter_id=meter_id)
    elif unit_id:
        qs = qs.filter(meter__unit_id=unit_id)
    elif prop_id:
        qs = qs.filter(meter__unit__property_id=prop_id)

    if q:
        qs = qs.filter(
            Q(meter__meter_number__icontains=q) |
            Q(meter__unit__unit_number__icontains=q) |
            Q(meter__unit__property__property_name__icontains=q)
        )
    return qs

# smart_meter/views.py (replace the headers/rows in both exporters)


def export_meter_readings_csv(request):
    qs = _filtered_readings_qs(request).order_by("-ts")
    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="meter_readings_{now_str}.csv"'
    writer = csv.writer(resp)

    headers = [
        "Timestamp", "Property", "Unit", "Meter",
        "Voltage_A(V)", "Current_A(A)", "Total_Power(W)",
        "Total_Energy(kWh)", "PF_Total"
    ]
    writer.writerow(headers)

    for r in qs.iterator(chunk_size=2000):
        ts = getattr(r, "ts", None)
        row = [
            ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "",
            getattr(r.meter.unit.property, "property_name", ""),
            getattr(r.meter.unit, "unit_number", ""),
            r.meter.meter_number,
            r.voltage_a if r.voltage_a is not None else "",
            r.current_a if r.current_a is not None else "",
            r.total_power if r.total_power is not None else "",
            r.total_energy if r.total_energy is not None else "",
            r.pf_total if r.pf_total is not None else "",
        ]
        writer.writerow(row)
    return resp


def export_meter_readings_xlsx(request):
    qs = _filtered_readings_qs(request).order_by("-ts")
    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")

    wb = Workbook()
    ws = wb.active
    ws.title = "Readings"

    headers = [
        "Timestamp", "Property", "Unit", "Meter",
        "Voltage_A(V)", "Current_A(A)", "Total_Power(W)",
        "Total_Energy(kWh)", "PF_Total"
    ]
    ws.append(headers)

    for r in qs.iterator(chunk_size=2000):
        ts = getattr(r, "ts", None)
        row = [
            ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "",
            getattr(r.meter.unit.property, "property_name", ""),
            getattr(r.meter.unit, "unit_number", ""),
            r.meter.meter_number,
            r.voltage_a if r.voltage_a is not None else None,
            r.current_a if r.current_a is not None else None,
            r.total_power if r.total_power is not None else None,
            r.total_energy if r.total_energy is not None else None,
            r.pf_total if r.pf_total is not None else None,
        ]
        ws.append(row)

    # Auto width
    for col in ws.columns:
        try:
            max_len = max(len(str(c.value))
                          if c.value is not None else 0 for c in col)
        except ValueError:
            max_len = 10
        letter = get_column_letter(col[0].column)
        ws.column_dimensions[letter].width = min(max(10, max_len + 2), 32)

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    resp = HttpResponse(
        bio.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="meter_readings_{now_str}.xlsx"'
    return resp

# smart_meter/views.py (exporters)


def meters_export_csv(request):
    qs = _meters_annotated_qs(request, online_minutes=10)
    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="meters_{now_str}.csv"'
    w = csv.writer(resp)

    headers = [
        "Status", "Meter #", "Name", "Property", "Unit",
        "Power", "Unit Rate", "Min Alert", "Min Cutoff", "Active",
        "Installed", "Balance",
        "Last Reading", "Voltage_A(V)", "Current_A(A)", "Total_kWh",
    ]
    w.writerow(headers)

    for m in qs.iterator(chunk_size=1000):
        w.writerow([
            "Online" if m.is_online else "Offline",
            m.meter_number,
            m.name,
            getattr(m.unit.property, "property_name", ""),
            getattr(m.unit, "unit_number", ""),
            m.power_status,
            float(m.unit_rate) if m.unit_rate is not None else "",
            float(m.min_balance_alert) if m.min_balance_alert is not None else "",
            float(m.min_balance_cutoff) if m.min_balance_cutoff is not None else "",
            "Yes" if m.is_active else "No",
            m.installed_at.strftime("%Y-%m-%d") if m.installed_at else "",
            float(m.balance) if m.balance is not None else "",
            m.last_ts.strftime("%Y-%m-%d %H:%M:%S") if m.last_ts else "",
            float(m.last_voltage_a) if m.last_voltage_a is not None else "",
            float(m.last_current_a) if m.last_current_a is not None else "",
            float(m.last_total_energy) if m.last_total_energy is not None else "",
        ])
    return resp


def meters_export_xlsx(request):
    qs = _meters_annotated_qs(request, online_minutes=10)
    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")

    wb = Workbook()
    ws = wb.active
    ws.title = "Meters"

    headers = [
        "Status", "Meter #", "Name", "Property", "Unit",
        "Power", "Unit Rate", "Min Alert", "Min Cutoff", "Active",
        "Installed", "Balance",
        "Last Reading", "Voltage_A(V)", "Current_A(A)", "Total_kWh",
    ]
    ws.append(headers)

    for m in qs.iterator(chunk_size=1000):
        ws.append([
            "Online" if m.is_online else "Offline",
            m.meter_number,
            m.name,
            getattr(m.unit.property, "property_name", ""),
            getattr(m.unit, "unit_number", ""),
            m.power_status,
            float(m.unit_rate) if m.unit_rate is not None else None,
            float(m.min_balance_alert) if m.min_balance_alert is not None else None,
            float(m.min_balance_cutoff) if m.min_balance_cutoff is not None else None,
            "Yes" if m.is_active else "No",
            m.installed_at.strftime("%Y-%m-%d") if m.installed_at else None,
            float(m.balance) if m.balance is not None else None,
            m.last_ts.strftime("%Y-%m-%d %H:%M:%S") if m.last_ts else None,
            float(m.last_voltage_a) if m.last_voltage_a is not None else None,
            float(m.last_current_a) if m.last_current_a is not None else None,
            float(m.last_total_energy) if m.last_total_energy is not None else None,
        ])

    # auto widths
    for col in ws.columns:
        try:
            max_len = max(len(str(c.value))
                          if c.value is not None else 0 for c in col)
        except ValueError:
            max_len = 10
        letter = get_column_letter(col[0].column)
        ws.column_dimensions[letter].width = min(max(10, max_len + 2), 32)

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    resp = HttpResponse(
        bio.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="meters_{now_str}.xlsx"'
    return resp


# --- Hourly Report ---


def hourly_report(request):
    """
    Shows per-hour usage (ΔkWh within each hour) & max current for the selected day.
    If a single meter is chosen -> one dataset; otherwise one line per meter.
    Avoids DB time zone funcs by grouping in Python.
    """
    # ----- filters -----
    prop_id = (request.GET.get("property") or "").strip()
    unit_id = (request.GET.get("unit") or "").strip()
    meter_id = (request.GET.get("meter") or "").strip()
    day_str = (request.GET.get("day") or "").strip()

    # default = today
    tz = timezone.get_current_timezone()
    today = timezone.localtime(timezone.now(), tz).date()
    target_day = today
    if day_str:
        try:
            y, m, d = map(int, day_str.split("-"))
            target_day = datetime(y, m, d).date()
        except Exception:
            target_day = today

    # dropdown datasets (same pattern as elsewhere)
    all_properties = Property.objects.all().order_by("property_name")

    units_qs = Unit.objects.all()
    if prop_id:
        units_qs = units_qs.filter(property_id=prop_id)
    filtered_units = units_qs.order_by("unit_number")

    meters_qs = Meter.objects.select_related("unit", "unit__property")
    if unit_id:
        meters_qs = meters_qs.filter(unit_id=unit_id)
    elif prop_id:
        meters_qs = meters_qs.filter(unit__property_id=prop_id)
    filtered_meters = meters_qs.order_by("meter_number")

    # which meters to plot
    selected_meters = filtered_meters
    if meter_id:
        selected_meters = selected_meters.filter(id=meter_id)

    selected_meters = list(selected_meters)
    if not selected_meters:
        return render(request, "smart_meter/hourly_report.html", {
            "all_properties": all_properties,
            "filtered_units": filtered_units,
            "filtered_meters": filtered_meters,
            "current_property": prop_id,
            "current_unit": unit_id,
            "current_meter": meter_id,
            "target_day": target_day,
            "labels": [f"{h:02d}:00" for h in range(24)],
            "usage_series": [],
            "current_series": [],
            "table_rows": [],
        })

    # ----- pull snapshots for that day -----
    day_start = timezone.make_aware(
        datetime(target_day.year, target_day.month, target_day.day, 0, 0, 0), tz)
    day_end = day_start + timedelta(days=1)

    qs = (MeterReading.objects
          .filter(meter__in=selected_meters, ts__gte=day_start, ts__lt=day_end)
          .values("meter_id", "ts", "total_energy", "current_a")
          .order_by("meter_id", "ts"))

    # group per meter → per hour
    # usage_per[meter_id][hour_index] = ΔkWh in that hour
    usage_per = {m.id: [0.0]*24 for m in selected_meters}
    current_per = {m.id: [0.0]*24 for m in selected_meters}

    # accumulate min/max kWh & max current per hour
    by_meter_hour = defaultdict(lambda: defaultdict(
        lambda: {"min": None, "max": None, "max_i": None}))

    for row in qs:
        mid = row["meter_id"]
        ts = row["ts"]
        # normalize to local tz and hour bucket
        ts_local = timezone.localtime(ts, tz)
        h = ts_local.hour

        kwh = Decimal(row["total_energy"] or 0)
        amp = Decimal(row["current_a"] or 0)

        bucket = by_meter_hour[mid][h]
        if bucket["min"] is None or kwh < bucket["min"]:
            bucket["min"] = kwh
        if bucket["max"] is None or kwh > bucket["max"]:
            bucket["max"] = kwh
        if bucket["max_i"] is None or amp > bucket["max_i"]:
            bucket["max_i"] = amp

    for m in selected_meters:
        for h in range(24):
            b = by_meter_hour[m.id].get(h)
            if not b:
                continue
            start = b["min"] or Decimal("0")
            end = b["max"] or Decimal("0")
            delta = end - start
            if delta < 0:
                delta = Decimal("0")
            usage_per[m.id][h] = float(delta)
            current_per[m.id][h] = float(b["max_i"] or 0)

    labels = [f"{h:02d}:00" for h in range(24)]

    # build chart series: one dataset per meter
    usage_series = []
    current_series = []
    for m in selected_meters:
        label = f"{m.meter_number} — {getattr(m.unit, 'unit_number', '')}"
        usage_series.append({"label": label, "data": usage_per[m.id]})
        current_series.append({"label": label, "data": current_per[m.id]})

    # simple table: hour + each meter’s usage (kWh)
    table_rows = []
    for idx, lab in enumerate(labels):
        row = {"hour": lab, "vals": []}
        for m in selected_meters:
            row["vals"].append({
                "label": m.meter_number,
                "usage": usage_per[m.id][idx],
                "current": current_per[m.id][idx],
            })
        table_rows.append(row)

    ctx = {
        "all_properties": all_properties,
        "filtered_units": filtered_units,
        "filtered_meters": filtered_meters,
        "current_property": prop_id,
        "current_unit": unit_id,
        "current_meter": meter_id,

        "target_day": target_day,
        "labels": labels,
        "usage_series": usage_series,
        "current_series": current_series,
        "table_rows": table_rows,
    }
    return render(request, "smart_meter/hourly_report.html", ctx)


# --- add imports at the top of views.py ---

# vendor helpers


class SwitchPowerForm(forms.Form):
    meter = forms.ModelChoiceField(
        queryset=Meter.objects.order_by("meter_number"),
        label="Meter",
        widget=forms.Select(attrs={"class": "form-select"})
    )
    action = forms.ChoiceField(
        choices=[("on", "Turn ON"), ("off", "Turn OFF")],
        widget=forms.RadioSelect
    )


def meter_switch(request):
    """Send ON/OFF to a meter using the vendor frame helper."""
    VIEW_NAME = "meter_switch"
    TEMPLATE_NAME = "smart_meter/control_switch.html"

    form = SwitchPowerForm(request.POST or None)
    context = {"form": form, "result": None}

    if request.method == "POST" and form.is_valid():
        meter = form.cleaned_data["meter"]
        on = form.cleaned_data["action"] == "on"
        byCmd = 0x1C if on else 0x1A
        cmd_name = "ON" if on else "OFF"

        # Build frame
        frame = build_switch_frame(meter.meter_number, byCmd)
        try:
            frame_hex = frame.hex().upper()
        except AttributeError:
            frame_hex = str(frame)

        # ---- AUDIT: request
        logger.info(
            "REQUEST FROM METER_SWITCH view=%s template=%s method=%s user=%s path=%s meter=%s cmd=%s(0x%02X) frame=%s",
            VIEW_NAME,
            TEMPLATE_NAME,
            request.method,
            getattr(request.user, "username", "anonymous"),
            getattr(request, "path", ""),
            meter.meter_number,
            cmd_name,
            byCmd,
            frame_hex,
        )
        # blank line separator

        # Optional feature flag (set DISABLE_CUTOFFS=False in settings for real switching)
        if DISABLE_CUTOFFS:
            res = {"ok": True, "error": None,
                   "payload": "skipped:DISABLE_CUTOFFS"}
            ok = True
            logger.info(
                "RESPONSE view=%s meter=%s cmd=%s ok=%s error=%s payload=%s",
                VIEW_NAME, meter.meter_number, cmd_name,
                res.get("ok"), res.get("error"), res.get("payload"),
            )
        else:
            try:
                secret = getattr(
                    settings, "METER_CTRL_SECRET", None)  # optional
                res = _send_switch(
                    meter_number=meter.meter_number,
                    frame=frame,
                    timeout=32.0,
                    expect_di=None,
                    allow_switch=True,                                 # <-- explicit
                    initiated_by=request.user.get_username(),          # <-- who clicked
                    reason="manual switch from UI",                    # <-- audit
                    auth=secret,                                       # <-- optional shared secret
                )
                ok = bool(res.get("ok"))
                logger.info(
                    "RESPONSE view=%s meter=%s cmd=%s ok=%s error=%s payload=%s",
                    VIEW_NAME, meter.meter_number, cmd_name,
                    res.get("ok"), res.get("error"), res.get("payload"),
                )
            except Exception as e:
                logger.exception(
                    "SEND_FAILED view=%s meter=%s cmd=%s error=%s",
                    VIEW_NAME, meter.meter_number, cmd_name, e
                )
                messages.error(request, f"Failed: {e}")

                return render(request, TEMPLATE_NAME, {**context, "result": {"ok": False, "error": str(e)}})

        # Update state and feedback
        if ok:
            try:
                refresh_live(meter.meter_number)  # best-effort
            except Exception:
                pass
            # Optional: keep Meter.power_status in sync if you use it
            try:
                Meter.objects.filter(pk=meter.pk).update(
                    power_status="on" if on else "off")
            except Exception:
                pass
            messages.success(
                request, f"Command sent. Reply: {res.get('reply', '')}")
        else:
            messages.error(
                request, f"Failed: {res.get('error', 'no reply')} (meter may be busy)")

        context["result"] = res
        logger.info("-------------------------------------")

    return render(request, TEMPLATE_NAME, context)


class PrepaidParamsForm(forms.Form):
    meter = forms.ModelChoiceField(
        queryset=Meter.objects.order_by("meter_number"),
        label="Meter",
        widget=forms.Select(attrs={"class": "form-select"})
    )
    # keep it practical: two prices + two alarm levels + overdraft
    rate1_price = forms.DecimalField(
        label="Rate 1 price (Rs/kWh)", decimal_places=4, max_digits=10, initial=0)
    rate2_price = forms.DecimalField(
        label="Rate 2 price (Rs/kWh)", decimal_places=4, max_digits=10, initial=0)
    alarm1 = forms.DecimalField(
        label="Alarm amount 1 (Rs)", decimal_places=2, max_digits=10, initial=0)
    alarm2 = forms.DecimalField(
        label="Alarm amount 2 (Rs)", decimal_places=2, max_digits=10, initial=0)
    overdraft = forms.DecimalField(
        label="Overdraft limit (Rs)", decimal_places=2, max_digits=10, initial=0)


# smart_meter/views.py

# vendor
# your control client


def prepaid_params(request):
    if request.method == "POST":
        post_meter_id = request.POST.get("meter")
        instance = None
        if post_meter_id:
            try:
                instance = MeterPrepaidSettings.objects.select_related(
                    "meter").get(meter_id=post_meter_id)
            except MeterPrepaidSettings.DoesNotExist:
                instance = None

        form = MeterPrepaidSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            pps = form.save()  # don't shadow django settings
            prepaid = DLT645_2007_Prepaid()
            params = pps.to_vendor_parameters()
            frame = prepaid.build_frame(pps.meter.meter_number, params)
            frame_hex = _as_hex(frame)

            secret = getattr(dj_settings, "METER_CTRL_SECRET",
                             None)  # read from Django settings

            # ---- DEFENSIVE GUARD: ensure we have a callable
            if send_via_db is None:
                messages.error(request, "Control sender unavailable")
                return redirect(request.META.get("HTTP_REFERER") or reverse("smart_meter:prepaid_params"))

            res = _call_send(
                meter_number=pps.meter.meter_number,                 # <-- FIX
                frame=frame,
                timeout=32.0,
                expect_di=None,
                allow_switch=True,
                initiated_by=getattr(
                    request.user, "get_username", lambda: "anonymous")(),
                reason="manual switch from UI",
                auth=secret,
            )
            if res.get("ok"):
                messages.success(
                    request, f"Prepaid parameters sent to {pps.meter.meter_number}.")
            else:
                messages.error(
                    request, f"Failed to send: {res.get('error', 'no reply')}")
            return redirect("smart_meter:prepaid_params")
    else:
        form = MeterPrepaidSettingsForm()

    return render(request, "smart_meter/prepaid_params.html", {"form": form})


@require_POST
def bulk_power_action(request):
    """
    Bulk ON/OFF using the same logic as `meter_switch`.
    POST:
      - action: 'cutoff' | 'restore'
      - scope : 'selected' | 'negative'
      - meters: repeated meter IDs (when scope='selected')
    """
    action = (request.POST.get("action") or "").lower()
    scope = (request.POST.get("scope") or "selected").lower()
    ids = request.POST.getlist("meters")  # <input name="meters" ...>

    if action not in ("cutoff", "restore"):
        messages.error(request, "Invalid bulk action.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("smart_meter:meter_list"))

    # Determine target meters strictly by meter_number (no IP dependency)
    if scope == "negative":
        neg_ids = list(
            LiveReading.objects.filter(
                balance__lt=0).values_list("meter_id", flat=True)
        )
        meters_qs = Meter.objects.filter(id__in=neg_ids)
    else:
        meters_qs = Meter.objects.filter(id__in=ids)

    total = meters_qs.count()
    if total == 0:
        messages.warning(request, "No meters to process.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("smart_meter:meter_list"))

    byCmd = 0x1C if action == "restore" else 0x1A  # 0x1C=ON, 0x1A=OFF
    cmd_name = "ON" if byCmd == 0x1C else "OFF"
    ok_count = 0
    failures = []



    # ---- AUDIT: bulk header
    logger.info(
        "BULK_REQUEST FROM BULK_POWER_ACTION  user=%s path=%s action=%s scope=%s count=%s ids=%s",
        getattr(request.user, "username", "anonymous"),
        getattr(request, "path", ""),
        cmd_name,
        scope,
        total,
        ",".join(map(str, ids)) if ids else "",
    )
    # blank line separator

    for m in meters_qs.iterator():
        try:
            frame = build_switch_frame(m.meter_number, byCmd)
            try:
                frame_hex = frame.hex().upper()
            except AttributeError:
                frame_hex = str(frame)

            # per-meter REQUEST
            logger.info(
                "REQUEST user=%s meter=%s cmd=%s(0x%02X) frame=%s",
                getattr(request.user, "username", "anonymous"),
                m.meter_number,
                cmd_name,
                byCmd,
                frame_hex,
            )
            # blank line separator

            # optional skip in dev/safety
            if DISABLE_CUTOFFS:
                res = {"ok": True, "error": None,
                       "payload": "skipped:DISABLE_CUTOFFS"}
                logger.info(
                    "RESPONSE meter=%s cmd=%s ok=%s error=%s payload=%s",
                    m.meter_number, cmd_name, res.get(
                        "ok"), res.get("error"), res.get("payload")
                )
                # blank line separator

            else:
                secret = getattr(
                    settings, "METER_CTRL_SECRET", None)  # optional

                # ---- DEFENSIVE GUARD: ensure we have a callable
                if send_via_db is None:
                    failures.append(
                        f"{m.meter_number}: control sender unavailable")
                    logger.error("RESPONSE meter=%s cmd=%s ok=%s error=%s payload=%s",
                                 m.meter_number, cmd_name, False, "control sender unavailable", None)
                    continue  # move on to the next meter

                res = _call_send(
                    meter_number=m.meter_number,
                    frame=frame,
                    timeout=32.0,
                    expect_di=None,
                    allow_switch=True,                                 # <-- explicit
                    initiated_by=request.user.get_username(),          # <-- who clicked
                    reason="manual switch from UI",                    # <-- audit
                    auth=secret,                                       # <-- optional shared secret
                )
                logger.info(
                    "RESPONSE meter=%s cmd=%s ok=%s error=%s payload=%s",
                    m.meter_number,
                    cmd_name,
                    res.get("ok"),
                    res.get("error"),
                    res.get("payload"),
                )
                # blank line separator

            if res.get("ok"):
                ok_count += 1
                # best-effort; don't count as failure
                try:
                    refresh_live(m.meter_number)
                except Exception:
                    pass
                # keep UI state if you store it on Meter
                if cmd_name == "OFF":
                    Meter.objects.filter(pk=m.pk).update(power_status="off")
                else:
                    Meter.objects.filter(pk=m.pk).update(power_status="on")
            else:
                failures.append(
                    f"{m.meter_number}: {res.get('error', 'no reply')}")

        except Exception as e:
            logger.exception(
                "SEND_FAILED meter=%s cmd=%s error=%s", m.meter_number, cmd_name, e)
            failures.append(f"{m.meter_number}: {e}")

    # ---- AUDIT: bulk summary
    logger.info(
        "BULK_RESPONSE action=%s total=%s ok=%s failed=%s",
        cmd_name, total, ok_count, len(failures)
    )
    # blank line separator
    logger.info("-------------------------------------")

    if ok_count:
        messages.success(
            request, f"{action.title()} sent to {ok_count}/{total} meter(s).")
    if failures:
        preview = "; ".join(failures[:5])
        more = f" (+{len(failures)-5} more)" if len(failures) > 5 else ""
        messages.error(
            request, f"Failed for {len(failures)} meter(s): {preview}{more}")

    return redirect(request.META.get("HTTP_REFERER") or reverse("smart_meter:meter_list"))


# Import the vendor frame builder (support both layouts)
try:
    from smart_meter.vendor.switch_OnOff import frame_command as build_switch_frame
except Exception:
    # fallback if no vendor/ folder
    from smart_meter.switch_OnOff import frame_command as build_switch_frame


def switch_lab(request):
    """
    Build (and optionally send) the vendor ON/OFF frame purely from meter_number.
    """
    VIEW_NAME = "switch_lab"
    TEMPLATE_NAME = "smart_meter/switch_lab.html"

    form = SwitchLabForm(request.POST or None)
    result = None
    send_result = None

    if request.method == "POST" and form.is_valid():
        meter_hex = form.cleaned_data["meter_number"]
        byCmd = 0x1C if form.cleaned_data["action"] == "on" else 0x1A
        cmd_name = "ON" if byCmd == 0x1C else "OFF"

        # Build frame via vendor function
        frame = build_switch_frame(meter_hex, byCmd)
        try:
            frame_hex = frame.hex().upper()
        except AttributeError:
            frame_hex = str(frame)

        # ---- AUDIT: request
        logger.info(
            "REQUEST FROM SWITCH_LAB view=%s template=%s method=%s user=%s path=%s meter=%s cmd=%s(0x%02X) frame=%s preview_only=%s",
            VIEW_NAME,
            TEMPLATE_NAME,
            request.method,
            getattr(request.user, "username", "anonymous"),
            getattr(request, "path", ""),
            meter_hex,
            cmd_name,
            byCmd,
            frame_hex,
            form.cleaned_data.get("preview_only"),
        )
        # blank line separator

        # Prepare preview info for the page
        result = {
            "cmd": cmd_name,
            "meter": meter_hex,
            "length": len(frame),
            "hex": frame_hex,
        }

        # Optionally send via listener (unless preview_only)
        if not form.cleaned_data.get("preview_only"):
            if DISABLE_CUTOFFS:
                send_result = {"ok": True, "error": None,
                               "payload": "skipped:DISABLE_CUTOFFS"}
                logger.info(
                    "RESPONSE view=%s meter=%s cmd=%s ok=%s error=%s payload=%s",
                    VIEW_NAME, meter_hex, cmd_name,
                    send_result.get("ok"),
                    send_result.get("error"),
                    send_result.get("payload"),
                )
                # blank line separator

            else:
                try:
                    send_result = send_via_db(
                        meter_hex, frame, timeout=32.0)
                    logger.info(
                        "RESPONSE view=%s meter=%s cmd=%s ok=%s error=%s payload=%s",
                        VIEW_NAME, meter_hex, cmd_name,
                        send_result.get("ok"),
                        send_result.get("error"),
                        send_result.get("payload"),
                    )
                    # blank line separator

                except Exception as e:
                    send_result = {"ok": False, "error": str(e)}
                    logger.exception(
                        "SEND_FAILED view=%s meter=%s cmd=%s error=%s",
                        VIEW_NAME, meter_hex, cmd_name, e
                    )

            # Flash UI messages (optional)
            if send_result.get("ok"):
                messages.success(request, "Command sent successfully.")
            else:
                messages.error(
                    request, f"Send failed: {send_result.get('error', 'no reply')}")

        # blank line separator
        logger.info("-------------------------------------")

    return render(request, TEMPLATE_NAME, {
        "form": form,
        "result": result,
        "send_result": send_result,
    })


# smart_meter/views.py


@login_required
# Optional: restrict who can add readings
@permission_required("smart_meter.add_MeterReading", raise_exception=True)
def meter_reading_create(request):
    """
    Create a manual reading. Redirect back to the listing, preserving filters.
    """
    # Preserve filters / return path
    next_qs = request.GET.urlencode() or request.META.get("QUERY_STRING", "")
    # explicit ?next=/smart_meter/readings...
    return_to = request.GET.get("next") or reverse("smart_meter:reading_list")
    if request.method == "POST":
        form = ReadingManualForm(request.POST, request=request)
        if form.is_valid():
            obj = form.save(commit=False)
            # obj.created_by = request.user  # if you have this field
            obj.save()
            messages.success(request, "Manual reading added.")
            return redirect(return_to)
    else:
        form = ReadingManualForm(request=request)

    return render(request, "smart_meter/reading_form.html", {"form": form, "return_to": return_to})


# smart_meter/views.py


@login_required
@permission_required("smart_meter.change_meterreading", raise_exception=True)
def meter_reading_row_edit(request, pk):
    r = get_object_or_404(MeterReading, pk=pk)

    # Cancel returns display row
    if request.GET.get("cancel"):
        return render(request, "smart_meter/partials/reading_row_display.html", {"r": r})

    if request.method == "POST":
        form = ReadingManualForm(
            request.POST, instance=r, request=request, prefix=f"r{r.pk}")
        if form.is_valid():
            form.save()
            # return the DISPLAY <tr> so the row snaps back
            return render(request, "smart_meter/partials/reading_row_display.html", {"r": r})
        # invalid -> return EDIT <tr> with errors
        return render(request, "smart_meter/partials/reading_row_edit.html", {"r": r, "form": form}, status=400)

    # GET -> return EDIT <tr>
    form = ReadingManualForm(instance=r, request=request, prefix=f"r{r.pk}")
    return render(request, "smart_meter/partials/reading_row_edit.html", {"r": r, "form": form})


@login_required
@permission_required("smart_meter.delete_meterreading", raise_exception=True)
@require_POST
def meter_reading_delete(request, pk):
    r = get_object_or_404(MeterReading, pk=pk)
    r.delete()
    # Redirect back to the same page (with the same filters)
    current_url = request.headers.get(
        "HX-Current-URL") or request.META.get("HTTP_REFERER") or reverse("smart_meter:reading_list")
    resp = HttpResponse(status=204)          # no content needed
    resp["HX-Redirect"] = current_url        # HTMX does a client-side redirect
    return resp


@login_required
@permission_required("smart_meter.change_meterreading", raise_exception=True)
def meter_reading_row(request, pk):
    r = get_object_or_404(MeterReading, pk=pk)
    return render(request, "smart_meter/partials/reading_row_display.html", {"r": r})

# views.py


@require_POST
def reset_meter_display_balance(request, meter_id):
    meter = get_object_or_404(Meter, pk=meter_id)
    frame_bytes = build_amount_init_frame(meter.meter_number, 0.00)

    res = _call_send(
        meter_number=meter.meter_number,
        frame=frame_bytes,  # send raw bytes; helper will adapt
        timeout=35.0,
        expect_di=None,
        initiated_by=request.user.get_username() if hasattr(request.user, "get_username") else "anonymous",
        reason="set display balance to 0.00",
        auth=getattr(settings, "METER_CTRL_SECRET", None),
    )

    if res.get("ok"):
        messages.success(request, f"Reset to 0.00 sent to {meter.meter_number}.")
    else:
        messages.error(request, f"Reset failed: {res.get('error', 'no reply')}")
    return redirect(request.META.get("HTTP_REFERER", "/"))

@require_POST
def set_meter_display_balance(request, meter_id):
    meter = get_object_or_404(Meter, pk=meter_id)

    try:
        amt = Decimal(request.POST.get("amount", "0"))
    except Exception:
        messages.error(request, "Invalid amount.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    if amt < 0:
        messages.error(request, "Amount must be ≥ 0.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    frame_bytes = build_amount_init_frame(meter.meter_number, float(amt))

    res = _call_send(
        meter_number=meter.meter_number,
        frame=frame_bytes,  # raw bytes again
        timeout=35.0,
        expect_di=None,
        initiated_by=request.user.get_username() if hasattr(request.user, "get_username") else "anonymous",
        reason=f"set display balance to {amt:.2f}",
        auth=getattr(settings, "METER_CTRL_SECRET", None),
    )

    if res.get("ok"):
        messages.success(request, f"Set {amt:.2f} sent to {meter.meter_number}.")
    else:
        messages.error(request, f"Set failed: {res.get('error', 'no reply')}")
    return redirect(request.META.get("HTTP_REFERER", "/"))

# smart_meter/views.py
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta
from django.db.models import Q

def _fmt(v, decimals=None):
    if v is None:
        return ""
    try:
        if decimals is None:
            return str(v)
        return f"{float(v):.{decimals}f}"
    except Exception:
        return str(v)

def _ts_iso(dt):
    return dt.isoformat() if dt else ""

def live_custom_data(request):
    # keep filters identical to live_custom
    q = (request.GET.get("q") or "").strip()
    offline_only = (request.GET.get("offline") == "1")

    (selected_meters,
     all_properties, filtered_units, filtered_meters,
     prop_id, unit_id, meter_id) = _filtered_meter_sets(request)

    qs = (
        LiveReading.objects
        .select_related("meter", "meter__unit", "meter__unit__property")
        .filter(meter__in=selected_meters)
        .order_by("meter__unit__property__property_name",
                  "meter__unit__unit_number",
                  "meter__meter_number")
    )

    if q:
        qs = qs.filter(
            Q(meter__unit__unit_number__icontains=q) |
            Q(meter__meter_number__icontains=q) |
            Q(meter__unit__property__property_name__icontains=q)
        )

    cutoff = timezone.now() - timedelta(minutes=ONLINE_MINUTES)

    payload = []
    for r in qs:
        is_online = bool(r.ts and r.ts >= cutoff)
        if offline_only and is_online:
            continue

        m = r.meter
        u = m.unit
        p = u.property

        payload.append({
            "meter_id": m.id,
            "is_online": is_online,
            "power_status": (m.power_status or "OFF").upper(),

            # values that map to your table columns
            "property_name": p.property_name or "",
            "property_short": (p.property_name or "")[:8],
            "unit_number": u.unit_number or "",
            "meter_number": m.meter_number or "",

            "updated_ts": _ts_iso(r.ts),
            # optional: pre-formatted display strings
            "source_ip": r.source_ip or "",
            "port": r.source_port or "",
            "balance": _fmt(r.balance, 2),
            "total_energy": _fmt(r.total_energy, 3),
            "voltage_a": _fmt(r.voltage_a, 1),
            "current_a": _fmt(r.current_a, 3),
            "total_power": _fmt(r.total_power, 3),
            "pf_total": _fmt(r.pf_total, 3),
        })

    return JsonResponse({"rows": payload, "online_minutes": ONLINE_MINUTES})
