# payments/views/allocation_api.py
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db import transaction

from payments.models import PaymentAllocation
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
      allocation_id
      lease_amount
      security_amount
      security_type
    """
    alloc_id = request.POST.get("allocation_id")
    alloc = PaymentAllocation.objects.select_related("payment").get(pk=alloc_id)

    lease_amt = D(request.POST.get("lease_amount"))
    sec_amt = D(request.POST.get("security_amount"))
    sec_type = (request.POST.get("security_type") or "PAYMENT").upper()

    alloc.lease_amount = lease_amt
    alloc.security_amount = sec_amt
    alloc.security_type = sec_type
    alloc.updated_by = request.user
    alloc.save()

    rebuild_allocation(
        payment=alloc.payment,
        lease_amount=lease_amt,
        security_amount=sec_amt,
        security_type=sec_type,
        user=request.user,
        reason="Inline edit from Cash Ledger modal",
    )

    return JsonResponse({
        "ok": True,
        "allocation_id": alloc.pk,
        "payment_id": alloc.payment_id,
        "total": float(lease_amt + sec_amt),
    })

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.http import JsonResponse, Http404
from payments.models import PaymentAllocation

@login_required
@require_GET
def allocation_prefill_api(request, pk: int):
    alloc = (PaymentAllocation.objects
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
        "allocation_id": alloc.pk,
        "payment_id": alloc.payment_id,
        "payment_amount": float(alloc.payment.amount or 0),
        "allocation_mode": mode,
        "lease_amount": float(lease_amt),
        "security_amount": float(sec_amt),
        "security_type": getattr(alloc, "security_type", "PAYMENT") or "PAYMENT",
    })
