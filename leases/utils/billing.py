# leases/utils/billing.py
from __future__ import annotations
from calendar import monthrange
from django.db.models import Exists, OuterRef,Sum
from datetime import date
from decimal import Decimal
from typing import Optional, Tuple,  Dict, Any

from django.apps import apps
from django.db import transaction
from invoices.models import RecurringCharge, ItemCategory
from typing import List
from datetime import date
from decimal import Decimal
from typing import Dict, Any, List, Tuple, Optional
from datetime import date, timedelta
# Use your real display names (these exist in your DB)
RENT         = "Rent"
MAINTENANCE  = "Society Maintenance"
SECURITY     = "Security Deposit"
WATER       = "Water"
INTERNET    = "Internet"
AGREEMENT_CAT = "Agreement Fee"    # canonical


CATEGORY_ALIASES = {
    "SECURITY": SECURITY,
    "Security": SECURITY,
    "Security Deposit": SECURITY,

    "Maintenance": MAINTENANCE,
    "Society Maintenance": MAINTENANCE,

    "Rent": RENT,

    # Internet is already fine – name in DB is "Internet"
    "Internet": INTERNET,

    # Force "Water" to use "Water Charges"
    "Water": "Water Charges",
    "Water Charges": "Water Charges",

    # Agreement: treat both as the same canonical category
    "Agreement Charges": "Agreement Fee",
    "Agreement Fee": "Agreement Fee",
}


# -----------------------
# helpers
# -----------------------

def _monthly_total(lease) -> Decimal:
    """
    Convenience: total monthly charge (rent + maintenance) for a lease.
    """
    return (lease.monthly_rent or Decimal("0")) + (lease.society_maintenance or Decimal("0"))

def _first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _invoice_dates_for_month(month_first: date) -> tuple[date, date]:
    issue = month_first
    return issue, issue + timedelta(days=5)


def _security_issue_date(lease) -> date:
    # Prefer agreement_date, else start_date, else today (last resort)
    return getattr(lease, "agreement_date", None) or lease.start_date or date.today()


def _first_of_next_month(ref: Optional[date] = None) -> date:
    today = ref or date.today()
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def _add_month(d: date) -> date:
    # first day of next month
    return date(d.year + (1 if d.month == 12 else 0), (1 if d.month == 12 else d.month + 1), 1)


def _month_starts_between(start: date, end: date):
    """Yield first day of each month from start..end (both inclusive)."""
    cur = _first_of_month(start)
    last = _first_of_month(end)
    while cur <= last:
        yield cur
        cur = _add_month(cur)


def _get_model(app_label: str, model_name: str):
    return apps.get_model(app_label, model_name)


def _get_or_create_category(name: str):
    """
    Resolve by canonical name; if missing, create the canonical one.
    """
    ItemCategory = _get_model("invoices", "ItemCategory")
    if ItemCategory is None:
        raise RuntimeError("invoices.ItemCategory not found")
    canonical = CATEGORY_ALIASES.get(name, name)
    cat = ItemCategory.objects.filter(name__iexact=canonical).first()
    if not cat:
        cat = ItemCategory.objects.create(name=canonical, is_active=True)
    return cat


def _create_invoice_with_items(
    *, lease, description: str, issue_date: date, due_date: Optional[date],
    # (line_description, amount, category_name)
    items: List[tuple[str, Decimal, str]]
):
    Invoice = _get_model("invoices", "Invoice")
    InvoiceItem = _get_model("invoices", "InvoiceItem")
    if Invoice is None or InvoiceItem is None:
        raise RuntimeError("invoices.Invoice/InvoiceItem required")

    total = sum((amt for _, amt, _ in items), Decimal("0"))
    inv = Invoice.objects.create(
        lease=lease,
        description=description,
        issue_date=issue_date,
        due_date=due_date or issue_date,
        amount=total,
    )
    for line_desc, amt, category_name in items:
        cat = _get_or_create_category(category_name)
        InvoiceItem.objects.create(
            invoice=inv,
            category=cat,
            description=line_desc,  # ← clean label
            amount=amt,
            is_recurring=False,
        )
    return inv


