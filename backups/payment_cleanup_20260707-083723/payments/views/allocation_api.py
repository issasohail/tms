# payments/views/allocation_api.py
from decimal import Decimal
from django.http import HttpResponseBadRequest, JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404

from payments.models import PaymentDetail
from payments.services.allocation import rebuild_allocation


def D(v):
    try:
        return Decimal(v or "0")
    except Exception:
        return Decimal("0")


@login_required
@require_POST
@transaction.atomic
def update_allocation(request):
    """
    POST:
      payment_detail_id
      lease_amount
      security_amount
      security_type
    """
    alloc_id = request.POST.get("payment_detail_id")
    if not alloc_id:
        return HttpResponseBadRequest("payment_detail_id required")

    alloc = get_object_or_404(PaymentDetail.objects.select_related("payment"), pk=alloc_id)

    lease_amt = D(request.POST.get("lease_amount"))
    sec_amt = D(request.POST.get("security_amount"))
    sec_type = (request.POST.get("security_type") or "PAYMENT").upper()
    payment = alloc.payment

    if sec_amt < 0:
        return JsonResponse({"ok": False, "error": "Security allocation amount cannot be negative."}, status=400)

    total = lease_amt + sec_amt
    if total != (payment.amount or Decimal("0.00")):
        return JsonResponse(
            {"ok": False, "error": f"Split total {total} must equal payment amount {payment.amount}."},
            status=400,
        )

    alloc = rebuild_allocation(
        payment=payment,
        lease_amount=lease_amt,
        security_amount=sec_amt,
        security_type=sec_type,
        user=request.user,
        reason="Inline edit from Cash Ledger modal",
    )

    return JsonResponse({
        "ok": True,
        "payment_detail_id": alloc.pk,
        "payment_id": alloc.payment_id,
        "total": float(total),
    })

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.http import JsonResponse, Http404
from payments.models import PaymentDetail

@login_required
@require_GET
def allocation_prefill_api(request, pk: int):
    alloc = (PaymentDetail.objects
             .select_related("payment")
             .filter(pk=pk).first())
    if not alloc:
        raise Http404("Allocation not found")

    lease_amt = alloc.lease_amount or 0
    sec_amt = alloc.security_amount or 0

    if lease_amt and sec_amt:
        mode = "SPLIT"
    elif sec_amt and not lease_amt:
        mode = "SECURITY"
    else:
        mode = "LEASE"

    return JsonResponse({
        "payment_detail_id": alloc.pk,
        "payment_id": alloc.payment_id,
        "payment_amount": float(alloc.payment.amount or 0),
        "allocation_mode": mode,
        "lease_amount": float(lease_amt),
        "security_amount": float(sec_amt),
        "security_type": getattr(alloc, "security_type", "PAYMENT") or "PAYMENT",
    })
