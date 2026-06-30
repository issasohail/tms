import json
import logging
from decimal import Decimal

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from invoices.models import Invoice
from leases.models import Lease
from maintenance.models import MaintenanceRequest
from payments.models import Payment
from tenants.models import Tenant

from .models import WhatsAppMessageLog, WhatsAppWebhookLog
from .services.whatsapp import WhatsAppService

logger = logging.getLogger(__name__)


def _can_view_whatsapp_logs(user):
    return user.is_staff or user.has_perm("core.view_globalsettings")


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request):
    if request.method == "GET":
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")
        if mode == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
            return HttpResponse(challenge or "")
        return HttpResponse("Forbidden", status=403)

    logger.info("WhatsApp webhook POST received")
    body_text = request.body.decode("utf-8", errors="replace")
    try:
        raw_payload = json.loads(body_text or "{}")
    except json.JSONDecodeError:
        raw_payload = {"raw_body": body_text}

    WhatsAppWebhookLog.objects.create(
        event_type=_webhook_event_type(raw_payload),
        payload=raw_payload,
        headers=_request_headers(request),
        method=request.method,
        remote_addr=_remote_addr(request),
    )

    try:
        payload = json.loads(body_text or "{}")
    except json.JSONDecodeError:
        logger.warning("Invalid WhatsApp webhook JSON received.")
        return HttpResponse(status=200)

    try:
        _log_webhook_payload(payload)
    except Exception:
        logger.exception("Failed to log WhatsApp webhook payload.")

    return HttpResponse(status=200)


def _request_headers(request):
    headers = {}
    for key, value in request.META.items():
        if key.startswith("HTTP_"):
            header = key[5:].replace("_", "-").title()
            headers[header] = value
        elif key in {"CONTENT_TYPE", "CONTENT_LENGTH"}:
            headers[key.replace("_", "-").title()] = value
    return headers


