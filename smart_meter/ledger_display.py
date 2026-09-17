"""Shared display, filtering, and export data for the two meter ledgers."""

from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models.functions import TruncDate
from django.utils import timezone

from smart_meter.models import MeterPrepaidRecharge, MeterRawFrame, MeterReading
from smart_meter.services.prepaid_money import decode_manufacturer_charge_frame


PERIODS = (
    ("all", "All dates"), ("this_week", "This week"),
    ("last_week", "Last week"), ("this_month", "This month"),
    ("last_month", "Last month"), ("this_quarter", "This quarter"),
    ("last_quarter", "Last quarter"), ("this_year", "This year"),
    ("last_year", "Last year"), ("custom", "Custom period"),
)


def _period_dates(period, today):
    if period in ("this_week", "last_week"):
        start = today - timedelta(days=today.weekday())
        if period == "last_week":
            start -= timedelta(days=7)
        return start, start + timedelta(days=6)
    if period in ("this_month", "last_month"):
        year, month = today.year, today.month
        if period == "last_month":
            year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return start, end - timedelta(days=1)
    if period in ("this_quarter", "last_quarter"):
        year = today.year
        quarter = (today.month - 1) // 3
        if period == "last_quarter":
            quarter -= 1
            if quarter < 0:
                year, quarter = year - 1, 3
        start = date(year, quarter * 3 + 1, 1)
        end = date(year + 1, 1, 1) if quarter == 3 else date(year, quarter * 3 + 4, 1)
        return start, end - timedelta(days=1)
    if period in ("this_year", "last_year"):
        year = today.year - (period == "last_year")
        return date(year, 1, 1), date(year, 12, 31)
    return None, None


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def ledger_filter(request):
    period = request.GET.get("period", "all")
    if period not in dict(PERIODS):
        period = "all"
    start, end = _period_dates(period, timezone.localdate())
    if period in ("all", "custom"):
        start = _parse_date(request.GET.get("from_date"))
        end = _parse_date(request.GET.get("to_date"))
        if period == "all" and (start or end):
            period = "custom"
    if start and end and start > end:
        start, end = end, start
    return {"period": period, "from_date": start, "to_date": end, "periods": PERIODS}


def _filter_dates(queryset, field, start, end):
    if start:
        queryset = queryset.filter(**{field + "__date__gte": start})
    if end:
        queryset = queryset.filter(**{field + "__date__lte": end})
    return queryset


def _page_groups(queryset, parameter, request, page_size, export):
    field, page_parameter = parameter
    days = (queryset.annotate(ledger_day=TruncDate(field))
            .values_list("ledger_day", flat=True).distinct().order_by("-ledger_day"))
    paginator = Paginator(days, page_size)
    page = paginator.get_page(request.GET.get(page_parameter))
    items = list(queryset if export else queryset.filter(**{field + "__date__in": list(page.object_list)}))
    groups = OrderedDict()
    for item in items:
        key = timezone.localtime(getattr(item, field)).date().isoformat()
        groups.setdefault(key, []).append(item)
    grouped = list(groups.values())
    offset = 0 if export else page.start_index() - 1
    result = []
    for day_number, group in enumerate(grouped, offset + 1):
        for sub_number, item in enumerate(group, 1):
            item.day_key = timezone.localtime(getattr(item, field)).date().isoformat()
            item.day_number = day_number
            item.sub_number = sub_number
            item.is_daily_latest = sub_number == 1
            item.day_has_more = len(group) > 1
            result.append(item)
    return result, page


def build_ledger_data(request, meter, *, export=False):
    filters = ledger_filter(request)
    start, end = filters["from_date"], filters["to_date"]
    readings_qs = _filter_dates(
        MeterReading.objects.filter(meter=meter, balance__isnull=False), "ts", start, end
    ).order_by("-ts", "-pk")
    readings, reading_page = _page_groups(readings_qs, ("ts", "reading_page"), request, 7, export)
    # The oldest visible movement still needs the reading immediately before the period.
    previous = None
    if readings:
        oldest = readings[-1]
        from django.db.models import Q
        previous = (MeterReading.objects.filter(meter=meter, balance__isnull=False)
                    .filter(Q(ts__lt=oldest.ts) | Q(ts=oldest.ts, pk__lt=oldest.pk))
                    .order_by("-ts", "-pk").first())
    for reading in reversed(readings):
        reading.balance_change = None
        reading.energy_change = None
        reading.estimated_charge = None
        if previous is not None:
            reading.balance_change = reading.balance - previous.balance
            current_energy = reading.forward_active_energy_kwh if reading.forward_active_energy_kwh is not None else reading.total_energy
            previous_energy = previous.forward_active_energy_kwh if previous.forward_active_energy_kwh is not None else previous.total_energy
            if current_energy is not None and previous_energy is not None:
                reading.energy_change = current_energy - previous_energy
                if reading.unit_rate is not None and reading.energy_change >= 0:
                    reading.estimated_charge = (reading.energy_change * reading.unit_rate).quantize(Decimal("0.01"))
        previous = reading
    reading_page.ledger_range = reading_page.paginator.get_elided_page_range(reading_page.number, on_each_side=2, on_ends=1)

    transactions_qs = _filter_dates(
        MeterPrepaidRecharge.objects.filter(pilot__meter=meter).select_related("created_by"),
        "created_at", start, end,
    ).order_by("-created_at", "-pk")
    transaction_paginator = Paginator(transactions_qs, 20)
    transaction_page = transaction_paginator.get_page(request.GET.get("transaction_page"))
    transaction_page.ledger_range = transaction_page.paginator.get_elided_page_range(transaction_page.number, on_each_side=2, on_ends=1)
    transactions = list(transactions_qs if export else transaction_page.object_list)
    for index, item in enumerate(transactions, 1 if export else transaction_page.start_index()):
        item.serial_number = index
        try:
            item.operation_label = decode_manufacturer_charge_frame(item.raw_command)["operation"].replace("recharge", "top up").title()
        except (TypeError, ValueError):
            item.operation_label = "Transaction"

    frames_qs = _filter_dates(
        MeterRawFrame.objects.filter(meter=meter, data_identifier="028011FF"),
        "received_at", start, end,
    ).order_by("-received_at", "-pk")
    frames, frame_page = _page_groups(frames_qs, ("received_at", "frame_page"), request, 7, export)
    frame_page.ledger_range = frame_page.paginator.get_elided_page_range(frame_page.number, on_each_side=2, on_ends=1)
    for frame in frames:
        frame.reported_balance = (frame.decoded_data or {}).get("balance")
        frame.reported_energy = (frame.decoded_data or {}).get("forward_active_energy_kwh") or (frame.decoded_data or {}).get("total_energy")
        frame.observation_day_key = frame.day_key
        frame.observation_day_number = frame.day_number
        frame.observation_sub_number = frame.sub_number

    page_parameters = ("reading_page", "transaction_page", "frame_page")
    page_queries = {}
    for key in page_parameters:
        query = request.GET.copy()
        query.pop(key, None)
        page_queries[key.replace("_page", "_query")] = query.urlencode()
    query = request.GET.copy()
    for key in page_parameters:
        query.pop(key, None)
    return {
        **filters, "readings": readings, "reading_page": reading_page,
        "has_collapsible_readings": any(reading.day_has_more for reading in readings),
        "transactions": transactions, "transaction_page": transaction_page,
        "raw_balance_frames": frames, "frame_page": frame_page,
        "ledger_query": query.urlencode(),
        **page_queries,
    }
