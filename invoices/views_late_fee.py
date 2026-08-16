from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .late_fees import (
    _attach_outstanding_balances,
    approve_pending_late_fee,
    get_due_reminder_number,
    process_invoice_late_fee_reminder,
    reject_pending_late_fee,
    run_due_late_fee_reminders,
)
from .models import Invoice, InvoiceLateFeeReminder


@login_required
@require_POST
def send_late_fee_reminder(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("lease", "lease__tenant"), pk=pk)
    result = process_invoice_late_fee_reminder(
        invoice,
        sent_via=InvoiceLateFeeReminder.SOURCE_MANUAL,
        user=request.user,
    )
    if result.get("ok"):
        fee = result.get("fee") or Decimal("0.00")
        reminder = result["reminder"]
        if reminder.status == InvoiceLateFeeReminder.STATUS_FEE_PENDING:
            messages.success(request, f"Reminder #{result['reminder_number']} sent. Late fee of {fee} is pending approval.")
        elif reminder.status == InvoiceLateFeeReminder.STATUS_FEE_APPLIED:
            messages.success(request, f"Reminder #{result['reminder_number']} sent. Late fee of {fee} applied.")
        else:
            messages.success(request, f"Reminder #{result['reminder_number']} sent.")
    else:
        messages.error(request, result.get("reason") or "Could not send late fee reminder.")
    return redirect("invoices:invoice_detail", pk=invoice.pk)


@login_required
def late_fee_pending_queue(request):
    from core.models import GlobalSettings
    from leases.models_late_fee import get_effective_late_fee_settings

    today = timezone.localdate()
    preview = run_due_late_fee_reminders(
        source=InvoiceLateFeeReminder.SOURCE_MANUAL,
        user=request.user,
        today=today,
        dry_run=True,
    )
    pending_reminders = (
        InvoiceLateFeeReminder.objects
        .filter(status=InvoiceLateFeeReminder.STATUS_FEE_PENDING)
        .select_related("invoice", "invoice__lease", "invoice__lease__tenant")
        .order_by("-created_at")
    )
    recent_reminders = (
        InvoiceLateFeeReminder.objects
        .select_related(
            "invoice", "invoice__lease__tenant", "invoice__lease__unit",
            "invoice__lease__unit__property", "created_by",
        )
        .order_by("-created_at")[:100]
    )
    settings_obj = GlobalSettings.get_solo()
    review_invoices = list(
        Invoice.objects
        .exclude(status__in=["paid", "cancelled"])
        .filter(due_date__lte=today)
        .select_related("lease__tenant", "lease__unit", "lease__unit__property")
        .prefetch_related("late_fee_reminders")
        .order_by("-due_date", "invoice_number")[:250]
    )
    _attach_outstanding_balances(review_invoices)
    ready_ids = {item["invoice_id"] for item in preview["details"]}
    excluded_invoices = []
    for invoice in review_invoices:
        if invoice.pk in ready_ids:
            continue
        amount = invoice.amount or Decimal("0.00")
        if amount <= 0:
            reason = "Zero amount — reminders and late fees are never sent."
        elif invoice.late_fee_hold_is_active(today):
            reason = f"Temporary hold through {invoice.late_fee_hold_until}."
            if invoice.late_fee_hold_reason:
                reason += f" {invoice.late_fee_hold_reason}"
        elif settings_obj.late_fee_skip_current_month and (
            invoice.issue_date.year,
            invoice.issue_date.month,
        ) == (today.year, today.month):
            reason = "Current-month reminders are disabled in Settings."
        elif (
            settings_obj.late_fee_automation_start_date
            and invoice.due_date < settings_obj.late_fee_automation_start_date
        ):
            reason = (
                "Due date is before the automation start date "
                f"({settings_obj.late_fee_automation_start_date})."
            )
        elif not settings_obj.late_fee_enabled:
            reason = "Late fees are disabled globally."
        else:
            cfg = get_effective_late_fee_settings(invoice.lease)
            if not cfg["enabled"]:
                reason = "Late fees are disabled for this lease."
            elif get_due_reminder_number(invoice, cfg, today=today) is None:
                reason = "Waiting for the grace period/interval, or maximum reminders reached."
            else:
                reason = "Excluded by the current safety settings."
        excluded_invoices.append({
            "invoice": invoice,
            "balance": invoice.outstanding_balance,
            "reason": reason,
        })
    return render(request, "invoices/late_fee_pending_queue.html", {
        "preview": preview,
        "pending_reminders": pending_reminders,
        "recent_reminders": recent_reminders,
        "excluded_invoices": excluded_invoices,
        "review_total": len(review_invoices),
        "review_limited": len(review_invoices) == 250,
        "today": today,
    })


@login_required
@require_POST
def run_late_fee_reminders_now(request):
    summary = run_due_late_fee_reminders(
        source=InvoiceLateFeeReminder.SOURCE_MANUAL,
        user=request.user,
    )
    message = (
        f"Late-fee run complete: {summary['processed']} reminder(s), "
        f"{summary['fees_applied']} fee(s) applied, "
        f"{summary['fees_pending']} pending, {summary['failed']} failed."
    )
    if summary.get("reason") or summary["failed"]:
        messages.warning(request, f"{message} {summary.get('reason', '')}".strip())
    else:
        messages.success(request, message)
    return redirect("invoices:late_fee_pending_queue")


@login_required
@require_POST
def set_invoice_late_fee_hold(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    raw_until = (request.POST.get("hold_until") or "").strip()
    try:
        hold_until = date.fromisoformat(raw_until)
    except ValueError:
        messages.error(request, "Choose a valid hold-until date.")
        return redirect("invoices:late_fee_pending_queue")
    today = timezone.localdate()
    if hold_until < today:
        messages.error(request, "The hold-until date cannot be in the past.")
        return redirect("invoices:late_fee_pending_queue")
    invoice.late_fee_hold_until = hold_until
    invoice.late_fee_hold_reason = (request.POST.get("reason") or "").strip()[:255]
    invoice.save(update_fields=["late_fee_hold_until", "late_fee_hold_reason", "updated_at"])
    messages.success(
        request,
        f"Invoice #{invoice.invoice_number} will receive no reminder or late fee through {hold_until}.",
    )
    return redirect("invoices:late_fee_pending_queue")


@login_required
@require_POST
def clear_invoice_late_fee_hold(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    invoice.late_fee_hold_until = None
    invoice.late_fee_hold_reason = ""
    invoice.save(update_fields=["late_fee_hold_until", "late_fee_hold_reason", "updated_at"])
    messages.success(request, f"Late-fee hold cleared for invoice #{invoice.invoice_number}.")
    return redirect("invoices:late_fee_pending_queue")


@login_required
@require_POST
def approve_late_fee_reminder(request, pk):
    reminder = get_object_or_404(InvoiceLateFeeReminder, pk=pk)
    result = approve_pending_late_fee(reminder, user=request.user)
    if result.get("ok"):
        messages.success(request, f"Late fee of {reminder.fee_amount} applied to invoice #{reminder.invoice.invoice_number}.")
    else:
        messages.error(request, result.get("reason") or "Could not approve late fee.")
    return redirect("invoices:late_fee_pending_queue")


@login_required
@require_POST
def reject_late_fee_reminder(request, pk):
    reminder = get_object_or_404(InvoiceLateFeeReminder, pk=pk)
    result = reject_pending_late_fee(reminder)
    if result.get("ok"):
        messages.success(request, "Pending late fee dismissed.")
    else:
        messages.error(request, result.get("reason") or "Could not dismiss late fee.")
    return redirect("invoices:late_fee_pending_queue")