def _ensure_or_update_recurring(
    *,
    lease,
    category: str,
    amount: Decimal,
    start: date,
    end: Optional[date],
):
    """
    Create/update invoices.RecurringCharge for a (lease, category).
    """
    RecurringCharge = _get_model("invoices", "RecurringCharge")
    if RecurringCharge is None:
        return None

    cat = _get_or_create_category(category)

    key = {"lease": lease, "category": cat}
    defaults = {
        "amount": amount,
        "start_date": start,
        "end_date": end,
        "active": True,
        "day_of_month": 1,
        "kind": "FIXED",
        "scope": "LEASE",
        "combine_with_rent": True,
        "description": category.title(),
    }
    obj, _ = RecurringCharge.objects.update_or_create(defaults=defaults, **key)
    return obj


def apply_initial_billing(
    lease,
    *,
    include_backfill: bool = True,
    update_existing: bool = False,
) -> None:
    """
    Create:
      - ONE invoice for Agreement Fee (if any and not already invoiced)
      - Monthly invoices from lease.start_date → today (or lease.end_date)
      - RecurringCharge rows for rent / maintenance / water / internet
    """
    today = date.today()
    effective_end = min(today, lease.end_date) if lease.end_date else today

    # ------------- ONE-TIME AGREEMENT FEE -------------
    agreement_amount = lease.agreement_charges or Decimal("0")

    if agreement_amount > 0:
        Invoice = _get_model("invoices", "Invoice")
        InvoiceItem = _get_model("invoices", "InvoiceItem")
        if Invoice is not None and InvoiceItem is not None:
            # Use canonical "Agreement Fee" category (id 5 in your screenshot)
            agreement_cat = _get_or_create_category("Agreement Fee")

            # "Already billed" means: there is any invoice item for this lease in that category
            already_billed = InvoiceItem.objects.filter(
                invoice__lease=lease,
                category=agreement_cat,
            ).exists()

            if not already_billed:
                issue = lease.agreement_date or lease.start_date or today
                due   = issue

                # This helper will again route "Agreement Fee" / "Agreement Charges"
                # through CATEGORY_ALIASES, so category id 5 is reused.
                _create_invoice_with_items(
                    lease=lease,
                    description=f"Agreement Charges {issue:%b %Y}",
                    issue_date=issue,
                    due_date=due,
                    items=[
                        ("Agreement Charges", agreement_amount, "Agreement Fee"),
                    ],
                )

    # ------------- MONTHLY INVOICES (BACKFILL) -------------
    if lease.start_date and lease.start_date <= effective_end:
        months = list(_month_starts_between(lease.start_date, effective_end))

        if include_backfill:
            # from lease.start_date → today/end
            for m in months:
                issue_date, due_date = _invoice_dates_for_month(m)
                _create_or_update_month_invoice(
                    lease,
                    m,
                    update_existing=update_existing,
                    issue_date=issue_date,
                    due_date=due_date,
                )
        else:
            # only current month (if present), no historic backfill
            cur_first = date(today.year, today.month, 1)
            if cur_first in months:
                issue_date, due_date = _invoice_dates_for_month(cur_first)
                _create_or_update_month_invoice(
                    lease,
                    cur_first,
                    update_existing=update_existing,
                    issue_date=issue_date,
                    due_date=due_date,
                )

    # ------------- RECURRING ROWS -------------
    start = lease.start_date
    end   = lease.end_date

    # If lease already ended before next month, skip creating recurring rows
    skip_recurring = lease.end_date and lease.end_date < _first_of_next_month(today)

    if not skip_recurring and start:
        if (lease.monthly_rent or 0) > 0:
            _ensure_or_update_recurring(
                lease=lease,
                category=RENT,
                amount=lease.monthly_rent,
                start=start,
                end=end,
            )
        if (lease.society_maintenance or 0) > 0:
            _ensure_or_update_recurring(
                lease=lease,
                category=MAINTENANCE,
                amount=lease.society_maintenance,
                start=start,
                end=end,
            )
        if (lease.water_charges or 0) > 0:
            _ensure_or_update_recurring(
                lease=lease,
                category=WATER,
                amount=lease.water_charges,
                start=start,
                end=end,
            )
        if (lease.internet_charges or 0) > 0:
            _ensure_or_update_recurring(
                lease=lease,
                category=INTERNET,
                amount=lease.internet_charges,
                start=start,
                end=end,
            )

        _ensure_current_cycle_invoice_items_if_missing(lease)


