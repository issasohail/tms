# invoices/services.py
from __future__ import annotations

from django.utils import timezone
from django.db.models import Sum
from calendar import monthrange
from datetime import date
from decimal import Decimal
from datetime import date, timedelta
from django.db import transaction
from django.utils.timezone import now

# IMPORTANT: import Lease from *this* app to avoid "name 'Lease' is not defined"
from django.apps import apps
from .models import Invoice, InvoiceItem, ItemCategory, RecurringCharge, WaterBill


def first_of_month(d: date) -> date:
    return d.replace(day=1)


def _lease_qs():
    """Resolve the Lease model at runtime to avoid circular/ordering issues."""
    try:
        Lease = apps.get_model('leases', 'Lease')
    except LookupError:
        Lease = apps.get_model('invoices', 'Lease')
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
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum

from invoices.models import Invoice   # adjust name if needed



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
        category=RENT_CATEGORY,   # or however you tag “main rent” rows
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
    defaults = {
        'due_date': period_date + timedelta(days=7),
        'description': f"Invoice for {period_date:%B %Y}",
    }
    invoice_fields = {f.name for f in Invoice._meta.fields}
    if 'amount' in invoice_fields:
        defaults['amount'] = Decimal('0.00')

    inv, _ = Invoice.objects.get_or_create(
        lease=lease,
        issue_date=period_date,
        defaults=defaults
    )
    return inv


def active_leases_qs():
    # adapt to your status field; using 'active' as seen in your codebase
    return _lease_qs().filter(status='active')


def _rule_targets(rc: RecurringCharge):
    if rc.scope == 'LEASE' and rc.lease_id:
        return active_leases_qs().filter(pk=rc.lease_id)
    if rc.scope == 'PROPERTY' and rc.property_id:
        return active_leases_qs().filter(unit__property_id=rc.property_id)
    return active_leases_qs()


def apply_fixed_recurring(period_date: date, cutoff_today: bool = False):
    """Apply all FIXED recurring rules into invoices for period_date."""
    from .models import RecurringCharge, InvoiceItem  # local to avoid import loops

    rules = (RecurringCharge.objects
             .filter(active=True, kind='FIXED')
             .select_related('lease', 'property', 'category'))

    period_first = first_of_month(period_date)
    period_last = last_of_month(period_date)
    today = date.today()
    is_current_month = (period_first.year ==
                        today.year and period_first.month == today.month)

    for rc in rules:
        # start must be in/before this period
        if rc.start_date and rc.start_date > period_last:
            continue
        # end must not be earlier than either the start of the period
        # OR 'today' when we are generating the current month with cutoff_today=True
        if rc.end_date:
            end_cut = today if (
                cutoff_today and is_current_month) else period_first
            if rc.end_date < end_cut:
                continue

        # choose targets by scope
        if rc.scope == 'LEASE' and rc.lease_id:
            targets = active_leases_qs().filter(pk=rc.lease_id)
        elif rc.scope == 'PROPERTY' and rc.property_id:
            targets = active_leases_qs().filter(unit__property_id=rc.property_id)
        else:  # GLOBAL
            targets = active_leases_qs()

        for lease in targets:
            inv = ensure_month_invoice(
                lease, period_first)  # invoice date = 1st
            amt = rc.amount or Decimal('0.00')
            # idempotent: avoid duplicates per (invoice, category, description)
            InvoiceItem.objects.get_or_create(
                invoice=inv,
                category=rc.category,
                description=(rc.description or rc.category.name),
                defaults={'amount': amt, 'is_recurring': True},
            )


def post_water_bill(water_bill_id: int):
    """
    Split a water bill evenly across active leases in that property and month.
    Idempotent: skips if already posted.
    """
    wb = WaterBill.objects.select_related('property').get(pk=water_bill_id)
    if wb.posted:
        return

    leases = list(active_leases_qs().filter(unit__property=wb.property))
    if not leases:
        wb.posted = True
        wb.save(update_fields=['posted'])
        return

    n = len(leases)
    base = (wb.total_amount / n).quantize(Decimal('0.01'))
    remainder = (wb.total_amount - base * n)    # may be 0.01..0.04 in PKR
    steps = int((remainder * 100).copy_abs())

    adjustments = [Decimal('0.00')] * n
    for i in range(steps):
        adjustments[i] += Decimal('0.01') if remainder > 0 else Decimal('-0.01')

    water_cat, _ = ItemCategory.objects.get_or_create(name='Water Charges')

    for lease, adj in zip(leases, adjustments):
        inv = ensure_month_invoice(lease, wb.period)
        InvoiceItem.objects.create(
            invoice=inv,
            category=water_cat,
            description=wb.description or f"Water charges {wb.period:%b %Y}",
            amount=base + adj
        )

    wb.posted = True
    wb.save(update_fields=['posted'])


@transaction.atomic
def run_monthly_billing_for(period_date: date, cutoff_today: bool = False):
    """One-click: ensure monthly invoices and apply recurring rules."""
    # 1) one invoice per active lease
    for lease in active_leases_qs():
        ensure_month_invoice(lease, first_of_month(period_date))

    # 2) apply FIXED recurring rows
    apply_fixed_recurring(period_date, cutoff_today=cutoff_today)

    # 3) optional: post any water bills for this month
    for wb in WaterBill.objects.filter(period=first_of_month(period_date), posted=False):
        post_water_bill(wb.id)

# add near your other billing helpers (uses your existing ensure_month_invoice)


