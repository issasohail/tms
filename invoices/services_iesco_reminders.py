from __future__ import annotations

from datetime import date, datetime

from django.db import transaction
from django.utils import timezone

from leases.models import Lease
from properties.models import Unit

from .models import IescoBillReading, IescoStandaloneMeter


def parsed_iesco_date(value) -> date | None:
    cleaned = " ".join(str(value or "").split())
    for fmt in ("%d %b %y", "%d-%b-%y", "%d %B %y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned.title(), fmt).date()
        except ValueError:
            continue
    return None


def reminder_recipient(reading):
    unit = (
        Unit.objects.select_related("property")
        .filter(electric_meter_num=reading.reference_no)
        .first()
    )
    if unit:
        target_date = parsed_iesco_date(reading.due_date) or timezone.localdate()
        lease = (
            Lease.objects.select_related("tenant")
            .filter(
                unit=unit,
                start_date__lte=target_date,
                end_date__gte=target_date,
            )
            .exclude(status__in=("pending_approval", "rejected"))
            .order_by("-start_date", "-id")
            .first()
        )
        tenant = getattr(lease, "tenant", None)
        tenant_phone = getattr(tenant, "phone", "") or ""
        if tenant_phone:
            return {
                "phone": tenant_phone,
                "recipient": tenant.get_full_name() or "Tenant",
                "source": "lease tenant",
                "label": f"{unit.property.property_name} - {unit.unit_number}",
                "tenant": tenant,
                "lease": lease,
            }
        owner_phone = unit.property.owner_phone or ""
        return {
            "phone": owner_phone,
            "recipient": unit.property.owner_name or "Property owner",
            "source": "property owner",
            "label": f"{unit.property.property_name} - {unit.unit_number}",
            "tenant": None,
            "lease": lease,
        }

    standalone = IescoStandaloneMeter.objects.filter(
        reference_no=reading.reference_no
    ).first()
    return {
        "phone": getattr(standalone, "phone", "") or "",
        "recipient": getattr(standalone, "description", "") or "Meter contact",
        "source": "standalone meter",
        "label": getattr(standalone, "description", "") or "Standalone meter",
        "tenant": None,
        "lease": None,
    }


def reading_requires_payment(reading):
    return bool(
        reading.current_month_paid is False
        and reading.grand_total_amount is not None
        and reading.grand_total_amount > 0
    )


def reading_meter_is_active(reading):
    unit_active = Unit.objects.filter(
        electric_meter_num=reading.reference_no,
        iesco_bill_active=True,
    ).exists()
    standalone_active = IescoStandaloneMeter.objects.filter(
        reference_no=reading.reference_no,
        is_active=True,
    ).exists()
    return unit_active or standalone_active


def build_iesco_reminder_message(reading, recipient):
    payment_status = reading.payment_status_display
    if payment_status == "Unpaid":
        status_note = "Please make the payment to avoid disconnection."
    elif payment_status == "Paid":
        status_note = "This bill is recorded as paid."
    elif payment_status == "No payment due":
        status_note = "No payment is currently due for this bill."
    else:
        status_note = "Please verify the payment status against the IESCO bill."
    return (
        f"IESCO bill status\n\n"
        f"Reference: {reading.reference_no}\n"
        f"Property / Unit: {recipient['label']}\n"
        f"Bill month: {reading.bill_month}\n"
        f"Units: {reading.units_display}\n"
        f"Grand total: {reading.grand_total_display}\n"
        f"Payment status: {payment_status}\n"
        f"Due date: {reading.due_date or 'Not available'}\n\n"
        f"{status_note}\n"
        f"View the current IESCO bill online:\n"
        f"https://bill.pitc.com.pk/gbill.aspx?refno={reading.reference_no}"
    )


@transaction.atomic
def send_iesco_reminder(
    reading_id, *, user=None, allow_resend=False, allow_any_status=False
):
    from whatsapp.services.whatsapp import WhatsAppService

    reading = IescoBillReading.objects.select_for_update().get(pk=reading_id)
    if not allow_any_status and not reading_requires_payment(reading):
        return {"ok": False, "reason": "The bill does not currently require payment."}
    if reading.reminder_sent_at and not allow_resend:
        return {"ok": False, "reason": "A reminder was already sent for this bill month."}
    recipient = reminder_recipient(reading)
    if not recipient["phone"]:
        return {"ok": False, "reason": "No WhatsApp recipient number is available."}

    body = build_iesco_reminder_message(reading, recipient)
    try:
        from .views_iesco import (
            _formatted_register_value,
            _formatted_units,
            _iesco_export_jpg,
        )

        previous = _formatted_register_value(reading, "previous")
        current = _formatted_register_value(reading, "present")
        lease_balance = (
            f"Rs. {recipient['lease'].get_balance:,.0f}"
            if recipient["lease"] is not None
            else "—"
        )

        image_bytes = _iesco_export_jpg(
            [{
                "property_unit": recipient["label"],
                "bill_active": f"{reading.bill_month}\nActive",
                "reference_meter": f"{reading.reference_no}\n{reading.meter_number_display}",
                "consumer_tenant": (
                    f"{reading.consumer_name or reading.consumer_id or '—'}\n"
                    f"{recipient['recipient']}"
                ),
                "readings": f"Previous: {previous}\nCurrent: {current}",
                "units": _formatted_units(reading),
                "per_unit": reading.per_unit_rate_display,
                "charges": (
                    f"Arrears: {reading.arrears_display}\n"
                    f"Current: {reading.current_bill_display}"
                ),
                "grand_total": reading.grand_total_display,
                "due_payment": (
                    f"{reading.due_date or '—'}\n{reading.payment_status_display}"
                ),
                "update_balance": (
                    f"{timezone.localtime(reading.updated_at).strftime('%Y-%m-%d %H:%M')}\n"
                    f"Balance: {lease_balance}"
                ),
            }],
            single=True,
        )
        result = WhatsAppService(created_by=user).send_image_bytes(
            recipient["phone"],
            image_bytes,
            filename=f"IESCO_{reading.reference_no}_{reading.bill_month.replace(' ', '_')}.jpg",
            caption=body,
            tenant=recipient["tenant"],
            lease=recipient["lease"],
        )
        result = result if isinstance(result, dict) else {"ok": False, "error": str(result)}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}

    reading.reminder_phone = recipient["phone"]
    if result.get("ok"):
        reading.reminder_sent_at = timezone.now()
        reading.reminder_error = ""
    else:
        reading.reminder_error = str(result.get("error") or result.get("reason") or "WhatsApp send failed.")[:500]
    reading.save(
        update_fields=[
            "reminder_phone",
            "reminder_sent_at",
            "reminder_error",
            "updated_at",
        ]
    )
    return {
        "ok": bool(result.get("ok")),
        "reason": reading.reminder_error,
        "recipient": recipient,
        "message": body,
        "reading": reading,
    }