# -----------------------
# PREVIEW (no DB writes)
# -----------------------


@transaction.atomic

def preview_initial_billing(lease) -> Dict[str, Any]:
    """
    Return a dict showing exactly what would be created for initial billing.
    No writes to DB.
    """
    plan: Dict[str, Any] = {
        "invoices_to_create": [],
        "recurring": [],
        "notes": [],
    }

    sec_issue = _security_issue_date(lease)
    sec_due = sec_issue + timedelta(days=5)
    sec = lease.security_deposit or Decimal("0")

    # (security preview is disabled now, as per your comment)
    # if sec > 0:
    #     plan["invoices_to_create"].append(...)

    today = date.today()
    effective_end = min(today, lease.end_date) if lease.end_date else today

    # ---------- Monthly invoices preview ----------
    if lease.start_date and lease.start_date <= effective_end:
        rent   = lease.monthly_rent or Decimal("0")
        maint  = lease.society_maintenance or Decimal("0")
        water  = getattr(lease, "water_charges", 0) or Decimal("0")
        net    = getattr(lease, "internet_charges", 0) or Decimal("0")

        month_list = list(_month_starts_between(lease.start_date, effective_end))
        month_rows = []
        has_existing = False

        for m in month_list:
            exists = _find_existing_invoice_for_month(lease, m) is not None
            month_rows.append({"month": m, "exists": exists})
            if exists:
                has_existing = True

        plan["backfill_months"] = month_list
        plan["has_existing_invoices"] = has_existing

        # If any month is before the first day of this month → ask confirmation
        first_of_this_month = date(today.year, today.month, 1)
        plan["requires_backfill_confirmation"] = any(
            m < first_of_this_month for m in month_list
        )

        # For each month, build items (Rent, Maintenance, Water, Internet)
        for m in month_list:
            issue_date, due_date = _invoice_dates_for_month(m)
            items: List[tuple[str, Decimal, str]] = []

            if rent > 0:
                items.append((f"Rent {m:%b %Y}", rent, RENT))
            if maint > 0:
                items.append((f"Society Maintenance {m:%b %Y}", maint, MAINTENANCE))
            if water > 0:
                items.append((f"Water {m:%b %Y}", water, WATER))
            if net > 0:
                items.append((f"Internet {m:%b %Y}", net, INTERNET))

            if items:
                plan["invoices_to_create"].append({
                    "description": f"Monthly charges {m:%b %Y}",
                    "issue_date": issue_date,
                    "due_date": due_date,
                    "items": items,   # stays as list of tuples, same structure as before
                })

    # ---------- Recurring preview (Rent/Maint/Water/Internet) ----------
    start = lease.start_date
    end = lease.end_date

    RecurringCharge = _get_model("invoices", "RecurringCharge")
    if RecurringCharge is not None:
        for tag, amt in (
            (RENT,        lease.monthly_rent or 0),
            (MAINTENANCE, lease.society_maintenance or 0),
            (WATER,       getattr(lease, "water_charges", 0) or 0),
            (INTERNET,    getattr(lease, "internet_charges", 0) or 0),
        ):
            if amt and amt > 0:
                cat = _get_or_create_category(tag)
                existing = RecurringCharge.objects.filter(
                    lease=lease, category=cat
                ).first()
                plan["recurring"].append({
                    "category": tag,
                    "action": "update" if existing else "create",
                    "before": {
                        "amount": getattr(existing, "amount", None),
                        "start_date": getattr(existing, "start_date", None),
                        "end_date": getattr(existing, "end_date", None),
                        "active": getattr(existing, "active", None) if existing else None,
                    },
                    "after": {
                        "amount": Decimal(amt),
                        "start_date": start,
                        "end_date": end,
                        "active": True,
                        "day_of_month": 1,
                    },
                })

    return plan

