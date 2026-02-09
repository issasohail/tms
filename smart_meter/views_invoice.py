# ================================
# 2) smart_meter/views_invoice.py
# ================================
from datetime import date
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.urls import reverse
from invoices.models import Invoice, ItemCategory
from smart_meter.models import Meter
from leases.models import Lease
from leases.models import Lease
from smart_meter.models import Meter
from .services.invoicing import compute_electric_bill, upsert_invoice_with_electric_item, ElectricBillContext
from invoices.models import Invoice, ItemCategory
from django.utils import timezone
from django.contrib import messages
# views_invoice.py (imports)
from datetime import date
from calendar import monthrange
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from leases.models import Lease
from smart_meter.models import Meter


@login_required
def electric_bill_preview(request: HttpRequest, lease_id: int, meter_id: int) -> HttpResponse:
    """Preview + inline edit for the electric bill line before committing to invoice (single meter)."""
    lease = get_object_or_404(Lease, pk=lease_id)
    meter = get_object_or_404(Meter, pk=meter_id)

    month = request.GET.get("month")  # YYYY-MM
    if not month:
        return HttpResponseBadRequest("Missing ?month=YYYY-MM")
    y, m = map(int, month.split("-"))
    # Compute calendar month
    from calendar import monthrange
    period_start = date(y, m, 1)
    period_end = date(y, m, monthrange(y, m)[1])

    ctx = compute_electric_bill(lease, meter, period_start, period_end)

    # Allow inline overrides via GET (for quick what-ifs)
    override_rate = request.GET.get("unit_rate")
    override_svc = request.GET.get("service_charges")
    if override_rate is not None:
        try:
            ctx.unit_rate = Decimal(override_rate)
        except Exception:
            pass
    if override_svc is not None:
        try:
            ctx.service_charges = Decimal(override_svc)
        except Exception:
            pass

    return render(request, "smart_meter/electric_bill_preview.html", {"ctx": ctx})


