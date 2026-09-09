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
from .models import IescoBillReading, InvoiceItem, ItemCategory
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


@transaction.atomic
def post_reading_to_invoice(reading_id: int, posting_month: date, *, user=None):
    reading = IescoBillReading.objects.select_for_update().get(pk=reading_id)
    if reading.posted_at:
        raise ValidationError("This IESCO bill has already been posted to an invoice.")

    units = list(
        Unit.objects.select_related("property").filter(
            electric_meter_num=reading.reference_no
        )
    )
    if not units:
        raise ValidationError("Assign this reference number to a unit before posting it.")
    if len(units) > 1:
        raise ValidationError(
            "This reference number is assigned to more than one unit. Correct the unit records before posting."
        )
    unit = units[0]
    month_end = (
        posting_month.replace(year=posting_month.year + 1, month=1)
        if posting_month.month == 12
        else posting_month.replace(month=posting_month.month + 1)
    )
    month_end = month_end - timezone.timedelta(days=1)
    lease = (
        Lease.objects.filter(
            unit=unit,
            status="active",
            start_date__lte=month_end,
            end_date__gte=posting_month,
        )
        .order_by("-start_date", "-id")
        .first()
    )
    if lease is None:
        raise ValidationError("No active lease covers the selected invoice month.")

    invoice = ensure_month_invoice(lease, posting_month)
    category, _ = ItemCategory.objects.get_or_create(name="Electricity Charges")
    description = f"IESCO bill {reading.bill_month} (Ref {reading.reference_no})"
    item, created = InvoiceItem.objects.get_or_create(
        invoice=invoice,
        category=category,
        description=description,
        defaults={"amount": parse_grand_total(reading.grand_total)},
    )
    if not created and item.amount != parse_grand_total(reading.grand_total):
        item.amount = parse_grand_total(reading.grand_total)
        item.save(update_fields=["amount"])

    reading.posted_invoice_item = item
    reading.posted_at = timezone.now()
    reading.posted_by = user if getattr(user, "is_authenticated", False) else None
    reading.save(update_fields=["posted_invoice_item", "posted_at", "posted_by", "updated_at"])
    return invoice, item


def payload_to_csv_row(payload: dict) -> dict:
    normalized = normalize_bill_payload(payload)
    row = {field: normalized.get(field) for field in IESCO_EXPORT_FIELDS}
    row["meter_readings"] = json.dumps(row["meter_readings"], ensure_ascii=False)
    row["bill_history"] = json.dumps(row["bill_history"], ensure_ascii=False)
    paid = row["current_month_paid"]
    row["current_month_paid"] = "" if paid is None else str(paid).lower()
    return row
