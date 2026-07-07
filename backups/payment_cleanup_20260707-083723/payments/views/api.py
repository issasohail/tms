# payments/views/api.py
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from payments.models import PaymentDetail

def api_allocation_receipt_whatsapp(request, pk):
    alloc = get_object_or_404(PaymentDetail, pk=pk)
    payment = alloc.payment
    lease = payment.lease
    tenant = lease.tenant

    phone = tenant.phone or ""
    message = f"""Dear {tenant.first_name},
Payment received for {lease.unit.property.property_name}
Unit: {lease.unit.unit_number}
Date: {payment.payment_date}
Total: Rs. {payment.amount}

Lease Portion: Rs. {alloc.lease_amount}
Security Portion: Rs. {alloc.security_amount}

Thank you!
"""

    return JsonResponse({
        "phone": phone,
        "message": message,
        "payment_detail_id": alloc.id
    })