@login_required
def electric_bill_commit(request: HttpRequest, lease_id: int, meter_id: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    lease = get_object_or_404(Lease, pk=lease_id)
    meter = get_object_or_404(Meter, pk=meter_id)

    # Posted fields from the preview form
    y, m = map(int, request.POST.get("month").split("-"))
    from calendar import monthrange
    period_start = date(y, m, 1)
    period_end = date(y, m, monthrange(y, m)[1])

    beg = Decimal(request.POST.get("beg_kwh") or "0")
    end = Decimal(request.POST.get("end_kwh") or "0")
    units = max(end - beg, Decimal("0"))
    unit_rate = Decimal(request.POST.get("unit_rate") or "0")
    service_charges = Decimal(request.POST.get("service_charges") or "0")

    # Build a context using the edited values (now using ElectricBillContext to avoid attr errors)
    ctx = ElectricBillContext(
        lease=lease,
        meter=meter,
        period_start=period_start,
        period_end=period_end,
        beg_kwh=beg,
        end_kwh=end,
        units=units,
        unit_rate=unit_rate,
        service_charges=service_charges,
    )

    inv = upsert_invoice_with_electric_item(ctx, item_category_id=7)

    # After commit, jump to invoice detail (admin or public)
    try:
        return redirect(reverse("invoice_update", kwargs={"pk": inv.pk}))

    except Exception:
        return redirect("/invoices/%d/" % inv.id)


def _next_month_start(d: date) -> date:
    return date(d.year + (1 if d.month == 12 else 0),
                1 if d.month == 12 else d.month + 1, 1)
# ---------- Bulk (All meters, monthly) --------------------------------------


@login_required
def electric_bill_bulk_preview(request: HttpRequest) -> HttpResponse:
    """
    Show a monthly, per-meter preview similar to your monthly report.
    - For meters with an existing electric item for that month → show Old vs New with actions (Keep / Replace)
    - For meters without an item → ready to Append
    Inline edit supported per row (rate, service, beg/end → recalculates total on the fly).
    """
    # Parse month from ?month=YYYY-MM or from ?start/&end (must be same month)
    month = (request.GET.get("month") or "").strip()
    if not month and request.GET.get("start") and request.GET.get("end"):
        try:
            s = date.fromisoformat(request.GET["start"])
            e = date.fromisoformat(request.GET["end"])
            if s.year == e.year and s.month == e.month:
                month = f"{s:%Y-%m}"
        except Exception:
            pass
    if not month:
        return HttpResponseBadRequest("Missing or invalid month (use ?month=YYYY-MM or matching start/end)")

    y, m = map(int, month.split("-"))
    from calendar import monthrange
    period_start = date(y, m, 1)
    period_end = date(y, m, monthrange(y, m)[1])

    # Only allow fully past months (server-side guard)
    today = timezone.localdate()
    if period_end >= today:
        messages.error(
            request, "You can add to invoice only after the month has passed.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # Filter set: property/unit optional; all meters by default from ?month=YYYY-MM or from ?start/&end (must be same month)
    month = (request.GET.get("month") or "").strip()
    if not month and request.GET.get("start") and request.GET.get("end"):
        try:
            s = date.fromisoformat(request.GET["start"])
            e = date.fromisoformat(request.GET["end"])
            if s.year == e.year and s.month == e.month:
                month = f"{s:%Y-%m}"
        except Exception:
            pass
    if not month:
        return HttpResponseBadRequest("Missing or invalid month (use ?month=YYYY-MM or matching start/end)")

    y, m = map(int, month.split("-"))
    from calendar import monthrange
    period_start = date(y, m, 1)
    period_end = date(y, m, monthrange(y, m)[1])

    # Filter set: property/unit optional; all meters by default
    from properties.models import Property, Unit
    meters = Meter.objects.select_related("unit", "unit__property").all()
    prop_id = (request.GET.get("property") or "").strip()
    unit_id = (request.GET.get("unit") or "").strip()
    if prop_id:
        meters = meters.filter(unit__property_id=prop_id)
    if unit_id:
        meters = meters.filter(unit_id=unit_id)

    rows = []
    duplicates = []

    try:
        category = ItemCategory.objects.get(pk=7)
    except ItemCategory.DoesNotExist:
        category = None

    for meter in meters:
        # Find overlapping lease for that unit and month
        lease = (
            Lease.objects
            .filter(unit=meter.unit, start_date__lte=period_end, end_date__gte=period_start)
            .order_by("-start_date")
            .first()
        )
        if not lease:
            continue

        ctx = compute_electric_bill(lease, meter, period_start, period_end)

        # Find existing invoice for the month
        posting_month = _next_month_start(period_start)
        inv = (Invoice.objects
               .filter(lease=lease,
                       issue_date__year=posting_month.year,
                       issue_date__month=posting_month.month)
               .order_by("issue_date").first())

        existing_item = None
        if inv and category:
            existing_item = inv.items.filter(category=category, description__icontains=f"Meter#={meter.meter_number}")\
                                     .filter(description__icontains=f"Billing Period={ctx.billing_period_label}").first()

        row = {
            "lease": lease,
            "meter": meter,
            "ctx": ctx,
            "invoice": inv,
            "existing": existing_item,
        }
        if existing_item:
            duplicates.append(row)
        else:
            rows.append(row)

    return render(request, "smart_meter/electric_bill_bulk_preview.html", {
        "month": month,
        "period_start": period_start,
        "period_end": period_end,
        "rows": rows,
        "duplicates": duplicates,
    })


@login_required
def electric_bill_bulk_commit(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    # Expect a multi-row post: fields like action-<meter_id>, beg-<meter_id>, end-<meter_id>, rate-<meter_id>, svc-<meter_id>, lease-<meter_id>
    month = request.POST.get("month")
    if not month:
        return HttpResponseBadRequest("Missing month")
    y, m = map(int, month.split("-"))
    from calendar import monthrange
    period_start = date(y, m, 1)
    period_end = date(y, m, monthrange(y, m)[1])

    acted = {"appended": [], "replaced": [], "skipped": []}

    # Iterate over posted meter ids
    meter_ids = [k.split("-", 1)[1]
                 for k in request.POST.keys() if k.startswith("action-")]

    for mid in meter_ids:
        action = request.POST.get(f"action-{mid}")  # append | replace | skip
        lease_id = request.POST.get(f"lease-{mid}")
        meter_id = int(mid)
        beg = Decimal(request.POST.get(f"beg-{mid}") or "0")
        end = Decimal(request.POST.get(f"end-{mid}") or "0")
        units = max(end - beg, Decimal("0"))
        rate = Decimal(request.POST.get(f"rate-{mid}") or "0")
        svc = Decimal(request.POST.get(f"svc-{mid}") or "0")

        if action == "skip":
            acted["skipped"].append(meter_id)
            continue

        lease = get_object_or_404(Lease, pk=lease_id)
        meter = get_object_or_404(Meter, pk=meter_id)

        ctx = ElectricBillContext(
            lease=lease,
            meter=meter,
            period_start=period_start,
            period_end=period_end,
            beg_kwh=beg,
            end_kwh=end,
            units=units,
            unit_rate=rate,
            service_charges=svc,
        )

        upsert_invoice_with_electric_item(ctx, item_category_id=7)
        acted["replaced" if action ==
              "replace" else "appended"].append(meter_id)

    messages.success(request, "Electric bill posting complete.")
    return redirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def electric_bill_preview_by_meter(request, meter_id: int):
    """
    Resolve the lease from (meter.unit, month), enforce 'past month' rule,
    then redirect to the single-meter preview: electric_bill_preview(lease_id, meter_id)?month=YYYY-MM
    """
    # Accept ?month=YYYY-MM OR matching ?start=YYYY-MM-DD&end=YYYY-MM-DD
    month = (request.GET.get("month") or "").strip()
    if not month and request.GET.get("start") and request.GET.get("end"):
        try:
            s = date.fromisoformat(request.GET["start"])
            e = date.fromisoformat(request.GET["end"])
            if s.year == e.year and s.month == e.month:
                month = f"{s:%Y-%m}"
        except Exception:
            pass

    if not month:
        messages.error(
            request, "Missing or invalid month (use ?month=YYYY-MM or matching start/end).")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    y, m = map(int, month.split("-"))
    period_start = date(y, m, 1)
    period_end = date(y, m, monthrange(y, m)[1])

    # Must be a fully past month
    today = timezone.localdate()
    if period_end >= today:
        messages.error(
            request, "You can add to invoice only after the month has passed.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    meter = get_object_or_404(Meter, pk=meter_id)

    # Find a lease overlapping that month for the meter's unit
    lease = (
        Lease.objects
        .filter(
            unit=meter.unit,
            start_date__lte=period_end,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=period_start))
        .order_by("-start_date")
        .first()
    )
    if not lease:
        messages.error(
            request, "No active lease found for this meter/unit in the selected month.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    url = reverse("smart_meter:electric_bill_preview",
                  args=[lease.id, meter.id])
    return redirect(f"{url}?month={month}")