def _remote_addr(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _webhook_event_type(payload):
    if not isinstance(payload, dict):
        return "unknown"
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            if value.get("statuses"):
                return "status"
            if value.get("messages"):
                return "message"
            if change.get("field"):
                return change.get("field")
    return payload.get("object", "") or "webhook"


def _log_webhook_payload(payload):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            metadata = value.get("metadata", {}) or {}
            phone_number = metadata.get("display_phone_number", "")

            for message in value.get("messages", []) or []:
                message_log = WhatsAppMessageLog.objects.create(
                    direction=WhatsAppMessageLog.DIRECTION_INBOUND,
                    phone_number=message.get("from", "") or phone_number,
                    wa_message_id=message.get("id", ""),
                    message_type=message.get("type", WhatsAppMessageLog.MESSAGE_TYPE_WEBHOOK),
                    status=WhatsAppMessageLog.STATUS_RECEIVED,
                    payload=message,
                    api_response={"entry_id": entry.get("id"), "field": change.get("field")},
                )
                _queue_ai_message(message_log.pk)

            for status in value.get("statuses", []) or []:
                conversation = status.get("conversation", {}) or {}
                status_name = status.get("status", WhatsAppMessageLog.STATUS_PENDING)
                message_id = status.get("id", "")
                existing_log = WhatsAppMessageLog.objects.filter(
                    direction=WhatsAppMessageLog.DIRECTION_OUTBOUND,
                    wa_message_id=message_id,
                ).order_by("-created_at").first()
                if existing_log:
                    existing_log.status = status_name
                    existing_log.conversation_id = conversation.get("id", existing_log.conversation_id)
                    existing_log.api_response = status
                    if status_name == WhatsAppMessageLog.STATUS_FAILED:
                        existing_log.error_text = _status_error_text(status)
                    existing_log.save(
                        update_fields=[
                            "status",
                            "conversation_id",
                            "api_response",
                            "error_text",
                            "updated_at",
                        ]
                    )

                WhatsAppMessageLog.objects.create(
                    direction=WhatsAppMessageLog.DIRECTION_STATUS,
                    phone_number=status.get("recipient_id", "") or phone_number,
                    conversation_id=conversation.get("id", ""),
                    wa_message_id=status.get("id", ""),
                    message_type=WhatsAppMessageLog.MESSAGE_TYPE_STATUS,
                    status=status_name,
                    payload=status,
                    api_response={"entry_id": entry.get("id"), "field": change.get("field")},
                )


def _status_error_text(status):
    errors = status.get("errors") or []
    if not errors:
        return ""
    first_error = errors[0] or {}
    title = first_error.get("title") or first_error.get("message") or "WhatsApp delivery failed"
    code = first_error.get("code")
    return f"{title}" + (f" (code {code})" if code else "")


def _queue_ai_message(message_log_id):
    from .services.queue import enqueue_whatsapp_ai_message

    enqueue_whatsapp_ai_message(message_log_id)


@login_required
@user_passes_test(_can_view_whatsapp_logs)
def webhook_log_list(request):
    if request.method == "POST":
        phone_number = (request.POST.get("phone_number") or "").strip()
        message_text = (request.POST.get("message_text") or "").strip()
        if not phone_number or not message_text:
            messages.error(request, "Phone number and message are required.")
            return redirect("whatsapp:webhook_log_list")

        result = WhatsAppService(created_by=request.user).send_text(phone_number, message_text)
        if result.get("ok"):
            messages.success(request, "WhatsApp message sent.")
        else:
            messages.error(request, result.get("error") or "WhatsApp message failed.")
        return redirect(f"{request.path}?phone={phone_number}")

    selected_phone = (request.GET.get("phone") or "").strip()
    logs = WhatsAppWebhookLog.objects.order_by("-created_at")
    paginator = Paginator(logs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    selected_log = None
    selected_payload = ""
    selected_headers = ""

    selected_id = request.GET.get("log")
    if selected_id:
        selected_log = get_object_or_404(WhatsAppWebhookLog, pk=selected_id)
    elif page_obj.object_list:
        selected_log = page_obj.object_list[0]

    if selected_log:
        selected_payload = json.dumps(selected_log.payload, indent=2, ensure_ascii=False)
        selected_headers = json.dumps(selected_log.headers, indent=2, ensure_ascii=False)

    conversation_summary = _conversation_summary()
    if not selected_phone and conversation_summary:
        selected_phone = conversation_summary[0]["phone_number"]
    conversation_messages = _conversation_messages(selected_phone) if selected_phone else []

    return render(
        request,
        "whatsapp/webhook_log_list.html",
        {
            "page_obj": page_obj,
            "selected_log": selected_log,
            "selected_payload": selected_payload,
            "selected_headers": selected_headers,
            "conversation_summary": conversation_summary,
            "conversation_messages": conversation_messages,
            "selected_phone": selected_phone,
        },
    )


def _conversation_summary():
    summary = []
    seen = set()
    logs = WhatsAppMessageLog.objects.exclude(phone_number="").order_by("-created_at")[:300]
    for log in logs:
        if log.phone_number in seen:
            continue
        seen.add(log.phone_number)
        summary.append(
            {
                "phone_number": log.phone_number,
                "last_direction": log.direction,
                "last_status": log.status,
                "last_message": _message_text(log),
                "last_at": log.created_at,
            }
        )
    return summary


def _conversation_messages(phone_number):
    rows = []
    logs = WhatsAppMessageLog.objects.filter(phone_number=phone_number).exclude(
        direction=WhatsAppMessageLog.DIRECTION_STATUS
    ).order_by("created_at")
    for log in logs:
        rows.append(
            {
                "id": log.id,
                "direction": log.direction,
                "status": log.status,
                "message_type": log.message_type,
                "message": _message_text(log),
                "created_at": log.created_at,
                "wa_message_id": log.wa_message_id,
                "error_text": log.error_text,
            }
        )
    return rows


def _message_text(log):
    payload = log.payload or {}
    if log.direction == WhatsAppMessageLog.DIRECTION_STATUS:
        return f"Status update: {log.status}"

    text_payload = payload.get("text") or {}
    if isinstance(text_payload, dict) and text_payload.get("body"):
        return text_payload.get("body")

    if payload.get("type") == "button":
        return (payload.get("button") or {}).get("text", "")
    if payload.get("type") == "interactive":
        interactive = payload.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return reply.get("title") or reply.get("id") or "Interactive reply"
    if payload.get("type") == "image":
        return (payload.get("image") or {}).get("caption") or "Image message"
    if payload.get("type") == "document":
        document = payload.get("document") or {}
        return document.get("caption") or document.get("filename") or "Document message"
    if payload.get("type"):
        return f"{payload.get('type').title()} message"
    return ""


@login_required
@require_POST
def send_object_message(request, object_type, object_id, action):
    obj = _get_whatsapp_object(object_type, object_id)
    tenant = _object_tenant(obj)
    lease = _object_lease(obj)
    phone = _object_phone(obj)
    if not phone:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "No tenant phone number is available for WhatsApp."}, status=400)
        messages.error(request, "No tenant phone number is available for WhatsApp.")
        return redirect(_object_redirect(obj))

    service = WhatsAppService(created_by=request.user)
    message_text = _object_message(obj, object_type, action)
    result = _send_action(request, service, obj, object_type, action, phone, message_text, tenant, lease)
    if _is_ajax(request):
        return JsonResponse(result, status=200 if result.get("ok") else 400)
    if result.get("ok"):
        messages.success(request, "WhatsApp message sent from TMS.")
    else:
        messages.error(request, result.get("error") or "WhatsApp message failed.")
    return redirect(_object_redirect(obj))


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _get_whatsapp_object(object_type, object_id):
    model_map = {
        "tenant": Tenant,
        "lease": Lease,
        "invoice": Invoice,
        "payment": Payment,
        "maintenance": MaintenanceRequest,
    }
    model = model_map.get(object_type)
    if model is None:
        raise Http404("Unsupported WhatsApp object.")
    return get_object_or_404(model, pk=object_id)