# -----------------------
# APPLY (writes to DB)
# -----------------------
# -----------------------
# PREVIEW (no DB writes)
# -----------------------
@transaction.atomic
def preview_billing_on_change(lease, old_lease) -> Dict[str, Any]:
    """
    Return a dict describing what would change on update, without writing DB.

    Adds:
      - backfill_months: [{"month": date, "exists": bool}, ...]
      - has_existing_invoices: bool
      - requires_backfill_confirmation: bool
      - recurring: list of diffs for rent/maintenance
      - security_item / requires_security_confirmation
      - recurring_start / recurring_end
      - rent/maintenance change flags & old/new amounts
    """
    plan: Dict[str, Any] = {
        "recurring": [],
        "security_item": None,
        "requires_security_confirmation": False,
        "backfill_months": [],
        "has_existing_invoices": False,
        "requires_backfill_confirmation": False,
    }

    ZERO = Decimal("0.00")

    # ---- rent / maintenance deltas (for UI) ----
    old_rent   = old_lease.monthly_rent or ZERO
    old_maint  = old_lease.society_maintenance or ZERO
    old_water  = old_lease.water_charges or ZERO
    old_net    = old_lease.internet_charges or ZERO

    new_rent   = lease.monthly_rent or ZERO
    new_maint  = lease.society_maintenance or ZERO
    new_water  = lease.water_charges or ZERO
    new_net    = lease.internet_charges or ZERO

    old_total = old_rent + old_maint + old_water + old_net
    new_total = new_rent + new_maint + new_water + new_net

    rent_changed      = (old_rent != new_rent)
    maint_changed     = (old_maint != new_maint)
    water_changed     = (old_water != new_water)
    internet_changed  = (old_net != new_net)
    end_date_changed  = (old_lease.end_date != lease.end_date)

    rent_or_term_changed = (
        rent_changed or maint_changed or water_changed or internet_changed or end_date_changed
    )


    plan.update({
        "old_rent": old_rent,
        "new_rent": new_rent,
        "rent_changed": rent_changed,

        "old_maintenance": old_maint,
        "new_maintenance": new_maint,
        "maintenance_changed": maint_changed,

        "old_water": old_water,
        "new_water": new_water,
        "water_changed": water_changed,

        "old_internet": old_net,
        "new_internet": new_net,
        "internet_changed": internet_changed,

        "old_rent_total": old_total,
        "new_rent_total": new_total,

        "end_date_changed": end_date_changed,
        "old_end_date": old_lease.end_date,
        "new_end_date": lease.end_date,
    })

    # ---- Recurring preview (start from NEXT month, not lease.start_date) ----
    if rent_or_term_changed:
        today = date.today()
        recurring_start = _first_of_next_month(today)
        # don't start before lease start
        if lease.start_date and lease.start_date > recurring_start:
            recurring_start = lease.start_date

        RecurringCharge = _get_model("invoices", "RecurringCharge")
        if RecurringCharge is not None:
            for tag, new_amt in (
                (RENT,        lease.monthly_rent or 0),
                (MAINTENANCE, lease.society_maintenance or 0),
                (WATER,       lease.water_charges or 0),
                (INTERNET,    lease.internet_charges or 0),
            ):
                if new_amt and new_amt > 0:
                    cat = _get_or_create_category(tag)
                    existing = RecurringCharge.objects.filter(
                        lease=lease, category=cat
                    ).first()
                    plan["recurring"].append({
                        "category": tag,
                        "action": "update" if existing else "create",
                        "before": {
                            "amount": getattr(existing, "amount", None),
                            "start_date": getattr(existing, "start_date", None),
                            "end_date": getattr(existing, "end_date", None),
                            "active": getattr(existing, "active", None) if existing else None,
                        },
                        "after": {
                            "amount": Decimal(new_amt),
                            "start_date": recurring_start,
                            "end_date": lease.end_date,
                            "active": True,
                            "day_of_month": 1,
                        },
                    })

        plan["recurring_start"] = recurring_start
    else:
        # no rent/term change → just echo current lease bounds
        plan["recurring_start"] = lease.start_date

    # ---- Backfill preview (lease.start_date → today or end) ----
    # ONLY when rent/maint/end_date changed
    today = date.today()
    effective_end = min(today, lease.end_date) if lease.end_date else today
    if rent_or_term_changed and lease.start_date and lease.start_date <= effective_end:
        months = list(_month_starts_between(lease.start_date, effective_end))
        first_of_this_month = _first_of_month(today)
        plan["requires_backfill_confirmation"] = any(
            m < first_of_this_month for m in months
        )

        rows = []
        has_existing = False
        for m in months:
            exists = _find_existing_invoice_for_month(lease, m) is not None
            rows.append({"month": m, "exists": exists})
            if exists:
                has_existing = True
        plan["backfill_months"] = rows
        plan["has_existing_invoices"] = has_existing

    # used by your JS modal
    plan["recurring_end"] = lease.end_date

    # ---- Security item change preview ----
    if (old_lease.security_deposit or ZERO) != (lease.security_deposit or ZERO):
        Invoice = _get_model("invoices", "Invoice")
        InvoiceItem = _get_model("invoices", "InvoiceItem")
        if Invoice and InvoiceItem:
            sec_inv = (
                Invoice.objects.filter(
                    lease=lease, description__icontains="security"
                ).order_by("issue_date", "id").last()
            )
            sec_cat = _get_or_create_category(SECURITY)
            sec_item = None
            inv_amount = None
            if sec_inv:
                sec_item = (
                    InvoiceItem.objects.filter(
                        invoice=sec_inv, category=sec_cat
                    ).order_by("id").last()
                )
                inv_amount = sec_item.amount if sec_item else ZERO

            plan["security_item"] = {
                "invoice_id": getattr(sec_inv, "id", None),
                "before": inv_amount,
                "after": lease.security_deposit or ZERO,
            }
            if inv_amount != (lease.security_deposit or ZERO):
                plan["requires_security_confirmation"] = True

    return plan

