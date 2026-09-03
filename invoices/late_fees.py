from decimal import Decimal

from django.db import transaction
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from invoices.historical_units import prepare_historical_invoice_units
from invoices.models import Invoice, InvoiceItem, InvoiceLateFeeReminder, ItemCategory

LATE_FEE_CATEGORY_NAME = "Late Fee"


def get_late_fee_category():
    category, _ = ItemCategory.objects.get_or_create(
        name=LATE_FEE_CATEGORY_NAME,
        defaults={"is_active": True},
    )
    return category


def get_invoice_principal_amount(invoice, late_fee_category=None):
    late_fee_category = late_fee_category or get_late_fee_category()
    total = (
        invoice.items
        .exclude(category=late_fee_category)
        .aggregate(total=Sum("amount"))
        .get("total")
    )
    return total if total is not None else (invoice.amount or Decimal("0.00"))


def calculate_late_fee(invoice, cfg, late_fee_category=None):
    base_amount = get_invoice_principal_amount(invoice, late_fee_category=late_fee_category)
    if cfg["type"] == "percent":
        fee = base_amount * (cfg["percent"] or Decimal("0.00")) / Decimal("100.00")
        return fee.quantize(Decimal("0.01"))
    return cfg["amount"] or Decimal("0.00")


def sent_reminder_count(invoice):
    prefetched = getattr(invoice, "_prefetched_objects_cache", {}).get(
        "late_fee_reminders"
    )
    if prefetched is not None:
        return sum(
            reminder.status != InvoiceLateFeeReminder.STATUS_FAILED
            for reminder in prefetched
        )
    return invoice.late_fee_reminders.exclude(
        status=InvoiceLateFeeReminder.STATUS_FAILED
    ).count()


def get_due_reminder_number(invoice, cfg, today=None):
    today = today or timezone.localdate()
    if not cfg["enabled"] or not invoice.due_date:
        return None

    days_overdue = (today - invoice.due_date).days
    if days_overdue < cfg["grace_days"]:
        return None

    sent_count = sent_reminder_count(invoice)
    max_reminders = cfg["max_reminders"] or 0
    if max_reminders and sent_count >= max_reminders:
        return None

    interval = cfg["reminder_interval_days"] or 1
    next_number = sent_count + 1
    days_needed = cfg["grace_days"] + ((next_number - 1) * interval)
    if days_overdue < days_needed:
        return None
    return next_number


def _send_late_fee_whatsapp(invoice, reminder_number, user=None):
    from leases.whatsapp import lease_whatsapp_context
    from whatsapp.services.whatsapp import WhatsAppService

    lease = invoice.lease
    tenant = getattr(lease, "tenant", None)
    phone = getattr(tenant, "phone", "") or ""
    if not phone:
        return {"ok": False, "error": "No tenant phone number on file."}

    service = WhatsAppService(created_by=user)
    if hasattr(service, "send_late_fee_reminder_template"):
        return service.send_late_fee_reminder_template(invoice, reminder_number, phone_number=phone)

    context = lease_whatsapp_context(lease)
    context.update({
        "INVOICE_NUMBER": invoice.invoice_number,
        "REMINDER_NUMBER": reminder_number,
        "DUE_DATE": invoice.due_date.strftime("%Y-%m-%d") if invoice.due_date else "",
        "DAYS_OVERDUE": (timezone.localdate() - invoice.due_date).days if invoice.due_date else "",
    })
    body = (
        "Dear [TENANT_NAME],\n"
        "This is late payment reminder #[REMINDER_NUMBER] for invoice #[INVOICE_NUMBER], "
        "due on [DUE_DATE]. Current balance: [BALANCE_AMOUNT].\n"
        "Please arrange payment as soon as possible to avoid further late fees."
    )
    for key, value in context.items():
        body = body.replace(f"[{key}]", str(value or ""))
    return service.send_text(phone, body, tenant=tenant, lease=lease, invoice=invoice)