def _object_tenant(obj):
    if isinstance(obj, Tenant):
        return obj
    if isinstance(obj, MaintenanceRequest):
        return obj.lease_tenant
    lease = _object_lease(obj)
    return getattr(lease, "tenant", None)


def _object_lease(obj):
    if isinstance(obj, Lease):
        return obj
    if isinstance(obj, Tenant):
        return _tenant_reminder_lease(obj)
    return getattr(obj, "lease", None)


def _object_phone(obj):
    tenant = _object_tenant(obj)
    return getattr(tenant, "phone", "") or ""


def _object_redirect(obj):
    if hasattr(obj, "get_absolute_url"):
        try:
            return obj.get_absolute_url()
        except Exception:
            pass
    if isinstance(obj, Tenant):
        return reverse("tenants:tenant_detail", args=[obj.pk])
    if isinstance(obj, Lease):
        return reverse("leases:lease_detail", args=[obj.pk])
    if isinstance(obj, Invoice):
        return reverse("invoices:invoice_detail", args=[obj.pk])
    if isinstance(obj, Payment):
        return reverse("payments:payment_detail", args=[obj.pk])
    if isinstance(obj, MaintenanceRequest):
        return reverse("maintenance:request_detail", args=[obj.pk])
    return reverse("whatsapp:webhook_log_list")


def _object_message(obj, object_type, action):
    if object_type == "tenant" and action == "text":
        lease = _tenant_reminder_lease(obj)
        if lease:
            return _lease_reminder_message(lease)
    if object_type == "payment" and action in {"receipt", "payment_confirmation"}:
        return _payment_receipt_message(obj)
    if object_type == "lease" and action in {"text", "lease"}:
        return _lease_reminder_message(obj)

    tenant = _object_tenant(obj)
    lease = _object_lease(obj)
    tenant_name = tenant.get_full_name() if tenant else "Tenant"
    unit = getattr(lease, "unit", None)
    property_obj = getattr(unit, "property", None)
    property_name = getattr(property_obj, "property_name", "") or ""
    unit_number = getattr(unit, "unit_number", "") or ""

    if object_type == "invoice":
        return (
            f"Dear {tenant_name},\n"
            f"Invoice #{getattr(obj, 'invoice_number', obj.pk)} is ready.\n"
            f"Amount: {getattr(obj, 'amount', '')}\n"
            f"Due Date: {getattr(obj, 'due_date', '')}\n"
            f"Property/Unit: {property_name} {unit_number}\n"
            "Thank you."
        )
    if object_type == "payment":
        return (
            f"Dear {tenant_name},\n"
            f"Payment confirmation for amount {getattr(obj, 'amount', '')}.\n"
            f"Date: {getattr(obj, 'payment_date', '')}\n"
            f"Property/Unit: {property_name} {unit_number}\n"
            "Thank you."
        )
    if object_type == "lease":
        return (
            f"Dear {tenant_name},\n"
            f"Lease update for {property_name} {unit_number}.\n"
            f"Lease period: {getattr(obj, 'start_date', '')} to {getattr(obj, 'end_date', '')}.\n"
            "Please contact management for details."
        )
    if object_type == "maintenance":
        return (
            f"Dear {tenant_name},\n"
            f"Maintenance update: {getattr(obj, 'title', '')}.\n"
            f"Status: {getattr(obj, 'get_status_display', lambda: getattr(obj, 'status', ''))()}.\n"
            "Thank you."
        )
    return f"Dear {tenant_name},\nThis is a message from TMS.\nThank you."


