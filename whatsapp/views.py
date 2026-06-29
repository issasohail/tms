import json
import logging

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

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
                WhatsAppMessageLog.objects.create(
                    direction=WhatsAppMessageLog.DIRECTION_INBOUND,
                    phone_number=message.get("from", "") or phone_number,
                    wa_message_id=message.get("id", ""),
                    message_type=message.get("type", WhatsAppMessageLog.MESSAGE_TYPE_WEBHOOK),
                    status=WhatsAppMessageLog.STATUS_RECEIVED,
                    payload=message,
                    api_response={"entry_id": entry.get("id"), "field": change.get("field")},
                )

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
    logs = WhatsAppMessageLog.objects.filter(phone_number=phone_number).order_by("created_at")
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
