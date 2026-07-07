# invoices/services.py
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import re

# IMPORTANT: import Lease from *this* app to avoid "name 'Lease' is not defined"
from django.apps import apps
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Invoice, InvoiceItem, ItemCategory, RecurringCharge, WaterBill


def first_of_month(d: date) -> date:
    return d.replace(day=1)


def invoice_due_date_from_lease(
    lease, issue_date: date, fallback: date | None = None
) -> date:
    """
    Resolve an invoice due date from Lease.due_date text for the invoice month.
    Examples: "5th of each month.", "5", "10th" -> that day in issue_date's month.
    """
    if not issue_date:
        return fallback or timezone.localdate()

    raw_due_date = (getattr(lease, "due_date", "") or "").strip()
    match = re.search(
        r"(?<!\d)([1-9]|[12]\d|3[01])(?:st|nd|rd|th)?(?!\d)",
        raw_due_date,
        re.IGNORECASE,
    )
    if not match:
        return fallback or issue_date

    due_day = int(match.group(1))
    last_day = monthrange(issue_date.year, issue_date.month)[1]
    return date(issue_date.year, issue_date.month, min(due_day, last_day))


def _lease_qs():
    """Resolve the Lease model at runtime to avoid circular/ordering issues."""
    try:
        Lease = apps.get_model("leases", "Lease")
    except LookupError:
        Lease = apps.get_model("invoices", "Lease")
    return Lease.objects.all()


# add near other imports


def _first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def last_of_month(d: date) -> date:
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def _add_months(d: date, n: int) -> date:
    y, m = d.year, d.month + n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1)


# ...
from datetime import datetime

from django.db.models import Q

from invoices.models import Invoice  # adjust name if needed

# ---------- date helpers ----------