# -----------------------
# APPLY (writes to DB)
# -----------------------
from decimal import Decimal
from datetime import date, timedelta
from typing import Tuple, Optional
from datetime import date, timedelta

@transaction.atomic
def update_billing_on_change(
    lease,
    old_lease,
    *,
    confirm_security_update: bool,
    include_backfill: bool = True,
    update_existing: bool = False,
) -> Tuple[bool, Optional[dict]]:
    """
    Apply updates for rent/maintenance/end_date + security deposit.
    Also (optionally) backfill per-month invoices from lease.start_date to today/end.

    Returns (requires_user_confirmation, context) if security needs confirm.
    """
    requires_confirmation = False
    context: Optional[dict] = None

    ZERO = Decimal("0.00")

    old_rent   = old_lease.monthly_rent or ZERO
    new_rent   = lease.monthly_rent or ZERO
    old_maint  = old_lease.society_maintenance or ZERO
    new_maint  = lease.society_maintenance or ZERO
    old_water  = old_lease.water_charges or ZERO
    new_water  = lease.water_charges or ZERO
    old_net    = old_lease.internet_charges or ZERO
    new_net    = lease.internet_charges or ZERO

    old_total = old_rent + old_maint + old_water + old_net
    new_total = new_rent + new_maint + new_water + new_net


    rent_changed       = (old_rent != new_rent)
    maint_changed      = (old_maint != new_maint)
    water_changed      = (old_water != new_water)
    internet_changed   = (old_net != new_net)
    end_date_changed   = (old_lease.end_date != lease.end_date)

    rent_or_term_changed = (
        rent_changed or maint_changed or water_changed or internet_changed or end_date_changed
    )


    # ---- 1) BACKFILL APPLY (ONLY if rent/term changed) ----
    today = date.today()
    effective_end = min(today, lease.end_date) if lease.end_date else today

    if rent_or_term_changed and lease.start_date and lease.start_date <= effective_end:
        months = list(_month_starts_between(lease.start_date, effective_end))
        if include_backfill:
            # from lease.start_date → today/end
            for m in months:
                issue_date, due_date = _invoice_dates_for_month(m)
                _create_or_update_month_invoice(
                    lease,
                    m,
                    update_existing=update_existing,
                    issue_date=issue_date,
                    due_date=due_date,
                )
        else:
            # only CURRENT month (if present), no historic backfill
            cur_first = date(today.year, today.month, 1)
            if cur_first in months:
                issue_date, due_date = _invoice_dates_for_month(cur_first)
                _create_or_update_month_invoice(
                    lease,
                    cur_first,
                    update_existing=update_existing,
                    issue_date=issue_date,
                    due_date=due_date,
                )

    # ---- 2) RECURRING APPLY (start = first day of next month) ----
    if rent_or_term_changed:
        # new rent/maintenance should apply from NEXT month
        recurring_start = _first_of_next_month(today)
        # but don't start before lease.start_date
        if lease.start_date and lease.start_date > recurring_start:
            recurring_start = lease.start_date

        # if lease already ends before that start, don't create recurring at all
        if not lease.end_date or lease.end_date >= recurring_start:
            if new_rent > 0:
                _ensure_or_update_recurring(
                    lease=lease,
                    category=RENT,
                    amount=new_rent,
                    start=recurring_start,
                    end=lease.end_date,
                )
            if new_maint > 0:
                _ensure_or_update_recurring(
                    lease=lease,
                    category=MAINTENANCE,
                    amount=new_maint,
                    start=recurring_start,
                    end=lease.end_date,
                )
            if new_water > 0:
                _ensure_or_update_recurring(
                    lease=lease,
                    category="Water",
                    amount=new_water,
                    start=recurring_start,
                    end=lease.end_date,
                )
            if new_net > 0:
                _ensure_or_update_recurring(
                    lease=lease,
                    category="Internet",
                    amount=new_net,
                    start=recurring_start,
                    end=lease.end_date,
                )

    # ---- 3) SECURITY ITEM CHANGE APPLY (no backfill tied to this) ----
    # The following is adding/updating secuirty deposit amount in the ledger by updating
    # invoice. However, we have change the logic so it shouldnt add or update in the invoice
    """
    if (old_lease.security_deposit or ZERO) != (lease.security_deposit or ZERO):
        Invoice = _get_model("invoices", "Invoice")
        InvoiceItem = _get_model("invoices", "InvoiceItem")
        if Invoice and InvoiceItem:
            sec_inv = (
                Invoice.objects.filter(
                    lease=lease, description__icontains="security"
                ).order_by("issue_date", "id").last()
            )
            sec_cat = _get_or_create_category(SECURITY)
            lease_amount = lease.security_deposit or ZERO

            if sec_inv is None:
                if not confirm_security_update:
                    requires_confirmation = True
                    context = {
                        "invoice_id": None,
                        "invoice_security_amount": ZERO,
                        "lease_security_amount_before": old_lease.security_deposit or ZERO,
                        "lease_security_amount_now": lease_amount,
                        "note": "No security invoice exists. A new one will be created.",
                    }
                    return requires_confirmation, context

                sec_issue = _security_issue_date(lease)
                sec_due = sec_issue + timedelta(days=5)
                sec_inv = _create_invoice_with_items(
                    lease=lease,
                    description="Security Deposit",
                    issue_date=sec_issue,
                    due_date=sec_due,
                    items=[("Security Deposit", lease_amount, SECURITY)],
                )
            else:
                sec_item = (
                    InvoiceItem.objects.filter(
                        invoice=sec_inv, category=sec_cat
                    ).order_by("id").last()
                )
                inv_amount = (sec_item.amount if sec_item else ZERO)

                if not confirm_security_update and inv_amount != lease_amount:
                    requires_confirmation = True
                    context = {
                        "invoice_id": sec_inv.id,
                        "invoice_security_amount": inv_amount,
                        "lease_security_amount_before": old_lease.security_deposit or ZERO,
                        "lease_security_amount_now": lease_amount,
                    }
                    return requires_confirmation, context

                if sec_item is None:
                    InvoiceItem.objects.create(
                        invoice=sec_inv,
                        category=sec_cat,
                        description="Security Deposit (added)",
                        amount=lease_amount,
                        is_recurring=False,
                    )
                else:
                    sec_item.amount = lease_amount
                    sec_item.description = "Security Deposit (updated)"
                    sec_item.save(update_fields=["amount", "description"])
    """
    # heal current-month header-without-items (optional)
    _ensure_current_cycle_invoice_items_if_missing(lease)

    if context is None:
        context = {
            "old_rent": old_rent,
            "new_rent": new_rent,
            "old_maintenance": old_maint,
            "new_maintenance": new_maint,
            "old_rent_total": old_total,
            "new_rent_total": new_total,
            "end_date_changed": end_date_changed,
            "old_end_date": old_lease.end_date,
            "new_end_date": lease.end_date,
        }

    return requires_confirmation, context