def _send_action(request, service, obj, object_type, action, phone, message_text, tenant, lease):
    if action == "template":
        return service.send_template(
            phone,
            getattr(settings, "WHATSAPP_DEFAULT_TEMPLATE_NAME", "hello_world"),
            language_code=getattr(settings, "WHATSAPP_DEFAULT_TEMPLATE_LANGUAGE", "en_US"),
            tenant=tenant,
            lease=lease,
        )
    if object_type == "invoice" and action == "invoice":
        message_text = _invoice_message(request, obj)
        image_bytes, filename = _invoice_jpg_attachment(obj)
        return service.send_image_bytes(phone, image_bytes, filename=filename, caption=message_text, tenant=tenant, lease=lease, invoice=obj)
    if object_type == "payment" and action in {"receipt", "payment_confirmation"}:
        message_text = _payment_receipt_message(obj)
        image_bytes, filename = _payment_jpg_attachment(obj)
        return service.send_image_bytes(phone, image_bytes, filename=filename, caption=message_text, tenant=tenant, lease=lease, payment=obj)
    if object_type == "lease" and action == "lease":
        return service.send_lease(obj, message=message_text)
    if object_type == "maintenance" and action == "maintenance_update":
        return service.send_maintenance_update(obj, message=message_text)
    return service.send_text(phone, message_text, tenant=tenant, lease=lease)


def _invoice_pdf_attachment(invoice):
    from invoices.views import _invoice_pdf_context, render_to_pdf

    pdf_bytes = render_to_pdf("invoices/invoice_pdf.html", _invoice_pdf_context(invoice))
    filename = f"Invoice_{getattr(invoice, 'invoice_number', invoice.pk)}.pdf"
    return pdf_bytes, filename


def _invoice_jpg_attachment(invoice):
    import fitz
    from django.core.cache import cache
    from django.template.loader import render_to_string
    from weasyprint import HTML
    from invoices.models import ItemCategory
    from invoices.services import security_deposit_totals

    items = list(invoice.items.select_related("category").all())
    parts = []
    for item in items:
        category_name = item.category.name if item.category_id else ""
        description = item.description or ""
        if category_name and description:
            parts.append(f"{category_name}: {description}")
        elif category_name:
            parts.append(category_name)
        elif description:
            parts.append(description)

    categories = cache.get("invoices.active_item_categories")
    if categories is None:
        categories = list(
            ItemCategory.objects.filter(is_active=True)
            .order_by("name")
            .values("id", "name")
        )
        cache.set("invoices.active_item_categories", categories, 60)

    lease = getattr(invoice, "lease", None)
    context = {
        "invoice": invoice,
        "items": items,
        "combined_description": ", ".join(parts),
        "computed_total": sum((item.amount for item in items), Decimal("0.00")),
        "lease_balance": lease.get_balance if lease else Decimal("0.00"),
        "sec_totals": security_deposit_totals(lease) if lease else {
            "required": 0,
            "paid_in": 0,
            "refunded": 0,
            "damages": 0,
            "balance_to_collect": 0,
            "currently_held": 0,
        },
        "categories": categories,
    }
    html_fragment = render_to_string("invoices/invoice_detail.html", context)
    html = f"<!doctype html><html><body class='exporting-jpg'>{html_fragment}</body></html>"
    pdf_bytes = HTML(string=html).write_pdf()
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = document.load_page(0)
        content_rect = page.get_bboxlog()
        crop_rect = None
        for item in content_rect:
            if len(item) >= 2 and item[0] != "ignore-text":
                rect = fitz.Rect(item[1])
                crop_rect = rect if crop_rect is None else crop_rect | rect
        if crop_rect and not crop_rect.is_empty:
            crop_rect = crop_rect + (-12, -12, 12, 12)
            crop_rect &= page.rect
            page.set_cropbox(crop_rect)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image_bytes = pixmap.tobytes("jpg", jpg_quality=88)
    finally:
        document.close()
    filename = f"Invoice_{getattr(invoice, 'invoice_number', invoice.pk)}.jpg"
    return image_bytes, filename