def first_day_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def last_day_of_month(d: date) -> date:
    # simplest: go to first of next month, subtract 1 day
    if d.month == 12:
        return date(d.year, 12, 31)
    from datetime import timedelta

    first_next = date(d.year + (d.month // 12), ((d.month % 12) + 1), 1)
    return first_next - timedelta(days=1)


def month_iter(start: date, end: date):
    """
    Yield the first day of each month from start..end inclusive.
    """
    if not start or not end:
        return
    cur = first_day_of_month(start)
    end_month = first_day_of_month(end)
    while cur <= end_month:
        yield cur
        # bump month
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)


def first_day_of_next_month(today: date) -> date:
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


# ---------- invoice helpers (adjust to your schema) ----------

RENT_CATEGORY = "RENT"
MAINT_CATEGORY = "MAINTENANCE"


def _monthly_total(lease: Lease) -> Decimal:
    rent = lease.monthly_rent or Decimal("0")
    maint = lease.society_maintenance or Decimal("0")
    return rent + maint


def _get_month_invoice(lease: Lease, month_start: date):
    """
    Return the main rent/maintenance invoice for that month (if any).
    You might have multiple rows; adjust the query as needed.
    """
    month_end = last_day_of_month(month_start)
    return Invoice.objects.filter(
        lease=lease,
        date__gte=month_start,
        date__lte=month_end,
        category=RENT_CATEGORY,  # or however you tag “main rent” rows
    ).first()


def _ensure_month_invoice(lease: Lease, month_start: date, amount: Decimal):
    """
    Create or update a monthly rent invoice for given month.
    """
    month_end = last_day_of_month(month_start)
    inv = _get_month_invoice(lease, month_start)
    if inv:
        inv.amount = amount
        inv.due_date = month_start  # or any rule you use
        inv.save(update_fields=["amount", "due_date"])
        return inv, False

    return Invoice.objects.create(
        lease=lease,
        date=month_start,
        due_date=month_start,
        amount=amount,
        category=RENT_CATEGORY,
        description=f"Monthly rent for {month_start.strftime('%b %Y')}",
    ), True


def ensure_month_invoice(lease, period_date):
    """
    Return the single invoice for (lease, period_date), creating it if needed.
    period_date should be the first day of that month.
    """
    # Build defaults, including amount=0.00 if the field exists on Invoice
    fallback_due_date = period_date + timedelta(days=7)
    defaults = {
        "due_date": invoice_due_date_from_lease(
            lease, period_date, fallback=fallback_due_date
        ),
        "description": f"Invoice for {period_date:%B %Y}",
    }
    invoice_fields = {f.name for f in Invoice._meta.fields}
    if "amount" in invoice_fields:
        defaults["amount"] = Decimal("0.00")

    inv, _ = Invoice.objects.get_or_create(
        lease=lease, issue_date=period_date, defaults=defaults
    )
    return inv


def active_leases_qs():
    # adapt to your status field; using 'active' as seen in your codebase
    return _lease_qs().filter(status="active")


def _rule_targets(rc: RecurringCharge):
    if rc.scope == "LEASE" and rc.lease_id:
        return active_leases_qs().filter(pk=rc.lease_id)
    if rc.scope == "PROPERTY" and rc.property_id:
        return active_leases_qs().filter(unit__property_id=rc.property_id)
    return active_leases_qs()


def apply_fixed_recurring(period_date: date, cutoff_today: bool = False):
    """Apply all FIXED recurring rules into invoices for period_date."""
    from .models import InvoiceItem, RecurringCharge  # local to avoid import loops

    rules = RecurringCharge.objects.filter(active=True, kind="FIXED").select_related(
        "lease", "property", "category"
    )

    period_first = first_of_month(period_date)
    period_last = last_of_month(period_date)
    today = date.today()
    is_current_month = (
        period_first.year == today.year and period_first.month == today.month
    )

    for rc in rules:
        # start must be in/before this period
        if rc.start_date and rc.start_date > period_last:
            continue
        # end must not be earlier than either the start of the period
        # OR 'today' when we are generating the current month with cutoff_today=True
        if rc.end_date:
            end_cut = today if (cutoff_today and is_current_month) else period_first
            if rc.end_date < end_cut:
                continue

        # choose targets by scope
        if rc.scope == "LEASE" and rc.lease_id:
            targets = active_leases_qs().filter(pk=rc.lease_id)
        elif rc.scope == "PROPERTY" and rc.property_id:
            targets = active_leases_qs().filter(unit__property_id=rc.property_id)
        else:  # GLOBAL
            targets = active_leases_qs()

        for lease in targets:
            inv = ensure_month_invoice(lease, period_first)  # invoice date = 1st
            amt = rc.amount or Decimal("0.00")
            # idempotent: avoid duplicates per (invoice, category, description)
            InvoiceItem.objects.get_or_create(
                invoice=inv,
                category=rc.category,
                description=(rc.description or rc.category.name),
                defaults={"amount": amt, "is_recurring": True},
            )


def post_water_bill(water_bill_id: int):
    """
    Split a water bill evenly across active leases in that property and month.
    Idempotent: skips if already posted.
    """
    wb = WaterBill.objects.select_related("property").get(pk=water_bill_id)
    if wb.posted:
        return

    leases = list(active_leases_qs().filter(unit__property=wb.property))
    if not leases:
        wb.posted = True
        wb.save(update_fields=["posted"])
        return

    n = len(leases)
    base = (wb.total_amount / n).quantize(Decimal("0.01"))
    remainder = wb.total_amount - base * n  # may be 0.01..0.04 in PKR
    steps = int((remainder * 100).copy_abs())

    adjustments = [Decimal("0.00")] * n
    for i in range(steps):
        adjustments[i] += Decimal("0.01") if remainder > 0 else Decimal("-0.01")

    water_cat, _ = ItemCategory.objects.get_or_create(name="Water Charges")

    for lease, adj in zip(leases, adjustments):
        inv = ensure_month_invoice(lease, wb.period)
        InvoiceItem.objects.create(
            invoice=inv,
            category=water_cat,
            description=wb.description or f"Water charges {wb.period:%b %Y}",
            amount=base + adj,
        )

    wb.posted = True
    wb.save(update_fields=["posted"])


@transaction.atomic
def run_monthly_billing_for(period_date: date, cutoff_today: bool = False):
    """One-click: ensure monthly invoices and apply recurring rules."""
    # 1) one invoice per active lease
    for lease in active_leases_qs():
        ensure_month_invoice(lease, first_of_month(period_date))

    # 2) apply FIXED recurring rows
    apply_fixed_recurring(period_date, cutoff_today=cutoff_today)

    # 3) optional: post any water bills for this month
    for wb in WaterBill.objects.filter(
        period=first_of_month(period_date), posted=False
    ):
        post_water_bill(wb.id)


# add near your other billing helpers (uses your existing ensure_month_invoice)


def backfill_recurring_to_invoices(recurring_id: int, end_period: date | None = None):
    """
    For a given RecurringCharge, post its amount onto monthly invoices from its start month
    up to (but not including) end_period's month (default: current month).
    Skips months outside the lease term. Avoids duplicates per (invoice, category, description).
    """
    from .models import InvoiceItem, RecurringCharge  # local import to avoid cycles

    rc = RecurringCharge.objects.select_related("lease", "category").get(
        pk=recurring_id
    )
    lease = rc.lease
    if not lease:
        return 0

    # figure time window
    today = date.today()
    end_period = _first_of_month(end_period or today)  # exclusive upper bound
    start = rc.start_date or getattr(lease, "start_date", None) or today
    cur = _first_of_month(start)

    # clamp to lease bounds
    lease_start = getattr(lease, "start_date", None)
    lease_end = getattr(lease, "end_date", None)
    if lease_start and cur < _first_of_month(lease_start):
        cur = _first_of_month(lease_start)
    if lease_end:
        last_allowed = _first_of_month(lease_end)
        if end_period > _add_months(last_allowed, 1):
            end_period = _add_months(last_allowed, 1)

    posted = 0
    while cur < end_period:
        inv = ensure_month_invoice(lease, cur)  # you already have this helper
        # avoid simple duplicates
        desc = rc.description or (rc.category.name if rc.category_id else "Recurring")
        defaults = {"amount": rc.amount or Decimal("0.00")}
        obj, created = InvoiceItem.objects.get_or_create(
            invoice=inv, category=rc.category, description=desc, defaults=defaults
        )
        if created:
            posted += 1
        cur = _add_months(cur, 1)
    return posted


# invoices/services.py

SECURITY_CATEGORY_NAME = "Security Deposit"


def _ItemCategory():
    return apps.get_model("invoices", "ItemCategory")


def _Invoice():
    return apps.get_model("invoices", "Invoice")


def _InvoiceItem():
    return apps.get_model("invoices", "InvoiceItem")


def get_security_category():
    Cat = _ItemCategory()
    cat, _ = Cat.objects.get_or_create(
        name=SECURITY_CATEGORY_NAME, defaults={"is_active": True}
    )
    return cat


def ensure_security_deposit_invoice_for(lease):
    """
    Ensure there is exactly one security deposit invoice+item for this lease.
    - Invoice: description='SECURITY_DEPOSIT'
    - Item:    category='Security Deposit', description='Security Deposit', amount=lease.security_amount
    - If amount <= 0, remove the item (and delete invoice if empty).
    """
    Invoice = _Invoice()
    InvoiceItem = _InvoiceItem()
    cat = get_security_category()

    amt = getattr(lease, "security_amount", None)
    if amt is None:
        return None

    issue_date = getattr(lease, "start_date", None) or timezone.now().date()
    due_date = issue_date

    inv = Invoice.objects.filter(lease=lease, description="SECURITY_DEPOSIT").first()
    if not inv:
        inv = Invoice(
            lease=lease,
            issue_date=issue_date,
            due_date=due_date,
            amount=Decimal("0.00"),
            status="draft",
            description="SECURITY_DEPOSIT",
        )
        inv.save()

    if Decimal(amt) <= 0:
        # remove any existing item and possibly the invoice
        InvoiceItem.objects.filter(
            invoice=inv, category=cat, description="Security Deposit"
        ).delete()
        if not inv.items.exists():
            inv.delete()
            return None
        return inv

    item, created = InvoiceItem.objects.get_or_create(
        invoice=inv,
        category=cat,
        description="Security Deposit",
        defaults={"amount": Decimal(amt), "is_recurring": False},
    )
    if not created and item.amount != Decimal(amt):
        item.amount = Decimal(amt)
        item.save()
    return inv


def security_deposit_balance(lease):
    """
    Current 'balance' for security deposit = total of security deposit items.
    (If you have payments/refunds/allocations, subtract them here.)
    """
    InvoiceItem = _InvoiceItem()
    cat = get_security_category()
    total = InvoiceItem.objects.filter(invoice__lease=lease, category=cat).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")
    return total


# invoices/services.py

from .models import SecurityDepositTransaction


def security_deposit_totals(lease):
    """
    Returns a dict of security deposit numbers for a lease:
      - required: from lease.security_deposit
      - paid_in: sum of PAYMENT
      - refunded: sum of REFUND
      - damages: sum of DAMAGE
      - balance_to_collect: required - paid_in
      - currently_held: paid_in - refunded - damages
    """
    qs = SecurityDepositTransaction.objects.filter(lease=lease)

    def _sum(q, types):
        return q.filter(type__in=types).aggregate(total=Sum("amount"))[
            "total"
        ] or Decimal("0.00")

    required = lease.security_deposit or Decimal("0.00")
    paid_in = _sum(qs, ["PAYMENT"])
    refunded = _sum(qs, ["REFUND"])
    damages = _sum(qs, ["DAMAGE"])

    balance_to_collect = required - paid_in
    currently_held = paid_in - refunded - damages

    return {
        "required": required,
        "paid_in": paid_in,
        "refunded": refunded,
        "damages": damages,
        "balance_to_collect": balance_to_collect,
        "currently_held": currently_held,
    }


# invoices/services.py


def security_deposit_totals(lease):
    """
    Return a dict with all security deposit summary numbers for a lease.
    Uses SecurityDepositTransaction rows + lease.security_deposit.

    Definitions:

    required           = lease.security_deposit
    paid_in            = sum of PAYMENT
    refunded           = sum of REFUND
    damages            = sum of DAMAGE
    adjust             = sum of ADJUST (signed)
    balance_to_collect = max(required - paid_in, 0)
    currently_held     = max(paid_in - refunded - damages, 0)
    """
    ZERO = Decimal("0.00")

    if not lease:
        return {
            "required": ZERO,
            "paid_in": ZERO,
            "refunded": ZERO,
            "refund_deductions": ZERO,
            "damages": ZERO,
            "adjust": ZERO,
            "balance_to_collect": ZERO,
            "currently_held": ZERO,
        }

    if hasattr(lease, "security_summary"):
        return lease.security_summary

    required = lease.security_deposit or ZERO

    qs = SecurityDepositTransaction.objects.filter(lease=lease)

    paid_in = qs.filter(type="PAYMENT").aggregate(total=Sum("amount"))["total"] or ZERO
    refunded = qs.filter(type="REFUND").aggregate(total=Sum("amount"))["total"] or ZERO
    refund_deductions = (
        qs.filter(type="REFUND").aggregate(total=Sum("deduction_amount"))["total"]
        or ZERO
    )
    damages = qs.filter(type="DAMAGE").aggregate(total=Sum("amount"))["total"] or ZERO
    adjust = qs.filter(type="ADJUST").aggregate(total=Sum("amount"))["total"] or ZERO

    balance_to_collect = max(required - paid_in, ZERO)
    currently_held = max(paid_in - refunded - refund_deductions - damages, ZERO)

    return {
        "required": required,
        "paid_in": paid_in,
        "refunded": refunded,
        "refund_deductions": refund_deductions,
        "damages": damages,
        "adjust": adjust,
        "balance_to_collect": balance_to_collect,
        "currently_held": currently_held,
    }


def security_deposit_balance(lease):
    """
    How much security the tenant STILL OWES (used in list/filters).
    Positive => still to collect from tenant.
    """
    return security_deposit_totals(lease)["balance_to_collect"]


# invoices/services.py

# you already have security_deposit_totals(lease)


def _lease_balance_value(lease) -> Decimal:
    v = getattr(lease, "get_balance", 0)
    try:
        v = v() if callable(v) else v
    except Exception:
        v = 0
    return Decimal(v or 0)


def _fmt_pkr(x: Decimal) -> str:
    x = Decimal(x or 0)
    return f"{x:,.2f}"


def build_security_receipt_message(request, tx) -> str:
    """
    Returns the WhatsApp message text for a single SecurityDepositTransaction.
    This matches your Security list template intent, but generated server-side.
    """
    lease = tx.lease
    tenant = lease.tenant
    unit = lease.unit
    prop = unit.property

    totals = security_deposit_totals(lease)

    required = Decimal(totals.get("required") or 0)
    bal_to_collect = Decimal(totals.get("balance_to_collect") or 0)

    status = "Pending" if bal_to_collect > 0 else "Paid"

    # Signed tx amount for message
    amt = Decimal(tx.amount or 0)
    if tx.type in ("REFUND", "DAMAGE"):
        amt = -amt

    # Heading
    heading_map = {
        "PAYMENT": "*Security Deposit payment received*",
        "REFUND": "*Security Deposit refunded*",
        "DAMAGE": "*Security Deposit used for damages*",
        "ADJUST": "*Security Deposit adjusted*",
        "REQUIRED": "*Security Deposit requirement recorded*",
    }
    heading = heading_map.get(tx.type, "*Security Deposit update*")

    # Dates
    period_start = (
        lease.start_date.strftime("%b %d, %Y")
        if getattr(lease, "start_date", None)
        else ""
    )
    period_end = (
        lease.end_date.strftime("%b %d, %Y") if getattr(lease, "end_date", None) else ""
    )
    tran_date = tx.date.strftime("%b %d, %Y") if getattr(tx, "date", None) else ""

    lease_bal = _lease_balance_value(lease)
    total_bal = lease_bal + bal_to_collect

    lines = [
        f"Dear {getattr(tenant, 'first_name', '') or 'Customer'},",
        f"{heading} for {getattr(prop, 'property_name', '') or ''}.",
        f"Unit: {getattr(unit, 'unit_number', '') or ''}",
        f"Period: {period_start} – {period_end}",
        f"Security Deposit: Rs. {_fmt_pkr(required)} ({status})",
    ]

    if bal_to_collect > 0:
        lines.append(f"Security Deposit Balance: Rs. {_fmt_pkr(bal_to_collect)}")

    lines += [
        f"Date: {tran_date}",
        f"*Security Amount: Rs. {_fmt_pkr(amt)}*",
        f"Lease Balance: Rs. {_fmt_pkr(lease_bal)}",
        f"*Total Balance: Rs. {_fmt_pkr(total_bal)}*",
        "",
        "Thank you!",
    ]

    return "\n".join(lines)


# ---------- monthly billing workflow ----------
import logging

from django.core.files.base import ContentFile

from .models import MonthlyBillingRun, MonthlyBillingRunItem

logger = logging.getLogger(__name__)


def parse_billing_month(value: str | None, default_today: date | None = None) -> date:
    if value:
        year, month = [int(part) for part in value.split("-", 1)]
        return date(year, month, 1)
    today = default_today or timezone.localdate()
    if today.day == 1:
        prev_month = today.month - 1 or 12
        prev_year = today.year - 1 if today.month == 1 else today.year
        return date(prev_year, prev_month, 1)
    return date(today.year, today.month, 1)


def monthly_period_end(month_start: date) -> date:
    return date(
        month_start.year,
        month_start.month,
        monthrange(month_start.year, month_start.month)[1],
    )


def previous_month_start(month_start: date) -> date:
    month_start = first_of_month(month_start)
    if month_start.month == 1:
        return date(month_start.year - 1, 12, 1)
    return date(month_start.year, month_start.month - 1, 1)


def get_or_create_monthly_billing_run(
    billing_month: date, *, created_by=None, created_by_label=""
):
    run, created = MonthlyBillingRun.objects.get_or_create(
        billing_month=first_of_month(billing_month),
        defaults={
            "run_date": timezone.localdate(),
            "created_by": created_by
            if getattr(created_by, "is_authenticated", False)
            else None,
            "created_by_label": created_by_label,
        },
    )
    if created:
        _run_log(run, "billing run started")
    return run


def _run_log(run, message):
    notes = (run.notes or "").strip()
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    run.notes = (notes + "\n" if notes else "") + f"[{stamp}] {message}"
    run.audit_log = list(run.audit_log or []) + [
        {"at": timezone.localtime().isoformat(), "message": message}
    ]
    run.save(update_fields=["notes", "audit_log", "updated_at"])
    logger.info("Monthly billing run %s: %s", run.pk, message)


def _item_log(item, message):
    entry = {"at": timezone.localtime().isoformat(), "message": message}
    item.log = list(item.log or []) + [entry]
    item.save(update_fields=["log", "updated_at"])
    logger.info("Monthly billing item %s: %s", item.pk, message)


def _tenant_name(tenant):
    return " ".join(
        filter(
            None, [getattr(tenant, "first_name", ""), getattr(tenant, "last_name", "")]
        )
    ).strip()


def _active_leases_for_month(month_start: date):
    month_end = monthly_period_end(month_start)
    return (
        active_leases_qs()
        .filter(start_date__lte=month_end)
        .filter(Q(end_date__gte=month_start) | Q(end_date__isnull=True))
        .select_related("tenant", "unit", "unit__property")
        .order_by("unit__property__property_name", "unit__unit_number", "id")
    )


def _recurring_rules_for_lease(lease, month_start: date):
    month_end = monthly_period_end(month_start)
    return (
        RecurringCharge.objects.filter(
            active=True,
            kind="FIXED",
            start_date__lte=month_end,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=month_start))
        .filter(
            Q(scope="GLOBAL")
            | Q(
                scope="PROPERTY",
                property_id=getattr(getattr(lease, "unit", None), "property_id", None),
            )
            | Q(scope="LEASE", lease_id=lease.pk)
        )
    )


