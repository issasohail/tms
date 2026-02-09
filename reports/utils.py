# reports/utils.py
from datetime import date, timedelta
from calendar import monthrange


def date_preset_range(preset: str):
    today = date.today()
    if preset == 'today':
        return today, today
    if preset == 'yesterday':
        y = today - timedelta(days=1)
        return y, y
    if preset == 'this_week':
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end
    if preset == 'last_week':
        end = today - timedelta(days=today.weekday()+1)
        start = end - timedelta(days=6)
        return start, end
    if preset == 'this_month':
        start = today.replace(day=1)
        end = today.replace(day=monthrange(today.year, today.month)[1])
        return start, end
    if preset == 'last_month':
        m = today.month - 1 or 12
        y = today.year - (1 if m == 12 else 0)
        start = date(y, m, 1)
        end = date(y, m, monthrange(y, m)[1])
        return start, end
    if preset == 'this_year':
        start = date(today.year, 1, 1)
        end = date(today.year, 12, 31)
        return start, end
    return None  # custom
