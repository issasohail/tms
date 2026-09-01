"""
Move-out final settlement billing.

Triggered when a lease's status becomes "ended"/"terminated", or when the
end_date is changed on a lease that is already ended/terminated. If the
lease has bill_water_charges=True and its unit has a smart meter, this
computes/posts a final electric charge (from the last billed period through
the new end_date) and lets the caller attach a manually-entered final water
charge, both posted onto the CURRENT month's invoice for the lease.

Used from:
  - leases/views.py (LeaseUpdateView) — interactive preview + confirm modal
  - leases/admin.py (LeaseAdminForm/LeaseAdmin) — blocking validation +
    a plain "final water amount" field, since admin has no JS modal step

Kept as its own module (rather than folded into leases/utils/billing.py or
invoices/services.py) so it's easy to find, test, and reuse from both call
sites without adding import-order risk to either of those larger files.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

ENDED_STATUSES = ("ended", "terminated")


def _reading_date(reading):
    ts = reading.ts
    if timezone.is_aware(ts):
        ts = timezone.localtime(ts)
    return ts.date()


def lease_is_ending(status: str) -> bool:
    return status in ENDED_STATUSES


def move_out_billing_trigger(old_lease, new_lease) -> bool:
    """True if this particular save should run the move-out settlement check.

    Fires when:
      - status just became ended/terminated (wasn't before), OR
      - status was already ended/terminated AND the end_date changed on
        this save (e.g. correcting the final date after the fact).
    """
    was_ending = lease_is_ending(old_lease.status)
    is_ending = lease_is_ending(new_lease.status)
    end_date_changed = old_lease.end_date != new_lease.end_date

    if is_ending and not was_ending:
        return True
    if is_ending and was_ending and end_date_changed:
        return True
    return False


def move_out_billing_applicable(lease) -> bool:
    """Only relevant if the lease bills water AND the unit has a smart meter."""
    unit = getattr(lease, "unit", None)
    return bool(
        getattr(lease, "bill_water_charges", False)
        and getattr(unit, "is_smart_meter", False)
    )


def _electric_installations_for_lease(lease, as_of: date):
    from smart_meter.models import MeterInstallation

    return (
        MeterInstallation.objects.filter(
            unit_id=lease.unit_id,
            meter__meter_type="electric",
            meter__billing_mode="postpaid",
            start_date__lte=as_of,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=as_of))
        .select_related("meter")
    )


def _electric_period_bounds(lease, end_date: date):
    """Period for the final electric reading: start of the month the lease
    ends in, through the end_date itself. Matches the normal monthly-run
    convention (calendar-month electric periods), just capped early."""
    period_start = end_date.replace(day=1)
    return period_start, end_date


def build_move_out_settlement_preview(lease, end_date: date | None = None) -> dict:
    """Non-mutating. Figures out whether the final settlement can proceed,
    and if so, computes an electric charge preview (does not write to DB).
    """
    from smart_meter.models import MeterReading

    end_date = end_date or lease.end_date or timezone.localdate()

    result = {
        "applicable": move_out_billing_applicable(lease),
        "blocked": False,
        "block_reason": "",
        "latest_reading_date": None,
        "installations": [],
        "electric_preview": None,
        "end_date": end_date,
    }
    if not result["applicable"]:
        return result

    installations = list(_electric_installations_for_lease(lease, end_date))
    result["installations"] = installations

    if not installations:
        result["blocked"] = True
        result["block_reason"] = (
            "No active electric smart-meter installation found for this "
            "lease as of the new end date."
        )
        return result

    latest = None
    for installation in installations:
        reading = (
            MeterReading.objects.filter(meter=installation.meter)
            .order_by("-ts")
            .first()
        )
        if reading and (latest is None or reading.ts > latest.ts):
            latest = reading

    if not latest:
        result["blocked"] = True
        result["block_reason"] = (
            "No meter reading found at all for this lease's electric meter. "
            "Add a reading before finalizing this move-out."
        )
        return result

    latest_date = _reading_date(latest)
    result["latest_reading_date"] = latest_date

    if latest_date < end_date:
        result["blocked"] = True
        result["block_reason"] = (
            f"Last meter reading is {latest_date:%Y-%m-%d}, but the lease "
            f"ends {end_date:%Y-%m-%d}. Update the meter reading to cover "
            f"the end date before finalizing this move-out."
        )
        return result

    # Reading covers the end date — build a (read-only) electric estimate.
    from smart_meter.services.invoicing import compute_electric_bill

    period_start, period_end = _electric_period_bounds(lease, end_date)
    total = Decimal("0.00")
    lines = []
    for installation in installations:
        ctx = compute_electric_bill(lease, installation.meter, period_start, period_end)
        line_total = Decimal(ctx.line_total or 0)
        total += line_total
        lines.append(
            {
                "meter": installation.meter.meter_number,
                "amount": line_total,
                "period_start": period_start,
                "period_end": period_end,
            }
        )
    result["electric_preview"] = {"total": total, "lines": lines}
    return result


@transaction.atomic
def apply_move_out_settlement(
    lease,
    *,
    water_amount=None,
    water_description=None,
    end_date: date | None = None,
    posting_month: date | None = None,
):
    """Actually post the final electric + water charges.

    posting_month defaults to the CURRENT calendar month (business decision:
    final settlement always lands on the invoice for whichever month the
    move-out is being processed in, regardless of which month end_date
    falls in). The electric usage period itself is still measured up to
    end_date, independent of posting_month.

    Raises ValidationError (without writing anything) if the settlement is
    blocked (e.g. stale meter reading) — callers should check
    build_move_out_settlement_preview() first if they want to show the
    reason before attempting to apply it, but this re-checks regardless so
    it's safe to call directly.
    """
    from invoices.models import InvoiceItem, ItemCategory
    from invoices.services import ensure_month_invoice
    from smart_meter.services.invoicing import (
        compute_electric_bill,
        upsert_invoice_with_electric_item,
    )

    end_date = end_date or lease.end_date or timezone.localdate()
    posting_month = (posting_month or timezone.localdate()).replace(day=1)

    preview = build_move_out_settlement_preview(lease, end_date)
    if not preview["applicable"]:
        return None
    if preview["blocked"]:
        raise ValidationError(preview["block_reason"])

    period_start, period_end = _electric_period_bounds(lease, end_date)
    invoice = None
    for installation in preview["installations"]:
        ctx = compute_electric_bill(lease, installation.meter, period_start, period_end)
        invoice = upsert_invoice_with_electric_item(ctx, posting_month=posting_month)

    if invoice is None:
        invoice = ensure_month_invoice(lease, posting_month)

    if water_amount is not None:
        amount = Decimal(str(water_amount or "0")).quantize(Decimal("0.01"))
        water_cat, _ = ItemCategory.objects.get_or_create(name="Water Charges")
        description = water_description or (
            f"Final water charge — move out {end_date:%b %Y}"
        )
        existing = invoice.items.filter(
            category=water_cat, description__icontains="move out"
        ).order_by("id").last()
        if existing:
            existing.amount = amount
            existing.description = description
            existing.is_recurring = False
            existing.save(update_fields=["amount", "description", "is_recurring"])
        else:
            InvoiceItem.objects.create(
                invoice=invoice,
                category=water_cat,
                description=description,
                amount=amount,
                is_recurring=False,
            )

    return invoice