def _month_invoice_for_lease(lease, month_start: date):
    return (
        Invoice.objects.filter(
            lease=lease,
            issue_date__year=month_start.year,
            issue_date__month=month_start.month,
        )
        .order_by("issue_date", "id")
        .first()
    )


def _invoice_has_water_item(invoice):
    if not invoice:
        return False
    return invoice.items.filter(category__name__icontains="water").exists()


def _invoice_has_billable_items(invoice):
    if not invoice:
        return False
    return invoice.items.exists()


def _recurring_setup_satisfied(lease, month_start: date, invoice=None):
    if _recurring_rules_for_lease(lease, month_start).exists():
        return True
    return _invoice_has_billable_items(invoice or _month_invoice_for_lease(lease, month_start))


def _invoice_water_item(invoice):
    if not invoice:
        return None
    return (
        invoice.items.filter(category__name__icontains="water").order_by("id").first()
    )


def _latest_meter_reading_for_lease(lease, period_end: date):
    from smart_meter.models import MeterInstallation, MeterReading

    installations = (
        MeterInstallation.objects.filter(
            lease=lease,
            meter__meter_type="electric",
            meter__billing_mode="postpaid",
            start_date__lte=period_end,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=period_end))
        .select_related("meter")
    )
    latest = None
    for installation in installations:
        reading = (
            MeterReading.objects.filter(meter=installation.meter)
            .order_by("-ts")
            .first()
        )
        if reading and (latest is None or reading.ts > latest.ts):
            latest = reading
    return latest, list(installations)


def _set_pending(item, code, message):
    item.status = MonthlyBillingRunItem.STATUS_PENDING
    item.issue_code = code
    item.issue_message = message
    item.save(update_fields=["status", "issue_code", "issue_message", "updated_at"])
    _item_log(item, message)


def _extend_unique(values, additions):
    seen = set()
    result = []
    for value in list(values or []) + list(additions or []):
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _track_created_records(item, *, invoice_ids=None, invoice_item_ids=None):
    invoice_ids = [int(v) for v in (invoice_ids or []) if v]
    invoice_item_ids = [int(v) for v in (invoice_item_ids or []) if v]
    if invoice_ids:
        item.created_invoice_ids = _extend_unique(item.created_invoice_ids, invoice_ids)
        run = item.billing_run
        run.created_invoice_ids = _extend_unique(run.created_invoice_ids, invoice_ids)
        run.save(update_fields=["created_invoice_ids", "updated_at"])
    if invoice_item_ids:
        item.created_invoice_item_ids = _extend_unique(
            item.created_invoice_item_ids, invoice_item_ids
        )
    if invoice_ids or invoice_item_ids:
        item.save(
            update_fields=[
                "created_invoice_ids",
                "created_invoice_item_ids",
                "updated_at",
            ]
        )


