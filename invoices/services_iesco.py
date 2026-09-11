from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from leases.models import Lease
from properties.models import Unit

from .iesco_bill_fetch import get_bill
from .models import (
    IescoBillReading,
    Invoice,
    InvoiceItem,
    ItemCategory,
    round_amount_up_to_nearest_10,
)
from .services import ensure_month_invoice


IESCO_EXPORT_FIELDS = (
    "reference_no",
    "fetched_at",
    "consumer_id",
    "consumer_name",
    "address",
    "tariff_category",
    "meter_type",
    "meter_readings",
    "units",
    "bill_month",
    "reading_date",
    "issue_date",
    "due_date",
    "current_bill",
    "arrears",
    "grand_total",
    "amount_paid",
    "payment_date",
    "bill_history",
    "current_month_paid",
    "description",
)

IESCO_TEXT_LIMITS = {
    "consumer_id": 20,
    "consumer_name": 255,
    "address": 500,
    "tariff_category": 100,
    "meter_type": 20,
    "units": 20,
    "reading_date": 20,
    "issue_date": 20,
    "due_date": 20,
    "current_bill": 30,
    "arrears": 30,
    "grand_total": 30,
    "amount_paid": 30,
    "payment_date": 20,
}


def validate_reference_no(value) -> str:
    reference_no = str(value or "").strip()
    if not re.fullmatch(r"\d{14}", reference_no):
        raise ValidationError("IESCO reference number must contain exactly 14 digits.")
    return reference_no


def _clean_text(payload, field, max_length, *, required=False):
    value = payload.get(field)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text.")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"{field} is required.")
    if len(value) > max_length:
        raise ValidationError(f"{field} must be at most {max_length} characters.")
    return value or None


def _decimal_amount(value):
    if not value:
        return None
    primary_value = str(value).split("/", 1)[0]
    cleaned = re.sub(r"[^0-9.\-]", "", primary_value.replace(",", ""))
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _format_amount(value):
    amount = _decimal_amount(value)
    if amount is None:
        return value
    return f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"


def normalize_bill_payload(payload) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("Each bill must be an object.")

    reference_no = validate_reference_no(payload.get("reference_no"))
    bill_month = _clean_text(payload, "bill_month", 20, required=True)
    values = {
        field: _clean_text(payload, field, max_length)
        for field, max_length in IESCO_TEXT_LIMITS.items()
    }
    values["grand_total"] = _format_amount(values["grand_total"])
    values["current_bill"] = _format_amount(values["current_bill"])
    values["arrears"] = _format_amount(values["arrears"])
    values["amount_paid"] = _format_amount(values["amount_paid"])
    grand_total_amount = _decimal_amount(values["grand_total"])
    arrears_amount = _decimal_amount(values["arrears"])
    if grand_total_amount is not None and arrears_amount is not None:
        values["current_bill"] = _format_amount(grand_total_amount - arrears_amount)

    fetched_at_value = payload.get("fetched_at")
    fetched_at = None
    if fetched_at_value not in (None, ""):
        if isinstance(fetched_at_value, datetime):
            fetched_at = fetched_at_value
        elif isinstance(fetched_at_value, str):
            fetched_at = parse_datetime(fetched_at_value)
        if fetched_at is None:
            raise ValidationError("fetched_at must be an ISO-8601 datetime.")
        if timezone.is_naive(fetched_at):
            fetched_at = timezone.make_aware(fetched_at)

    bill_history = payload.get("bill_history")
    if bill_history in (None, ""):
        bill_history = []
    if isinstance(bill_history, str):
        try:
            bill_history = json.loads(bill_history)
        except json.JSONDecodeError as exc:
            raise ValidationError("bill_history must contain valid JSON.") from exc
    if not isinstance(bill_history, list) or len(bill_history) > 24:
        raise ValidationError("bill_history must be a list of at most 24 rows.")
    if any(not isinstance(row, dict) for row in bill_history):
        raise ValidationError("Each bill_history row must be an object.")

    meter_readings = payload.get("meter_readings")
    if meter_readings in (None, ""):
        meter_readings = []
    if isinstance(meter_readings, str):
        try:
            meter_readings = json.loads(meter_readings)
        except json.JSONDecodeError as exc:
            raise ValidationError("meter_readings must contain valid JSON.") from exc
    if not isinstance(meter_readings, list) or len(meter_readings) > 8:
        raise ValidationError("meter_readings must be a list of at most 8 rows.")
    if any(not isinstance(row, dict) for row in meter_readings):
        raise ValidationError("Each meter_readings row must be an object.")

    paid = payload.get("current_month_paid")
    if isinstance(paid, str):
        normalized_paid = paid.strip().lower()
        if normalized_paid in ("", "unknown", "null", "none"):
            paid = None
        elif normalized_paid in ("true", "1", "yes", "paid"):
            paid = True
        elif normalized_paid in ("false", "0", "no", "unpaid"):
            paid = False
    if paid is not None and not isinstance(paid, bool):
        raise ValidationError("current_month_paid must be true, false, or blank.")

    return {
        "reference_no": reference_no,
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "bill_month": bill_month,
        **values,
        "meter_readings": meter_readings,
        "bill_history": bill_history,
        "current_month_paid": paid,
        "description": str(payload.get("description") or "").strip()[:255],
    }


