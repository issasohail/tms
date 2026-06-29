import json
import logging

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
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

    return render(
        request,
        "whatsapp/webhook_log_list.html",
        {
            "page_obj": page_obj,
            "selected_log": selected_log,
            "selected_payload": selected_payload,
            "selected_headers": selected_headers,
        },
    )


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