def _refresh_run_counts(run):
    items = run.items.all()
    excluded_statuses = [
        MonthlyBillingRunItem.STATUS_SKIPPED,
        MonthlyBillingRunItem.STATUS_EXCLUDED,
    ]
    run.total_active_leases = items.exclude(status__in=excluded_statuses).count()
    run.recurring_created_count = items.filter(recurring_invoice_created=True).count()
    run.missing_recurring_count = items.filter(
        issue_code=MonthlyBillingRunItem.ISSUE_MISSING_RECURRING
    ).count()
    run.electric_ready_count = items.filter(
        electric_required=True, electric_ready=True
    ).count()
    run.electric_pending_count = items.filter(
        electric_required=True,
        electric_ready=False,
        status=MonthlyBillingRunItem.STATUS_PENDING,
    ).count()
    run.manual_electric_count = items.filter(manual_electric=True).count()
    run.water_missing_count = items.filter(
        issue_code=MonthlyBillingRunItem.ISSUE_WATER_MISSING
    ).count()
    run.ready_to_send_count = items.filter(
        status=MonthlyBillingRunItem.STATUS_READY
    ).count()
    run.pdf_generating_count = items.filter(
        status=MonthlyBillingRunItem.STATUS_READY, invoice_pdf=""
    ).count()
    run.sending_count = (
        items.filter(status=MonthlyBillingRunItem.STATUS_READY)
        .exclude(invoice_pdf="")
        .count()
    )
    run.pending_attention_count = items.filter(
        status=MonthlyBillingRunItem.STATUS_PENDING
    ).count()
    run.sent_count = items.filter(status=MonthlyBillingRunItem.STATUS_SENT).count()
    run.failed_count = items.filter(status=MonthlyBillingRunItem.STATUS_FAILED).count()
    run.excluded_count = items.filter(
        status=MonthlyBillingRunItem.STATUS_EXCLUDED
    ).count()
    run.rolled_back_count = items.filter(
        status=MonthlyBillingRunItem.STATUS_ROLLED_BACK
    ).count()
    run.skipped_count = items.filter(
        status=MonthlyBillingRunItem.STATUS_SKIPPED
    ).count()
    active_count = items.exclude(
        status__in=excluded_statuses + [MonthlyBillingRunItem.STATUS_ROLLED_BACK]
    ).count()
    if active_count and run.sent_count == active_count:
        run.status = MonthlyBillingRun.STATUS_COMPLETED
    elif run.rolled_back_count and run.rolled_back_count == items.count():
        run.status = MonthlyBillingRun.STATUS_ROLLED_BACK
    elif run.failed_count:
        run.status = MonthlyBillingRun.STATUS_PARTIAL
    elif run.pending_attention_count:
        run.status = MonthlyBillingRun.STATUS_PREFLIGHT
    elif run.sent_count and not run.ready_to_send_count:
        run.status = MonthlyBillingRun.STATUS_SENT
    elif run.ready_to_send_count:
        run.status = MonthlyBillingRun.STATUS_READY
    run.save()


def _skip_stale_monthly_billing_items(run, active_lease_ids):
    stale_items = run.items.exclude(lease_id__in=active_lease_ids).exclude(
        status__in=[
            MonthlyBillingRunItem.STATUS_SENT,
            MonthlyBillingRunItem.STATUS_EXCLUDED,
            MonthlyBillingRunItem.STATUS_ROLLED_BACK,
        ]
    )
    for item in stale_items:
        item.status = MonthlyBillingRunItem.STATUS_SKIPPED
        item.issue_code = MonthlyBillingRunItem.ISSUE_INACTIVE_LEASE
        item.issue_message = "Lease is not active for this billing month."
        item.save(update_fields=["status", "issue_code", "issue_message", "updated_at"])
        _item_log(item, "skipped because lease is not active for billing month")


def _progress(progress_callback, item, index, total, step):
    if progress_callback:
        progress_callback(item=item, index=index, total=total, step=step)


def _preflight_progress(progress_callback, item, lease_index, lease_total, step_number, step):
    steps_per_lease = 5
    _progress(
        progress_callback,
        item,
        ((lease_index - 1) * steps_per_lease) + step_number,
        lease_total * steps_per_lease,
        step,
    )