def due_reminder_readings(today=None):
    today = today or timezone.localdate()
    candidates = IescoBillReading.objects.filter(
        current_month_paid=False,
        reminder_sent_at__isnull=True,
    ).order_by("due_date", "reference_no")
    return [
        reading
        for reading in candidates
        if reading_meter_is_active(reading)
        and reading_requires_payment(reading)
        and (parsed_iesco_date(reading.due_date) or date.max) <= today
    ]


def run_due_iesco_reminders(*, today=None, user=None, dry_run=False):
    readings = due_reminder_readings(today=today)
    summary = {"due": len(readings), "ready": 0, "sent": 0, "failed": 0, "details": []}
    for reading in readings:
        recipient = reminder_recipient(reading)
        if dry_run:
            result = {"ok": bool(recipient["phone"]), "reason": "" if recipient["phone"] else "No WhatsApp recipient number is available."}
            summary["ready" if result.get("ok") else "failed"] += 1
        else:
            result = send_iesco_reminder(reading.pk, user=user)
            summary["sent" if result.get("ok") else "failed"] += 1
        summary["details"].append(
            {
                "reference_no": reading.reference_no,
                "bill_month": reading.bill_month,
                "due_date": reading.due_date,
                "recipient": recipient["recipient"],
                "phone": recipient["phone"],
                "ok": result.get("ok"),
                "reason": result.get("reason", ""),
            }
        )
    return summary
