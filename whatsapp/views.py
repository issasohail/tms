import json
import logging
import hashlib
import hmac
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib import messages
from django import forms
from django.core.paginator import Paginator
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.db import transaction
from django.db.models import Max, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from invoices.models import Invoice
from leases.models import Lease
from maintenance.models import MaintenanceRequest
from payments.models import Payment
from properties.models import Property, Unit
from tenants.models import Tenant
from handyman.models import HandymanProfile

from .models import (
    PendingWhatsAppMedia,
    WhatsAppAIInteractionLog,
    WhatsAppConversation,
    WhatsAppMessageLog,
    WhatsAppStaffPropertyAccess,
    WhatsAppUtilityTemplate,
    WhatsAppWebhookLog,
)
from .services.tenant_context import find_active_leases_for_phone
from .services.whatsapp import WhatsAppService, is_whatsapp_session_open

logger = logging.getLogger(__name__)
TENANT_SIMULATOR_GROUP = "Tenant Simulator"


def _can_view_whatsapp_logs(user):
    return user.is_staff or user.has_perm("core.view_globalsettings")


@login_required
@require_http_methods(["GET", "POST"])
def whatsapp_staff_access(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only a superuser can manage WhatsApp staff access.")

    simulator_group, _created = Group.objects.get_or_create(name=TENANT_SIMULATOR_GROUP)
    User = get_user_model()
    staff_users = list(
        User.objects.filter(is_active=True, is_staff=True)
        .prefetch_related("groups")
        .order_by("username")
    )
    properties = list(Property.objects.order_by("property_name", "id"))

    if request.method == "POST":
        staff_user = get_object_or_404(
            User,
            pk=request.POST.get("staff_id"),
            is_active=True,
            is_staff=True,
        )
        if request.POST.get("simulator_enabled") == "on":
            staff_user.groups.add(simulator_group)
        else:
            staff_user.groups.remove(simulator_group)

        selected_property_ids = {
            int(value)
            for value in request.POST.getlist("property_ids")
            if value.isdigit()
        }
        valid_property_ids = set(
            Property.objects.filter(pk__in=selected_property_ids).values_list("pk", flat=True)
        )
        existing = {
            access.property_id: access
            for access in WhatsAppStaffPropertyAccess.objects.filter(staff_user=staff_user)
        }
        for property_obj in properties:
            should_enable = property_obj.pk in valid_property_ids
            access = existing.get(property_obj.pk)
            if access and access.is_active != should_enable:
                access.is_active = should_enable
                access.save(update_fields=["is_active", "updated_at"])
            elif not access and should_enable:
                WhatsAppStaffPropertyAccess.objects.create(
                    staff_user=staff_user,
                    property=property_obj,
                    is_active=True,
                    notes="Managed from WhatsApp Staff Access.",
                )
        messages.success(request, f"WhatsApp access updated for {staff_user.username}.")
        return redirect("whatsapp:staff_access")

    staff_rows = []
    for staff_user in staff_users:
        active_property_ids = set(
            WhatsAppStaffPropertyAccess.objects.filter(
                staff_user=staff_user,
                is_active=True,
            ).values_list("property_id", flat=True)
        )
        staff_rows.append({
            "user": staff_user,
            "simulator_enabled": (
                staff_user.is_superuser
                or staff_user.groups.filter(name=TENANT_SIMULATOR_GROUP).exists()
            ),
            "properties": [
                {"property": property_obj, "selected": property_obj.pk in active_property_ids}
                for property_obj in properties
            ],
            "active_property_count": len(active_property_ids),
        })
    return render(
        request,
        "whatsapp/staff_access.html",
        {
            "staff_rows": staff_rows,
            "property_count": len(properties),
            "simulator_group": simulator_group,
        },
    )


class WhatsAppUtilityTemplateForm(forms.ModelForm):
    class Meta:
        model = WhatsAppUtilityTemplate
        fields = [
            "template_name",
            "language_code",
            "body_text",
            "body_variables",
            "button_label",
            "button_parameter_source",
            "is_active",
            "notes",
        ]
        widgets = {
            "template_name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "language_code": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "body_text": forms.Textarea(attrs={"rows": 7, "class": "form-control form-control-sm"}),
            "body_variables": forms.Textarea(attrs={"rows": 5, "class": "form-control form-control-sm font-monospace"}),
            "button_label": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "button_parameter_source": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control form-control-sm"}),
        }


class WhatsAppSimulatorForm(forms.Form):
    ROLE_CHOICES = [("tenant", "Tenant"), ("staff", "Staff"), ("handyman", "Handyman")]

    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.Select(attrs={"class": "form-select form-select-sm"}))
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.filter(is_active=True).order_by("first_name", "last_name", "id"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm select2"}),
    )
    lease = forms.ModelChoiceField(
        queryset=Lease.objects.select_related("tenant", "unit__property").filter(status="active").order_by("unit__property__property_name", "unit__unit_number"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm select2"}),
    )
    staff = forms.ModelChoiceField(
        queryset=get_user_model().objects.filter(is_active=True, is_staff=True).order_by("username"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm select2"}),
    )
    handyman = forms.ModelChoiceField(
        queryset=HandymanProfile.objects.filter(is_active=True).order_by("full_name", "id"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm select2"}),
    )
    message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 3, "placeholder": "Type the simulated inbound WhatsApp message"}),
    )
    media = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control form-control-sm", "accept": "image/*,video/*,.pdf,.doc,.docx"}))
    delivery_phone = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control form-control-sm", "placeholder": "03122550183"}))
    send_to_phone = forms.BooleanField(required=False, label="Send the simulated response to the delivery phone")
    new_session = forms.BooleanField(required=False, label="Reset this simulated conversation before sending")

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        actor = cleaned.get(role)
        if not actor:
            self.add_error(role, f"Select a {role} account.")
        if not cleaned.get("message") and not cleaned.get("media"):
            self.add_error("message", "Enter a message or attach media.")
        lease = cleaned.get("lease")
        tenant = cleaned.get("tenant")
        if role == "tenant" and lease and tenant and lease.tenant_id != tenant.pk:
            self.add_error("lease", "The selected lease does not belong to the selected tenant.")
        if cleaned.get("send_to_phone") and not cleaned.get("delivery_phone"):
            self.add_error("delivery_phone", "Enter the phone that should receive the simulated response.")
        return cleaned


class _SimulatorWhatsAppService(WhatsAppService):
    def __init__(self, delivery_phone="", deliver=False, label="Simulation"):
        super().__init__()
        self.delivery_phone = delivery_phone
        self.deliver = deliver
        self.label = label
        self.responses = []

    def send_text(self, phone_number, message, **kwargs):
        self.responses.append(str(message))
        if self.deliver and self.delivery_phone:
            return super().send_text(self.delivery_phone, f"[{self.label}]\n{message}", **kwargs)
        return {"ok": True, "simulated": True}


@login_required
@user_passes_test(_can_view_whatsapp_logs)
@require_http_methods(["GET", "POST"])
def whatsapp_simulator(request):
    initial_phone = getattr(request.user, "whatsapp_number", "") or "03122550183"
    form = WhatsAppSimulatorForm(request.POST or None, request.FILES or None, initial={"delivery_phone": initial_phone})
    responses = []
    conversation = None
    if request.method == "POST" and form.is_valid():
        from .services.whatsapp_ai import WhatsAppAIAssistant

        role = form.cleaned_data["role"]
        actor = form.cleaned_data[role]
        role_code = {"tenant": "1", "staff": "2", "handyman": "3"}[role]
        synthetic_phone = f"999{role_code}{actor.pk:08d}"
        conversation, _ = WhatsAppConversation.objects.get_or_create(phone_number=synthetic_phone)
        if form.cleaned_data["new_session"]:
            conversation.selected_mode = ""
            conversation.mode_expires_at = None
            conversation.pending_state = ""
            conversation.context = {}
            conversation.selected_lease = None
            conversation.selected_property = None
            conversation.selected_unit = None
        conversation.context = {
            **(conversation.context or {}),
            "simulator_identity": {"role": role, "object_id": actor.pk, "started_by_user_id": request.user.pk},
        }
        lease = form.cleaned_data.get("lease") if role == "tenant" else None
        if role == "tenant" and not lease:
            lease = Lease.objects.select_related("unit__property").filter(tenant=actor, status="active").order_by("-start_date", "-id").first()
        if lease:
            conversation.tenant = actor
            conversation.selected_lease = lease
            conversation.selected_property = lease.unit.property
            conversation.selected_unit = lease.unit
        conversation.save()

        upload = form.cleaned_data.get("media")
        message_type = "text"
        payload = {"type": "text", "text": {"body": form.cleaned_data.get("message") or ""}}
        pending_media = None
        if upload:
            message_type = "image" if (upload.content_type or "").startswith("image/") else "video" if (upload.content_type or "").startswith("video/") else "document"
            payload = {
                "type": message_type,
                message_type: {"caption": form.cleaned_data.get("message") or "", "filename": upload.name, "mime_type": upload.content_type or ""},
            }
            pending_media = PendingWhatsAppMedia.objects.create(
                conversation=conversation,
                phone=synthetic_phone,
                file=upload,
                original_filename=upload.name,
                media_type=message_type,
                lease=lease,
                tenant=getattr(lease, "tenant", None),
                property=getattr(getattr(lease, "unit", None), "property", None),
                unit=getattr(lease, "unit", None),
                ai_notes="Uploaded through the authenticated WhatsApp simulator.",
            )
        message_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=synthetic_phone,
            wa_message_id=f"sim-{uuid.uuid4().hex}",
            message_type=message_type,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload=payload,
            api_response={"simulator": True, "simulator_pending_media_id": getattr(pending_media, "pk", None), "started_by_user_id": request.user.pk},
            tenant=getattr(lease, "tenant", None),
            lease=lease,
            created_by=request.user,
        )
        actor_label = actor.get_full_name() if role == "tenant" else actor.get_username() if role == "staff" else str(actor)
        service = _SimulatorWhatsAppService(
            form.cleaned_data.get("delivery_phone") or "",
            form.cleaned_data.get("send_to_phone", False),
            f"{role.title()} Simulation - {actor_label}",
        )
        WhatsAppAIAssistant(service=service).handle_inbound_message(message_log)
        responses = service.responses
        messages.success(request, "Simulated WhatsApp message processed.")
    return render(request, "whatsapp/simulator.html", {"form": form, "responses": responses, "simulator_conversation": conversation})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request):
    if request.method == "GET":
        if not _verification_attempt_allowed(request):
            return HttpResponse("Too Many Requests", status=429)
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")
        if mode == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
            return HttpResponse(challenge or "")
        return HttpResponse("Forbidden", status=403)

    if not _valid_webhook_signature(request):
        logger.warning("Rejected WhatsApp webhook with invalid Meta signature.")
        return HttpResponse("Invalid signature", status=403)

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
            if value.get("calls"):
                return "call"
            if change.get("field"):
                return change.get("field")
    return payload.get("object", "") or "webhook"


def _log_webhook_payload(payload):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            metadata = value.get("metadata", {}) or {}
            phone_number = metadata.get("display_phone_number", "")

            # Provider call events are routed through an intentionally unsupported
            # abstraction today, so enabling future Meta calling cannot fake a call.
            for call_event in value.get("calls", []) or []:
                from whatsapp.services.handover.calling import WhatsAppCallingService
                WhatsAppCallingService().process_incoming_call_event(call_event)

            for message in value.get("messages", []) or []:
                inbound_phone = WhatsAppService.normalize_phone_number(message.get("from", "") or phone_number)
                message_id = message.get("id", "")
                inbound_at = _message_received_at(message)
                with transaction.atomic():
                    conversation = _touch_inbound_conversation(inbound_phone, message, inbound_at)
                    if conversation:
                        conversation = WhatsAppConversation.objects.select_for_update().get(
                            pk=conversation.pk
                        )
                    if message_id and WhatsAppMessageLog.objects.select_for_update().filter(
                        direction=WhatsAppMessageLog.DIRECTION_INBOUND,
                        wa_message_id=message_id,
                    ).exists():
                        logger.info(
                            "Ignored duplicate WhatsApp inbound message_id=%s state=already_recorded",
                            message_id,
                        )
                        continue
                    message_log = WhatsAppMessageLog.objects.create(
                        direction=WhatsAppMessageLog.DIRECTION_INBOUND,
                        phone_number=inbound_phone,
                        wa_message_id=message_id,
                        message_type=message.get("type", WhatsAppMessageLog.MESSAGE_TYPE_WEBHOOK),
                        status=WhatsAppMessageLog.STATUS_RECEIVED,
                        payload=message,
                        api_response={"entry_id": entry.get("id"), "field": change.get("field")},
                        tenant=conversation.tenant if conversation else None,
                        lease=conversation.selected_lease if conversation else None,
                    )
                if _inbound_rate_allowed(inbound_phone):
                    _queue_ai_message(message_log.pk)
                else:
                    logger.warning("Rate-limited inbound WhatsApp sender %s", inbound_phone)

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


def _message_received_at(message):
    timestamp = message.get("timestamp")
    if timestamp:
        try:
            return timezone.datetime.fromtimestamp(int(timestamp), tz=timezone.get_current_timezone())
        except (TypeError, ValueError, OSError):
            pass
    return timezone.now()


def _valid_webhook_signature(request):
    app_secret = getattr(settings, "WHATSAPP_APP_SECRET", "")
    if not app_secret:
        # Local DEBUG compatibility only; production fails closed.
        logger.warning("WHATSAPP_APP_SECRET is not configured; production webhooks are rejected.")
        return bool(settings.DEBUG)
    supplied = request.META.get("HTTP_X_HUB_SIGNATURE_256", "")
    if not supplied.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied[7:], expected)


def _verification_attempt_allowed(request):
    key = f"whatsapp:webhook-verify:{_remote_addr(request) or 'unknown'}"
    try:
        attempts = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=60)
        attempts = 1
    return attempts <= int(getattr(settings, "WHATSAPP_WEBHOOK_VERIFY_RATE_LIMIT", 20))


def _inbound_rate_allowed(phone_number):
    minute = timezone.now().strftime("%Y%m%d%H%M")
    key = f"whatsapp:inbound-rate:{phone_number}:{minute}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=120)
        count = 1
    return count <= int(getattr(settings, "WHATSAPP_INBOUND_MESSAGES_PER_MINUTE", 30))


def _touch_inbound_conversation(phone_number, message, received_at):
    if not phone_number:
        return None
    conversation, created = WhatsAppConversation.objects.get_or_create(phone_number=phone_number)
    if created:
        lease = find_active_leases_for_phone(phone_number).first()
        if lease:
            conversation.tenant = lease.tenant
            conversation.selected_lease = lease
            conversation.selected_property = getattr(lease.unit, "property", None)
            conversation.selected_unit = lease.unit
    conversation.last_message_at = received_at
    conversation.last_inbound_message_at = received_at
    conversation.last_inbound_message_id = message.get("id", "")
    conversation.save(update_fields=[
        "tenant",
        "selected_lease",
        "selected_property",
        "selected_unit",
        "last_message_at",
        "last_inbound_message_at",
        "last_inbound_message_id",
        "updated_at",
    ])
    return conversation


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
@require_POST
def replay_ai_message(request, message_log_id):
    message_log = get_object_or_404(
        WhatsAppMessageLog,
        pk=message_log_id,
        direction=WhatsAppMessageLog.DIRECTION_INBOUND,
    )
    from .services.whatsapp_ai import process_inbound_whatsapp_message

    process_inbound_whatsapp_message(message_log)
    messages.success(request, "WhatsApp AI replay completed.")
    return redirect(f"{reverse('whatsapp:webhook_log_list')}?phone={message_log.phone_number}")


@login_required
@user_passes_test(_can_view_whatsapp_logs)
def utility_template_list(request):
    templates = WhatsAppUtilityTemplate.objects.order_by("key")
    return render(
        request,
        "whatsapp/utility_template_list.html",
        {
            "templates": templates,
            "embed": request.GET.get("embed") == "1",
        },
    )


@login_required
@user_passes_test(_can_view_whatsapp_logs)
def utility_template_edit(request, pk):
    template = get_object_or_404(WhatsAppUtilityTemplate, pk=pk)
    if request.method == "POST":
        form = WhatsAppUtilityTemplateForm(request.POST, instance=template)
        if form.is_valid():
            form.save()
            messages.success(request, "WhatsApp Utility template settings updated.")
            return redirect("whatsapp:utility_template_list")
    else:
        form = WhatsAppUtilityTemplateForm(instance=template)

    return render(
        request,
        "whatsapp/utility_template_form.html",
        {
            "form": form,
            "template_obj": template,
            "embed": request.GET.get("embed") == "1",
        },
    )


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
    search_query = (request.GET.get("q") or "").strip()
    selected_location = (request.GET.get("location") or "").strip()
    try:
        selected_tenant_id = int(request.GET.get("tenant") or 0) or None
    except (TypeError, ValueError):
        selected_tenant_id = None
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
    conversation_summary = _filter_conversation_summary(
        conversation_summary,
        search_query=search_query,
        tenant_id=selected_tenant_id,
        location=selected_location,
    )
    if not selected_phone and conversation_summary:
        selected_phone = conversation_summary[0]["phone_number"]
    conversation_messages = _conversation_messages(selected_phone) if selected_phone else []
    selected_conversation = (
        _selected_conversation_context(selected_phone) if selected_phone else {}
    )

    property_filter_options = [
        {
            "value": f"property:{property.pk}",
            "label": f"All {(property.property_name or '')[:8]}",
        }
        for property in Property.objects.order_by("property_name", "id")
    ]
    unit_filter_options = [
        {
            "value": f"unit:{unit.pk}",
            "label": f"{(unit.property.property_name or '')[:8]} / {unit.unit_number}",
        }
        for unit in Unit.objects.select_related("property").order_by(
            "property__property_name", "unit_number", "id"
        )
    ]

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
            "selected_conversation": selected_conversation,
            "search_query": search_query,
            "selected_tenant_id": selected_tenant_id,
            "selected_location": selected_location,
            "tenant_filter_options": Tenant.objects.order_by(
                "first_name", "last_name", "id"
            ),
            "property_filter_options": property_filter_options,
            "unit_filter_options": unit_filter_options,
        },
    )


def _conversation_summary():
    summary = []
    seen = set()
    latest_message_ids = (
        WhatsAppMessageLog.objects.exclude(phone_number="")
        .values("phone_number")
        .annotate(latest_id=Max("id"))
        .values("latest_id")
    )
    logs = (
        WhatsAppMessageLog.objects.filter(pk__in=Subquery(latest_message_ids))
        .select_related("tenant", "lease__tenant", "lease__unit__property")
        .order_by("-created_at")
    )
    for index, log in enumerate(logs, start=1):
        if log.phone_number in seen:
            continue
        seen.add(log.phone_number)
        context = _conversation_context_for_phone(log.phone_number, log)
        summary.append(
            {
                "sn": len(summary) + 1,
                "phone_number": log.phone_number,
                "tenant_name": context["tenant_name"],
                "tenant_id": context["tenant_id"],
                "property_unit": context["property_unit"],
                "property_id": context["property_id"],
                "unit_id": context["unit_id"],
                "lease_id": context["lease_id"],
                "last_direction": log.direction,
                "last_status": log.status,
                "last_message": _message_text(log),
                "last_at": log.created_at,
                "needs_reply": _conversation_needs_reply(log.phone_number),
            }
        )
    return summary


def _conversation_messages(phone_number):
    rows = []
    logs = list(
        WhatsAppMessageLog.objects.filter(phone_number=phone_number)
        .exclude(direction=WhatsAppMessageLog.DIRECTION_STATUS)
        .order_by("created_at")
    )
    media_by_message_id = {
        media.original_whatsapp_message_id: media
        for media in PendingWhatsAppMedia.objects.filter(
            original_whatsapp_message_id__in=[log.id for log in logs]
        )
    }
    ai_by_message_id = {
        item.message_log_id: item
        for item in WhatsAppAIInteractionLog.objects.filter(message_log_id__in=[log.id for log in logs])
        .order_by("message_log_id", "created_at")
    }
    for log in logs:
        media = media_by_message_id.get(log.id)
        ai_log = ai_by_message_id.get(log.id)
        rows.append(
            {
                "id": log.id,
                "direction": log.direction,
                "status": log.status,
                "message_type": log.message_type,
                "message": _message_text(log),
                "media": _media_preview(media),
                "created_at": log.created_at,
                "wa_message_id": log.wa_message_id,
                "error_text": log.error_text,
                "ai": ai_log,
            }
        )
    return rows


def _conversation_needs_reply(phone_number):
    latest = (
        WhatsAppMessageLog.objects.filter(phone_number=phone_number)
        .exclude(direction=WhatsAppMessageLog.DIRECTION_STATUS)
        .order_by("-created_at")
        .first()
    )
    return bool(latest and latest.direction == WhatsAppMessageLog.DIRECTION_INBOUND)


def _conversation_context_for_phone(phone_number, latest_log=None):
    conversation = (
        WhatsAppConversation.objects.select_related(
            "tenant",
            "selected_lease__tenant",
            "selected_lease__unit__property",
            "selected_property",
            "selected_unit",
        )
        .filter(phone_number=phone_number)
        .first()
    )
    lease = getattr(conversation, "selected_lease", None) if conversation else None
    tenant = getattr(conversation, "tenant", None) if conversation else None
    selected_property = getattr(conversation, "selected_property", None) if conversation else None
    selected_unit = getattr(conversation, "selected_unit", None) if conversation else None

    if not lease and latest_log:
        lease = getattr(latest_log, "lease", None)
    if not tenant and latest_log:
        tenant = getattr(latest_log, "tenant", None)
    if not lease:
        lease = find_active_leases_for_phone(phone_number).first()
    if lease:
        tenant = tenant or lease.tenant
        selected_property = getattr(lease.unit, "property", None)
        selected_unit = lease.unit

    tenant_name = tenant.get_full_name() if tenant else ""
    property_unit = ""
    if selected_property or selected_unit:
        property_name = getattr(selected_property, "property_name", "") or "-"
        unit_number = getattr(selected_unit, "unit_number", "") or "-"
        property_unit = f"{property_name} / {unit_number}"

    return {
        "tenant_name": tenant_name,
        "tenant_id": getattr(tenant, "pk", None),
        "property_unit": property_unit,
        "property_id": getattr(selected_property, "pk", None),
        "unit_id": getattr(selected_unit, "pk", None),
        "lease_id": getattr(lease, "pk", None),
    }


def _filter_conversation_summary(
    summary, *, search_query="", tenant_id=None, location=""
):
    filtered = list(summary)

    if tenant_id:
        filtered = [row for row in filtered if row["tenant_id"] == tenant_id]

    if location.startswith("property:"):
        try:
            property_id = int(location.split(":", 1)[1])
        except (TypeError, ValueError):
            property_id = None
        if property_id:
            filtered = [row for row in filtered if row["property_id"] == property_id]
    elif location.startswith("unit:"):
        try:
            unit_id = int(location.split(":", 1)[1])
        except (TypeError, ValueError):
            unit_id = None
        if unit_id:
            filtered = [row for row in filtered if row["unit_id"] == unit_id]

    query = (search_query or "").strip().casefold()
    if not query:
        return filtered

    query_digits = "".join(character for character in query if character.isdigit())
    message_phone_matches = set()
    for log in WhatsAppMessageLog.objects.exclude(phone_number="").iterator():
        payload_text = json.dumps(log.payload or {}, ensure_ascii=False, default=str)
        message_blob = " ".join(
            [
                log.phone_number or "",
                _message_text(log),
                log.error_text or "",
                log.template_name or "",
                log.status or "",
                log.message_type or "",
                payload_text,
            ]
        ).casefold()
        phone_digits = "".join(
            character for character in (log.phone_number or "") if character.isdigit()
        )
        if query in message_blob or (
            query_digits and query_digits in phone_digits
        ):
            message_phone_matches.add(log.phone_number)

    result = []
    for row in filtered:
        row_blob = " ".join(
            [
                row["phone_number"] or "",
                row["tenant_name"] or "",
                row["property_unit"] or "",
                row["last_message"] or "",
                row["last_status"] or "",
            ]
        ).casefold()
        row_phone_digits = "".join(
            character
            for character in (row["phone_number"] or "")
            if character.isdigit()
        )
        if (
            query in row_blob
            or row["phone_number"] in message_phone_matches
            or (query_digits and query_digits in row_phone_digits)
        ):
            result.append(row)
    return result


def _selected_conversation_context(phone_number):
    context = _conversation_context_for_phone(phone_number)
    active_leases = list(find_active_leases_for_phone(phone_number))
    tenant = active_leases[0].tenant if active_leases else None

    if not tenant and context["tenant_id"]:
        tenant = Tenant.objects.filter(pk=context["tenant_id"]).first()

    selected_digits = WhatsAppService.normalize_phone_number(phone_number)
    tenant_phone = getattr(tenant, "phone", "") or ""
    tenant_digits = WhatsAppService.normalize_phone_number(tenant_phone)
    phone_matches_tenant = bool(
        selected_digits
        and tenant_digits
        and selected_digits[-10:] == tenant_digits[-10:]
    )

    return {
        "tenant_id": getattr(tenant, "pk", None),
        "tenant_name": tenant.get_full_name() if tenant else context["tenant_name"],
        "tenant_phone": tenant_phone if phone_matches_tenant else "",
        "active_leases": [
            {
                "id": lease.pk,
                "property_name": lease.unit.property.property_name,
                "unit_number": lease.unit.unit_number,
                "end_date": lease.end_date,
            }
            for lease in active_leases
        ],
    }


def _media_preview(media):
    if not media:
        return None

    file_field = getattr(media, "file", None)
    file_name = getattr(file_field, "name", "") or ""
    unavailable = not file_name or "/unavailable/" in file_name.replace("\\", "/")
    exists = False
    if file_name and not unavailable:
        try:
            exists = file_field.storage.exists(file_name)
        except Exception:
            exists = False
    if unavailable or not exists:
        return {
            "available": False,
            "note": media.ai_notes or "Media unavailable or download failed.",
        }

    media_type = (media.media_type or "").lower()
    lower_name = file_name.lower()
    if media_type == "image" or lower_name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        kind = "image"
    elif media_type == "video" or lower_name.endswith((".mp4", ".mov", ".webm")):
        kind = "video"
    elif lower_name.endswith(".pdf"):
        kind = "pdf"
    else:
        kind = "file"

    return {
        "available": True,
        "kind": kind,
        "url": file_field.url,
        "label": media.original_filename or file_name.rsplit("/", 1)[-1],
        "purpose": media.get_purpose_display(),
    }


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
        if result.get("message_type") == WhatsAppMessageLog.MESSAGE_TYPE_TEMPLATE:
            messages.success(request, f"WhatsApp template sent: {result.get('template_name') or 'template'}.")
        else:
            messages.success(request, "WhatsApp session message sent from TMS.")
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
            language_code=getattr(settings, "WHATSAPP_DEFAULT_TEMPLATE_LANGUAGE", "en"),
            tenant=tenant,
            lease=lease,
        )
    if object_type == "invoice" and action == "invoice":
        message_text = _invoice_message(request, obj)
        if not is_whatsapp_session_open(phone):
            return service.send_invoice_notice_template(obj, phone_number=phone)
        image_bytes, filename = _invoice_jpg_attachment(obj)
        return service.send_image_bytes(phone, image_bytes, filename=filename, caption=message_text, tenant=tenant, lease=lease, invoice=obj)
    if object_type == "payment" and action in {"receipt", "payment_confirmation"}:
        message_text = _payment_receipt_message(obj)
        if not is_whatsapp_session_open(phone):
            return service.send_payment_confirmation_template(obj, phone_number=phone)
        image_bytes, filename = _payment_jpg_attachment(obj)
        return service.send_image_bytes(phone, image_bytes, filename=filename, caption=message_text, tenant=tenant, lease=lease, payment=obj)
    if object_type == "lease" and action == "lease":
        return service.send_lease(obj, message=message_text)
    if object_type == "lease" and action == "renewal":
        return service.send_lease_renewal_notice_templates(obj, phone_number=phone)
    if object_type == "lease" and action == "text" and not is_whatsapp_session_open(phone):
        return service.send_balance_reminder_template(obj, phone_number=phone)
    if object_type == "tenant" and action == "text" and lease and not is_whatsapp_session_open(phone):
        return service.send_balance_reminder_template(lease, phone_number=phone)
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
        language_code="en",
        message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEMPLATE,
    )
    return JsonResponse(result)