# Two tiny helpers if you want to call these directly after a user clicks "Yes"
@transaction.atomic
def apply_billing_on_change_with_confirmation(lease, old_lease):
    return update_billing_on_change(lease, old_lease, confirm_security_update=True)


@transaction.atomic
def apply_billing_on_change_without_confirmation(lease, old_lease):
    return update_billing_on_change(lease, old_lease, confirm_security_update=False)


# --- add inside leases/utils/billing.py (near other helpers) ---

def security_deposit_balance(lease) -> Decimal:
    """
    Security deposit balance TO COLLECT for a lease, based on SecurityDepositTransaction.

    Logic:
      - required = sum of REQUIRED rows
      - paid_in = sum of PAYMENT rows
      - adjust = sum of ADJUST rows (can be + or - to tweak required)
      - we ignore REFUND/DAMAGE for "to collect" (those are money going OUT,
        usually at move-out; they don't reopen the initial requirement).

      balance_to_collect = max((required + adjust) - paid_in, 0)
    """
    ZERO = Decimal("0.00")

    SDT = _get_model("invoices", "SecurityDepositTransaction")
    if SDT is None:
        # Fallback: use lease.security_deposit if model not available
        return lease.security_deposit or ZERO

    qs = SDT.objects.filter(lease=lease)

    # Aggregate totals by type
    rows = qs.values("type").annotate(total=Sum("amount"))

    required = ZERO
    paid_in = ZERO
    adjust = ZERO

    for row in rows:
        t = row["type"]
        total = row["total"] or ZERO
        if t == "REQUIRED":
            required += total
        elif t == "PAYMENT":
            paid_in += total
        elif t == "ADJUST":
            adjust += total
        # REFUND / DAMAGE are ignored for "to collect" here

    # If no REQUIRED rows but lease.security_deposit is set, use it as fallback
    if required == ZERO and (lease.security_deposit or ZERO) > ZERO:
        required = lease.security_deposit or ZERO

    effective_required = required + adjust
    balance = effective_required - paid_in
    return balance if balance > ZERO else ZERO
