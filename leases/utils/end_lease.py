"""Atomic lease-ending settlement used by the lease detail quick action."""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
import re
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from invoices.models import (
    Invoice, InvoiceItem, ItemCategory, SecurityDepositLedgerTransfer,
    SecurityDepositTransaction,
)
from invoices.models import round_amount_up_to_nearest_10
from invoices.services import ensure_month_invoice, security_deposit_totals
from payments.models import Payment
from payments.services.payment_detail import rebuild_payment_detail

from .move_out_billing import build_move_out_settlement_preview


ZERO = Decimal("0.00")
BASE_CHARGES = (
    ("Rent", "monthly_rent"),
    ("Society Maintenance", "society_maintenance"),
    ("Water Charges", "water_charges"),
    ("Internet", "internet_charges"),
)


def money(value) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except Exception as exc:
        raise ValidationError("Enter valid charge amounts.") from exc


def _rounded(value) -> Decimal:
    return money(round_amount_up_to_nearest_10(money(value)))


def _proration_window(lease, end_date: date) -> tuple[int, int]:
    days_in_month = monthrange(end_date.year, end_date.month)[1]
    first = end_date.replace(day=1)
    occupied_from = max(first, lease.start_date)
    occupied_days = (end_date - occupied_from).days + 1
    return max(occupied_days, 0), days_in_month