def fetch_bill_payload(reference_no: str, *, description="") -> dict:
    bill = get_bill(validate_reference_no(reference_no))
    payload = asdict(bill)
    if not bill.raw_found or not bill.bill_month:
        raise ValidationError("IESCO returned a page without the required bill fields.")
    payload["description"] = description
    return normalize_bill_payload(payload)


def save_bill_payload(payload: dict) -> tuple[IescoBillReading, bool]:
    payload = normalize_bill_payload(payload)
    reference_no = payload.pop("reference_no")
    bill_month = payload.pop("bill_month")
    payload.pop("description", None)
    fetched_at = payload.get("fetched_at")
    if fetched_at:
        payload["fetched_at"] = parse_datetime(fetched_at)
    # Every fresh fetch/import invalidates any prior reconciliation confirmation.
    # The normalized payload is trusted as parsed data only until a staff user
    # explicitly verifies and confirms it for energy reconciliation.
    payload.update({
        "trust_status": IescoBillReading.TRUST_PARSED,
        "verified_at": None,
        "verified_by": None,
        "confirmed_at": None,
        "confirmed_by": None,
    })
    return IescoBillReading.objects.update_or_create(
        reference_no=reference_no,
        bill_month=bill_month,
        defaults=payload,
    )


def bill_month_start(value: str) -> date:
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    for fmt in ("%b %y", "%B %y", "%b %Y", "%B %Y"):
        try:
            parsed = datetime.strptime(cleaned.title(), fmt).date()
            return parsed.replace(day=1)
        except ValueError:
            continue
    raise ValidationError("Select the invoice month because the IESCO bill month was not recognized.")


def parse_grand_total(value) -> Decimal:
    cleaned = re.sub(r"[^0-9.\-]", "", str(value or "").replace(",", ""))
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise ValidationError("The IESCO grand total is not a valid amount.")
    if amount <= 0:
        raise ValidationError("The IESCO grand total must be greater than zero.")
    return amount.quantize(Decimal("0.01"))


class IescoInvoiceAmountChangeRequired(Exception):
    def __init__(self, *, reading, invoice, item, new_amount):
        self.reading = reading
        self.invoice = invoice
        self.item = item
        self.old_amount = item.amount
        self.new_amount = new_amount
        super().__init__(
            f"Invoice amount changed from Rs. {self.old_amount:,.2f} "
            f"to Rs. {self.new_amount:,.2f}."
        )


def _invoice_meter_value(reading, field):
    rows = reading.register_display_rows
    if not rows:
        return "Not available"
    if len(rows) == 1:
        return str(rows[0].get(field) or "Not available")
    return ", ".join(
        f"{row['short_label']} {row.get(field) or 'N/A'}" for row in rows
    )


def _invoice_usage_text(reading):
    if reading.has_export_registers:
        return (
            f"I {reading.import_units_display}, E {reading.export_units_display}, "
            f"N {reading.net_units_display}"
        )
    return reading.units_display


def calculate_iesco_invoice_charge(reading, lease, posting_month):
    """Return an auditable charge without rebilling verified prior electricity."""
    current_amount = reading.current_bill_amount
    grand_total = reading.grand_total_amount
    arrears = max(reading.arrears_amount or Decimal("0"), Decimal("0"))
    if current_amount is None:
        current_amount = grand_total
    if current_amount is None:
        raise ValidationError("The IESCO current bill is not a valid amount.")

    prior_item = None
    credited_prior_amount = Decimal("0")
    unbilled_arrears = Decimal("0")
    if arrears > 0 and grand_total is not None:
        prior_item = (
            InvoiceItem.objects.select_related("invoice")
            .filter(
                invoice__lease=lease,
                invoice__issue_date__lt=posting_month,
                category__name="Electricity Charges",
                description__contains=f"(Ref {reading.reference_no})",
            )
            .exclude(invoice__status="cancelled")
            .exclude(invoice__lifecycle_status__in=("cancelled", "void"))
            .order_by("-invoice__issue_date", "-id")
            .first()
        )
        if prior_item is not None:
            credited_prior_amount = min(
                max(prior_item.amount or Decimal("0"), Decimal("0")),
                arrears,
            )
            unbilled_arrears = arrears - credited_prior_amount

    charge = current_amount + unbilled_arrears
    if grand_total is not None and arrears > 0:
        verified_charge = grand_total - credited_prior_amount - (
            arrears if prior_item is None else Decimal("0")
        )
        if charge != verified_charge:
            raise ValidationError(
                "The IESCO current bill, arrears, and grand total do not reconcile. "
                "Review the saved reading before invoicing."
            )

    if prior_item is not None:
        explanation = (
            f"Current Rs. {current_amount:,.2f}; prior billed credit "
            f"Rs. {credited_prior_amount:,.2f}; new arrears/penalty "
            f"Rs. {unbilled_arrears:,.2f}"
        )
    elif arrears > 0:
        explanation = (
            f"Current Rs. {current_amount:,.2f}; arrears Rs. {arrears:,.2f} "
            "excluded because no prior matching IESCO invoice was verified"
        )
    else:
        explanation = f"Current Rs. {current_amount:,.2f}; no arrears"
    return charge, explanation, prior_item