from decimal import Decimal
from datetime import date

def _ensure_current_cycle_invoice_items_if_missing(lease) -> None:
    """
    Ensure the current month's invoice has Rent / Maintenance / Water / Internet.
    Unlike _create_or_update_month_invoice, this helper ONLY works with TODAY's month.
    """
    Invoice = _get_model("invoices", "Invoice")
    InvoiceItem = _get_model("invoices", "InvoiceItem")
    if Invoice is None or InvoiceItem is None:
        return

    today = date.today()
    first_next = _first_of_next_month(today)

    # Only apply if lease started before next month
    if not lease.start_date or lease.start_date >= first_next:
        return

    # Invoice label for THIS month
    month_label = f"Monthly charges {today:%b %Y}"

    # Find or create invoice header
    inv = (
        Invoice.objects.filter(
            lease=lease,
            description__icontains="monthly charges"
        )
        .order_by("issue_date", "id")
        .last()
    )

    if inv is None:
        inv = Invoice.objects.create(
            lease=lease,
            description=month_label,
            issue_date=today,
            due_date=today,
            amount=Decimal("0"),
        )

    # Amounts
    rent_amt  = lease.monthly_rent or Decimal("0")
    maint_amt = lease.society_maintenance or Decimal("0")
    water_amt = lease.water_charges or Decimal("0")
    net_amt   = lease.internet_charges or Decimal("0")

    def ensure_line(cat_name: str, amount: Decimal, desc: str):
        if amount <= 0:
            return
        cat = _get_or_create_category(cat_name)
        exists = InvoiceItem.objects.filter(invoice=inv, category=cat).exists()
        if not exists:
            InvoiceItem.objects.create(
                invoice=inv,
                category=cat,
                description=desc,
                amount=amount,
                is_recurring=False,
            )

    # Add missing lines for this month
    ensure_line(RENT,        rent_amt,  f"Rent {today:%b %Y}")
    ensure_line(MAINTENANCE, maint_amt, f"Society Maintenance {today:%b %Y}")
    ensure_line(WATER,       water_amt, f"Water {today:%b %Y}")
    ensure_line(INTERNET,    net_amt,   f"Internet {today:%b %Y}")

