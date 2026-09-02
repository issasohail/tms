from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_POST

from invoices.services import lease_outstanding_totals, security_deposit_totals
from core.utils.identity import format_phone
from payments.models import PaymentDetail
from payments.services.payment_detail import rebuild_payment_detail
from smart_meter.models import Meter, MeterInstallation


def _dec(value, default="0.00"):
    try:
        return Decimal(str(value or default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _money(value):
    return f"Rs. {_dec(value):,.2f}"


@login_required
@require_GET
def payment_detail_prefill_api(request):
    payment_detail_id = request.GET.get("payment_detail_id")
    if not payment_detail_id:
        return HttpResponseBadRequest("payment_detail_id required")

    detail = get_object_or_404(
        PaymentDetail.objects.select_related("payment", "payment__lease"),
        pk=payment_detail_id,
    )
    meter_options = list(
        Meter.objects.filter(
            installations__lease=detail.payment.lease,
        ).distinct().order_by("meter_number").values("id", "meter_number")
    )
    return JsonResponse({
        "payment_detail_id": detail.id,
        "payment_id": detail.payment_id,
        "payment_amount": str(getattr(detail.payment, "amount", "0.00") or "0.00"),
        "lease_amount": str(detail.lease_amount or "0.00"),
        "security_amount": str(detail.security_amount or "0.00"),
        "electricity_amount": str(detail.electricity_amount or "0.00"),
        "electricity_meter_id": detail.electricity_meter_id,
        "electricity_meter_options": meter_options,
        "security_type": detail.security_type or "PAYMENT",
    })


@login_required
@require_POST
def payment_detail_update_api(request):
    payment_detail_id = request.POST.get("payment_detail_id")
    if not payment_detail_id:
        return HttpResponseBadRequest("payment_detail_id required")

    detail = get_object_or_404(
        PaymentDetail.objects.select_related("payment", "payment__lease"),
        pk=payment_detail_id,
    )
    payment = detail.payment
    if not payment:
        return HttpResponseBadRequest("Payment detail has no payment.")

    lease_amt = _dec(request.POST.get("lease_amount"))
    sec_amt = _dec(request.POST.get("security_amount"))
    electricity_amt = _dec(request.POST.get("electricity_amount"))
    electricity_meter_id = request.POST.get("electricity_meter")
    electricity_meter = None
    if electricity_meter_id:
        electricity_meter = get_object_or_404(Meter, pk=electricity_meter_id)
    sec_type = (request.POST.get("security_type") or detail.security_type or "PAYMENT").upper()

    if sec_amt < 0:
        return JsonResponse({"error": "Security payment detail amount cannot be negative."}, status=400)
    if electricity_amt < 0 or electricity_amt > max(lease_amt, Decimal("0.00")):
        return JsonResponse(
            {"error": "Electricity allocation must be between zero and the positive lease amount."},
            status=400,
        )
    if electricity_amt > 0 and not electricity_meter:
        return JsonResponse({"error": "Select an electricity meter."}, status=400)
    if electricity_meter and not MeterInstallation.objects.filter(
        lease=payment.lease,
        meter=electricity_meter,
    ).exists():
        return JsonResponse({"error": "The selected meter is not linked to this lease."}, status=400)

    total = lease_amt + sec_amt
    if total != payment.amount:
        return JsonResponse(
            {"error": f"Split total ({total}) must equal payment amount ({payment.amount})"},
            status=400,
        )

    detail = rebuild_payment_detail(
        payment=payment,
        lease_amount=lease_amt,
        security_amount=sec_amt,
        electricity_amount=electricity_amt,
        electricity_meter=electricity_meter,
        security_type=sec_type,
        user=request.user,
        reason="Payment detail edited from payment list",
    )
    totals = lease_outstanding_totals(payment.lease)
    return JsonResponse(
        {
            "ok": True,
            "payment_detail_id": detail.id,
            "totals": {key: str(value) for key, value in totals.items()},
        }
    )


@login_required
@require_GET
def api_payment_detail_receipt_whatsapp(request, pk: int):
    detail = get_object_or_404(
        PaymentDetail.objects.select_related(
            "payment",
            "payment__lease",
            "payment__lease__tenant",
            "payment__lease__unit",
            "payment__lease__unit__property",
        ),
        pk=pk,
    )
    payment = detail.payment
    lease = getattr(payment, "lease", None)
    tenant = getattr(lease, "tenant", None)
    unit = getattr(lease, "unit", None)
    prop = getattr(unit, "property", None)

    totals = security_deposit_totals(lease) if lease else {"required": 0, "balance_to_collect": 0}
    sec_status = "Pending" if (totals.get("balance_to_collect") or 0) > 0 else "Paid"

    lines = [
        f"Dear {getattr(tenant, 'first_name', '') or 'Customer'},",
        f"*Payment received* for {getattr(prop, 'property_name', '') or ''}.",
        f"Unit: {getattr(unit, 'unit_number', '') or ''}",
    ]
    if getattr(payment, "payment_date", None):
        lines.append(f"*Date: {payment.payment_date:%b %d, %Y}*")
    lease_amount = _dec(detail.lease_amount)
    security_amount = _dec(detail.security_amount)
    electricity_amount = _dec(detail.electricity_amount)
    positive_parts = [
        label
        for label, value in (
            ("Lease", lease_amount),
            ("Security", security_amount),
        )
        if value > 0
    ]
    amount_label = "Total Amount Received"
    if detail.security_type != "REFUND" and len(positive_parts) == 1:
        amount_label = f"{amount_label} for {positive_parts[0]}"
    lines.append(f"*{amount_label}: {_money(payment.amount)}*")
    if len(positive_parts) > 1 and lease_amount > 0:
        lines.append(f"Lease Portion: {_money(detail.lease_amount)}")
    if len(positive_parts) > 1 and security_amount > 0:
        label = "Security Refund" if detail.security_type == "REFUND" else "Security Portion"
        lines.append(f"{label}: {_money(detail.security_amount)}")
        lines.append(f"Security Status: {sec_status}")
    if electricity_amount > 0:
        meter_number = getattr(detail.electricity_meter, "meter_number", "")
        lines.append(f"Electricity Allocation ({meter_number}): {_money(electricity_amount)}")
    lease_balance = getattr(lease, "get_balance", 0) if lease else 0
    if callable(lease_balance):
        lease_balance = lease_balance()
    total_balance = _dec(lease_balance) + _dec(totals.get("balance_to_collect"))
    lines.append(f"Total Balance: {_money(total_balance)}")
    lines.append("Thank you.")

    payload = {
        "phone": getattr(tenant, "phone", "") or "",
        "phone_display": format_phone(getattr(tenant, "phone", "")),
        "message": "\n".join(line for line in lines if line),
        "payment_detail_id": detail.id,
    }
    if request.GET.get("open") == "1":
        from leases.whatsapp import build_whatsapp_url

        whatsapp_url = build_whatsapp_url(payload["phone"], payload["message"])
        if not whatsapp_url:
            return HttpResponseBadRequest("Tenant phone number is missing or invalid.")
        return redirect(whatsapp_url)
    return JsonResponse(payload)
