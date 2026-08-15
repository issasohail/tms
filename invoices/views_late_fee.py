from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .late_fees import approve_pending_late_fee, process_invoice_late_fee_reminder, reject_pending_late_fee
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
    pending_reminders = (
        InvoiceLateFeeReminder.objects
        .filter(status=InvoiceLateFeeReminder.STATUS_FEE_PENDING)
        .select_related("invoice", "invoice__lease", "invoice__lease__tenant")
        .order_by("-created_at")
    )
    return render(request, "invoices/late_fee_pending_queue.html", {
        "pending_reminders": pending_reminders,
    })


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