def _find_existing_invoice_for_month(lease, month_first: date):
    """Return an existing monthly invoice for that lease+month if present, else None."""
    Invoice = _get_model("invoices", "Invoice")
    if Invoice is None:
        return None
    label = f"{month_first:%b %Y}"
    try:
        # tweak the filter if you store the month differently
        return Invoice.objects.filter(
            lease=lease, description__icontains=label
        ).latest("id")
    except Invoice.DoesNotExist:
        return None


def _create_or_update_month_invoice(
    lease,
    month_first: date,
    *,
    update_existing: bool,
    issue_date: date,
    due_date: date,
):
    """
    Create a monthly invoice, or update line amounts if it already exists (when allowed).
    Returns (changed: bool, invoice).
    """
    InvoiceItem = _get_model("invoices", "InvoiceItem")
    inv = _find_existing_invoice_for_month(lease, month_first)

    rent  = lease.monthly_rent or Decimal("0")
    maint = lease.society_maintenance or Decimal("0")
    water = lease.water_charges or Decimal("0")
    net   = lease.internet_charges or Decimal("0")

    # -------------------
    # CREATE NEW INVOICE
    # -------------------
    if inv is None:
        items: List[tuple[str, Decimal, str]] = []

        if rent > 0:
            items.append((f"Rent {month_first:%b %Y}", rent, RENT))

        if maint > 0:
            items.append(
                (f"Society Maintenance {month_first:%b %Y}", maint, MAINTENANCE)
            )

        if water > 0:
            items.append((f"Water {month_first:%b %Y}", water, WATER))

        if net > 0:
            items.append((f"Internet {month_first:%b %Y}", net, INTERNET))

        inv = _create_invoice_with_items(
            lease=lease,
            description=f"Monthly charges {month_first:%b %Y}",
            issue_date=issue_date,
            due_date=due_date,
            items=items,
        )
        return True, inv

    # -------------------
    # UPDATE EXISTING
    # -------------------
    if not update_existing or InvoiceItem is None:
        return False, inv  # leave it as-is

    # Upsert items keyed by "type" (your existing design)
    type_map = {
        RENT:        (f"Rent {month_first:%b %Y}", rent),
        MAINTENANCE: (f"Society Maintenance {month_first:%b %Y}", maint),
        WATER:       (f"Water {month_first:%b %Y}", water),
        INTERNET:    (f"Internet {month_first:%b %Y}", net),
    }

    changed = False

    for type_code, (desc, amount) in type_map.items():
        if amount <= 0:
            continue
        item, created = InvoiceItem.objects.get_or_create(
            invoice=inv,
            type=type_code,  # keep your existing 'type' field
            defaults={
                "description": desc,
                "amount": amount,
            },
        )
        if created:
            changed = True
        else:
            if item.description != desc or item.amount != amount:
                item.description = desc
                item.amount = amount
                item.save(update_fields=["description", "amount"])
                changed = True

    return changed, inv