def backfill_recurring_to_invoices(recurring_id: int, end_period: date | None = None):
    """
    For a given RecurringCharge, post its amount onto monthly invoices from its start month
    up to (but not including) end_period's month (default: current month).
    Skips months outside the lease term. Avoids duplicates per (invoice, category, description).
    """
    from .models import RecurringCharge, InvoiceItem  # local import to avoid cycles

    rc = RecurringCharge.objects.select_related(
        'lease', 'category').get(pk=recurring_id)
    lease = rc.lease
    if not lease:
        return 0

    # figure time window
    today = date.today()
    end_period = _first_of_month(
        end_period or today)       # exclusive upper bound
    start = rc.start_date or getattr(lease, 'start_date', None) or today
    cur = _first_of_month(start)

    # clamp to lease bounds
    lease_start = getattr(lease, 'start_date', None)
    lease_end = getattr(lease, 'end_date', None)
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
        desc = rc.description or (
            rc.category.name if rc.category_id else "Recurring")
        defaults = {'amount': rc.amount or Decimal('0.00')}
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
    return apps.get_model('invoices', 'ItemCategory')


def _Invoice():
    return apps.get_model('invoices', 'Invoice')


def _InvoiceItem():
    return apps.get_model('invoices', 'InvoiceItem')


def get_security_category():
    Cat = _ItemCategory()
    cat, _ = Cat.objects.get_or_create(
        name=SECURITY_CATEGORY_NAME, defaults={'is_active': True})
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

    amt = getattr(lease, 'security_amount', None)
    if amt is None:
        return None

    issue_date = getattr(lease, 'start_date', None) or timezone.now().date()
    due_date = issue_date

    inv = Invoice.objects.filter(
        lease=lease, description="SECURITY_DEPOSIT").first()
    if not inv:
        inv = Invoice(lease=lease, issue_date=issue_date, due_date=due_date, amount=Decimal('0.00'),
                      status='draft', description="SECURITY_DEPOSIT")
        inv.save()

    if Decimal(amt) <= 0:
        # remove any existing item and possibly the invoice
        InvoiceItem.objects.filter(
            invoice=inv, category=cat, description="Security Deposit").delete()
        if not inv.items.exists():
            inv.delete()
            return None
        return inv

    item, created = InvoiceItem.objects.get_or_create(
        invoice=inv,
        category=cat,
        description="Security Deposit",
        defaults={'amount': Decimal(amt), 'is_recurring': False}
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
    total = (InvoiceItem.objects
             .filter(invoice__lease=lease, category=cat)
             .aggregate(total=Sum('amount'))['total'] or Decimal('0.00'))
    return total
# invoices/services.py
from decimal import Decimal
from django.db.models import Sum

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
        return q.filter(type__in=types).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')

    required = lease.security_deposit or Decimal('0.00')
    paid_in = _sum(qs, ['PAYMENT'])
    refunded = _sum(qs, ['REFUND'])
    damages = _sum(qs, ['DAMAGE'])

    balance_to_collect = required - paid_in
    currently_held = paid_in - refunded - damages

    return {
        'required': required,
        'paid_in': paid_in,
        'refunded': refunded,
        'damages': damages,
        'balance_to_collect': balance_to_collect,
        'currently_held': currently_held,
    }

# invoices/services.py
from decimal import Decimal
from django.db.models import Sum
from .models import SecurityDepositTransaction  # adjust path if needed


from decimal import Decimal
from django.db.models import Sum

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
    effective_paid_in  = paid_in + adjust
    balance_to_collect = max(required - effective_paid_in, 0)
    currently_held     = max(effective_paid_in - refunded - damages, 0)
    """
    ZERO = Decimal('0.00')

    if not lease:
        return {
            "required": ZERO,
            "paid_in": ZERO,
            "refunded": ZERO,
            "damages": ZERO,
            "adjust": ZERO,
            "balance_to_collect": ZERO,
            "currently_held": ZERO,
        }

    required = lease.security_deposit or ZERO

    qs = SecurityDepositTransaction.objects.filter(lease=lease)

    paid_in  = qs.filter(type="PAYMENT").aggregate(total=Sum("amount"))["total"] or ZERO
    refunded = qs.filter(type="REFUND").aggregate(total=Sum("amount"))["total"] or ZERO
    damages  = qs.filter(type="DAMAGE").aggregate(total=Sum("amount"))["total"] or ZERO
    adjust   = qs.filter(type="ADJUST").aggregate(total=Sum("amount"))["total"] or ZERO

    effective_paid_in = paid_in + adjust

    balance_to_collect = max(required - effective_paid_in, ZERO)
    currently_held     = max(effective_paid_in - refunded - damages, ZERO)

    return {
        "required": required,
        "paid_in": paid_in,
        "refunded": refunded,
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
    return security_deposit_totals(lease)['balance_to_collect']

# invoices/services.py
from decimal import Decimal
from django.utils import timezone

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
        "PAYMENT":  "*Security Deposit payment received*",
        "REFUND":   "*Security Deposit refunded*",
        "DAMAGE":   "*Security Deposit used for damages*",
        "ADJUST":   "*Security Deposit adjusted*",
        "REQUIRED": "*Security Deposit requirement recorded*",
    }
    heading = heading_map.get(tx.type, "*Security Deposit update*")

    # Dates
    period_start = lease.start_date.strftime("%b %d, %Y") if getattr(lease, "start_date", None) else ""
    period_end = lease.end_date.strftime("%b %d, %Y") if getattr(lease, "end_date", None) else ""
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