def process_invoice_late_fee_reminder(
    invoice,
    sent_via="manual",
    user=None,
    force=False,
    today=None,
):
    from core.models import GlobalSettings
    from leases.models_late_fee import get_effective_late_fee_settings
    from whatsapp.models import WhatsAppMessageLog

    if invoice.status in ("paid", "cancelled"):
        return {"ok": False, "reason": "Invoice is paid or cancelled."}
    if (invoice.amount or Decimal("0.00")) <= 0:
        return {"ok": False, "reason": "Zero-amount invoices do not receive reminders or late fees."}
    if not invoice.due_date:
        return {"ok": False, "reason": "Invoice has no due date."}

    today = today or timezone.localdate()
    settings_obj = GlobalSettings.get_solo()
    if settings_obj.late_fee_skip_current_month and (
        invoice.issue_date.year,
        invoice.issue_date.month,
    ) == (today.year, today.month):
        return {"ok": False, "reason": "Current-month reminders are disabled in Settings."}
    if invoice.late_fee_hold_is_active(today):
        return {
            "ok": False,
            "reason": f"Reminder and late fee are on hold through {invoice.late_fee_hold_until}.",
        }

    cfg = get_effective_late_fee_settings(invoice.lease)
    if not cfg["enabled"]:
        return {"ok": False, "reason": "Late fees are disabled for this lease."}

    days_overdue = (today - invoice.due_date).days
    if days_overdue < cfg["grace_days"]:
        return {"ok": False, "reason": "Grace period has not elapsed yet."}

    with transaction.atomic():
        invoice = (
            Invoice.objects
            .select_for_update()
            .select_related("lease", "lease__tenant")
            .get(pk=invoice.pk)
        )
        if (invoice.amount or Decimal("0.00")) <= 0:
            return {"ok": False, "reason": "Zero-amount invoices do not receive reminders or late fees."}
        if settings_obj.late_fee_skip_current_month and (
            invoice.issue_date.year,
            invoice.issue_date.month,
        ) == (today.year, today.month):
            return {"ok": False, "reason": "Current-month reminders are disabled in Settings."}
        if invoice.late_fee_hold_is_active(today):
            return {
                "ok": False,
                "reason": f"Reminder and late fee are on hold through {invoice.late_fee_hold_until}.",
            }
        cfg = get_effective_late_fee_settings(invoice.lease)
        reminder_number = get_due_reminder_number(invoice, cfg, today=today)
        if reminder_number is None:
            if not force:
                return {"ok": False, "reason": "No reminder is due yet."}
            sent_count = sent_reminder_count(invoice)
            max_reminders = cfg["max_reminders"] or 0
            if max_reminders and sent_count >= max_reminders:
                return {"ok": False, "reason": "Maximum reminders already reached."}
            reminder_number = sent_count + 1

        reminder, created = InvoiceLateFeeReminder.objects.get_or_create(
            invoice=invoice,
            reminder_number=reminder_number,
            defaults={
                "sent_via": sent_via,
                "status": InvoiceLateFeeReminder.STATUS_FAILED,
                "created_by": user,
            },
        )
        if not created and reminder.status != InvoiceLateFeeReminder.STATUS_FAILED:
            return {"ok": False, "reason": "This reminder was already processed."}

        send_result = _send_late_fee_whatsapp(invoice, reminder_number, user=user)
        send_result = send_result if isinstance(send_result, dict) else {"ok": False, "error": str(send_result)}

        whatsapp_log = None
        log_id = send_result.get("log_id")
        if log_id:
            whatsapp_log = WhatsAppMessageLog.objects.filter(pk=log_id).first()

        reminder.sent_via = sent_via
        reminder.status = InvoiceLateFeeReminder.STATUS_SENT
        reminder.whatsapp_message = whatsapp_log
        reminder.created_by = user
        reminder.error_text = ""

        if not send_result.get("ok"):
            reminder.status = InvoiceLateFeeReminder.STATUS_FAILED
            reminder.error_text = send_result.get("error", "") or send_result.get("reason", "")
            reminder.save(update_fields=[
                "sent_via", "status", "whatsapp_message", "created_by", "error_text"
            ])
            return {"ok": False, "reason": "WhatsApp send failed.", "reminder": reminder}

        late_fee_category = get_late_fee_category()
        fee = calculate_late_fee(invoice, cfg, late_fee_category=late_fee_category)
        cap = GlobalSettings.get_solo().billing_cap_amount or Decimal("0.00")
        current_total = invoice.amount or Decimal("0.00")
        if cap and current_total >= cap:
            fee = Decimal("0.00")
        elif cap and current_total + fee > cap:
            fee = cap - current_total

        reminder.fee_amount = fee
        if fee > 0 and cfg["auto_apply"]:
            item = InvoiceItem.objects.create(
                invoice=invoice,
                category=late_fee_category,
                description=f"Late fee - reminder #{reminder_number} sent {today:%Y-%m-%d}",
                amount=fee,
                is_recurring=False,
            )
            invoice.status = "overdue"
            invoice.save(update_fields=["status", "updated_at"])
            reminder.late_fee_item = item
            reminder.status = InvoiceLateFeeReminder.STATUS_FEE_APPLIED
        elif fee > 0:
            reminder.status = InvoiceLateFeeReminder.STATUS_FEE_PENDING

        reminder.save(update_fields=[
            "sent_via", "status", "whatsapp_message", "created_by", "error_text",
            "fee_amount", "late_fee_item",
        ])
        return {"ok": True, "reminder": reminder, "fee": fee, "reminder_number": reminder_number}


