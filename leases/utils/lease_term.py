from datetime import date, timedelta

from dateutil.relativedelta import relativedelta


DEFAULT_LEASE_MONTHS = 11


def calculate_lease_end_date(start_date: date, lease_months: int) -> date:
    """Return one day before the same calendar day after the agreement term."""
    if not start_date:
        raise ValueError("A lease start date is required.")
    try:
        months = int(lease_months)
    except (TypeError, ValueError) as exc:
        raise ValueError("Agreement term must be a whole number of months.") from exc
    if months < 1:
        raise ValueError("Agreement term must be at least one month.")
    return start_date + relativedelta(months=months) - timedelta(days=1)


def infer_lease_months(start_date: date, end_date: date, default=DEFAULT_LEASE_MONTHS) -> int:
    """Infer the closest stored term for historical rows without changing their dates."""
    if not start_date or not end_date or end_date < start_date:
        return default
    target = end_date + timedelta(days=1)
    months = (target.year - start_date.year) * 12 + target.month - start_date.month
    if target.day < start_date.day:
        months -= 1
    return max(1, months or default)