def _invoice_message(request, invoice):
    from invoices.views import build_invoice_whatsapp_message

    return build_invoice_whatsapp_message(request, invoice)


def _payment_receipt_message(payment):
    from payments.views.payments import build_payment_receipt_message

    return build_payment_receipt_message(None, payment)


def _lease_reminder_message(lease):
    from leases.models import WhatsAppTemplate
    from leases.whatsapp import render_whatsapp_template

    template, rendered = render_whatsapp_template(
        WhatsAppTemplate.TEMPLATE_BALANCE_REMINDER,
        lease,
        request=None,
    )
    if rendered:
        due_date = getattr(lease, "due_date", "") or ""
        if due_date and due_date not in rendered:
            rendered = f"{rendered.rstrip()}\nDue Date: {due_date}"
        return rendered

    tenant = getattr(lease, "tenant", None)
    unit = getattr(lease, "unit", None)
    property_obj = getattr(unit, "property", None)
    first_name = getattr(tenant, "first_name", "") or "Customer"
    property_name = getattr(property_obj, "property_name", "") or ""
    unit_number = getattr(unit, "unit_number", "") or ""
    balance = getattr(lease, "get_balance", 0)
    if callable(balance):
        balance = balance()
    due_date = getattr(lease, "due_date", "") or ""

    lines = [
        f"Dear {first_name},",
        "",
        "Payment Reminder:",
        f"Property: {property_name}",
        f"Unit: {unit_number}",
    ]
    if due_date:
        lines.append(f"Due Date: {due_date}")
    lines.extend([
        f"Balance Due: Rs. {float(balance or 0):,.2f}",
        "",
        "Please pay at your earliest convenience.",
    ])
    return "\n".join(lines)


def _tenant_reminder_lease(tenant):
    if not tenant:
        return None

    leases = getattr(tenant, "leases", None)
    if leases is None:
        return getattr(tenant, "lease", None)

    active_lease = leases.filter(status="active").order_by("-start_date", "-id").first()
    if active_lease:
        return active_lease
    return leases.order_by("-start_date", "-id").first()


def _payment_pdf_attachment(payment, request):
    from payments.pdf_utils import generate_payment_pdf

    return generate_payment_pdf(payment, request=request)


def _payment_jpg_attachment(payment):
    import fitz
    from django.template.loader import render_to_string
    from weasyprint import HTML

    html = render_to_string("payments/payment_pdf.html", {"payment": payment, "is_pdf": True})
    pdf_bytes = HTML(string=html).write_pdf()
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = document.load_page(0)
        crop_rect = None
        for item in page.get_bboxlog():
            if len(item) >= 2 and item[0] != "ignore-text":
                rect = fitz.Rect(item[1])
                crop_rect = rect if crop_rect is None else crop_rect | rect
        if crop_rect and not crop_rect.is_empty:
            crop_rect = crop_rect + (-14, -14, 14, 14)
            crop_rect &= page.rect
            page.set_cropbox(crop_rect)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image_bytes = pixmap.tobytes("jpg", jpg_quality=90)
    finally:
        document.close()
    filename = f"Payment_Receipt_{getattr(payment, 'pk', 'receipt')}.jpg"
    return image_bytes, filename


@staff_member_required
@require_POST
def send_hello_world_test(request):
    phone = request.POST.get("phone") or request.GET.get("phone")
    if not phone:
        return JsonResponse({"ok": False, "error": "Phone number is required."}, status=400)

    service = WhatsAppService(created_by=request.user)
    result = service.send_template(
        phone,
        "hello_world",
        language_code="en_US",
        message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEMPLATE,
    )
    return JsonResponse(result)
