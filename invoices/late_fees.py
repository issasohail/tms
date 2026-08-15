from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

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
    if not invoice.due_date:
        return {"ok": False, "reason": "Invoice has no due date."}

    cfg = get_effective_late_fee_settings(invoice.lease)
    if not cfg["enabled"]:
        return {"ok": False, "reason": "Late fees are disabled for this lease."}

    today = today or timezone.localdate()
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


def collect_due_invoices(today=None):
    today = today or timezone.localdate()
    return (
        Invoice.objects
        .exclude(status__in=["paid", "cancelled"])
        .filter(due_date__lte=today)
        .select_related("lease", "lease__tenant")
        .prefetch_related("late_fee_reminders", "items")
    )


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
        "dry_run": bool(dry_run),
        "details": [],
    }
    if not settings_obj.late_fee_enabled:
        summary["reason"] = "Late fees are disabled."
        return summary
    if automatic and not settings_obj.late_fee_auto_send_reminders:
        summary["reason"] = "Automatic late fee reminders are disabled."
        return summary

    for invoice in collect_due_invoices(today=today):
        summary["examined"] += 1
        cfg = get_effective_late_fee_settings(invoice.lease)
        reminder_number = get_due_reminder_number(invoice, cfg, today=today)
        if reminder_number is None:
            summary["skipped"] += 1
            continue

        summary["due"] += 1
        if dry_run:
            summary["processed"] += 1
            summary["details"].append({
                "invoice_id": invoice.pk,
                "invoice_number": invoice.invoice_number,
                "reminder_number": reminder_number,
            })
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
            summary["details"].append({
                "invoice_id": invoice.pk,
                "invoice_number": invoice.invoice_number,
                "reminder_number": reminder_number,
                "error": str(exc),
            })
            continue

        if not result.get("ok"):
            summary["failed"] += 1
            summary["details"].append({
                "invoice_id": invoice.pk,
                "invoice_number": invoice.invoice_number,
                "reminder_number": reminder_number,
                "error": result.get("reason") or "Late-fee reminder failed.",
            })
            continue

        summary["processed"] += 1
        reminder = result["reminder"]
        if reminder.status == InvoiceLateFeeReminder.STATUS_FEE_APPLIED:
            summary["fees_applied"] += 1
        elif reminder.status == InvoiceLateFeeReminder.STATUS_FEE_PENDING:
            summary["fees_pending"] += 1

    summary["skipped"] += max(
        0, summary["examined"] - summary["due"] - summary["skipped"]
    )
    return summary