def approve_pending_late_fee(reminder, user=None):
    if reminder.status != InvoiceLateFeeReminder.STATUS_FEE_PENDING:
        return {"ok": False, "reason": "This reminder has no pending fee."}
    if reminder.fee_amount <= 0:
        return {"ok": False, "reason": "Fee amount is zero."}
    invoice = reminder.invoice
    today = timezone.localdate()
    if invoice.late_fee_hold_is_active(today):
        return {
            "ok": False,
            "reason": f"Reminder and late fee are on hold through {invoice.late_fee_hold_until}.",
        }
    from core.models import GlobalSettings
    settings_obj = GlobalSettings.get_solo()
    if settings_obj.late_fee_skip_current_month and (
        invoice.issue_date.year,
        invoice.issue_date.month,
    ) == (today.year, today.month):
        return {"ok": False, "reason": "Current-month reminders are disabled in Settings."}

    with transaction.atomic():
        reminder = InvoiceLateFeeReminder.objects.select_for_update().get(pk=reminder.pk)
        if reminder.status != InvoiceLateFeeReminder.STATUS_FEE_PENDING:
            return {"ok": False, "reason": "This reminder has already been handled."}
        item = InvoiceItem.objects.create(
            invoice=reminder.invoice,
            category=get_late_fee_category(),
            description=f"Late fee - reminder #{reminder.reminder_number} approved {timezone.localdate():%Y-%m-%d}",
            amount=reminder.fee_amount,
            is_recurring=False,
        )
        reminder.invoice.status = "overdue"
        reminder.invoice.save(update_fields=["status", "updated_at"])
        reminder.late_fee_item = item
        reminder.status = InvoiceLateFeeReminder.STATUS_FEE_APPLIED
        reminder.save(update_fields=["late_fee_item", "status"])
    return {"ok": True, "reminder": reminder}


def reject_pending_late_fee(reminder):
    if reminder.status != InvoiceLateFeeReminder.STATUS_FEE_PENDING:
        return {"ok": False, "reason": "This reminder has no pending fee."}
    reminder.status = InvoiceLateFeeReminder.STATUS_SENT
    reminder.fee_amount = Decimal("0.00")
    reminder.save(update_fields=["status", "fee_amount"])
    return {"ok": True, "reminder": reminder}


def collect_due_invoices(today=None, start_date=None, skip_current_month=False):
    today = today or timezone.localdate()
    invoices = prepare_historical_invoice_units(
        Invoice.objects
        .exclude(status__in=["paid", "cancelled"])
        .filter(amount__gt=0, due_date__lte=today)
        .filter(Q(late_fee_hold_until__isnull=True) | Q(late_fee_hold_until__lt=today))
        .select_related("lease", "lease__tenant", "lease__unit", "lease__unit__property")
        .prefetch_related("late_fee_reminders", "items")
    )
    if start_date:
        invoices = invoices.filter(due_date__gte=start_date)
    if skip_current_month:
        invoices = invoices.exclude(
            issue_date__year=today.year,
            issue_date__month=today.month,
        )
    return invoices


def _invoice_summary_detail(invoice, reminder_number, error=None):
    unit = getattr(invoice, "historical_unit", None)
    property_obj = getattr(unit, "property", None) if unit else None
    detail = {
        "invoice_id": invoice.pk,
        "invoice_number": invoice.invoice_number,
        "reminder_number": reminder_number,
        "property_name": getattr(property_obj, "property_name", "") or "—",
        "unit_name": getattr(unit, "unit_number", "") or "—",
        "tenant_name": invoice.lease.tenant.get_full_name() or "—",
        "invoice_date": invoice.issue_date.isoformat() if invoice.issue_date else "",
        "due_date": invoice.due_date.isoformat() if invoice.due_date else "",
        "balance": str(invoice.outstanding_balance),
    }
    if error:
        detail["error"] = str(error)
    return detail