def run_monthly_billing_preflight(
    billing_month: date,
    *,
    created_by=None,
    created_by_label="",
    dry_run=False,
    progress_callback=None,
):
    billing_month = first_of_month(billing_month)
    electric_month = previous_month_start(billing_month)
    electric_month_end = monthly_period_end(electric_month)
    leases = list(_active_leases_for_month(billing_month))
    if dry_run:
        rows = []
        missing = manual = meter_pending = water_missing = would_send = 0
        for lease in leases:
            unit = getattr(lease, "unit", None)
            existing_invoice = _month_invoice_for_lease(lease, billing_month)
            recurring_found = _recurring_setup_satisfied(
                lease, billing_month, existing_invoice
            )
            latest, installations = _latest_meter_reading_for_lease(
                lease, electric_month_end
            )
            is_smart = bool(getattr(unit, "is_smart_meter", False))
            water_required = bool(
                getattr(lease, "bill_water_charges", True)
                and (getattr(lease, "water_charges", None) or Decimal("0.00")) > 0
            )
            status = "would_send"
            reasons = []
            if not recurring_found:
                missing += 1
                reasons.append("Would skip recurring: no active recurring rule.")
            if not is_smart:
                manual += 1
                reasons.append(
                    "Manual Electric Billing: unit is not enrolled in Smart Meter billing."
                )
            elif (
                not installations
                or not latest
                or timezone.localtime(latest.ts).date() < electric_month_end
            ):
                meter_pending += 1
                if latest:
                    reasons.append(
                        f"Would skip electric: last reading at {timezone.localtime(latest.ts):%Y-%m-%d %H:%M}; period ends {electric_month_end:%Y-%m-%d}."
                    )
                else:
                    reasons.append(
                        f"Would skip electric: no meter reading found; period ends {electric_month_end:%Y-%m-%d}."
                    )
            if water_required and not _invoice_has_water_item(existing_invoice):
                water_missing += 1
                reasons.append("Water charge missing for this lease invoice.")
            if reasons:
                status = "would_skip"
            else:
                would_send += 1
            rows.append(
                {
                    "lease_id": lease.pk,
                    "tenant": _tenant_name(getattr(lease, "tenant", None)),
                    "property": getattr(
                        getattr(unit, "property", None), "property_name", ""
                    ),
                    "unit": getattr(unit, "unit_number", ""),
                    "invoice": getattr(existing_invoice, "invoice_number", ""),
                    "status": status,
                    "reasons": reasons
                    or ["Would create/update invoice, PDF, and WhatsApp queue."],
                }
            )
        return {
            "billing_month": billing_month.isoformat(),
            "electric_period": f"{electric_month:%B %Y}",
            "active_leases": len(leases),
            "missing_recurring": missing,
            "manual_electric": manual,
            "meter_pending": meter_pending,
            "water_missing": water_missing,
            "would_send": would_send,
            "rows": rows,
            "dry_run": True,
        }

    run = get_or_create_monthly_billing_run(
        billing_month, created_by=created_by, created_by_label=created_by_label
    )
    run.status = MonthlyBillingRun.STATUS_PREFLIGHT
    run.save(update_fields=["status", "updated_at"])
    _skip_stale_monthly_billing_items(run, [lease.pk for lease in leases])

    for index, lease in enumerate(leases, start=1):
        tenant = getattr(lease, "tenant", None)
        unit = getattr(lease, "unit", None)
        prop = getattr(unit, "property", None)
        item, _ = MonthlyBillingRunItem.objects.get_or_create(
            billing_run=run,
            lease=lease,
            defaults={"tenant": tenant, "unit": unit, "property": prop},
        )
        item.tenant = tenant
        item.unit = unit
        item.property = prop
        item.status = MonthlyBillingRunItem.STATUS_DRAFT
        item.issue_code = ""
        item.issue_message = ""
        item.error_text = ""
        item.invoice = _month_invoice_for_lease(lease, billing_month)
        item.recurring_invoice_found = _recurring_setup_satisfied(
            lease, billing_month, item.invoice
        ) or not getattr(lease, "bill_recurring_charges", True)
        item.invoice_total = getattr(item.invoice, "amount", None)
        item.save()
        _preflight_progress(
            progress_callback, item, index, len(leases), 1, "Checking lease"
        )

        issues = []

        if lease.end_date and lease.end_date < billing_month:
            issues.append((
                MonthlyBillingRunItem.ISSUE_INACTIVE_LEASE,
                f"Lease status is Active but end date ({lease.end_date:%Y-%m-%d}) has already passed. Renew or end the lease before billing.",
            ))

        tenant_phone = (getattr(tenant, "phone", "") or "").strip()
        digits_only = "".join(ch for ch in tenant_phone if ch.isdigit())
        if not tenant_phone or len(digits_only) < 10:
            issues.append((
                MonthlyBillingRunItem.ISSUE_PHONE_MISSING,
                "Tenant phone is missing or looks invalid for WhatsApp sending.",
            ))

        _preflight_progress(
            progress_callback, item, index, len(leases), 2, "Checking recurring invoices"
        )
        if not item.recurring_invoice_found:
            issues.append((
                MonthlyBillingRunItem.ISSUE_MISSING_RECURRING,
                "Active lease has no recurring invoice setup.",
            ))

        item.water_required = bool(
            getattr(lease, "bill_water_charges", True)
            and (getattr(lease, "water_charges", None) or Decimal("0.00")) > 0
        )
        water_item = _invoice_water_item(item.invoice)
        item.water_resolved = (
            not item.water_required
            or bool(water_item)
            or bool(item.water_charge is not None)
        )
        if water_item:
            item.water_charge = water_item.amount
        _preflight_progress(
            progress_callback, item, index, len(leases), 3, "Checking water charges"
        )

        latest, installations = _latest_meter_reading_for_lease(
            lease, electric_month_end
        )
        is_smart_meter = bool(getattr(unit, "is_smart_meter", False))
        item.manual_electric = bool(
            getattr(lease, "electricity_bill_by_owner", True) and not is_smart_meter
        )
        item.electric_required = bool(
            getattr(lease, "electricity_bill_by_owner", True) and is_smart_meter
        )
        item.electric_period_start = electric_month
        item.electric_period_end = electric_month_end
        item.latest_meter_reading_date = (
            timezone.localtime(latest.ts).date() if latest else None
        )
        item.electric_ready = not item.electric_required
        _preflight_progress(
            progress_callback, item, index, len(leases), 4, "Checking electric readings"
        )
        _item_log(item, "active lease checked")

        if item.electric_required:
            if (
                not installations
                or not latest
                or item.latest_meter_reading_date < electric_month_end
            ):
                issues.append((
                    MonthlyBillingRunItem.ISSUE_METER_MISSING,
                    "Last reading at "
                    + (
                        f"{item.latest_meter_reading_date:%Y-%m-%d}"
                        if item.latest_meter_reading_date
                        else "not available"
                    )
                    + f"; electric period ends {electric_month_end:%Y-%m-%d}.",
                ))
            elif any(not installation.meter.is_active for installation in installations):
                issues.append((
                    MonthlyBillingRunItem.ISSUE_METER_OFFLINE,
                    f"Meter appears offline/stale. Latest reading date: {item.latest_meter_reading_date:%Y-%m-%d}.",
                ))
            else:
                item.electric_ready = True
                _item_log(item, "meter reading verified")
        elif item.manual_electric:
            item.issue_code = MonthlyBillingRunItem.ISSUE_MANUAL_ELECTRIC
            item.issue_message = (
                "Manual Electric Billing: unit is not enrolled in Smart Meter billing."
            )
            _item_log(item, "manual electric billing identified")

        if item.water_required and not item.water_resolved:
            issues.append((
                MonthlyBillingRunItem.ISSUE_WATER_MISSING,
                "Water charge missing for this lease invoice.",
            ))

        if issues:
            code, message = issues[0]
            item.status = MonthlyBillingRunItem.STATUS_PENDING
            item.issue_code = code
            item.issue_message = message
            _item_log(item, message)
        item.save()
        _preflight_progress(
            progress_callback, item, index, len(leases), 5, "Saving preflight result"
        )

    _refresh_run_counts(run)
    _run_log(run, "preflight completed")
    return run


def generate_monthly_billing_invoices(run, *, dry_run=False, progress_callback=None):
    if dry_run:
        return {
            "billing_month": run.billing_month.isoformat(),
            "would_create_or_update": run.items.exclude(
                status=MonthlyBillingRunItem.STATUS_EXCLUDED
            ).count(),
            "would_skip": run.items.filter(
                status__in=[
                    MonthlyBillingRunItem.STATUS_PENDING,
                    MonthlyBillingRunItem.STATUS_EXCLUDED,
                ]
            ).count(),
            "dry_run": True,
        }
    _run_log(run, "recurring invoice generation started")
    before = {
        item.lease_id: set(
            InvoiceItem.objects.filter(
                invoice__lease=item.lease, invoice__issue_date=run.billing_month
            ).values_list("pk", flat=True)
        )
        for item in run.items.exclude(
            status=MonthlyBillingRunItem.STATUS_SKIPPED
        ).select_related("lease")
    }
    apply_fixed_recurring(run.billing_month)
    item_list = list(run.items.select_related("lease", "tenant", "property", "unit"))
    for index, item in enumerate(item_list, start=1):
        _progress(
            progress_callback, item, index, len(item_list), "Recurring Generation"
        )
        if item.status == MonthlyBillingRunItem.STATUS_EXCLUDED:
            continue
        if (
            item.status == MonthlyBillingRunItem.STATUS_PENDING
            and item.issue_code == MonthlyBillingRunItem.ISSUE_MISSING_RECURRING
        ):
            continue
        invoice = _month_invoice_for_lease(item.lease, run.billing_month)
        item.invoice = invoice
        current = set()
        if invoice:
            current = set(invoice.items.values_list("pk", flat=True))
        item.recurring_invoice_created = bool(
            current - before.get(item.lease_id, set())
        )
        item.recurring_invoice_found = _recurring_rules_for_lease(
            item.lease, run.billing_month
        ).exists() or _invoice_has_billable_items(invoice)
        item.invoice_total = getattr(invoice, "amount", None)
        item.save()
        if invoice and invoice.pk not in [
            int(v) for v in (item.created_invoice_ids or [])
        ]:
            before_items = before.get(item.lease_id, set())
            new_items = current - before_items
            if new_items:
                _track_created_records(
                    item, invoice_ids=[invoice.pk], invoice_item_ids=list(new_items)
                )
        _item_log(
            item,
            "invoice generated"
            if item.invoice
            else "recurring invoice generation failed",
        )
        if not invoice:
            _set_pending(
                item,
                MonthlyBillingRunItem.ISSUE_RECURRING_FAILED,
                "Recurring invoice generation failed.",
            )
    _refresh_run_counts(run)
    _run_log(run, "recurring invoice generation completed")
    return run


