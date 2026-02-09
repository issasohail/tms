from django.utils import timezone
from datetime import timedelta


def apply_date_range_filter(
    qs,
    *,
    date_field: str,
    date_range: str | None,
    start_date: str | None,
    end_date: str | None,
):
    """
    Apply date filters to any queryset using:
      - presets (today, yesterday, this_week, last_week, this_month, last_month, this_year)
      - OR manual start_date / end_date

    date_field: model field name (e.g. 'payment_date', 'date')
    """

    today = timezone.now().date()

    # ----- Preset ranges -----
    if date_range and date_range != "all":
        if date_range == "today":
            return qs.filter(**{date_field: today})

        if date_range == "yesterday":
            y = today - timedelta(days=1)
            return qs.filter(**{date_field: y})

        if date_range == "this_week":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
            return qs.filter(**{f"{date_field}__range": (start, end)})

        if date_range == "last_week":
            start = today - timedelta(days=today.weekday() + 7)
            end = start + timedelta(days=6)
            return qs.filter(**{f"{date_field}__range": (start, end)})

        if date_range == "this_month":
            start = today.replace(day=1)
            end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            return qs.filter(**{f"{date_field}__range": (start, end)})

        if date_range == "last_month":
            first_this = today.replace(day=1)
            end = first_this - timedelta(days=1)
            start = end.replace(day=1)
            return qs.filter(**{f"{date_field}__range": (start, end)})

        if date_range == "this_year":
            start = today.replace(month=1, day=1)
            end = today.replace(month=12, day=31)
            return qs.filter(**{f"{date_field}__range": (start, end)})

    # ----- Manual range -----
    if start_date:
        qs = qs.filter(**{f"{date_field}__gte": start_date})
    if end_date:
        qs = qs.filter(**{f"{date_field}__lte": end_date})

    return qs