def _attach_outstanding_balances(invoices):
    """Prime Invoice.accounting_allocation without per-invoice payment queries."""
    from payments.models import Payment

    lease_ids = {invoice.lease_id for invoice in invoices}
    if not lease_ids:
        return
    zero = Decimal("0.00")
    money_field = DecimalField(max_digits=12, decimal_places=2)
    payment_totals = {
        row["lease_id"]: row["total"] or zero
        for row in (
            Payment.objects.filter(lease_id__in=lease_ids)
            .values("lease_id")
            .annotate(total=Coalesce(
                Sum(Case(
                    When(detail__isnull=False, then=F("detail__lease_amount")),
                    default=F("amount"),
                    output_field=money_field,
                )),
                Value(zero),
                output_field=money_field,
            ))
        )
    }
    available_by_lease = dict(payment_totals)
    allocations = {}
    rows = list(
        Invoice.objects.filter(lease_id__in=lease_ids)
        .order_by("lease_id", "issue_date", "id")
        .values("id", "lease_id", "amount", "due_date", "lifecycle_status", "status")
    )
    eligible_rows = [
        row for row in rows
        if row["lifecycle_status"] not in {"cancelled", "void"}
        and row["status"] != "cancelled"
    ]
    last_by_lease = {row["lease_id"]: row["id"] for row in eligible_rows}
    today = timezone.localdate()
    for row in eligible_rows:
        amount = row["amount"] or zero
        available = available_by_lease.get(row["lease_id"], zero)
        allocated = min(max(available, zero), amount)
        available -= allocated
        available_by_lease[row["lease_id"]] = available
        outstanding = max(amount - allocated, zero)
        if amount <= 0 or allocated >= amount:
            status = "overpaid" if (
                row["id"] == last_by_lease.get(row["lease_id"]) and available > 0
            ) else "paid"
        elif allocated > 0:
            status = "partially_paid"
        elif row["due_date"] and row["due_date"] < today:
            status = "overdue"
        else:
            status = "unpaid"
        allocations[row["id"]] = (allocated, outstanding, status)
    for invoice in invoices:
        invoice._accounting_allocation_cache = allocations.get(
            invoice.pk,
            (zero, invoice.amount or zero, "unpaid"),
        )


def _send_staff_late_fee_summary(settings_obj, details, user=None):
    staff = getattr(settings_obj, "whatsapp_accounts_staff", None)
    phone = getattr(staff, "whatsapp_number", "") if staff else ""
    if not phone:
        return {"ok": False, "error": "No WhatsApp number is configured for Accounts staff."}

    from whatsapp.services.whatsapp import WhatsAppService

    header = [
        f"Late-fee reminder run: {timezone.localdate():%Y-%m-%d}",
        f"Processed: {len(details)}",
        "",
    ]
    lines = []
    for detail in details:
        lines.append(
            f"#{detail['invoice_number']} | {detail['property_name']} / {detail['unit_name']} | "
            f"{detail['tenant_name']} | Balance Rs. {detail['balance']}"
        )
    chunks = []
    current = list(header)
    current_length = len("\n".join(current))
    for line in lines:
        if current_length + len(line) + 1 > 3000 and len(current) > len(header):
            chunks.append("\n".join(current))
            current = [f"Late-fee reminder run (continued): {timezone.localdate():%Y-%m-%d}", ""]
            current_length = len("\n".join(current))
        current.append(line)
        current_length += len(line) + 1
    chunks.append("\n".join(current))

    service = WhatsAppService(created_by=user)
    try:
        for chunk in chunks:
            result = service.send_text(phone, chunk)
            if not isinstance(result, dict) or not result.get("ok"):
                return result if isinstance(result, dict) else {"ok": False, "error": str(result)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "messages_sent": len(chunks)}