def _billable_days(occupied_days: int, days_in_month: int, interval_days: int) -> int:
    interval_days = max(int(interval_days or 1), 1)
    if occupied_days <= 0:
        return 0
    return min(((occupied_days + interval_days - 1) // interval_days) * interval_days, days_in_month)


def _category(name: str) -> ItemCategory:
    category = ItemCategory.objects.filter(name__iexact=name).first()
    if category:
        return category
    return ItemCategory.objects.create(name=name)


def _month_invoice(lease, end_date: date):
    return (
        Invoice.objects.filter(
            lease=lease,
            issue_date__year=end_date.year,
            issue_date__month=end_date.month,
        )
        .exclude(status="cancelled")
        .exclude(description__startswith="Move-out settlement charges - lease ended")
        .order_by("issue_date", "id")
        .first()
    )


def _settlement_invoice(lease, end_date: date) -> Invoice:
    description = f"Move-out settlement charges - lease ended {end_date:%Y-%m-%d}"
    obsolete_drafts = Invoice.objects.filter(
        lease=lease,
        status="draft",
        description__startswith="Move-out settlement charges - lease ended",
    ).exclude(description=description)
    for obsolete in obsolete_drafts:
        obsolete.status = "cancelled"
        obsolete.notes = "\n".join(
            filter(None, [obsolete.notes, f"Superseded by settlement preview for {end_date:%Y-%m-%d}."])
        )
        obsolete.save(update_fields=["status", "notes", "updated_at"])
    invoice = Invoice.objects.filter(lease=lease, description=description).order_by("id").first()
    if invoice:
        today = date.today()
        update_fields = []
        if invoice.status == "cancelled" and "Superseded by settlement preview" in (invoice.notes or ""):
            invoice.status = "draft"
            update_fields.append("status")
        if invoice.issue_date != today or invoice.due_date != today:
            invoice.issue_date = today
            invoice.due_date = today
            update_fields.extend(["issue_date", "due_date"])
        if update_fields:
            invoice.save(update_fields=list(dict.fromkeys(update_fields + ["updated_at"])))
        return invoice
    today = date.today()
    return Invoice.objects.create(
        lease=lease,
        issue_date=today,
        due_date=today,
        status="draft",
        description=description,
        amount=ZERO,
    )


def _base_item(invoice: Invoice, category_name: str):
    if invoice is None:
        return None
    aliases = {
        "Water Charges": ("Water Charges", "Water"),
        "Society Maintenance": ("Society Maintenance", "Maintenance"),
    }
    names = aliases.get(category_name, (category_name,))
    return invoice.items.filter(category__name__in=names).order_by("id").first()


def _electric_items(invoice: Invoice, settlement_preview: dict):
    if invoice is None:
        return InvoiceItem.objects.none()
    items = InvoiceItem.objects.none()
    for installation in settlement_preview.get("installations") or []:
        items = items | invoice.items.filter(
            Q(description__icontains=f"Meter#={installation.meter.meter_number}")
            & Q(description__icontains=(
                f"Billing Period={settlement_preview['end_date'].replace(day=1):%Y-%m-%d} "
                f"to {settlement_preview['end_date']:%Y-%m-%d}"
            )),
        )
    return items.distinct()


def _invoice_total(invoice: Invoice) -> Decimal:
    if invoice is None:
        return ZERO
    return money(invoice.items.aggregate(total=Sum("amount"))["total"] or ZERO)


def _inspection_state(lease) -> dict:
    inspection = lease.inspections.prefetch_related("keys").order_by("-inspection_date", "-id").first()
    complete = bool(
        inspection
        and inspection.status in {inspection.STATUS_COMPLETED, inspection.STATUS_APPROVED}
        and inspection.completion_percent == 100
    )
    outstanding_keys = 0
    key_rows = []
    if inspection:
        key_rows = list(inspection.keys.all())
        outstanding_keys = sum(
            max((row.quantity_issued or 0) - (row.quantity_returned or 0), 0)
            for row in key_rows
        )
    unit_keys = max(getattr(lease.unit, "keys", 0) or 0, 0)
    keys_returned = bool(inspection) and outstanding_keys == 0 and (bool(key_rows) or unit_keys == 0)
    return {
        "inspection": inspection,
        "inspection_complete": complete,
        "keys_returned": keys_returned,
        "outstanding_keys": outstanding_keys,
    }


def move_out_charge_defaults(lease) -> dict:
    building_type = getattr(lease.unit, "building_type", None) or getattr(
        lease.unit, "interest_type", None
    )
    return {
        "building_type": building_type,
        "inspection_charge": money(
            getattr(
                building_type,
                "inspection_incomplete_charge",
                getattr(lease.unit, "inspection_incomplete_charge", "5000.00"),
            )
        ),
        "key_charge": money(
            getattr(
                building_type,
                "key_card_not_returned_charge",
                getattr(lease.unit, "key_card_not_returned_charge", "1000.00"),
            )
        ),
    }


def _prepare_final_period_proration(
    lease, invoice, end_date, occupied_days, days_in_month, interval_days=None
):
    marker = f"END_LEASE_PRORATED:{end_date:%Y-%m-%d}"
    interval_days = int(interval_days or lease.effective_proration_interval_days or 1)
    billed_days = _billable_days(occupied_days, days_in_month, interval_days)
    interval_label = lease.effective_proration_interval_label
    handled_item_ids = set()
    for category_name, field_name in BASE_CHARGES:
        existing = _base_item(invoice, category_name)
        suppressed = (
            f"END_LEASE_SUPPRESS_CATEGORY:{category_name}:{end_date:%Y-%m-%d}"
            in (invoice.notes or "")
        )
        manually_edited = bool(
            existing
            and f"END_LEASE_MANUAL_ITEM:{existing.pk}:{end_date:%Y-%m-%d}"
            in (invoice.notes or "")
        )
        if suppressed:
            if existing:
                handled_item_ids.add(existing.pk)
            continue
        if manually_edited:
            handled_item_ids.add(existing.pk)
            continue
        monthly = money(getattr(lease, field_name, ZERO))
        amount = _rounded(monthly * billed_days / days_in_month) if monthly > ZERO else ZERO
        if amount > ZERO or existing:
            item = _upsert_item(
                invoice,
                category_name=category_name,
                description=(
                    f"Final prorated {category_name.lower()} {end_date:%b %Y} "
                    f"({occupied_days} occupied; {billed_days}/{days_in_month} billed, "
                    f"{interval_label.lower()} interval; original Rs. {monthly:,.2f})"
                ),
                amount=amount,
                existing=existing,
            )
            handled_item_ids.add(item.pk)

    original_pattern = re.compile(r"^END_LEASE_PRORATE_ORIGINAL:(\d+):([0-9.]+)$", re.MULTILINE)
    original_amounts = {
        int(item_id): money(amount)
        for item_id, amount in original_pattern.findall(invoice.notes or "")
    }
    note_lines = [line for line in (invoice.notes or "").splitlines() if line != marker]
    for item in invoice.items.select_related("category").filter(is_recurring=True).order_by("id"):
        if item.pk in handled_item_ids or item.category.name.lower().startswith("electric"):
            continue
        if f"END_LEASE_MANUAL_ITEM:{item.pk}:{end_date:%Y-%m-%d}" in (invoice.notes or ""):
            continue
        original = original_amounts.get(item.pk, money(item.amount))
        if item.pk not in original_amounts:
            note_lines.append(f"END_LEASE_PRORATE_ORIGINAL:{item.pk}:{original}")
        base_description = re.sub(
            r"\s*\[Move-out proration:.*?\]\s*$", "", item.description or item.category.name
        ).strip()
        item.description = (
            f"{base_description} [Move-out proration: {occupied_days} occupied; "
            f"{billed_days}/{days_in_month} billed, {interval_label.lower()} interval; "
            f"original Rs. {original:,.2f}]"
        )
        item.amount = _rounded(original * billed_days / days_in_month)
        item.save(update_fields=["description", "amount"])

    note_lines.append(marker)
    invoice.notes = "\n".join(filter(None, note_lines))
    invoice.save(update_fields=["notes", "updated_at"])
    invoice.refresh_from_db()
    return billed_days


def prorate_invoice_for_move_out(invoice: Invoice, end_date: date) -> dict:
    """Idempotently prorate an invoice and add actual electricity through move-out."""
    lease = invoice.lease
    if end_date < lease.start_date or end_date > date.today():
        raise ValidationError("Enter a valid move-out date that is not in the future.")
    occupied_days, days_in_month = _proration_window(lease, end_date)
    interval_days = lease.effective_proration_interval_days
    settlement_preview = build_move_out_settlement_preview(lease, end_date=end_date)
    if settlement_preview["applicable"] and settlement_preview["blocked"]:
        raise ValidationError(settlement_preview["block_reason"])
    billed_days = _prepare_final_period_proration(
        lease,
        invoice,
        end_date,
        occupied_days,
        days_in_month,
        lease.effective_proration_interval_days,
    )
    electric_amount = ZERO
    if settlement_preview["applicable"]:
        electric_amount = money(settlement_preview["electric_preview"]["total"])
        billing_period = f"Billing Period={end_date.replace(day=1):%Y-%m-%d} to {end_date:%Y-%m-%d}"
        existing = invoice.items.filter(
            category__name__istartswith="Electric",
            description__icontains=billing_period,
        ).first()
        _upsert_item_exact(
            invoice,
            category_name="Electricity",
            description=f"Final electricity through move-out {end_date:%Y-%m-%d}; {billing_period}",
            amount=electric_amount,
            existing=existing,
        )
    invoice.refresh_from_db()
    return {
        "invoice": invoice,
        "occupied_days": occupied_days,
        "billable_days": billed_days,
        "days_in_month": days_in_month,
        "interval_days": lease.effective_proration_interval_days,
        "interval_label": lease.effective_proration_interval_label,
        "electric_amount": electric_amount,
    }


def _prepare_settlement_invoice(
    lease,
    *,
    invoice,
    end_date,
    occupied_days,
    days_in_month,
    electric_amount,
    electric_exact,
    other_amount,
    other_description,
    inspection_state,
    inspection_charge,
    key_charge,
):
    """Keep move-out-only charges on their own editable invoice."""
    marker = f"END_LEASE_PREPARED:{end_date:%Y-%m-%d}"
    checklist_lines = (
        (
            "Move-out inspection sheet not completed",
            not inspection_state["inspection_complete"],
            inspection_charge,
        ),
        (
            "Key/key card not recorded as returned",
            not inspection_state["keys_returned"],
            key_charge,
        ),
    )
    for description, should_charge, amount in checklist_lines:
        InvoiceItem.objects.filter(
            invoice__lease=lease,
            description=description,
        ).exclude(invoice=invoice).delete()
        existing_checklist_item = invoice.items.filter(description=description).first()
        suppressed = f"END_LEASE_SUPPRESS:{description}" in (invoice.notes or "")
        if should_charge and amount > ZERO and not suppressed:
            _upsert_item(
                invoice,
                category_name="Repair",
                description=description,
                amount=amount,
                existing=existing_checklist_item,
            )
        elif existing_checklist_item:
            existing_checklist_item.delete()

    electricity_item = invoice.items.filter(description__startswith="Final electricity charge").first()
    if electric_amount > ZERO:
        item_kwargs = {
            "invoice": invoice,
            "category_name": "Electricity",
            "description": f"Final electricity charge - move out {end_date:%Y-%m-%d}",
            "amount": electric_amount,
            "existing": electricity_item,
        }
        if electric_exact:
            _upsert_item_exact(**item_kwargs)
        else:
            _upsert_item(**item_kwargs)
    elif electricity_item:
        electricity_item.delete()
    other_item = invoice.items.filter(description__startswith="Move-out other charge:").first()
    if other_amount > ZERO:
        _upsert_item(
            invoice,
            category_name="Other Charges",
            description=f"Move-out other charge: {other_description}",
            amount=other_amount,
            existing=other_item,
        )
    elif other_item:
        other_item.delete()

    if not invoice.items.exists():
        _upsert_item(
            invoice,
            category_name="Other Charges",
            description="Final settlement adjustment",
            amount=ZERO,
        )

    if marker not in (invoice.notes or ""):
        invoice.notes = "\n".join(filter(None, [invoice.notes, marker]))
        invoice.save(update_fields=["notes", "updated_at"])


def _lease_balance(lease, *, exclude_invoice_ids=None) -> Decimal:
    invoices = Invoice.objects.filter(lease=lease).exclude(status="cancelled")
    if exclude_invoice_ids:
        invoices = invoices.exclude(pk__in=exclude_invoice_ids)
    invoiced = invoices.aggregate(total=Sum("amount"))["total"] or ZERO
    paid = ZERO
    for payment in lease.payments.select_related("detail").all():
        detail = getattr(payment, "detail", None)
        paid += detail.lease_amount if detail else payment.amount
    return money(invoiced - paid)


def _fully_covered_invoice_ids(lease) -> set[int]:
    """Apply lease-level payments FIFO and return invoices covered in full."""
    available = ZERO
    for payment in lease.payments.select_related("detail").all():
        detail = getattr(payment, "detail", None)
        available += money(detail.lease_amount if detail else payment.amount)

    covered = set()
    for invoice in (
        Invoice.objects.filter(lease=lease)
        .exclude(status="cancelled")
        .order_by("issue_date", "id")
    ):
        amount = money(invoice.amount)
        if amount <= ZERO:
            continue
        if available < amount:
            break
        available = money(available - amount)
        covered.add(invoice.pk)
    return covered


def build_end_lease_preview(
    lease,
    *,
    end_date: date,
    final_electric_amount=None,
    other_amount=None,
    other_description="",
    future_invoice_action="cancel",
    inspection_complete=None,
    keys_returned=None,
    inspection_charge=None,
    key_charge=None,
) -> dict:
    if lease.status != "active":
        raise ValidationError("Only an active lease can be ended with this action.")
    if end_date < lease.start_date:
        raise ValidationError("Lease end date cannot be before the start date.")
    if end_date > date.today():
        raise ValidationError("Lease end date cannot be in the future.")
    if future_invoice_action not in {"cancel", "keep"}:
        raise ValidationError("Choose whether future invoices should be cancelled or kept.")

    future_invoices = list(
        Invoice.objects.filter(lease=lease, issue_date__gt=end_date)
        .exclude(status="cancelled")
        .exclude(description__startswith="Move-out settlement charges - lease ended")
        .order_by("issue_date", "id")
    )
    future_invoice_ids = [item.pk for item in future_invoices if item.status != "cancelled"]

    occupied_days, days_in_month = _proration_window(lease, end_date)
    interval_days = lease.effective_proration_interval_days
    billable_days = _billable_days(occupied_days, days_in_month, interval_days)
    manual_electric = final_electric_amount not in (None, "")
    settlement_preview = build_move_out_settlement_preview(lease, end_date=end_date)
    if manual_electric:
        electric_amount = _rounded(final_electric_amount)
        if electric_amount < ZERO:
            raise ValidationError("Final electricity charge cannot be negative.")
    elif settlement_preview["applicable"]:
        if settlement_preview["blocked"]:
            raise ValidationError(settlement_preview["block_reason"])
        electric_amount = _rounded(settlement_preview["electric_preview"]["total"])
    else:
        electric_amount = ZERO

    other_amount = _rounded(other_amount)
    if other_amount < ZERO:
        raise ValidationError("Other final charge cannot be negative.")

    final_period_invoice = _month_invoice(lease, end_date) or ensure_month_invoice(
        lease, end_date.replace(day=1)
    )
    _prepare_final_period_proration(
        lease,
        final_period_invoice,
        end_date,
        occupied_days,
        days_in_month,
        interval_days,
    )
    invoice = _settlement_invoice(lease, end_date)
    inspection_state = _inspection_state(lease)
    charge_defaults = move_out_charge_defaults(lease)
    inspection_charge = money(
        charge_defaults["inspection_charge"] if inspection_charge in (None, "") else inspection_charge
    )
    key_charge = money(
        charge_defaults["key_charge"] if key_charge in (None, "") else key_charge
    )
    if inspection_charge < ZERO or key_charge < ZERO:
        raise ValidationError("Move-out charges cannot be negative.")
    if inspection_complete in {True, False}:
        inspection_state["inspection_complete"] = inspection_complete
    if keys_returned in {True, False}:
        inspection_state["keys_returned"] = keys_returned
    billing_period_text = (
        f"Billing Period={end_date.replace(day=1):%Y-%m-%d} to {end_date:%Y-%m-%d}"
    )
    matching_electric_items = list(
        InvoiceItem.objects.filter(
            invoice__lease=lease,
            invoice__status__in=["draft", "sent", "overdue"],
            category__name__istartswith="Electric",
            description__icontains=billing_period_text,
        )
        .exclude(invoice=invoice)
        .select_related("invoice", "category")
    )
    future_electric_items = [
        invoice_item
        for invoice_item in matching_electric_items
        if invoice_item.invoice_id in future_invoice_ids
    ]
    current_electric_items = [
        invoice_item
        for invoice_item in matching_electric_items
        if invoice_item.invoice_id not in future_invoice_ids
    ]
    future_electric_amount = money(sum((item.amount for item in future_electric_items), ZERO))
    future_has_electricity = bool(future_electric_items)
    current_has_electricity = bool(current_electric_items)
    electricity_suppressed = (
        not manual_electric
        and Invoice.objects.filter(
            lease=lease,
            notes__contains=f"END_LEASE_SUPPRESS_CATEGORY:Electricity:{end_date:%Y-%m-%d}",
        ).exists()
    )
    electricity_transfer_on_confirm = ZERO
    if electricity_suppressed:
        electricity_to_post = ZERO
    elif current_has_electricity:
        electricity_to_post = ZERO
    elif manual_electric:
        electricity_to_post = electric_amount
    elif future_has_electricity:
        electricity_to_post = ZERO
        if future_invoice_action == "cancel":
            electricity_transfer_on_confirm = future_electric_amount
    else:
        electricity_to_post = electric_amount
    _prepare_settlement_invoice(
        lease,
        invoice=invoice,
        end_date=end_date,
        occupied_days=occupied_days,
        days_in_month=days_in_month,
        electric_amount=electricity_to_post,
        electric_exact=False,
        other_amount=other_amount,
        other_description=other_description.strip() or "Other move-out charges",
        inspection_state=inspection_state,
        inspection_charge=inspection_charge,
        key_charge=key_charge,
    )
    invoice.refresh_from_db()
    lines = [
        {
            "id": item.pk,
            "category_id": item.category_id,
            "category": item.category.name,
            "description": item.description or "",
            "amount": money(item.amount),
        }
        for item in invoice.items.select_related("category").order_by("id")
    ]

    gross_balance = _lease_balance(
        lease,
        exclude_invoice_ids=(future_invoice_ids if future_invoice_action == "cancel" else None),
    )
    gross_balance = money(gross_balance + electricity_transfer_on_confirm)
    security_held = money(security_deposit_totals(lease)["currently_held"])
    security_applied = min(max(gross_balance, ZERO), security_held)
    balance_after_security = money(gross_balance - security_applied)
    security_refund = money(security_held - security_applied)
    lease_credit = money(max(-balance_after_security, ZERO))
    refund_due = money(security_refund + lease_credit)
    billing_month_start = end_date.replace(day=1)
    prior_invoices = Invoice.objects.filter(
        lease=lease,
        issue_date__lt=billing_month_start,
    ).exclude(status="cancelled")
    fully_covered_invoice_ids = _fully_covered_invoice_ids(lease)
    outstanding_prior_invoices = list(
        prior_invoices
        .exclude(status__in=["paid", "cancelled"])
        .exclude(pk__in=fully_covered_invoice_ids)
        .exclude(description__startswith="Move-out settlement charges - lease ended")
        .order_by("issue_date", "id")
    )
    review_invoices = list(future_invoices) + outstanding_prior_invoices
    if occupied_days < days_in_month and all(
        row.pk != final_period_invoice.pk for row in review_invoices
    ):
        review_invoices.append(final_period_invoice)
    if all(row.pk != invoice.pk for row in review_invoices):
        review_invoices.append(invoice)
    review_invoices.sort(key=lambda row: (row.issue_date, row.pk))

    return {
        "lease": lease,
        "invoice": invoice,
        "final_period_invoice": final_period_invoice,
        "end_date": end_date,
        "billing_month_start": billing_month_start,
        "occupied_days": occupied_days,
        "billable_days": billable_days,
        "days_in_month": days_in_month,
        "proration_interval_days": interval_days,
        "proration_interval_label": lease.effective_proration_interval_label,
        "lines": lines,
        "invoice_total": _invoice_total(invoice),
        "inspection_state": inspection_state,
        "move_out_building_type": charge_defaults["building_type"],
        "inspection_charge": inspection_charge,
        "key_charge": key_charge,
        "settlement_preview": settlement_preview,
        "manual_electric": manual_electric,
        "electric_amount": electric_amount,
        "electricity_in_future_invoice": future_has_electricity,
        "electricity_in_current_invoice": current_has_electricity,
        "electricity_suppressed": electricity_suppressed,
        "future_electric_amount": future_electric_amount,
        "electricity_posted_to_settlement": electricity_to_post,
        "electricity_transfer_on_confirm": electricity_transfer_on_confirm,
        "other_amount": other_amount,
        "other_description": other_description.strip() or "Other move-out charges",
        "future_invoice_action": future_invoice_action,
        "future_invoices": future_invoices,
        "review_invoices": review_invoices,
        "future_invoice_total": money(sum(
            (item.amount or ZERO for item in future_invoices if item.status != "cancelled"), ZERO
        )),
        "gross_balance": gross_balance,
        "security_held": security_held,
        "security_applied": security_applied,
        "balance_after_security": balance_after_security,
        "security_refund": security_refund,
        "lease_credit": lease_credit,
        "refund_due": refund_due,
        "amount_payable": money(max(balance_after_security, ZERO)),
    }


def _upsert_item(invoice, *, category_name, description, amount, existing=None):
    amount = _rounded(amount)
    item = existing or invoice.items.filter(description=description).first()
    if item:
        item.category = _category(category_name)
        item.description = description
        item.amount = amount
        item.is_recurring = False
        item.save(update_fields=["category", "description", "amount", "is_recurring"])
        return item
    return InvoiceItem.objects.create(
        invoice=invoice,
        category=_category(category_name),
        description=description,
        amount=amount,
        is_recurring=False,
    )


def _upsert_item_exact(invoice, *, category_name, description, amount, existing=None):
    """Preserve the exact amount of an already-issued charge moved between invoices."""
    amount = money(amount)
    category = _category(category_name)
    item = existing or invoice.items.filter(description=description).first()
    if item:
        InvoiceItem.objects.filter(pk=item.pk).update(
            category=category,
            description=description,
            amount=amount,
            is_recurring=False,
        )
        item.refresh_from_db()
    else:
        item = InvoiceItem(
            invoice=invoice,
            category=category,
            description=description,
            amount=amount,
            is_recurring=False,
        )
        InvoiceItem.objects.bulk_create([item])
    total = invoice.items.aggregate(total=Sum("amount"))["total"] or ZERO
    Invoice.objects.filter(pk=invoice.pk).update(amount=total)
    invoice.amount = money(total)
    return item


def _create_security_ledger_transfer(
    lease, *, amount, transfer_date, user=None, notes="", reason="Security deposit transferred to tenant ledger"
):
    """Create an internal (non-cash) lease credit plus an auditable security transfer.

    The Payment row is intentionally method-less and is used only because the existing
    lease ledger is invoice/payment based. The security movement is *TRANSFERRED*, not
    PAID, so this never claims money was actually refunded to the tenant.
    """
    amount = money(amount)
    if amount <= ZERO:
        raise ValidationError("Transfer amount must be greater than zero.")
    reference = f"SECLEDGER-{lease.pk}-{transfer_date:%Y%m%d}-{timezone.now():%H%M%S%f}"
    transfer_note = (
        f"Internal security-deposit ledger transfer on {transfer_date:%Y-%m-%d}. "
        "This is not a cash/bank refund. Record the actual outgoing refund separately."
    )
    if notes:
        transfer_note += f" {notes.strip()}"
    transfer_payment = Payment.objects.create(
        lease=lease,
        payment_date=transfer_date,
        amount=amount,
        payment_method=None,
        reference_number=reference,
        description="Security deposit ledger credit (internal transfer)",
        notes=transfer_note,
    )
    rebuild_payment_detail(
        payment=transfer_payment,
        lease_amount=amount,
        security_amount=ZERO,
        user=user,
        reason="Internal security deposit transfer to lease ledger",
    )
    movement = SecurityDepositTransaction.objects.create(
        lease=lease,
        date=transfer_date,
        type="REFUND",
        amount=ZERO,
        deduction_amount=amount,
        deduction_reason="Transferred to lease ledger for tenant refund",
        refund_status="TRANSFERRED",
        payment=transfer_payment,
        notes=transfer_note,
    )
    event = SecurityDepositLedgerTransfer.objects.create(
        lease=lease,
        amount=amount,
        transaction_date=transfer_date,
        reason=reason or "Security deposit transferred to tenant ledger",
        reference=reference,
        ledger_credit_payment=transfer_payment,
        security_movement=movement,
        created_by=user,
    )
    return transfer_payment, movement, event


@transaction.atomic
def transfer_pending_security_to_lease_ledger(lease, *, user=None) -> dict:
    """Convert a legacy pending security refund into an auditable lease credit."""
    lease = lease.__class__.objects.select_for_update().get(pk=lease.pk)
    if lease.status != "ended":
        raise ValidationError("Pending security can only be transferred after the lease has ended.")

    pending_rows = list(
        lease.security_transactions.select_for_update().filter(
            type="REFUND",
            refund_status__in=["PENDING", "APPROVED"],
            payment__isnull=True,
        )
    )
    pending_total = money(sum((row.amount or ZERO for row in pending_rows), ZERO))
    held = money(security_deposit_totals(lease)["currently_held"])
    transfer_amount = min(pending_total, held)
    if transfer_amount <= ZERO:
        raise ValidationError("There is no pending held security to transfer.")

    payment, movement, event = _create_security_ledger_transfer(
        lease,
        amount=transfer_amount,
        transfer_date=lease.end_date or date.today(),
        user=user,
        notes="Converted from the legacy pending-refund workflow.",
    )
    for pending in pending_rows:
        pending.refund_status = "CANCELLED"
        pending.notes = "\n".join(
            filter(
                None,
                [
                    pending.notes,
                    f"Replaced by lease-ledger transfer Payment #{payment.pk}.",
                ],
            )
        )
        pending.save(update_fields=["refund_status", "notes"])

    return {
        "lease": lease,
        "amount": transfer_amount,
        "payment": payment,
        "movement": movement,
        "transfer": event,
    }


@transaction.atomic
def transfer_refundable_security_to_lease_ledger(
    lease, *, user=None, transaction_date=None, reason=""
) -> dict:
    """Transfer the full currently refundable security balance to the lease ledger."""
    lease = lease.__class__.objects.select_for_update().get(pk=lease.pk)
    if lease.status != "ended":
        raise ValidationError("Security deposit can only be transferred after the lease has ended.")
    totals = security_deposit_totals(lease)
    amount = money(totals.get("currently_held") or ZERO)
    if amount <= ZERO:
        raise ValidationError("There is no refundable security balance available to transfer.")
    transaction_date = transaction_date or timezone.localdate()
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required for the security deposit transfer.")
    payment, movement, event = _create_security_ledger_transfer(
        lease, amount=amount, transfer_date=transaction_date, user=user, reason=reason
    )
    return {
        "lease": lease, "amount": amount, "payment": payment,
        "movement": movement, "transfer": event, "totals_before": totals,
    }


@transaction.atomic
def reverse_security_ledger_transfer(transfer, *, user=None, reason="") -> dict:
    transfer = SecurityDepositLedgerTransfer.objects.select_for_update().select_related(
        "lease", "security_movement", "ledger_credit_payment"
    ).get(pk=transfer.pk)
    if transfer.reversed_at:
        raise ValidationError("This security deposit ledger transfer has already been reversed.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reversal reason is required.")

    reversal = Payment.objects.create(
        lease=transfer.lease,
        payment_date=timezone.localdate(),
        amount=transfer.amount,
        payment_method=None,
        reference_number=f"REV-{transfer.reference}",
        description="Reversal of security deposit ledger credit",
        notes=f"Reversal of {transfer.reference}. {reason}",
    )
    rebuild_payment_detail(
        payment=reversal,
        lease_amount=-transfer.amount,
        security_amount=ZERO,
        user=user,
        reason=f"Reverse internal security ledger transfer {transfer.reference}",
    )
    movement = transfer.security_movement
    movement.refund_status = "CANCELLED"
    movement.notes = "\n".join(filter(None, [movement.notes, f"Transfer reversed: {reason}"]))
    movement.save(update_fields=["refund_status", "notes"])
    transfer.reversed_at = timezone.now()
    transfer.reversed_by = user
    transfer.reversal_reason = reason
    transfer.reversal_payment = reversal
    transfer.save(update_fields=[
        "reversed_at", "reversed_by", "reversal_reason", "reversal_payment"
    ])
    return {"transfer": transfer, "reversal_payment": reversal, "amount": transfer.amount}


@transaction.atomic
def end_lease(lease, *, user=None, notes="", **preview_kwargs) -> dict:
    lease = lease.__class__.objects.select_for_update().get(pk=lease.pk)
    preview = build_end_lease_preview(lease, **preview_kwargs)
    end_date = preview["end_date"]
    invoice = preview["invoice"]

    if preview["future_invoice_action"] == "cancel":
        for future_invoice in preview["future_invoices"]:
            if future_invoice.status == "cancelled":
                continue
            original_status = future_invoice.status
            audit_note = (
                f"Cancelled by End Lease workflow because lease ended {end_date:%Y-%m-%d}. "
                f"Original status: {original_status}. Original amount: "
                f"Rs. {money(future_invoice.amount):,.2f}."
            )
            future_invoice.status = "cancelled"
            future_invoice.notes = "\n".join(
                filter(None, [future_invoice.notes, audit_note])
            )
            future_invoice.save(update_fields=["status", "notes", "updated_at"])
    else:
        for future_invoice in preview["future_invoices"]:
            if future_invoice.status == "cancelled":
                continue
            audit_note = (
                f"Kept and approved by End Lease workflow after staff review; "
                f"lease ended {end_date:%Y-%m-%d}."
            )
            future_invoice.notes = "\n".join(
                filter(None, [future_invoice.notes, audit_note])
            )
            update_fields = ["notes", "updated_at"]
            if future_invoice.status == "draft":
                future_invoice.status = "sent"
                update_fields.append("status")
            future_invoice.save(update_fields=update_fields)

    if preview["electricity_transfer_on_confirm"] > ZERO:
        billing_period = (
            f"Billing Period={end_date.replace(day=1):%Y-%m-%d} to {end_date:%Y-%m-%d}"
        )
        _upsert_item_exact(
            invoice,
            category_name="Electricity",
            description=(
                f"Final electricity transferred from cancelled future invoice; {billing_period}"
            ),
            amount=preview["electricity_transfer_on_confirm"],
            existing=invoice.items.filter(
                description__startswith="Final electricity charge"
            ).first(),
        )

    invoice.status = "sent"
    invoice.description = f"Final settlement - lease ended {end_date:%Y-%m-%d}"
    invoice.save(update_fields=["status", "description", "updated_at"])

    note_text = f"Security applied to final lease settlement dated {end_date:%Y-%m-%d}."
    if notes:
        note_text += f" {notes.strip()}"
    if preview["security_applied"] > ZERO:
        payment = Payment.objects.create(
            lease=lease,
            payment_date=end_date,
            amount=preview["security_applied"],
            description="Security deposit applied to final lease balance",
            notes=note_text,
        )
        rebuild_payment_detail(
            payment=payment,
            lease_amount=preview["security_applied"],
            security_amount=ZERO,
            user=user,
            reason="Applied automatically by End Lease workflow",
        )
        SecurityDepositTransaction.objects.create(
            lease=lease,
            date=end_date,
            type="REFUND",
            amount=ZERO,
            deduction_amount=preview["security_applied"],
            deduction_reason="Applied to final lease balance",
            refund_status="PAID",
            payment=payment,
            notes=note_text,
        )

    if preview["security_refund"] > ZERO:
        _create_security_ledger_transfer(
            lease,
            amount=preview["security_refund"],
            transfer_date=end_date,
            user=user,
            notes=(
                f"Existing lease credit before transfer: Rs. "
                f"{preview['lease_credit']:,.2f}. {notes.strip()}"
            ).strip(),
        )

    lease.end_date = end_date
    lease.status = "ended"
    lease.notes = "\n".join(filter(None, [lease.notes, f"Lease ended: {notes.strip()}" if notes else ""]))
    lease.save(update_fields=["end_date", "status", "notes", "updated_at"])
    lease.unit_occupancies.filter(move_out_date__isnull=True).update(move_out_date=end_date)
    lease.recurringcharge_set.filter(
        Q(end_date__isnull=True) | Q(end_date__gt=end_date)
    ).update(end_date=end_date)

    preview["invoice"] = invoice
    preview["final_balance"] = _lease_balance(lease)
    return preview


@transaction.atomic
def rollback_end_lease(lease, *, restored_end_date: date, user=None, notes="") -> dict:
    """Reverse records created by End Lease while retaining an audit trail."""
    lease = lease.__class__.objects.select_for_update().get(pk=lease.pk)
    if lease.status != "ended":
        raise ValidationError("Only an ended lease can be rolled back.")
    ended_date = lease.end_date
    if restored_end_date <= ended_date:
        raise ValidationError("The restored lease end date must be after the move-out date.")

    settlement_invoices = list(
        Invoice.objects.filter(lease=lease).filter(
            Q(description=f"Final settlement - lease ended {ended_date:%Y-%m-%d}")
            | Q(description=f"Move-out settlement charges - lease ended {ended_date:%Y-%m-%d}")
        )
    )
    for invoice in settlement_invoices:
        invoice.status = "cancelled"
        invoice.notes = "\n".join(
            filter(
                None,
                [
                    invoice.notes,
                    f"End Lease rollback on {date.today():%Y-%m-%d}. {notes.strip()}".strip(),
                ],
            )
        )
        invoice.save(update_fields=["status", "notes", "updated_at"])

    restored_invoices = []
    cancellation_text = f"Cancelled by End Lease workflow because lease ended {ended_date:%Y-%m-%d}."
    for invoice in Invoice.objects.filter(
        lease=lease, status="cancelled", notes__contains=cancellation_text
    ):
        status_match = re.search(r"Original status: ([a-z_]+)\.", invoice.notes or "")
        invoice.status = status_match.group(1) if status_match else "sent"
        invoice.notes = "\n".join(
            filter(None, [invoice.notes, f"Restored by End Lease rollback on {date.today():%Y-%m-%d}."])
        )
        invoice.save(update_fields=["status", "notes", "updated_at"])
        restored_invoices.append(invoice)

    reversed_security = ZERO
    rollback_marker = f"End Lease rollback for {ended_date:%Y-%m-%d}"
    security_rows = lease.security_transactions.filter(date=ended_date, type="REFUND")
    for movement in security_rows:
        is_workflow_row = (
            "final lease settlement" in (movement.notes or "").lower()
            or "unused security transferred to the lease ledger" in (movement.notes or "").lower()
            or "pending security refund" in (movement.notes or "").lower()
            or "pending tenant refund" in (movement.notes or "").lower()
            or "applied to final lease balance" in (movement.deduction_reason or "").lower()
        )
        if not is_workflow_row or movement.refund_status == "CANCELLED":
            continue
        transfer_event = getattr(movement, "ledger_transfer_event", None)
        if transfer_event and not transfer_event.reversed_at:
            reversal_result = reverse_security_ledger_transfer(
                transfer_event,
                user=user,
                reason=f"{rollback_marker}. {notes.strip()}".strip(),
            )
            reversed_security += money(reversal_result["amount"])
            continue
        applied_payment = movement.payment
        if applied_payment and movement.deduction_amount > ZERO:
            reversal_exists = Payment.objects.filter(
                lease=lease, description=rollback_marker, notes__contains=f"Payment #{applied_payment.pk}"
            ).exists()
            if not reversal_exists:
                detail = getattr(applied_payment, "detail", None)
                lease_amount = money(detail.lease_amount if detail else applied_payment.amount)
                reversal = Payment.objects.create(
                    lease=lease,
                    payment_date=date.today(),
                    amount=-lease_amount,
                    description=rollback_marker,
                    notes=f"Reverses security application Payment #{applied_payment.pk}. {notes.strip()}".strip(),
                )
                rebuild_payment_detail(
                    payment=reversal,
                    lease_amount=-lease_amount,
                    security_amount=ZERO,
                    user=user,
                    reason="End Lease rollback",
                )
                reversed_security += lease_amount
        movement.refund_status = "CANCELLED"
        movement.notes = "\n".join(
            filter(None, [movement.notes, f"Cancelled by {rollback_marker}."])
        )
        movement.save(update_fields=["refund_status", "notes"])

    # Defensive finalization: End Lease-created security REFUND movements remain
    # auditable but must no longer be active after rollback.
    lease.security_transactions.filter(
        date=ended_date,
        type="REFUND",
    ).filter(
        Q(deduction_reason="Applied to final lease balance")
        | Q(deduction_reason="Transferred to lease ledger for tenant refund")
        | Q(notes__icontains="final lease settlement")
        | Q(notes__icontains="unused security transferred to the lease ledger")
    ).exclude(refund_status="CANCELLED").update(refund_status="CANCELLED")

    lease.recurringcharge_set.filter(end_date=ended_date).update(end_date=None)
    for occupancy in lease.unit_occupancies.filter(move_out_date=ended_date):
        occupancy.move_out_date = None
        occupancy.save(update_fields=["move_out_date", "active_lease_key", "updated_at"])

    lease.status = "active"
    lease.end_date = restored_end_date
    lease.notes = "\n".join(
        filter(None, [lease.notes, f"{rollback_marker}; restored end date {restored_end_date:%Y-%m-%d}. {notes.strip()}".strip()])
    )
    lease.save(update_fields=["status", "end_date", "notes", "updated_at"])
    return {
        "lease": lease,
        "ended_date": ended_date,
        "restored_end_date": restored_end_date,
        "settlement_invoices": settlement_invoices,
        "restored_invoices": restored_invoices,
        "reversed_security": reversed_security,
    }


def tenant_message(result: dict) -> str:
    tenant = result["lease"].tenant
    amount = result["amount_payable"]
    if result["refund_due"] > ZERO:
        closing = (
            f"We owe you Rs. {result['refund_due']:,.2f}. Please reply with your bank/account "
            "name, account/IBAN number, and bank name so accounts can arrange the refund."
        )
    elif amount > ZERO:
        closing = f"Your final amount payable is Rs. {amount:,.2f}. Please arrange payment."
    else:
        closing = "Your final account is settled and no payment is due."
    return (
        f"Dear {tenant.get_full_name()}, your lease ended on {result['end_date']:%d %b %Y}. "
        f"Your final account balance is Rs. {result['gross_balance']:,.2f}. Security applied: "
        f"Rs. {result['security_applied']:,.2f}. {closing}"
    )


def staff_message(result: dict) -> str:
    lease = result["lease"]
    future_action = (
        "cancelled"
        if result["future_invoice_action"] == "cancel"
        else "kept as approved charges"
    )
    return (
        f"Lease ended - accounts action\nTenant: {lease.tenant.get_full_name()}\n"
        f"Phone: {lease.tenant.phone or '-'}\nProperty/Unit: {lease.unit.property} / {lease.unit}\n"
        f"End date: {result['end_date']:%Y-%m-%d}\nFinal account balance before security: "
        f"Rs. {result['gross_balance']:,.2f}\nSecurity applied: Rs. {result['security_applied']:,.2f}\n"
        f"Tenant payable: Rs. {result['amount_payable']:,.2f}\nRefund due: Rs. {result['refund_due']:,.2f}\n"
        f"Invoices after end date: {len(result['future_invoices'])} ({future_action}), "
        f"total Rs. {result['future_invoice_total']:,.2f}\n"
        "Refund is pending until tenant account details are received and payment is recorded."
    )


def accounts_staff_for_lease(lease):
    """Use the existing property-aware WhatsApp accounts routing."""
    from whatsapp.services.handover.routing import eligible_staff

    route_context = SimpleNamespace(
        department="accounts",
        property=lease.unit.property,
        property_id=lease.unit.property_id,
        assigned_staff_id=None,
    )
    return eligible_staff(route_context)