def generate_monthly_billing_electric(run, *, dry_run=False, progress_callback=None):
    from smart_meter.models import MeterInstallation
    from smart_meter.services.invoicing import (
        compute_electric_bill,
        upsert_invoice_with_electric_item,
    )

    period_start = previous_month_start(run.billing_month)
    period_end = monthly_period_end(period_start)
    if dry_run:
        return {
            "billing_month": run.billing_month.isoformat(),
            "electric_period": f"{period_start:%B %Y}",
            "would_create_or_update": run.items.filter(electric_required=True)
            .exclude(
                status__in=[
                    MonthlyBillingRunItem.STATUS_PENDING,
                    MonthlyBillingRunItem.STATUS_EXCLUDED,
                ]
            )
            .count(),
            "manual_electric": run.items.filter(manual_electric=True).count(),
            "dry_run": True,
        }
    _run_log(
        run,
        f"electric billing started for {period_start:%B %Y}, posting into {run.billing_month:%B %Y}",
    )
    item_list = list(
        run.items.filter(electric_required=True)
        .exclude(
            status__in=[
                MonthlyBillingRunItem.STATUS_PENDING,
                MonthlyBillingRunItem.STATUS_EXCLUDED,
            ]
        )
        .select_related("lease", "tenant", "property", "unit")
    )
    for index, item in enumerate(item_list, start=1):
        _progress(progress_callback, item, index, len(item_list), "Electric Generation")
        installations = (
            MeterInstallation.objects.filter(
                lease=item.lease,
                meter__meter_type="electric",
                meter__billing_mode="postpaid",
                start_date__lte=period_end,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=period_start))
            .select_related("meter")
        )
        total = Decimal("0.00")
        try:
            for installation in installations:
                ctx = compute_electric_bill(
                    item.lease, installation.meter, period_start, period_end
                )
                before_invoice = _month_invoice_for_lease(item.lease, run.billing_month)
                before_item_ids = (
                    set(before_invoice.items.values_list("pk", flat=True))
                    if before_invoice
                    else set()
                )
                inv = upsert_invoice_with_electric_item(
                    ctx, posting_month=run.billing_month
                )
                after_item_ids = set(inv.items.values_list("pk", flat=True))
                created_items = after_item_ids - before_item_ids
                created_invoice_ids = [] if before_invoice else [inv.pk]
                _track_created_records(
                    item,
                    invoice_ids=created_invoice_ids,
                    invoice_item_ids=list(created_items),
                )
                item.invoice = inv
                total += Decimal(ctx.line_total or 0)
            item.electric_charge = total
            item.electric_ready = True
            item.invoice_total = getattr(item.invoice, "amount", None)
            item.save()
            _item_log(item, "electric charge created")
        except Exception as exc:
            item.error_text = str(exc)
            item.save(update_fields=["error_text", "updated_at"])
            _set_pending(
                item,
                MonthlyBillingRunItem.ISSUE_ELECTRIC_UNVERIFIED,
                "Electric billing not verified.",
            )
    _refresh_run_counts(run)
    _run_log(run, "electric billing completed")
    return run


def resolve_monthly_billing_water(
    item, *, amount=None, description=None, not_applicable=False, apply_property=False
):
    if not_applicable:
        item.water_required = False
        item.water_resolved = True
        item.water_charge = Decimal("0.00")
        item.issue_code = ""
        item.issue_message = ""
        item.status = MonthlyBillingRunItem.STATUS_DRAFT
        item.save()
        _item_log(item, "water marked not applicable")
        _refresh_run_counts(item.billing_run)
        return item

    if not getattr(item.lease, "bill_water_charges", True):
        return resolve_monthly_billing_water(item, not_applicable=True)

    amount = Decimal(str(amount or "0")).quantize(Decimal("0.01"))
    invoice = item.invoice or ensure_month_invoice(
        item.lease, item.billing_run.billing_month
    )
    water_cat, _ = ItemCategory.objects.get_or_create(name="Water Charges")
    before_invoice = item.invoice
    water_item = _invoice_water_item(invoice)
    created = False
    water_description = (
        description
        or getattr(water_item, "description", "")
        or f"Water charges {item.billing_run.billing_month:%b %Y}"
    )
    if water_item:
        water_item.category = water_cat
        water_item.description = water_description
        water_item.amount = amount
        water_item.is_recurring = False
        water_item.save(
            update_fields=["category", "description", "amount", "is_recurring"]
        )
    else:
        water_item = InvoiceItem.objects.create(
            invoice=invoice,
            category=water_cat,
            description=water_description,
            amount=amount,
            is_recurring=False,
        )
        created = True
    if not before_invoice:
        _track_created_records(item, invoice_ids=[invoice.pk])
    if created:
        _track_created_records(item, invoice_item_ids=[water_item.pk])
    item.invoice = invoice
    item.water_required = True
    item.water_resolved = True
    item.water_charge = amount
    item.issue_code = ""
    item.issue_message = ""
    item.status = MonthlyBillingRunItem.STATUS_DRAFT
    item.invoice_total = invoice.amount
    item.save()
    _item_log(item, "water charge saved")
    if apply_property and item.property_id:
        apply_property_water_charge(
            item.billing_run,
            item.property,
            amount=amount,
            description=description
            or f"Water charges {item.billing_run.billing_month:%b %Y}",
            source_item=item,
        )
    _refresh_run_counts(item.billing_run)
    return item


def apply_property_water_charge(
    run, property_obj, *, amount, description, source_item=None
):
    amount = Decimal(str(amount or "0")).quantize(Decimal("0.01"))
    updated = 0
    for item in (
        run.items.filter(property=property_obj)
        .exclude(status=MonthlyBillingRunItem.STATUS_EXCLUDED)
        .select_related("lease")
    ):
        if source_item and item.pk == source_item.pk:
            continue
        if not getattr(item.lease, "bill_water_charges", True):
            continue
        if not (getattr(item.lease, "water_charges", None) or Decimal("0.00")) > 0:
            continue
        resolve_monthly_billing_water(
            item,
            amount=amount,
            description=description,
            not_applicable=False,
            apply_property=False,
        )
        updated += 1
    _run_log(
        run,
        f"water charge applied to {updated} other occupied unit(s) in {property_obj}",
    )
    return updated


def approve_monthly_billing_electric_reading(item):
    if not item.electric_required:
        return item
    period_end = item.electric_period_end or monthly_period_end(
        previous_month_start(item.billing_run.billing_month)
    )
    latest, installations = _latest_meter_reading_for_lease(
        item.lease, period_end
    )
    if not installations:
        raise ValueError("No active postpaid electric meter installation found for this lease.")
    if not latest:
        raise ValueError("No electric meter reading found to approve.")

    item.latest_meter_reading_date = timezone.localtime(latest.ts).date()
    item.electric_ready = True
    item.issue_code = ""
    item.issue_message = ""
    item.status = MonthlyBillingRunItem.STATUS_DRAFT
    item.error_text = ""
    item.save()

    reading_value = latest.total_energy
    if reading_value is not None:
        item.lease.electricity_meter_reading = str(reading_value)
        item.lease.save(update_fields=["electricity_meter_reading", "updated_at"])

    _item_log(
        item,
        f"electric reading approved through {item.latest_meter_reading_date:%Y-%m-%d}",
    )
    _refresh_run_counts(item.billing_run)
    return item


def _evaluate_ready_state(item, run):
    """Evaluate and persist the ready/pending state for a single item.

    This is the per-item body previously inlined inside
    prepare_monthly_billing_ready()'s loop. Kept as its own function so a
    single item can be re-validated (e.g. after a water save or an accepted
    meter reading) without re-running this check for every other item in
    the run.
    """
    invoice = item.invoice or _month_invoice_for_lease(item.lease, run.billing_month)
    item.invoice = invoice
    item.invoice_total = getattr(invoice, "amount", None)
    if item.status == MonthlyBillingRunItem.STATUS_PENDING and item.issue_code:
        item.save()
        return
    if not invoice:
        _set_pending(
            item,
            MonthlyBillingRunItem.ISSUE_RECURRING_FAILED,
            "Recurring invoice generation failed.",
        )
        return
    if not _recurring_setup_satisfied(item.lease, run.billing_month, invoice):
        _set_pending(
            item,
            MonthlyBillingRunItem.ISSUE_MISSING_RECURRING,
            "Active lease has no recurring invoice setup.",
        )
        return
    if item.water_required and not item.water_resolved:
        _set_pending(
            item,
            MonthlyBillingRunItem.ISSUE_WATER_MISSING,
            "Water charge missing for this lease invoice.",
        )
        return
    if not getattr(getattr(item.lease, "tenant", None), "phone", ""):
        _set_pending(
            item, MonthlyBillingRunItem.ISSUE_PHONE_MISSING, "Tenant phone missing."
        )
        return
    if (item.invoice_total or Decimal("0.00")) <= 0:
        _set_pending(
            item, MonthlyBillingRunItem.ISSUE_ZERO_TOTAL, "Zero invoice total."
        )
        return
    item.status = MonthlyBillingRunItem.STATUS_READY
    item.issue_code = ""
    item.issue_message = ""
    item.save()
    _item_log(item, "invoice ready to send")


