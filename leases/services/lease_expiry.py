"""Shared lease-expiry countdown rules used by lease and tenant lists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from django.utils import timezone

LEASE_EXPIRY_NOTICE_DAYS = 60
ELIGIBLE_LEASE_STATUSES = frozenset({"active"})


@dataclass(frozen=True)
class LeaseExpiryCountdown:
    days_left: int
    label: str


def get_lease_expiry_countdown(lease, *, today: Optional[date] = None):
    """Return the shared countdown value for an eligible lease, otherwise ``None``.

    The inclusive 60-day window is intentional: a lease ending exactly 60 days
    from the application's local date is included.
    """
    end_date = getattr(lease, "end_date", None)
    status = getattr(lease, "status", None)

    if not end_date or status not in ELIGIBLE_LEASE_STATUSES:
        return None

    local_today = today or timezone.localdate()
    days_left = (end_date - local_today).days
    if not 0 <= days_left <= LEASE_EXPIRY_NOTICE_DAYS:
        return None

    unit = "day" if days_left == 1 else "days"
    return LeaseExpiryCountdown(days_left=days_left, label=f"{days_left} {unit} left")


def attach_lease_expiry_countdown(lease, *, today: Optional[date] = None):
    """Attach template-ready countdown attributes without issuing any queries."""
    countdown = get_lease_expiry_countdown(lease, today=today)
    lease.expiry_days_left = countdown.days_left if countdown else None
    lease.expiry_countdown_label = countdown.label if countdown else ""
    return countdown