@transaction.atomic
def post_reading_to_invoice(
    reading_id: int,
    posting_month: date,
    *,
    user=None,
    confirm_amount_change=False,
):
    reading = IescoBillReading.objects.select_for_update().get(pk=reading_id)

    units = list(
        Unit.objects.select_related("property").filter(
            electric_meter_num=reading.reference_no
        )
    )
    if not units:
        raise ValidationError("Assign this reference number to a unit before posting it.")
    if len(units) > 1:
        raise ValidationError(
            "The bill reading is saved, but its tenant invoice was not created because "
            "this reference number is assigned to multiple units. Open the IESCO meter "
            "list, edit or remove the duplicate assignment so exactly one unit owns this "
            "reference, then run Make Invoices again."
        )
    unit = units[0]
    month_end = (
        posting_month.replace(year=posting_month.year + 1, month=1)
        if posting_month.month == 12
        else posting_month.replace(month=posting_month.month + 1)
    )
    month_end = month_end - timezone.timedelta(days=1)
    lease = (
        Lease.objects.select_for_update()
        .filter(
            unit=unit,
            start_date__lte=month_end,
            end_date__gte=posting_month,
        )
        .exclude(status__in=("pending_approval", "rejected"))
        .order_by("-start_date", "-id")
        .first()
    )
    if lease is None:
        raise ValidationError("No approved lease covers the selected invoice month.")

    monthly_invoices = list(
        Invoice.objects.select_for_update()
        .filter(lease=lease, issue_date=posting_month)
        .order_by("id")[:2]
    )
    if len(monthly_invoices) > 1:
        raise ValidationError(
            "More than one invoice already exists for this lease and billing month. "
            "Resolve the duplicate invoices before posting the IESCO bill."
        )
    invoice = monthly_invoices[0] if monthly_invoices else ensure_month_invoice(lease, posting_month)
    category, _ = ItemCategory.objects.get_or_create(name="Electricity Charges")
    description_prefix = f"IESCO bill {reading.bill_month} (Ref {reading.reference_no})"
    bill_amount, calculation, _prior_item = calculate_iesco_invoice_charge(
        reading, lease, posting_month
    )
    if bill_amount <= 0:
        raise ValidationError("The IESCO current bill must be greater than zero.")
    new_amount = round_amount_up_to_nearest_10(bill_amount)
    description = (
        f"{description_prefix} — Total units consumed {_invoice_usage_text(reading)}; "
        f"Previous {_invoice_meter_value(reading, 'previous')}; "
        f"Current {_invoice_meter_value(reading, 'present')}; "
        f"Due {reading.due_date or 'Not available'} — {calculation}"
    )
    item = None
    if reading.posted_invoice_item_id:
        item = (
            InvoiceItem.objects.select_for_update()
            .filter(pk=reading.posted_invoice_item_id, invoice=invoice)
            .first()
        )
    if item is None:
        item = (
            InvoiceItem.objects.select_for_update()
            .filter(
                invoice=invoice,
                category=category,
                description__startswith=description_prefix,
            )
            .first()
        )

    action = "unchanged"
    if item is None:
        item = InvoiceItem.objects.create(
            invoice=invoice,
            category=category,
            description=description,
            amount=new_amount,
        )
        action = "created"
    elif item.amount != new_amount:
        if not confirm_amount_change:
            raise IescoInvoiceAmountChangeRequired(
                reading=reading,
                invoice=invoice,
                item=item,
                new_amount=new_amount,
            )
        item.amount = new_amount
        item.description = description
        item.save(update_fields=["amount", "description"])
        action = "updated"
    elif item.description != description:
        item.description = description
        item.save(update_fields=["description"])

    reading.posted_invoice_item = item
    if not reading.posted_at:
        reading.posted_at = timezone.now()
    reading.posted_by = user if getattr(user, "is_authenticated", False) else None
    reading.save(update_fields=["posted_invoice_item", "posted_at", "posted_by", "updated_at"])
    return invoice, item, action


def payload_to_csv_row(payload: dict) -> dict:
    normalized = normalize_bill_payload(payload)
    row = {field: normalized.get(field) for field in IESCO_EXPORT_FIELDS}
    row["meter_readings"] = json.dumps(row["meter_readings"], ensure_ascii=False)
    row["bill_history"] = json.dumps(row["bill_history"], ensure_ascii=False)
    paid = row["current_month_paid"]
    row["current_month_paid"] = "" if paid is None else str(paid).lower()
    return row