def prepare_monthly_billing_ready_for_item(item):
    """Re-validate a single item's ready state, then refresh run-level
    aggregate counts. Use this from single-item actions (water save, accept
    meter reading, etc.) instead of prepare_monthly_billing_ready(run),
    which re-validates every item in the run and is only meant for the
    bulk 'Ready Validation' action.
    """
    run = item.billing_run
    if item.status in (
        MonthlyBillingRunItem.STATUS_SENT,
        MonthlyBillingRunItem.STATUS_SKIPPED,
        MonthlyBillingRunItem.STATUS_EXCLUDED,
        MonthlyBillingRunItem.STATUS_ROLLED_BACK,
    ):
        return run
    _evaluate_ready_state(item, run)
    _refresh_run_counts(run)
    return run


def prepare_monthly_billing_ready(run, *, progress_callback=None):
    item_list = list(
        run.items.exclude(
            status__in=[
                MonthlyBillingRunItem.STATUS_SENT,
                MonthlyBillingRunItem.STATUS_SKIPPED,
                MonthlyBillingRunItem.STATUS_EXCLUDED,
                MonthlyBillingRunItem.STATUS_ROLLED_BACK,
            ]
        ).select_related("lease", "tenant", "property", "unit")
    )
    for index, item in enumerate(item_list, start=1):
        _progress(progress_callback, item, index, len(item_list), "Ready Validation")
        _evaluate_ready_state(item, run)
    _refresh_run_counts(run)
    return run


def generate_monthly_billing_pdfs(run, *, progress_callback=None):

    _run_log(run, "PDF generation started")
    item_list = list(
        run.items.filter(status=MonthlyBillingRunItem.STATUS_READY)
        .exclude(status=MonthlyBillingRunItem.STATUS_EXCLUDED)
        .select_related("invoice", "tenant", "property", "unit")
    )
    for index, item in enumerate(item_list, start=1):
        _progress(progress_callback, item, index, len(item_list), "Generating PDF")
        generate_monthly_billing_pdf_for_item(item)
    _refresh_run_counts(run)
    _run_log(run, "PDF generation completed")
    return run


def generate_monthly_billing_pdf_for_item(item):
    from invoices.views import _invoice_pdf_context, render_to_pdf

    if item.status == MonthlyBillingRunItem.STATUS_EXCLUDED:
        _item_log(item, "PDF skipped because item is excluded")
        return item
    if not item.invoice:
        _set_pending(
            item, MonthlyBillingRunItem.ISSUE_RECURRING_FAILED, "Invoice missing."
        )
        return item
    try:
        pdf_content = render_to_pdf(
            "invoices/invoice_pdf.html", _invoice_pdf_context(item.invoice)
        )
        filename = f"Invoice_{item.invoice.invoice_number or item.invoice_id}.pdf"
        item.invoice_pdf.save(filename, ContentFile(pdf_content), save=False)
        item.save(update_fields=["invoice_pdf", "updated_at"])
        _item_log(item, "PDF generated")
    except Exception as exc:
        item.error_text = str(exc)
        item.save(update_fields=["error_text", "updated_at"])
        _set_pending(
            item, MonthlyBillingRunItem.ISSUE_PDF_FAILED, "PDF generation failed."
        )
    _refresh_run_counts(item.billing_run)
    return item


def build_monthly_invoice_whatsapp_message(invoice, billing_month):
    lease = invoice.lease
    tenant = lease.tenant
    unit = lease.unit
    prop = unit.property
    items = list(invoice.items.select_related("category"))

    def total_for(name):
        return sum(
            (
                line.amount
                for line in items
                if name in (line.category.name or "").lower()
            ),
            Decimal("0.00"),
        )

    rent = total_for("rent")
    electricity = total_for("electric")
    water = total_for("water")
    other = (invoice.amount or Decimal("0.00")) - rent - electricity - water
    lines = [
        f"Dear {_tenant_name(tenant) or 'Customer'},",
        "",
        f"*Invoice #{invoice.invoice_number}*",
        f"Billing Month: {billing_month:%B %Y}",
        f"Property: {prop.property_name}",
        f"Unit: {unit.unit_number}",
        f"Rent: Rs. {_fmt_pkr(rent)}",
        f"Electricity: Rs. {_fmt_pkr(electricity)}",
        f"Water: Rs. {_fmt_pkr(water)}",
        f"Other / Previous balance: Rs. {_fmt_pkr(other)}",
        f"Total Amount: Rs. {_fmt_pkr(invoice.amount or Decimal('0.00'))}",
        f"Due Date: {invoice.due_date:%b %d, %Y}" if invoice.due_date else "",
        "",
        "PDF invoice is attached.",
        "Thank you!",
    ]
    return "\n".join(line for line in lines if line)


def _monthly_invoice_pdf_bytes(item):
    from invoices.views import _invoice_pdf_context, render_to_pdf

    if not item.invoice:
        return None
    if item.invoice_pdf:
        item.invoice_pdf.open("rb")
        try:
            return item.invoice_pdf.read()
        finally:
            item.invoice_pdf.close()
    return render_to_pdf(
        "invoices/invoice_pdf.html", _invoice_pdf_context(item.invoice)
    )


def send_monthly_billing_ready(
    run, *, created_by=None, retry_failed=False, dry_run=False, progress_callback=None
):
    from whatsapp.services.whatsapp import WhatsAppService

    queryset = (
        run.items.select_related("invoice", "lease", "lease__tenant")
        .filter(status=MonthlyBillingRunItem.STATUS_READY)
        .exclude(status=MonthlyBillingRunItem.STATUS_EXCLUDED)
    )
    if retry_failed:
        queryset = (
            run.items.select_related("invoice", "lease", "lease__tenant")
            .filter(status=MonthlyBillingRunItem.STATUS_FAILED)
            .exclude(status=MonthlyBillingRunItem.STATUS_EXCLUDED)
        )
    if dry_run:
        return {"ready_to_send": queryset.count(), "dry_run": True}
    service = WhatsAppService(
        created_by=created_by
        if getattr(created_by, "is_authenticated", False)
        else None
    )
    _run_log(run, "WhatsApp sending started")
    item_list = list(queryset.select_related("tenant", "property", "unit"))
    for index, item in enumerate(item_list, start=1):
        _progress(progress_callback, item, index, len(item_list), "Sending WhatsApp")
        if item.status == MonthlyBillingRunItem.STATUS_SENT and not retry_failed:
            continue
        phone = getattr(getattr(item.lease, "tenant", None), "phone", "")
        if not phone:
            _set_pending(
                item, MonthlyBillingRunItem.ISSUE_PHONE_MISSING, "Tenant phone missing."
            )
            continue
        try:
            pdf_bytes = _monthly_invoice_pdf_bytes(item)
            if not pdf_bytes:
                _set_pending(
                    item,
                    MonthlyBillingRunItem.ISSUE_PDF_FAILED,
                    "Invoice PDF could not be generated.",
                )
                continue
            result = service.send_invoice(
                item.invoice,
                phone_number=phone,
                message=build_monthly_invoice_whatsapp_message(
                    item.invoice, run.billing_month
                ),
                pdf_bytes=pdf_bytes,
                filename=f"Invoice_{item.invoice.invoice_number or item.invoice_id}.pdf",
            )
            item.whatsapp_status = "sent" if result.get("ok") else "failed"
            if result.get("ok"):
                log_id = result.get("log_id")
                item.whatsapp_message_id = str(log_id or "")
                item.status = MonthlyBillingRunItem.STATUS_SENT
                item.sent_at = timezone.now()
                item.error_text = ""
                item.save()
                _item_log(item, "WhatsApp sent")
            else:
                item.status = MonthlyBillingRunItem.STATUS_FAILED
                item.error_text = result.get("error", "WhatsApp send failed.")
                item.issue_code = MonthlyBillingRunItem.ISSUE_WHATSAPP_FAILED
                item.issue_message = "WhatsApp send failed."
                item.save()
                _item_log(item, "WhatsApp failed")
        except Exception as exc:
            item.status = MonthlyBillingRunItem.STATUS_FAILED
            item.issue_code = MonthlyBillingRunItem.ISSUE_WHATSAPP_FAILED
            item.issue_message = "WhatsApp send failed."
            item.error_text = str(exc)
            item.save()
            _item_log(item, "WhatsApp failed")
    _refresh_run_counts(run)
    _run_log(run, "WhatsApp sending completed")
    return run