def run_due_late_fee_reminders(
    *,
    source=InvoiceLateFeeReminder.SOURCE_AUTO,
    user=None,
    today=None,
    dry_run=False,
):
    """Run every legitimately due reminder through the single invoice service.

    Scheduler runs respect the automatic-reminder switch. Manual batch runs are a
    fallback for missed due reminders and intentionally ignore only that switch.
    All grace, interval, maximum, invoice-status and lease override rules remain in
    force for both sources.
    """
    from core.models import GlobalSettings
    from leases.models_late_fee import get_effective_late_fee_settings

    today = today or timezone.localdate()
    automatic = source == InvoiceLateFeeReminder.SOURCE_AUTO
    settings_obj = GlobalSettings.get_solo()
    summary = {
        "examined": 0,
        "due": 0,
        "processed": 0,
        "failed": 0,
        "fees_applied": 0,
        "fees_pending": 0,
        "skipped": 0,
        "excluded_before_start": 0,
        "excluded_zero_amount": 0,
        "excluded_current_month": 0,
        "excluded_on_hold": 0,
        "staff_summary_sent": False,
        "staff_summary_error": "",
        "automation_start_date": settings_obj.late_fee_automation_start_date,
        "dry_run": bool(dry_run),
        "details": [],
    }
    if not settings_obj.late_fee_enabled:
        summary["reason"] = "Late fees are disabled."
        return summary
    if automatic and not settings_obj.late_fee_auto_send_reminders:
        summary["reason"] = "Automatic late fee reminders are disabled."
        return summary

    base_due = Invoice.objects.exclude(status__in=["paid", "cancelled"]).filter(
        due_date__lte=today
    )
    summary["excluded_zero_amount"] = base_due.filter(
        Q(amount__lte=0) | Q(amount__isnull=True)
    ).count()
    positive_due = base_due.filter(amount__gt=0)
    summary["excluded_on_hold"] = positive_due.filter(
        late_fee_hold_until__gte=today
    ).count()
    if settings_obj.late_fee_skip_current_month:
        summary["excluded_current_month"] = positive_due.filter(
            issue_date__year=today.year,
            issue_date__month=today.month,
        ).filter(
            Q(late_fee_hold_until__isnull=True) | Q(late_fee_hold_until__lt=today)
        ).count()

    all_due_invoices = collect_due_invoices(
        today=today,
        skip_current_month=settings_obj.late_fee_skip_current_month,
    )
    start_date = settings_obj.late_fee_automation_start_date
    if start_date:
        summary["excluded_before_start"] = all_due_invoices.filter(
            due_date__lt=start_date
        ).count()
    eligible_invoices = list(collect_due_invoices(
        today=today,
        start_date=start_date,
        skip_current_month=settings_obj.late_fee_skip_current_month,
    ))
    _attach_outstanding_balances(eligible_invoices)
    for invoice in eligible_invoices:
        summary["examined"] += 1
        cfg = get_effective_late_fee_settings(invoice.lease)
        reminder_number = get_due_reminder_number(invoice, cfg, today=today)
        if reminder_number is None:
            summary["skipped"] += 1
            continue

        summary["due"] += 1
        if dry_run:
            summary["processed"] += 1
            summary["details"].append(
                _invoice_summary_detail(invoice, reminder_number)
            )
            continue

        try:
            result = process_invoice_late_fee_reminder(
                invoice,
                sent_via=source,
                user=user,
                today=today,
            )
        except Exception as exc:
            summary["failed"] += 1
            summary["details"].append(
                _invoice_summary_detail(invoice, reminder_number, error=exc)
            )
            continue

        if not result.get("ok"):
            summary["failed"] += 1
            summary["details"].append(_invoice_summary_detail(
                invoice,
                reminder_number,
                error=result.get("reason") or "Late-fee reminder failed.",
            ))
            continue

        summary["processed"] += 1
        reminder = result["reminder"]
        if reminder.status == InvoiceLateFeeReminder.STATUS_FEE_APPLIED:
            summary["fees_applied"] += 1
        elif reminder.status == InvoiceLateFeeReminder.STATUS_FEE_PENDING:
            summary["fees_pending"] += 1
        summary["details"].append(
            _invoice_summary_detail(invoice, reminder_number)
        )

    summary["skipped"] += max(
        0, summary["examined"] - summary["due"] - summary["skipped"]
    )
    if (
        not dry_run
        and summary["processed"]
        and settings_obj.late_fee_staff_summary_enabled
    ):
        staff_result = _send_staff_late_fee_summary(
            settings_obj,
            [detail for detail in summary["details"] if not detail.get("error")],
            user=user,
        )
        summary["staff_summary_sent"] = bool(staff_result.get("ok"))
        if not staff_result.get("ok"):
            summary["staff_summary_error"] = (
                staff_result.get("error") or staff_result.get("reason") or "Staff summary failed."
            )
    return summary