def send_monthly_billing_item(item, *, created_by=None):
    from whatsapp.services.whatsapp import WhatsAppService

    if item.status == MonthlyBillingRunItem.STATUS_EXCLUDED:
        _item_log(item, "WhatsApp skipped because item is excluded")
        return item
    phone = getattr(getattr(item.lease, "tenant", None), "phone", "")
    if not phone:
        _set_pending(
            item, MonthlyBillingRunItem.ISSUE_PHONE_MISSING, "Tenant phone missing."
        )
        return item

    service = WhatsAppService(
        created_by=created_by
        if getattr(created_by, "is_authenticated", False)
        else None
    )
    try:
        pdf_bytes = _monthly_invoice_pdf_bytes(item)
        if not pdf_bytes:
            _set_pending(
                item,
                MonthlyBillingRunItem.ISSUE_PDF_FAILED,
                "Invoice PDF could not be generated.",
            )
            return item
        result = service.send_invoice(
            item.invoice,
            phone_number=phone,
            message=build_monthly_invoice_whatsapp_message(
                item.invoice, item.billing_run.billing_month
            ),
            pdf_bytes=pdf_bytes,
            filename=f"Invoice_{item.invoice.invoice_number or item.invoice_id}.pdf",
        )
        item.whatsapp_status = "sent" if result.get("ok") else "failed"
        if result.get("ok"):
            item.whatsapp_message_id = str(result.get("log_id") or "")
            item.status = MonthlyBillingRunItem.STATUS_SENT
            item.sent_at = timezone.now()
            item.error_text = ""
            item.issue_code = ""
            item.issue_message = ""
            item.save()
            _item_log(item, "WhatsApp resent")
        else:
            item.status = MonthlyBillingRunItem.STATUS_FAILED
            item.error_text = result.get("error", "WhatsApp send failed.")
            item.issue_code = MonthlyBillingRunItem.ISSUE_WHATSAPP_FAILED
            item.issue_message = "WhatsApp send failed."
            item.save()
            _item_log(item, "WhatsApp resend failed")
    except Exception as exc:
        item.status = MonthlyBillingRunItem.STATUS_FAILED
        item.issue_code = MonthlyBillingRunItem.ISSUE_WHATSAPP_FAILED
        item.issue_message = "WhatsApp send failed."
        item.error_text = str(exc)
        item.save()
        _item_log(item, "WhatsApp resend failed")
    _refresh_run_counts(item.billing_run)
    return item


def run_monthly_billing_dry_run(billing_month: date, *, run=None):
    summary = run_monthly_billing_preflight(billing_month, dry_run=True)
    if run:
        summary["recurring"] = generate_monthly_billing_invoices(run, dry_run=True)
        summary["electric"] = generate_monthly_billing_electric(run, dry_run=True)
        summary["whatsapp"] = send_monthly_billing_ready(run, dry_run=True)
        run.dry_run_summary = summary
        run.save(update_fields=["dry_run_summary", "updated_at"])
        _run_log(run, "dry run completed")
    return summary


def run_monthly_billing_full(run, *, created_by=None, progress_callback=None):
    _run_log(run, "run billing started")
    run_monthly_billing_preflight(
        run.billing_month, created_by=created_by, progress_callback=progress_callback
    )
    generate_monthly_billing_invoices(run, progress_callback=progress_callback)
    generate_monthly_billing_electric(run, progress_callback=progress_callback)
    prepare_monthly_billing_ready(run, progress_callback=progress_callback)
    _run_log(run, "run billing completed")
    return run


def exclude_monthly_billing_item(item, *, reason, user=None):
    item.status = MonthlyBillingRunItem.STATUS_EXCLUDED
    item.excluded_reason = reason or "Excluded from billing run."
    item.excluded_by = user if getattr(user, "is_authenticated", False) else None
    item.excluded_at = timezone.now()
    item.issue_code = ""
    item.issue_message = ""
    item.save(
        update_fields=[
            "status",
            "excluded_reason",
            "excluded_by",
            "excluded_at",
            "issue_code",
            "issue_message",
            "updated_at",
        ]
    )
    _item_log(item, f"excluded from billing run: {item.excluded_reason}")
    _refresh_run_counts(item.billing_run)
    return item


def _invoice_has_payment_or_allocation(invoice):
    if not invoice:
        return False, ""
    if invoice.status == "paid":
        return True, "Invoice is marked paid."
    if getattr(invoice.lease, "payments", None) and invoice.lease.payments.exists():
        return True, "Lease has payment records; review allocations before rollback."
    return False, ""


@transaction.atomic
def rollback_monthly_billing_item(item, *, user=None):
    if item.status == MonthlyBillingRunItem.STATUS_SENT:
        raise ValueError("Sent invoice cannot be rolled back automatically.")
    invoice = item.invoice
    blocked, reason = _invoice_has_payment_or_allocation(invoice)
    if blocked:
        item.rollback_message = reason
        item.save(update_fields=["rollback_message", "updated_at"])
        raise ValueError(reason)

    item_ids = [int(v) for v in (item.created_invoice_item_ids or []) if v]
    if item_ids:
        InvoiceItem.objects.filter(pk__in=item_ids, invoice=invoice).delete()
    if invoice and invoice.pk in [int(v) for v in (item.created_invoice_ids or [])]:
        invoice.delete()
        item.invoice = None
    elif invoice:
        invoice.amount = sum(
            (line.amount for line in invoice.items.all()), Decimal("0.00")
        )
        invoice.save(update_fields=["amount", "updated_at"])

    item.status = MonthlyBillingRunItem.STATUS_ROLLED_BACK
    item.rolled_back_at = timezone.now()
    item.rollback_message = "Rolled back records created by this billing run."
    item.invoice_pdf = None
    item.whatsapp_status = ""
    item.whatsapp_message_id = ""
    item.sent_at = None
    item.save(
        update_fields=[
            "status",
            "rolled_back_at",
            "rollback_message",
            "invoice",
            "invoice_pdf",
            "whatsapp_status",
            "whatsapp_message_id",
            "sent_at",
            "updated_at",
        ]
    )
    _item_log(item, "rolled back")
    _refresh_run_counts(item.billing_run)
    return item


def rollback_monthly_billing_run(run, *, user=None, progress_callback=None):
    blocked = []
    item_list = list(
        run.items.exclude(
            status__in=[
                MonthlyBillingRunItem.STATUS_ROLLED_BACK,
                MonthlyBillingRunItem.STATUS_EXCLUDED,
            ]
        ).select_related("tenant", "property", "unit")
    )
    for index, item in enumerate(item_list, start=1):
        _progress(progress_callback, item, index, len(item_list), "Rollback")
        try:
            rollback_monthly_billing_item(item, user=user)
        except ValueError as exc:
            blocked.append({"item_id": item.pk, "reason": str(exc)})
    if blocked:
        _run_log(run, f"rollback partially blocked for {len(blocked)} item(s)")
    else:
        _run_log(run, "billing run rolled back")
    _refresh_run_counts(run)
    return blocked


def audit_electric_posting_inconsistencies(billing_month: date | None = None):

    qs = InvoiceItem.objects.filter(
        category__name__icontains="electric", description__icontains="Billing Period="
    ).select_related("invoice", "invoice__lease")
    rows = []
    for line in qs.order_by("-invoice__issue_date", "invoice_id")[:1000]:
        desc = line.description or ""
        marker = "Billing Period="
        if marker not in desc:
            continue
        period_text = desc.split(marker, 1)[1].split(",", 1)[0].strip()
        try:
            period_start = datetime.strptime(
                period_text.split(" to ", 1)[0], "%Y-%m-%d"
            ).date()
        except Exception:
            continue
        expected_posting = _add_months(first_of_month(period_start), 1)
        invoice_month = first_of_month(line.invoice.issue_date)
        if billing_month and invoice_month != first_of_month(billing_month):
            continue
        if invoice_month != expected_posting:
            rows.append(
                {
                    "invoice_id": line.invoice_id,
                    "invoice_number": line.invoice.invoice_number,
                    "lease_id": line.invoice.lease_id,
                    "item_id": line.pk,
                    "electric_period": period_text,
                    "invoice_month": invoice_month.isoformat(),
                    "expected_posting_month": expected_posting.isoformat(),
                    "amount": str(line.amount),
                }
            )
    return rows
