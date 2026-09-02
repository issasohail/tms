# core/views.py
import hashlib
import json
import logging
import uuid

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth import logout
from django.contrib import messages
from django.core.files.base import ContentFile
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.http import FileResponse, Http404, HttpResponseForbidden, JsonResponse
from django.urls import reverse, reverse_lazy
from django.views.generic import FormView
from django.shortcuts import get_object_or_404, redirect, render
from core.utils.embed import embed_redirect
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote

from django.db import models, transaction
from django.db.models import Case, DecimalField, F, OuterRef, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, Greatest

from .forms import GlobalSettingsForm
from .models import GlobalSettings, TenantIncomeBracket, TenantOccupationOption
from .public_urls import build_public_path_url, build_public_url
from .pending_approval_queue import (
    actionable_media_count,
    eligible_pending_media_queryset,
    pending_approval_actionable_counts,
    pending_approval_count,
    pending_approval_status_filters,
)
from tenants.models import Tenant, TenantInterestType
from payments.models import Payment
from invoices.models import Invoice
from invoices.models import SecurityDepositTransaction
from expenses.models import Expense
from properties.models import BuildingType, Property, Unit
from leases.models import Lease, LeaseRenewal
from smart_meter.models import LiveReading
from smart_meter.status import online_threshold_minutes
from django.contrib.auth.decorators import login_required

logger = logging.getLogger(__name__)
VIDEO_FRAME_NOTE_PREFIX = "[Extracted video frame]"


PENDING_KIND_LABELS = {
    "lease": "Pending Lease",
    "agreement": "Pending Agreement Edit",
    "payment": "WhatsApp Payment",
    "media": "WhatsApp Document / Media",
    "maintenance": "WhatsApp Maintenance",
    "family": "Lease Family Member",
    "police": "Police Verification",
    "registration": "Tenant Registration",
}

PENDING_APPROVAL_STATUS_CHOICES = (
    ("pending", "Pending"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("all", "All statuses"),
)

PENDING_APPROVAL_DATE_CHOICES = (
    ("all", "All dates"),
    ("this_week", "This week"),
    ("last_week", "Last week"),
    ("this_month", "This month"),
    ("last_month", "Last month"),
    ("custom", "Custom dates"),
)

PENDING_APPROVAL_TYPE_CHOICES = (
    ("all", "All approval types"),
    ("lease", "Leases"),
    ("agreement", "Agreement Edits"),
    ("payment", "WhatsApp Payments"),
    ("media", "WhatsApp Documents / Media"),
    ("maintenance", "WhatsApp Maintenance"),
    ("family", "Lease Family Members"),
    ("police", "Police Verification"),
    ("registration", "Tenant Registration Submissions"),
)


def _pending_approval_filter_state(request):
    valid_statuses = {value for value, _label in PENDING_APPROVAL_STATUS_CHOICES}
    valid_ranges = {value for value, _label in PENDING_APPROVAL_DATE_CHOICES}
    valid_types = {value for value, _label in PENDING_APPROVAL_TYPE_CHOICES}
    selected_status = (request.GET.get("status") or "pending").lower()
    selected_range = (request.GET.get("date_range") or "all").lower()
    selected_type = (request.GET.get("approval_type") or "all").lower()
    if selected_status not in valid_statuses:
        selected_status = "pending"
    if selected_range not in valid_ranges:
        selected_range = "all"
    if selected_type not in valid_types:
        selected_type = "all"

    today = timezone.localdate()
    date_from = None
    date_to = None
    if selected_range == "this_week":
        date_from = today - timedelta(days=today.weekday())
        date_to = today
    elif selected_range == "last_week":
        this_week_start = today - timedelta(days=today.weekday())
        date_from = this_week_start - timedelta(days=7)
        date_to = this_week_start - timedelta(days=1)
    elif selected_range == "this_month":
        date_from = today.replace(day=1)
        date_to = today
    elif selected_range == "last_month":
        this_month_start = today.replace(day=1)
        date_to = this_month_start - timedelta(days=1)
        date_from = date_to.replace(day=1)
    elif selected_range == "custom":
        date_from = parse_date(request.GET.get("date_from", ""))
        date_to = parse_date(request.GET.get("date_to", ""))
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from

    try:
        property_id = int(request.GET.get("property", "") or 0) or None
    except (TypeError, ValueError):
        property_id = None
    try:
        unit_id = int(request.GET.get("unit", "") or 0) or None
    except (TypeError, ValueError):
        unit_id = None

    return {
        "status": selected_status,
        "approval_type": selected_type,
        "date_range": selected_range,
        "date_from": date_from,
        "date_to": date_to,
        "date_from_value": date_from.isoformat() if date_from else "",
        "date_to_value": date_to.isoformat() if date_to else "",
        "property_id": property_id,
        "unit_id": unit_id,
        "search": (request.GET.get("q") or "").strip()[:160],
    }


def _filter_pending_approval_queryset(queryset, filters, status_filters, date_field="created_at"):
    selected_status = filters["status"]
    if selected_status != "all":
        queryset = queryset.filter(status_filters[selected_status])
    if filters["date_from"]:
        queryset = queryset.filter(**{f"{date_field}__date__gte": filters["date_from"]})
    if filters["date_to"]:
        queryset = queryset.filter(**{f"{date_field}__date__lte": filters["date_to"]})
    return queryset


def _filter_pending_approval_scope(
    queryset,
    filters,
    *,
    property_field=None,
    unit_field=None,
    search_fields=(),
):
    if filters["property_id"]:
        if not property_field:
            return queryset.none()
        queryset = queryset.filter(**{property_field: filters["property_id"]})
    if filters["unit_id"]:
        if not unit_field:
            return queryset.none()
        queryset = queryset.filter(**{unit_field: filters["unit_id"]})
    if filters["search"] and search_fields:
        search_query = models.Q()
        for field_name in search_fields:
            search_query |= models.Q(**{f"{field_name}__icontains": filters["search"]})
        queryset = queryset.filter(search_query)
    return queryset


def _pending_item_urls(kind, item):
    if kind == "registration":
        return {
            "detail": reverse("tenants:registration_submission_detail", args=[item.pk]),
            "approve": "",
            "reject": "",
            "delete": reverse("core:pending_approval_delete", args=[kind, item.pk]),
        }
    urls = {
        "detail": reverse("core:pending_approval_detail", args=[kind, item.pk]),
        "approve": reverse("core:pending_approval_approve", args=[kind, item.pk]),
        "reject": reverse("core:pending_approval_reject", args=[kind, item.pk]),
        "delete": reverse("core:pending_approval_delete", args=[kind, item.pk]),
    }
    if kind == "family":
        urls.update({
            "photo": reverse("core:pending_family_file", args=[item.pk, "photo"]),
            "cnic_front": reverse("core:pending_family_file", args=[item.pk, "cnic_front"]),
            "cnic_back": reverse("core:pending_family_file", args=[item.pk, "cnic_back"]),
        })
    return urls


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _pending_ajax_response(request, message, *, redirect_url="", status=200):
    if not _is_ajax(request):
        return None
    return JsonResponse(
        {
            "ok": status < 400,
            "message": message,
            "redirect_url": redirect_url,
            "pending_approval_count": pending_approval_count(),
        },
        status=status,
    )


def _group_pending_media(items):
    """Show one approval row for files deliberately uploaded in the same batch."""
    grouped = []
    seen_batches = set()
    for item in items:
        if item.batch_key:
            if item.batch_key in seen_batches:
                continue
            seen_batches.add(item.batch_key)
            item.pending_group_items = [
                candidate for candidate in items
                if candidate.batch_key == item.batch_key
            ]
        else:
            item.pending_group_items = [item]
        item.pending_group_count = len(item.pending_group_items)
        grouped.append(item)
    return grouped


def _property_unit_label(item):
    property_obj = getattr(item, "property", None)
    unit = getattr(item, "unit", None)
    lease = getattr(item, "lease", None)
    if lease:
        unit = getattr(lease, "unit", None)
        property_obj = getattr(unit, "property", property_obj)
    elif unit and not property_obj:
        property_obj = getattr(unit, "property", None)
    property_name = getattr(property_obj, "property_name", "") or str(property_obj or "")
    unit_number = getattr(unit, "unit_number", "") or str(unit or "")
    if property_name and unit_number:
        return f"{property_name} / {unit_number}"
    return property_name or unit_number or "-"


def _media_preview_kind(file_name, media_type=""):
    name = (file_name or "").lower()
    media_type = (media_type or "").lower()
    if media_type.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return "image"
    if media_type.startswith("video/") or name.endswith((".mp4", ".mov", ".webm")):
        return "video"
    if media_type == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    return "file"


def _pending_media_context(media):
    if not media or not getattr(media, "file", None):
        return {"file_url": "", "preview_kind": "file", "file_size": None}
    if getattr(media, "processing", False):
        # Video/audio downloads happen in the background (see
        # whatsapp.tasks.download_pending_media_task). Until that task clears
        # `processing`, media.file.name is only a placeholder path with no
        # bytes behind it yet - returning file_url here 404s in the browser.
        return {
            "file_url": "",
            "preview_kind": "processing",
            "filename": media.original_filename or media.file.name,
            "file_size": None,
            "retry_available": bool(
                media.whatsapp_media_id
                and media.updated_at <= timezone.now() - timedelta(minutes=2)
            ),
        }
    try:
        file_size = media.file.size
    except (FileNotFoundError, OSError, ValueError):
        file_size = None
    if file_size is None:
        # File is marked as downloaded but isn't actually on disk/storage
        # (failed background download, moved/missing file, etc). Same
        # treatment as "processing" so the UI doesn't offer a dead link.
        # ai_notes already carries the specific reason written by
        # whatsapp.tasks.download_pending_media_task (token issue, size
        # limit, no media id, etc) - surface it instead of a generic message.
        return {
            "file_url": "",
            "preview_kind": "unavailable",
            "filename": media.original_filename or media.file.name,
            "file_size": None,
            "status_note": media.ai_notes or "The file is missing or failed to download.",
            "retry_available": bool(media.whatsapp_media_id),
        }
    return {
        "file_url": media.file.url,
        "preview_kind": _media_preview_kind(media.file.name, media.media_type),
        "filename": media.original_filename or media.file.name,
        "file_size": file_size,
        "is_extracted_frame": VIDEO_FRAME_NOTE_PREFIX in (media.ai_notes or ""),
    }


def _whatsapp_api_display_number():
    """Return the Meta WhatsApp display number seen in recent webhook metadata."""
    from whatsapp.models import WhatsAppWebhookLog
    from whatsapp.services.whatsapp import WhatsAppService

    configured_number = GlobalSettings.get_solo().whatsapp_number
    if configured_number:
        return WhatsAppService.normalize_phone_number(configured_number)
    for log in WhatsAppWebhookLog.objects.order_by("-created_at")[:50]:
        for entry in (log.payload or {}).get("entry", []):
            for change in entry.get("changes", []):
                display_number = (
                    change.get("value", {}).get("metadata", {}).get(
                        "display_phone_number", ""
                    )
                )
                if display_number:
                    return WhatsAppService.normalize_phone_number(display_number)
    return ""


def _handyman_maintenance_message(request, pending, ticket, handyman):
    tenant = pending.tenant or getattr(pending.lease, "tenant", None)
    tenant_name = tenant.get_full_name() if tenant else "-"
    tenant_phone = getattr(tenant, "phone", "") or pending.phone or "-"
    location = _property_unit_label(pending)
    staff_name = request.user.get_full_name() or request.user.get_username()
    settings_obj = GlobalSettings.get_solo()
    staff_phone = (
        getattr(request.user, "whatsapp_number", "")
        or settings_obj.whatsapp_number
        or "-"
    )
    detail_url = build_public_url("maintenance:request_detail", args=[ticket.pk])
    api_number = _whatsapp_api_display_number()
    api_number_label = f"+{api_number}" if api_number else "this WhatsApp API number"
    photo_command = settings_obj.handyman_job_photo_command or "PHOTO"
    invoice_command = settings_obj.handyman_invoice_command or "INVOICE"

    lines = [
        f"Hello {handyman.full_name},",
        "A maintenance job has been assigned to you.",
        "",
        f"Job: #{ticket.pk} - {ticket.title}",
        f"Location: {location}",
        f"Tenant: {tenant_name}",
        f"Tenant number: {tenant_phone}",
        f"Staff contact: {staff_name} - {staff_phone}",
        f"Priority: {ticket.get_priority_display()}",
        f"Details: {pending.description or ticket.description or '-'}",
        f"TMS detail: {detail_url}",
        "",
        "Please inspect the job and take clear photos before starting work. "
        "Take a video where necessary.",
        "",
        "IMPORTANT - After the repair, send clear pictures of the completed work "
        "and the bill for payment:",
        f"1. Send {photo_command} {ticket.pk}, then send the repair photos/video.",
        f"2. Send {invoice_command} {ticket.pk}, then send the bill/invoice.",
        f"Send them to WhatsApp API number {api_number_label}. "
        "Always include the job number so files go to the correct request.",
    ]
    return "\n".join(lines)


def _send_handyman_maintenance_media(request, service, phone_number, ticket):
    """Send approved tenant media as native WhatsApp attachments for this job."""
    sent_count = 0
    failed_count = 0
    for index, media in enumerate(ticket.media.filter(is_active=True), start=1):
        if not media.file:
            continue
        file_url = build_public_path_url(media.file.url)
        caption = f"Job #{ticket.pk} submitted media {index}: {ticket.title}"
        try:
            if media.is_image:
                result = service.send_image(
                    phone_number,
                    file_url,
                    caption=caption,
                    maintenance_request=ticket,
                )
            elif media.is_video:
                result = service.send_video(
                    phone_number,
                    file_url,
                    caption=caption,
                    maintenance_request=ticket,
                )
            else:
                result = service.send_document(
                    phone_number,
                    file_url,
                    filename=media.display_filename,
                    caption=caption,
                    maintenance_request=ticket,
                )
        except Exception:
            result = {"ok": False}
        if result.get("ok"):
            sent_count += 1
        else:
            failed_count += 1
    return sent_count, failed_count


def _can_delete_pending_approval(user, item):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    opts = item._meta
    return user.has_perm(f"{opts.app_label}.delete_{opts.model_name}")


def _can_change_pending_approval(user, item):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    opts = item._meta
    return user.has_perm(f"{opts.app_label}.change_{opts.model_name}")


def _can_hard_delete_pending_approval(user, item):
    if not _can_delete_pending_approval(user, item):
        return False
    if item._meta.label_lower == "leases.lease":
        return getattr(item, "status", "") in {"pending_approval", "rejected"}
    return True


@login_required
def pending_approvals(request):
    from handyman.models import HandymanProfile
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia, PendingWhatsAppPayment, WhatsAppExternalLinkToken
    from leases.models import PendingAgreementApproval, PendingLeaseFamilyMemberSubmission, PendingPoliceVerificationSubmission
    from tenants.models import TenantRegistrationSubmission

    filters = _pending_approval_filter_state(request)
    pending_status_filters = pending_approval_status_filters()
    common_status_filters = {
        "pending": pending_status_filters["common"],
        "approved": models.Q(status="approved"),
        "rejected": models.Q(status="rejected"),
    }
    lease_status_filters = {
        "pending": pending_status_filters["lease"],
        "approved": models.Q(status__in=["active", "ended", "terminated"]),
        "rejected": models.Q(status="rejected"),
    }
    payment_status_filters = {
        "pending": pending_status_filters["payment"],
        "approved": models.Q(status=PendingWhatsAppPayment.STATUS_APPROVED) | models.Q(approved=True),
        "rejected": models.Q(status=PendingWhatsAppPayment.STATUS_REJECTED) | models.Q(rejected=True),
    }
    registration_status_filters = {
        "pending": pending_status_filters["registration"],
        "approved": models.Q(status=TenantRegistrationSubmission.STATUS_APPROVED),
        "rejected": models.Q(status=TenantRegistrationSubmission.STATUS_REJECTED),
    }
    use_global_pending_counts = (
        filters["status"] == "pending"
        and filters["date_range"] == "all"
        and filters["property_id"] is None
        and filters["unit_id"] is None
        and not filters["search"]
    )
    global_pending_counts = (
        pending_approval_actionable_counts(request)
        if use_global_pending_counts
        else {}
    )

    pending_payments = _filter_pending_approval_queryset(
        PendingWhatsAppPayment.objects.select_related("tenant", "lease", "property", "unit"),
        filters,
        payment_status_filters,
    ).order_by("-created_at")
    pending_payments = _filter_pending_approval_scope(
        pending_payments,
        filters,
        property_field="property_id",
        unit_field="unit_id",
        search_fields=(
            "phone", "tenant__first_name", "tenant__last_name", "reference",
            "property__property_name", "unit__unit_number", "ai_notes",
        ),
    )
    pending_media_queryset = eligible_pending_media_queryset().select_related(
        "tenant", "lease", "property", "unit", "submitted_by_staff"
    )
    pending_media_queryset = _filter_pending_approval_queryset(
        pending_media_queryset, filters, common_status_filters
    ).order_by("-created_at")
    pending_media_queryset = _filter_pending_approval_scope(
        pending_media_queryset,
        filters,
        property_field="property_id",
        unit_field="unit_id",
        search_fields=(
            "phone", "tenant__first_name", "tenant__last_name", "original_filename",
            "property__property_name", "unit__unit_number", "purpose", "target_kind", "ai_notes",
        ),
    )
    pending_media_action_count = (
        global_pending_counts["media"]
        if use_global_pending_counts
        else actionable_media_count(pending_media_queryset)
    )
    pending_media = _group_pending_media(list(pending_media_queryset[:200]))[:50]
    pending_maintenance = _filter_pending_approval_queryset(
        PendingWhatsAppMaintenance.objects.select_related("tenant", "lease", "property", "unit"),
        filters,
        common_status_filters,
    ).order_by("-created_at")
    pending_maintenance = _filter_pending_approval_scope(
        pending_maintenance,
        filters,
        property_field="property_id",
        unit_field="unit_id",
        search_fields=(
            "phone", "tenant__first_name", "tenant__last_name", "property__property_name",
            "unit__unit_number", "issue_type", "description", "ai_notes",
        ),
    )
    approval_lease_ids = WhatsAppExternalLinkToken.objects.filter(
        link_type=WhatsAppExternalLinkToken.LINK_LEASE_CREATION,
        target_app_label="leases",
        target_model="lease",
        target_object_id__isnull=False,
    ).values_list("target_object_id", flat=True)
    approval_lease_queryset = Lease.objects.filter(
        models.Q(status__in=["pending_approval", "rejected"])
        | models.Q(pk__in=approval_lease_ids)
    ).select_related("tenant", "unit__property")
    pending_leases = _filter_pending_approval_queryset(
        approval_lease_queryset,
        filters,
        lease_status_filters,
    ).order_by("-created_at")
    pending_leases = _filter_pending_approval_scope(
        pending_leases,
        filters,
        property_field="unit__property_id",
        unit_field="unit_id",
        search_fields=(
            "tenant__first_name", "tenant__last_name", "tenant__phone", "tenant__cnic",
            "unit__property__property_name", "unit__unit_number", "notes", "terms",
        ),
    )
    pending_agreements = _filter_pending_approval_queryset(
        PendingAgreementApproval.objects.select_related(
            "lease__tenant", "lease__unit__property", "submitted_by"
        ),
        filters,
        common_status_filters,
    ).order_by("-created_at")
    pending_agreements = _filter_pending_approval_scope(
        pending_agreements,
        filters,
        property_field="lease__unit__property_id",
        unit_field="lease__unit_id",
        search_fields=(
            "lease__tenant__first_name", "lease__tenant__last_name", "lease__tenant__phone",
            "lease__unit__property__property_name", "lease__unit__unit_number", "proposed_terms", "review_notes",
        ),
    )
    pending_family = _filter_pending_approval_queryset(
        PendingLeaseFamilyMemberSubmission.objects.select_related(
            "lease__tenant", "lease__unit__property", "primary_tenant", "relationship_type"
        ),
        filters,
        common_status_filters,
    ).order_by("-created_at")
    pending_family = _filter_pending_approval_scope(
        pending_family,
        filters,
        property_field="lease__unit__property_id",
        unit_field="lease__unit_id",
        search_fields=(
            "first_name", "last_name", "phone", "cnic", "notes",
            "lease__tenant__first_name", "lease__tenant__last_name",
            "lease__unit__property__property_name", "lease__unit__unit_number",
        ),
    )
    pending_police = _filter_pending_approval_queryset(
        PendingPoliceVerificationSubmission.objects.select_related(
            "lease__tenant", "lease__unit__property", "tenant"
        ),
        filters,
        common_status_filters,
        date_field="submitted_at",
    ).order_by("-submitted_at")
    pending_police = _filter_pending_approval_scope(
        pending_police,
        filters,
        property_field="lease__unit__property_id",
        unit_field="lease__unit_id",
        search_fields=(
            "phone", "tenant__first_name", "tenant__last_name", "original_filename", "notes",
            "lease__unit__property__property_name", "lease__unit__unit_number",
        ),
    )
    pending_registrations = _filter_pending_approval_queryset(
        TenantRegistrationSubmission.objects.select_related("tenant").prefetch_related("pending_people"),
        filters,
        registration_status_filters,
        date_field="submitted_at",
    ).order_by("-submitted_at")
    pending_registrations = _filter_pending_approval_scope(
        pending_registrations,
        filters,
        search_fields=("tenant__first_name", "tenant__last_name", "tenant__phone", "tenant__cnic", "admin_notes"),
    )

    def section(title, kind, queryset_or_items, count=None):
        if isinstance(queryset_or_items, list):
            items = queryset_or_items
            if count is None:
                count = len(items)
        else:
            if count is None:
                count = queryset_or_items.count()
            items = list(queryset_or_items[:50])
        return {"title": title, "kind": kind, "items": items, "count": count}

    sections = [
        section("Leases", "lease", pending_leases, global_pending_counts.get("lease")),
        section("Agreement Edits", "agreement", pending_agreements, global_pending_counts.get("agreement")),
        section("WhatsApp Payments", "payment", pending_payments, global_pending_counts.get("payment")),
        section(
            "WhatsApp Documents / Media",
            "media",
            pending_media,
            count=pending_media_action_count,
        ),
        section("WhatsApp Maintenance", "maintenance", pending_maintenance, global_pending_counts.get("maintenance")),
        section("Lease Family Members", "family", pending_family, global_pending_counts.get("family")),
        section("Police Verification", "police", pending_police, global_pending_counts.get("police")),
        section("Tenant Registration Submissions", "registration", pending_registrations, global_pending_counts.get("registration")),
    ]
    for section in sections:
        section["items"] = [
            {
                "object": item,
                "urls": _pending_item_urls(section["kind"], item),
                "property_unit_label": _property_unit_label(item),
                "media_previews": (
                    [
                        _pending_media_context(media)
                        for media in getattr(item, "pending_group_items", [item])
                    ]
                    if section["kind"] == "media"
                    else []
                ),
                "can_change": _can_change_pending_approval(request.user, item),
                "can_delete": _can_hard_delete_pending_approval(request.user, item),
            }
            for item in section["items"]
        ]
    visible_sections = [
        section
        for section in sections
        if section["count"]
        and (
            filters["approval_type"] == "all"
            or section["kind"] == filters["approval_type"]
        )
    ]
    return render(
        request,
        "core/pending_approvals.html",
        {
            "sections": sections,
            "visible_sections": visible_sections,
            "visible_approval_count": sum(section["count"] for section in visible_sections),
            "approval_filters": filters,
            "status_choices": PENDING_APPROVAL_STATUS_CHOICES,
            "approval_type_choices": PENDING_APPROVAL_TYPE_CHOICES,
            "date_range_choices": PENDING_APPROVAL_DATE_CHOICES,
            "filter_properties": Property.objects.order_by("property_name"),
            "filter_units": Unit.objects.filter(
                **({"property_id": filters["property_id"]} if filters["property_id"] else {})
            ).select_related("property").order_by("property__property_name", "unit_number"),
            "active_handymen": HandymanProfile.objects.filter(
                is_active=True
            ).order_by("-is_preferred", "full_name"),
        },
    )


def _pending_item_for_kind(kind, pk):
    from leases.models import PendingAgreementApproval, PendingLeaseFamilyMemberSubmission, PendingPoliceVerificationSubmission
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia, PendingWhatsAppPayment

    if kind == "lease":
        return get_object_or_404(Lease.objects.select_related("tenant", "unit__property"), pk=pk)
    if kind == "agreement":
        return get_object_or_404(
            PendingAgreementApproval.objects.select_related("lease__tenant", "lease__unit__property", "submitted_by"),
            pk=pk,
        )
    if kind == "payment":
        return get_object_or_404(
            PendingWhatsAppPayment.objects.select_related("tenant", "lease", "property", "unit", "created_payment"),
            pk=pk,
        )
    if kind == "media":
        return get_object_or_404(
            PendingWhatsAppMedia.objects.select_related("tenant", "lease", "property", "unit", "approved_by"),
            pk=pk,
        )
    if kind == "maintenance":
        return get_object_or_404(
            PendingWhatsAppMaintenance.objects.select_related("tenant", "lease", "property", "unit", "created_request", "approved_by")
            .prefetch_related("media"),
            pk=pk,
        )
    if kind == "family":
        return get_object_or_404(
            PendingLeaseFamilyMemberSubmission.objects.select_related(
                "lease__tenant",
                "lease__unit__property",
                "primary_tenant",
                "relationship_type",
                "created_tenant",
                "created_family_member",
                "reviewed_by",
            ),
            pk=pk,
        )
    if kind == "police":
        return get_object_or_404(
            PendingPoliceVerificationSubmission.objects.select_related(
                "lease__tenant",
                "lease__unit__property",
                "tenant",
                "reviewed_by",
                "approved_document",
            ),
            pk=pk,
        )
    raise Http404("Unknown pending approval type.")


@login_required
@require_POST
def pending_approval_delete(request, kind, pk):
    """Hard-delete an approval row when the user has its Django delete permission."""
    from tenants.models import TenantRegistrationSubmission
    from whatsapp.models import PendingWhatsAppMedia
    from core.pending_approval_purge import hard_delete_pending_objects

    if kind == "registration":
        item = get_object_or_404(TenantRegistrationSubmission, pk=pk)
    else:
        item = _pending_item_for_kind(kind, pk)

    if not _can_hard_delete_pending_approval(request.user, item):
        raise PermissionDenied

    deleted_count = 1
    if kind == "media" and getattr(item, "batch_key", None):
        batch = PendingWhatsAppMedia.objects.filter(
            batch_key=item.batch_key,
            status=item.status,
        )
        deleted_count = batch.count()
        hard_delete_pending_objects(batch)
    elif kind == "lease":
        item.delete()
    else:
        hard_delete_pending_objects([item])

    message = (
        f"Pending approval deleted ({deleted_count} linked files)."
        if deleted_count > 1
        else "Pending approval deleted."
    )
    messages.success(request, message)
    response = _pending_ajax_response(request, message)
    return response or redirect("core:pending_approvals")


@login_required
def pending_approval_detail(request, kind, pk):
    from handyman.models import HandymanProfile
    from properties.models import Unit

    item = _pending_item_for_kind(kind, pk)
    media_items = []
    media_preview = None
    has_extracted_video_frames = False
    if kind == "media":
        if item.batch_key:
            batch_media = item.__class__.objects.filter(
                status=item.STATUS_PENDING,
                batch_key=item.batch_key,
            ).order_by("created_at", "pk")
            media_items = [
                {"object": media, **_pending_media_context(media)}
                for media in batch_media
            ]
            has_extracted_video_frames = any(
                media.get("is_extracted_frame", False) for media in media_items
            )
        else:
            media_preview = _pending_media_context(item)
    elif kind == "payment":
        if getattr(item, "screenshot", None):
            media_preview = {
                "file_url": item.screenshot.url,
                "preview_kind": _media_preview_kind(item.screenshot.name),
                "filename": item.screenshot.name,
            }
    elif kind == "maintenance":
        media_items = [
            {"object": media, **_pending_media_context(media)}
            for media in item.media.all()
        ]
    elif kind == "police":
        media_preview = {
            "file_url": item.file.url if item.file else "",
            "preview_kind": _media_preview_kind(item.file.name if item.file else ""),
            "filename": item.original_filename or (item.file.name if item.file else ""),
        }
    return render(
        request,
        "core/pending_approval_detail.html",
        {
            "kind": kind,
            "kind_label": PENDING_KIND_LABELS.get(kind, "Pending Approval"),
            "item": item,
            "media_preview": media_preview,
            "media_items": media_items,
            "has_extracted_video_frames": has_extracted_video_frames,
            "property_unit_label": _property_unit_label(item),
            "urls": _pending_item_urls(kind, item),
            "active_handymen": (
                HandymanProfile.objects.filter(is_active=True).order_by(
                    "-is_preferred", "full_name"
                )
                if kind == "maintenance"
                else HandymanProfile.objects.none()
            ),
            "whatsapp_api_number": (
                _whatsapp_api_display_number() if kind == "maintenance" else ""
            ),
            "media_reassignment_units": (
                Unit.objects.select_related("property").order_by("property__property_name", "unit_number")
                if kind == "media" else Unit.objects.none()
            ),
        },
    )


@login_required
@require_POST
def save_pending_video_frames(request, pk):
    """Store browser-selected JPEG frames beside a pending WhatsApp video."""
    from PIL import Image, UnidentifiedImageError
    from whatsapp.models import PendingWhatsAppMedia

    video = get_object_or_404(
        PendingWhatsAppMedia.objects.select_related(
            "conversation",
            "original_whatsapp_message",
            "tenant",
            "lease",
            "property",
            "unit",
        ),
        pk=pk,
        status=PendingWhatsAppMedia.STATUS_PENDING,
    )
    if video.processing:
        return JsonResponse(
            {"ok": False, "message": "The WhatsApp video is still downloading."},
            status=409,
        )
    if (video.media_type or "").lower() != "video":
        return JsonResponse(
            {"ok": False, "message": "Photo frames can only be extracted from a video."},
            status=400,
        )
    if not _pending_media_source_exists(video):
        return JsonResponse(
            {"ok": False, "message": "The source video is missing from storage."},
            status=400,
        )

    frames = request.FILES.getlist("frames")
    if not frames:
        return JsonResponse(
            {"ok": False, "message": "Choose at least one video frame."},
            status=400,
        )
    if len(frames) > 24:
        return JsonResponse(
            {"ok": False, "message": "A maximum of 24 photos can be saved at once."},
            status=400,
        )

    for frame in frames:
        if frame.size > 5 * 1024 * 1024:
            return JsonResponse(
                {
                    "ok": False,
                    "message": f"{frame.name}: extracted photo exceeds 5 MiB.",
                },
                status=400,
            )
        try:
            with Image.open(frame) as image:
                image.verify()
            frame.seek(0)
        except (UnidentifiedImageError, OSError, ValueError):
            return JsonResponse(
                {"ok": False, "message": f"{frame.name}: invalid image data."},
                status=400,
            )

    batch_key = video.batch_key or uuid.uuid4()
    created = []
    with transaction.atomic():
        if not video.batch_key:
            video.batch_key = batch_key
            video.save(update_fields=["batch_key", "updated_at"])
        existing_count = PendingWhatsAppMedia.objects.filter(
            batch_key=batch_key,
            ai_notes__contains=VIDEO_FRAME_NOTE_PREFIX,
        ).count()
        for offset, frame in enumerate(frames, start=1):
            sequence = existing_count + offset
            extracted = PendingWhatsAppMedia(
                conversation=video.conversation,
                original_whatsapp_message=video.original_whatsapp_message,
                phone=video.phone,
                original_filename=f"{video.pk}-frame-{sequence:02d}.jpg",
                media_type="image",
                purpose=video.purpose,
                target_kind=video.target_kind,
                batch_key=batch_key,
                tenant=video.tenant,
                lease=video.lease,
                property=video.property,
                unit=video.unit,
                ai_confidence=video.ai_confidence,
                ai_notes=(
                    f"{VIDEO_FRAME_NOTE_PREFIX} Selected from "
                    f"{video.original_filename or 'WhatsApp video'} during admin review."
                ),
                processing=False,
            )
            extracted.file.save(extracted.original_filename, frame, save=False)
            extracted.full_clean()
            extracted.save()
            created.append(extracted.pk)

    return JsonResponse(
        {
            "ok": True,
            "message": f"Saved {len(created)} selected photo(s) from the video.",
            "created_ids": created,
            "redirect_url": reverse(
                "core:pending_approval_detail", args=["media", video.pk]
            ),
        }
    )


@login_required
@require_POST
def retry_pending_media_download(request, pk):
    from whatsapp.models import PendingWhatsAppMedia
    from whatsapp.services.queue import enqueue_pending_media_download

    with transaction.atomic():
        media = get_object_or_404(
            PendingWhatsAppMedia.objects.select_for_update(),
            pk=pk,
            status=PendingWhatsAppMedia.STATUS_PENDING,
        )
        if not media.whatsapp_media_id:
            messages.error(
                request,
                "This item has no WhatsApp media ID and cannot be downloaded again.",
            )
            return redirect("core:pending_approval_detail", kind="media", pk=media.pk)
        if (
            media.processing
            and media.updated_at > timezone.now() - timedelta(minutes=2)
        ):
            messages.info(request, "The media download is already running.")
            return redirect("core:pending_approval_detail", kind="media", pk=media.pk)

        media.processing = True
        retry_note = "Download retry requested."
        if retry_note not in media.ai_notes:
            media.ai_notes = f"{media.ai_notes} {retry_note}".strip()
        media.save(update_fields=["processing", "ai_notes", "updated_at"])
        transaction.on_commit(lambda: enqueue_pending_media_download(media.pk))

    messages.success(request, "Media download restarted. Refresh this page in a moment.")
    return redirect("core:pending_approval_detail", kind="media", pk=media.pk)


@login_required
def pending_family_file(request, pk, field_name):
    from leases.models import PendingLeaseFamilyMemberSubmission

    if field_name not in {"photo", "cnic_front", "cnic_back"}:
        raise Http404("Unknown file field.")
    item = get_object_or_404(PendingLeaseFamilyMemberSubmission, pk=pk)
    file_field = getattr(item, field_name)
    if not file_field:
        raise Http404("File not found.")
    try:
        return FileResponse(file_field.open("rb"), filename=file_field.name.rsplit("/", 1)[-1])
    except FileNotFoundError:
        raise Http404("File is missing from storage.")


def _attach_pending_media_from_core(pending, user):
    from leases.models import LeaseDocument
    from leases.models_lease_photos import LeaseMedia
    from properties.models import PropertyMedia, UnitMedia
    from whatsapp.models import PendingWhatsAppMedia

    gallery_description = "" if (pending.ai_notes or "").startswith(
        VIDEO_FRAME_NOTE_PREFIX
    ) else pending.ai_notes[:300]

    missing_file_message = (
        "The source media file is missing from storage. Restore or re-upload it before approval."
    )
    if pending.processing:
        raise ValueError(
            "This WhatsApp media file is still downloading. Please wait a moment and try approval again."
        )
    if not pending.file or not pending.file.name:
        raise ValueError(missing_file_message)
    try:
        source_exists = pending.file.storage.exists(pending.file.name)
    except Exception as exc:
        raise ValueError(missing_file_message) from exc
    if not source_exists:
        raise ValueError(missing_file_message)
    try:
        with pending.file.storage.open(pending.file.name, "rb") as source_file:
            content = ContentFile(
                source_file.read(),
                name=pending.original_filename or pending.file.name,
            )
    except Exception as exc:
        raise ValueError(missing_file_message) from exc

    destination = None
    try:
        if pending.target_kind == PendingWhatsAppMedia.TARGET_LEASE_PHOTO:
            if not pending.lease_id:
                raise ValueError("Lease Gallery requires a lease target.")
            processing_thumbnail = "lease-media-processing"
            destination = LeaseMedia.objects.create(
                lease=pending.lease,
                file=content,
                thumbnail=processing_thumbnail,
                media_type="image" if pending.media_type == "image" else "video" if pending.media_type == "video" else "file",
                title=pending.original_filename or "WhatsApp lease photo",
                description=gallery_description,
                original_filename=pending.original_filename,
                uploaded_by=user,
            )
            if destination.thumbnail.name == processing_thumbnail:
                destination.thumbnail = None
                destination.save(update_fields=["thumbnail", "updated_at"])
        elif pending.target_kind == PendingWhatsAppMedia.TARGET_LEASE_ESTAMP:
            if not pending.lease_id:
                raise ValueError("E-Stamp Paper requires a confirmed lease target.")
            from leases.services.estamp import normalize_estamp_pdf
            from leases.views_lease_files import _estamp_filename

            try:
                normalized = normalize_estamp_pdf(content)
            except Exception as exc:
                messages_list = getattr(exc, "messages", None)
                raise ValueError(
                    messages_list[0]
                    if messages_list
                    else "The E-Stamp PDF could not be validated."
                ) from exc
            normalized.name = _estamp_filename(pending.lease)
            destination = LeaseDocument.objects.create(
                lease=pending.lease,
                file=normalized,
                original_filename=normalized.name,
                display_name=normalized.name,
                category="estamp_paper",
                description=pending.ai_notes,
                uploaded_by=pending.submitted_by_staff or user,
            )
        elif (
            pending.target_kind == PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT
            or pending.purpose == PendingWhatsAppMedia.PURPOSE_LEASE
        ):
            if not pending.lease_id:
                raise ValueError("Lease Document requires a lease target.")
            destination = LeaseDocument.objects.create(
                lease=pending.lease,
                file=content,
                original_filename=pending.original_filename,
                display_name=pending.original_filename or "WhatsApp lease document",
                category="other",
                description=pending.ai_notes,
                uploaded_by=user,
            )
        elif pending.purpose == PendingWhatsAppMedia.PURPOSE_PROPERTY:
            if not pending.property_id:
                raise ValueError("Property Photo requires a property target.")
            destination = PropertyMedia.objects.create(
                property=pending.property,
                file=content,
                description=gallery_description,
                uploaded_by=user,
                original_filename=pending.original_filename,
            )
        elif pending.purpose == PendingWhatsAppMedia.PURPOSE_UNIT:
            if not pending.unit_id:
                raise ValueError("Unit Photo requires a unit target.")
            destination = UnitMedia.objects.create(
                unit=pending.unit,
                file=content,
                description=gallery_description,
                uploaded_by=user,
                original_filename=pending.original_filename,
            )
        else:
            raise ValueError(
                "Choose Lease Gallery, Lease Document, Property Photo, or Unit Photo before approval."
            )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            "The media could not be saved to the selected destination. Please try again after checking storage."
        ) from exc

    try:
        destination_exists = bool(
            destination
            and destination.file
            and destination.file.name
            and destination.file.storage.exists(destination.file.name)
        )
    except Exception as exc:
        raise ValueError(
            "The media could not be saved to the selected destination. Please try again after checking storage."
        ) from exc
    if not destination_exists:
        raise ValueError(
            "The media could not be saved to the selected destination. Please try again after checking storage."
        )
    return destination


def _pending_media_source_exists(pending):
    if pending.processing:
        raise ValueError(
            "This WhatsApp media file is still downloading. Please wait a moment and try approval again."
        )
    if not pending.file or not pending.file.name:
        return False
    try:
        return pending.file.storage.exists(pending.file.name)
    except Exception as exc:
        raise ValueError(
            "The source media storage could not be checked. Please try approval again."
        ) from exc


def _apply_pending_media_destination(pending, submitted_destination):
    from whatsapp.models import PendingWhatsAppMedia

    if not submitted_destination:
        return
    try:
        target_kind, submitted_id = submitted_destination.split(":", 1)
        submitted_id = int(submitted_id)
    except (TypeError, ValueError):
        raise ValueError("The selected media destination is invalid.")

    destination_config = {
        PendingWhatsAppMedia.TARGET_LEASE_PHOTO: (
            "lease_id",
            PendingWhatsAppMedia.PURPOSE_LEASE,
            "The selected lease target is invalid for this pending media.",
        ),
        PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT: (
            "lease_id",
            PendingWhatsAppMedia.PURPOSE_LEASE,
            "The selected lease target is invalid for this pending media.",
        ),
        PendingWhatsAppMedia.TARGET_LEASE_ESTAMP: (
            "lease_id",
            PendingWhatsAppMedia.PURPOSE_LEASE,
            "The selected lease target is invalid for this pending E-Stamp.",
        ),
        PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO: (
            "property_id",
            PendingWhatsAppMedia.PURPOSE_PROPERTY,
            "The selected property target is invalid for this pending media.",
        ),
        PendingWhatsAppMedia.TARGET_UNIT_PHOTO: (
            "unit_id",
            PendingWhatsAppMedia.PURPOSE_UNIT,
            "The selected unit target is invalid for this pending media.",
        ),
    }
    config = destination_config.get(target_kind)
    if not config:
        raise ValueError("The selected media destination is invalid.")
    relation_field, purpose, error_message = config
    if getattr(pending, relation_field) != submitted_id:
        raise ValueError(error_message)
    pending.target_kind = target_kind
    pending.purpose = purpose


def _reclassify_pending_media_as_payment(pending):
    from whatsapp.models import PendingWhatsAppMedia, PendingWhatsAppPayment
    from whatsapp.services.ai_config import get_whatsapp_ai_config
    from whatsapp.services.media_processor import run_payment_ocr

    if pending.status != PendingWhatsAppMedia.STATUS_PENDING:
        raise ValueError("This media has already been reviewed.")
    ocr_json = run_payment_ocr(pending, get_whatsapp_ai_config())
    lookup = {}
    if pending.original_whatsapp_message_id:
        lookup["original_whatsapp_message_id"] = pending.original_whatsapp_message_id
    elif pending.file and pending.file.name:
        lookup["screenshot"] = pending.file.name
    payment = PendingWhatsAppPayment.objects.filter(
        **lookup,
        rejected=False,
    ).order_by("-created_at").first() if lookup else None
    if payment is None:
        payment = PendingWhatsAppPayment()
    payment.tenant = pending.tenant or getattr(pending.lease, "tenant", None)
    payment.lease = pending.lease
    payment.property = pending.property or getattr(getattr(pending.lease, "unit", None), "property", None)
    payment.unit = pending.unit or getattr(pending.lease, "unit", None)
    payment.phone = pending.phone
    payment.screenshot = pending.file
    payment.ocr_json = json.loads(json.dumps(ocr_json, cls=DjangoJSONEncoder))
    payment.amount = ocr_json.get("amount")
    payment.date = ocr_json.get("date")
    payment.reference = ocr_json.get("reference", "")
    payment.bank_information = ocr_json.get("bank_information") or {}
    payment.ai_confidence = int(ocr_json.get("confidence") or 0)
    payment.ai_notes = "Reclassified from WhatsApp Documents / Media for payment verification."
    payment.original_whatsapp_message = pending.original_whatsapp_message
    payment.conversation = pending.conversation
    payment.status = PendingWhatsAppPayment.STATUS_PENDING
    payment.approved = False
    payment.rejected = False
    payment.save()

    pending.purpose = PendingWhatsAppMedia.PURPOSE_PAYMENT
    pending.target_kind = ""
    pending.ai_confidence = max(pending.ai_confidence or 0, int(ocr_json.get("confidence") or 0))
    pending.ai_notes = "\n".join(
        part for part in (
            pending.ai_notes.strip(),
            f"Reclassified as Payment Receipt; pending payment #{payment.pk} created.",
        ) if part
    )
    pending.save(update_fields=["purpose", "target_kind", "ai_confidence", "ai_notes", "updated_at"])
    return payment


def _reclassify_pending_media_as_maintenance(pending):
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia
    from whatsapp.services.maintenance_ai import (
        _message_text,
        detect_maintenance_issue,
    )

    if pending.status != PendingWhatsAppMedia.STATUS_PENDING:
        raise ValueError("This media has already been reviewed.")

    batch_items = list(
        PendingWhatsAppMedia.objects.select_for_update()
        .filter(
            status=PendingWhatsAppMedia.STATUS_PENDING,
            batch_key=pending.batch_key,
        )
        .order_by("created_at", "pk")
    ) if pending.batch_key else [pending]

    existing = (
        PendingWhatsAppMaintenance.objects.select_for_update()
        .filter(
            status=PendingWhatsAppMaintenance.STATUS_PENDING,
            media__in=batch_items,
        )
        .distinct()
        .first()
    )
    message_text = ""
    if pending.original_whatsapp_message_id:
        message_text = _message_text(
            pending.original_whatsapp_message.payload or {}
        ).strip()
    issue_type, urgency, confidence = detect_maintenance_issue(
        "\n".join(
            part for part in (message_text, pending.ai_notes) if part
        )
    )
    if existing is None:
        lease = pending.lease
        unit = pending.unit or getattr(lease, "unit", None)
        property_obj = pending.property or getattr(unit, "property", None)
        tenant = pending.tenant or getattr(lease, "tenant", None)
        existing = PendingWhatsAppMaintenance.objects.create(
            conversation=pending.conversation,
            original_whatsapp_message=pending.original_whatsapp_message,
            phone=pending.phone,
            tenant=tenant,
            lease=lease,
            property=property_obj,
            unit=unit,
            issue_type=issue_type,
            urgency=urgency,
            description=(
                message_text
                or pending.ai_notes.strip()
                or f"Maintenance attachment: {pending.original_filename or pending.file.name}"
            ),
            ai_confidence=max(pending.ai_confidence or 0, confidence),
            ai_notes=(
                "Moved from WhatsApp Documents / Media for maintenance review."
            ),
        )
    existing.media.add(*batch_items)

    audit_note = f"Moved to pending maintenance #{existing.pk}."
    for media in batch_items:
        media.purpose = PendingWhatsAppMedia.PURPOSE_MAINTENANCE
        media.target_kind = ""
        media.ai_notes = "\n".join(
            part for part in (media.ai_notes.strip(), audit_note) if part
        )
        media.save(
            update_fields=["purpose", "target_kind", "ai_notes", "updated_at"]
        )
    return existing


def _reclassify_pending_payment_as_media(pending, submitted_destination):
    from whatsapp.models import PendingWhatsAppMedia, PendingWhatsAppPayment

    if pending.approved or pending.rejected:
        raise ValueError("This payment has already been reviewed.")
    linked_media = PendingWhatsAppMedia.objects.filter(status=PendingWhatsAppMedia.STATUS_PENDING)
    if pending.original_whatsapp_message_id:
        linked_media = linked_media.filter(original_whatsapp_message_id=pending.original_whatsapp_message_id)
    elif pending.screenshot and pending.screenshot.name:
        linked_media = linked_media.filter(file=pending.screenshot.name)
    else:
        linked_media = linked_media.none()
    media = linked_media.order_by("-created_at").first()
    if media is None:
        if not pending.screenshot or not pending.screenshot.name:
            raise ValueError("This pending payment has no screenshot to reclassify.")
        media = PendingWhatsAppMedia.objects.create(
            conversation=pending.conversation,
            original_whatsapp_message=pending.original_whatsapp_message,
            phone=pending.phone,
            file=pending.screenshot.name,
            original_filename=pending.screenshot.name.rsplit("/", 1)[-1],
            media_type="image",
            tenant=pending.tenant,
            lease=pending.lease,
            property=pending.property,
            unit=pending.unit,
            purpose=PendingWhatsAppMedia.PURPOSE_OTHER,
            ai_notes="Reclassified from a pending WhatsApp payment.",
        )
    _apply_pending_media_destination(media, submitted_destination)
    media.status = PendingWhatsAppMedia.STATUS_PENDING
    media.ai_notes = "\n".join(
        part for part in (
            media.ai_notes.strip(),
            f"Reclassified from pending payment #{pending.pk}.",
        ) if part
    )
    media.save(update_fields=["purpose", "target_kind", "status", "ai_notes", "updated_at"])

    pending.rejected = True
    pending.approved = False
    pending.status = PendingWhatsAppPayment.STATUS_REJECTED
    pending.ai_notes = "\n".join(
        part for part in (
            pending.ai_notes.strip(),
            f"Reclassified to {media.get_target_kind_display()} as pending media #{media.pk}.",
        ) if part
    )
    pending.save(update_fields=["rejected", "approved", "status", "ai_notes", "updated_at"])
    return media


def _approve_pending_payment(pending, user):
    from whatsapp.models import PendingWhatsAppMedia, PendingWhatsAppPayment

    if pending.approved or pending.rejected:
        raise ValueError("This payment has already been reviewed.")
    if not pending.lease_id or not pending.amount:
        raise ValueError("Payment needs a lease and amount before approval.")
    payment = Payment.objects.create(
        lease=pending.lease,
        payment_date=pending.date or timezone.localdate(),
        amount=pending.amount,
        reference_number=pending.reference or "",
        notes=f"Created from WhatsApp pending payment #{pending.pk}. {pending.ai_notes}",
    )
    pending.created_payment = payment
    pending.approved = True
    pending.rejected = False
    pending.status = PendingWhatsAppPayment.STATUS_APPROVED
    pending.approved_by = user
    pending.approved_at = timezone.now()
    pending.save(update_fields=[
        "created_payment", "approved", "rejected", "status", "approved_by", "approved_at", "updated_at"
    ])
    linked_media = PendingWhatsAppMedia.objects.filter(
        status=PendingWhatsAppMedia.STATUS_PENDING,
        purpose=PendingWhatsAppMedia.PURPOSE_PAYMENT,
    )
    if pending.original_whatsapp_message_id:
        linked_media = linked_media.filter(
            original_whatsapp_message_id=pending.original_whatsapp_message_id
        )
    elif pending.screenshot and pending.screenshot.name:
        linked_media = linked_media.filter(file=pending.screenshot.name)
    else:
        linked_media = linked_media.none()
    linked_media.update(
        status=PendingWhatsAppMedia.STATUS_APPROVED,
        approved_by=user,
        approved_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return payment


@transaction.atomic
def _approve_pending_maintenance(pending, user, handyman=None):
    from handyman.services import assign_handyman
    from maintenance.models import MaintenanceRequest, MaintenanceRequestMedia
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia

    if pending.status != PendingWhatsAppMaintenance.STATUS_PENDING:
        raise ValueError("This maintenance submission has already been reviewed.")
    if not pending.unit_id:
        raise ValueError("Maintenance needs a unit before approval.")
    pending_media = list(pending.media.select_related("original_whatsapp_message").order_by("created_at", "pk"))
    for media in pending_media:
        if media.processing:
            raise ValueError(
                f'Maintenance media "{media.original_filename or "file"}" is still downloading. '
                "Please wait a moment and try approval again."
            )
        if not media.file or not media.file.name:
            raise ValueError(
                "A maintenance media file is missing. Restore or re-upload it before approval."
            )
        try:
            source_exists = media.file.storage.exists(media.file.name)
        except Exception as exc:
            raise ValueError(
                "A maintenance media file could not be checked. Please verify media storage and try again."
            ) from exc
        if not source_exists:
            raise ValueError(
                f'Maintenance media "{media.original_filename or media.file.name}" '
                "is missing from storage. Restore or re-upload it before approval."
            )
    ticket = MaintenanceRequest.objects.create(
        lease=pending.lease,
        unit=pending.unit,
        tenant=pending.tenant,
        title=pending.issue_type or "WhatsApp Maintenance",
        description=pending.description,
        source=MaintenanceRequest.SOURCE_MANUAL,
        category=pending.issue_type or "General",
        priority="urgent" if pending.urgency in {"urgent", "emergency"} else "normal",
        created_by=user,
    )
    seen_media_keys = set()
    for media_order, media in enumerate(pending_media, start=1):
        provider_key = (media.whatsapp_media_id or "").strip()
        if provider_key and ("provider", provider_key) in seen_media_keys:
            media.status = PendingWhatsAppMedia.STATUS_APPROVED
            media.approved_by = user
            media.approved_at = timezone.now()
            media.ai_notes = "\n".join(filter(None, [media.ai_notes, "Duplicate provider media ID skipped during maintenance approval."]))
            media.save(update_fields=["status", "approved_by", "approved_at", "ai_notes", "updated_at"])
            continue
        try:
            source_file_size = media.file.size
        except (FileNotFoundError, OSError, ValueError):
            source_file_size = None
        try:
            with media.file.storage.open(media.file.name, "rb") as source_file:
                source_bytes = source_file.read()
                source_checksum = hashlib.sha256(source_bytes).hexdigest()
                checksum_key = ("checksum", source_checksum)
                if not provider_key and checksum_key in seen_media_keys:
                    media.status = PendingWhatsAppMedia.STATUS_APPROVED
                    media.approved_by = user
                    media.approved_at = timezone.now()
                    media.ai_notes = "\n".join(filter(None, [media.ai_notes, "Duplicate media checksum skipped during maintenance approval."]))
                    media.save(update_fields=["status", "approved_by", "approved_at", "ai_notes", "updated_at"])
                    continue
                content = ContentFile(
                    source_bytes,
                    name=media.original_filename or media.file.name,
                )
        except (FileNotFoundError, OSError) as exc:
            raise ValueError(
                f'Maintenance media "{media.original_filename or media.file.name}" '
                "is missing from storage. Restore or re-upload it before approval."
            ) from exc
        seen_media_keys.add(("provider", provider_key) if provider_key else ("checksum", source_checksum))
        MaintenanceRequestMedia.objects.create(
            request=ticket,
            file=content,
            description=media.ai_notes[:255],
            uploaded_by=user,
            original_filename=media.original_filename,
            source_pending_media_id=media.pk,
            source_provider_media_id=media.whatsapp_media_id or "",
            source_whatsapp_message_id=(
                media.original_whatsapp_message.wa_message_id
                if media.original_whatsapp_message_id else ""
            ),
            source_message_timestamp=(
                media.original_whatsapp_message.created_at
                if media.original_whatsapp_message_id else media.created_at
            ),
            source_media_type=media.media_type or "",
            source_file_size=source_file_size,
            source_checksum=source_checksum,
            source_order=media_order,
        )
        media.status = PendingWhatsAppMedia.STATUS_APPROVED
        media.approved_by = user
        media.approved_at = timezone.now()
        media.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    pending.created_request = ticket
    pending.status = PendingWhatsAppMaintenance.STATUS_APPROVED
    pending.approved_by = user
    pending.approved_at = timezone.now()
    pending.save(update_fields=["created_request", "status", "approved_by", "approved_at", "updated_at"])
    assignment = None
    if handyman:
        assignment = assign_handyman(ticket, handyman, assigned_by=user)
    return ticket, assignment


@login_required
@require_POST
def pending_approval_approve(request, kind, pk):
    from leases.models import PendingAgreementApproval
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia

    item = _pending_item_for_kind(kind, pk)
    if not _can_change_pending_approval(request.user, item):
        raise PermissionDenied
    try:
        if kind == "payment" and request.POST.get("approval_action") == "reclassify":
            with transaction.atomic():
                media = _reclassify_pending_payment_as_media(
                    item,
                    request.POST.get("reclassify_destination", ""),
                )
            messages.success(request, "Payment screenshot moved to Documents / Media for approval.")
            return redirect("core:pending_approval_detail", kind="media", pk=media.pk)
        if kind == "lease":
            if item.status != "pending_approval":
                raise ValueError("This lease is not pending approval.")
            item.status = "active"
            item.save(update_fields=["status", "updated_at"])
            ajax_response = _pending_ajax_response(request, "Lease approved and activated.")
            if ajax_response:
                return ajax_response
            messages.success(request, "Lease approved and activated.")
            return redirect("leases:lease_detail", pk=item.pk)
        if kind == "agreement":
            if item.status != PendingAgreementApproval.STATUS_PENDING:
                raise ValueError("This agreement edit is not pending.")
            item.status = PendingAgreementApproval.STATUS_APPROVED
            item.reviewed_by = request.user
            item.reviewed_at = timezone.now()
            item.lease.terms = item.proposed_terms
            item.lease.save(update_fields=["terms", "updated_at"])
            item.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])
            ajax_response = _pending_ajax_response(request, "Agreement edit approved and applied.")
            if ajax_response:
                return ajax_response
            messages.success(request, "Agreement edit approved and applied.")
            return redirect("leases:lease_detail", pk=item.lease_id)
        if kind == "family":
            from leases.views import approve_pending_family_submission
            approve_pending_family_submission(item, request.user)
            ajax_response = _pending_ajax_response(request, "Family member update approved.")
            if ajax_response:
                return ajax_response
            if getattr(item, "action", "") == "remove":
                messages.success(request, "Family member removal approved.")
            else:
                messages.success(request, "Family member approved and added to lease.")
            return redirect("leases:lease_detail", pk=item.lease_id)
        if kind == "police":
            from leases.services.police_verification import approve_police_submission
            approve_police_submission(item, request.user)
            ajax_response = _pending_ajax_response(request, "Police verification approved and attached to the lease.")
            if ajax_response:
                return ajax_response
            messages.success(request, "Police verification approved and attached to the lease.")
            return redirect("leases:lease_detail", pk=item.lease_id)
        if kind == "payment":
            payment = _approve_pending_payment(item, request.user)
            try:
                from whatsapp.services.whatsapp import WhatsAppService

                WhatsAppService(created_by=request.user).send_payment_confirmation(
                    payment,
                    phone_number=item.phone,
                    message=(
                        f"Payment verified. Rs. {payment.amount:,.2f} dated "
                        f"{payment.payment_date.strftime('%d-%m-%Y')} has been approved and posted."
                    ),
                )
            except Exception:
                logger.exception(
                    "Payment #%s was approved, but WhatsApp confirmation failed.",
                    payment.pk,
                )
                messages.warning(
                    request,
                    "Payment was posted, but the WhatsApp confirmation could not be sent.",
                )
            messages.success(request, "WhatsApp payment approved and posted.")
        elif kind == "media":
            if request.POST.get("media_destination") == "maintenance":
                with transaction.atomic():
                    item = (
                        PendingWhatsAppMedia.objects.select_for_update()
                        .select_related(
                            "conversation",
                            "original_whatsapp_message",
                            "lease__tenant",
                            "lease__unit__property",
                            "tenant",
                            "property",
                            "unit",
                        )
                        .get(pk=item.pk)
                    )
                    maintenance = _reclassify_pending_media_as_maintenance(item)
                move_message = (
                    f"Media moved to WhatsApp Maintenance #{maintenance.pk} "
                    "and kept pending for maintenance approval."
                )
                ajax_response = _pending_ajax_response(request, move_message)
                if ajax_response:
                    return ajax_response
                messages.success(request, move_message)
                return redirect(
                    "core:pending_approval_detail",
                    kind="maintenance",
                    pk=maintenance.pk,
                )
            if request.POST.get("media_destination") == "payment_receipt":
                with transaction.atomic():
                    item = PendingWhatsAppMedia.objects.select_for_update().select_related(
                        "conversation", "lease__tenant", "lease__unit__property", "tenant", "property", "unit"
                    ).get(pk=item.pk)
                    payment = _reclassify_pending_media_as_payment(item)
                move_message = "Media moved to WhatsApp Payments for pending bank verification."
                ajax_response = _pending_ajax_response(request, move_message)
                if ajax_response:
                    return ajax_response
                messages.success(request, move_message)
                return redirect("core:pending_approval_detail", kind="payment", pk=payment.pk)
            with transaction.atomic():
                item = PendingWhatsAppMedia.objects.select_for_update().select_related(
                    "lease", "property", "unit"
                ).get(pk=item.pk)
                if item.status != PendingWhatsAppMedia.STATUS_PENDING:
                    raise ValueError("This media has already been reviewed.")
                batch_items = list(
                    PendingWhatsAppMedia.objects.select_for_update().filter(
                        status=PendingWhatsAppMedia.STATUS_PENDING,
                        batch_key=item.batch_key,
                    ).order_by("created_at", "pk")
                ) if item.batch_key else [item]
                explicit_selection = (
                    request.POST.get("media_selection") == "explicit"
                )
                selected_ids = {
                    int(media_id)
                    for media_id in request.POST.getlist("selected_media_ids")
                    if media_id.isdigit()
                }
                batch_ids = {batch_item.pk for batch_item in batch_items}
                if explicit_selection:
                    if not selected_ids:
                        raise ValueError(
                            "Select at least one extracted photo before approval."
                        )
                    if not selected_ids.issubset(batch_ids):
                        raise ValueError("The selected media files are invalid.")
                    approval_items = [
                        batch_item
                        for batch_item in batch_items
                        if batch_item.pk in selected_ids
                    ]
                else:
                    approval_items = batch_items
                approved_count = 0
                missing_count = 0
                reassigned_unit_id = request.POST.get("reassign_unit", "")
                reassigned_unit = None
                if reassigned_unit_id:
                    try:
                        reassigned_unit = Unit.objects.select_related("property").get(pk=reassigned_unit_id)
                    except Unit.DoesNotExist as exc:
                        raise ValueError("The selected property / unit is no longer available.") from exc
                elif not request.POST.get("media_destination", ""):
                    raise ValueError("Choose a media destination or reassign this media to a property / unit.")
                for batch_item in approval_items:
                    if reassigned_unit and reassigned_unit.pk != batch_item.unit_id:
                        old_destination = _property_unit_label(batch_item)
                        batch_item.property = reassigned_unit.property
                        batch_item.unit = reassigned_unit
                        batch_item.lease = None
                        batch_item.tenant = None
                        _apply_pending_media_destination(batch_item, f"unit_photo:{reassigned_unit.pk}")
                        batch_item.ai_notes = "\n".join(part for part in (
                            batch_item.ai_notes.strip(),
                            f"Reassigned by {request.user.get_username()} from {old_destination} to {reassigned_unit.property.property_name} - Unit {reassigned_unit.unit_number}.",
                        ) if part)
                    else:
                        _apply_pending_media_destination(
                            batch_item, request.POST.get("media_destination", "")
                        )
                    if _pending_media_source_exists(batch_item):
                        _attach_pending_media_from_core(batch_item, request.user)
                    else:
                        missing_count += 1
                        audit_note = (
                            "Approved without destination attachment because the source file "
                            f"was missing from storage. Approved by {request.user.get_username()} "
                            f"at {timezone.now().isoformat()}."
                        )
                        batch_item.ai_notes = "\n".join(
                            part for part in (batch_item.ai_notes.strip(), audit_note) if part
                        )
                    batch_item.status = PendingWhatsAppMedia.STATUS_APPROVED
                    batch_item.approved_by = request.user
                    batch_item.approved_at = timezone.now()
                    batch_item.save(update_fields=[
                        "purpose", "target_kind", "tenant", "lease", "property", "unit", "ai_notes", "status", "approved_by", "approved_at", "updated_at"
                    ])
                    approved_count += 1
                if explicit_selection:
                    rejected_at = timezone.now()
                    for batch_item in batch_items:
                        if batch_item.pk in selected_ids:
                            continue
                        batch_item.status = PendingWhatsAppMedia.STATUS_REJECTED
                        batch_item.ai_notes = "\n".join(
                            part
                            for part in (
                                batch_item.ai_notes.strip(),
                                (
                                    "Not selected during extracted-photo approval by "
                                    f"{request.user.get_username()} at {rejected_at.isoformat()}."
                                ),
                            )
                            if part
                        )
                        batch_item.save(
                            update_fields=["status", "ai_notes", "updated_at"]
                        )
            if missing_count:
                approval_message = (
                    f"{approved_count} WhatsApp media file(s) approved. "
                    f"{missing_count} missing source file(s) were marked approved but could not be attached to the destination."
                )
                messages.warning(request, approval_message)
            else:
                approval_message = f"{approved_count} WhatsApp media file(s) approved."
                messages.success(request, approval_message)
            ajax_response = _pending_ajax_response(request, approval_message)
            if ajax_response:
                return ajax_response
        elif kind == "maintenance":
            from handyman.models import HandymanProfile
            from whatsapp.services.whatsapp import WhatsAppService

            notify_mode = (request.POST.get("notify_mode") or "").strip()
            handyman_id = (request.POST.get("handyman") or "").strip()
            if handyman_id and not notify_mode:
                notify_mode = "api"
            if notify_mode in {"api", "manual"} and not handyman_id:
                raise ValueError("Select a handyman before sending the assignment.")
            handyman = None
            if handyman_id:
                handyman = HandymanProfile.objects.filter(
                    pk=handyman_id, is_active=True
                ).first()
                if not handyman:
                    raise ValueError("Select a valid active handyman.")

            ticket, assignment = _approve_pending_maintenance(
                item, request.user, handyman=handyman
            )
            messages.success(request, "Maintenance request approved and created.")
            if assignment:
                messages.success(
                    request, f"Assigned to {assignment.handyman.full_name}."
                )
            if assignment and notify_mode in {"api", "manual"}:
                handyman_phone = assignment.handyman.display_phone
                if not handyman_phone:
                    messages.warning(
                        request,
                        "The handyman was assigned, but no WhatsApp/phone number is saved.",
                    )
                else:
                    assignment_message = _handyman_maintenance_message(
                        request, item, ticket, assignment.handyman
                    )
                    if notify_mode == "manual":
                        normalized_phone = WhatsAppService.normalize_phone_number(
                            handyman_phone
                        )
                        manual_url = f"https://wa.me/{normalized_phone}?text={quote(assignment_message)}"
                        ajax_response = _pending_ajax_response(
                            request,
                            "Maintenance approved and assigned. Opening WhatsApp.",
                            redirect_url=manual_url,
                        )
                        if ajax_response:
                            return ajax_response
                        return redirect(manual_url)
                    try:
                        result = WhatsAppService(created_by=request.user).send_text(
                            handyman_phone,
                            assignment_message,
                            maintenance_request=ticket,
                        )
                    except Exception as exc:
                        result = {"ok": False, "error": str(exc)}
                    if result.get("ok"):
                        sent_count, failed_count = _send_handyman_maintenance_media(
                            request,
                            WhatsAppService(created_by=request.user),
                            handyman_phone,
                            ticket,
                        )
                        assignment.handyman_notified_at = timezone.now()
                        assignment.save(
                            update_fields=["handyman_notified_at", "updated_at"]
                        )
                        messages.success(
                            request,
                            "Assignment sent to the handyman via WhatsApp API. "
                            f"{sent_count} media file(s) sent directly."
                        )
                        if failed_count:
                            messages.warning(
                                request,
                                f"{failed_count} media file(s) could not be sent directly. "
                                "The handyman can still open them from the TMS job detail.",
                            )
                    else:
                        messages.warning(
                            request,
                            "The handyman was assigned, but WhatsApp API sending failed: "
                            + (result.get("error") or "Unknown API error."),
                        )
            ajax_response = _pending_ajax_response(
                request, "Maintenance request approved and created."
            )
            if ajax_response:
                return ajax_response
            return redirect("maintenance:request_detail", pk=ticket.pk)
        else:
            raise Http404("Unknown pending approval type.")
    except ValueError as exc:
        if kind == "maintenance" and hasattr(item, "ai_notes"):
            item.ai_notes = "\n".join(
                part for part in (item.ai_notes.strip(), f"Approval requires attention: {exc}") if part
            )
            item.save(update_fields=["ai_notes", "updated_at"])
        ajax_response = _pending_ajax_response(request, str(exc), status=400)
        if ajax_response:
            return ajax_response
        messages.error(request, str(exc))
        return redirect("core:pending_approval_detail", kind=kind, pk=pk)
    ajax_response = _pending_ajax_response(request, "Pending item approved.")
    if ajax_response:
        return ajax_response
    return embed_redirect(request, "core:pending_approvals")


@login_required
@require_POST
def pending_approval_reject(request, kind, pk):
    from leases.models import PendingAgreementApproval, PendingLeaseFamilyMemberSubmission, PendingPoliceVerificationSubmission
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia, PendingWhatsAppPayment

    item = _pending_item_for_kind(kind, pk)
    if not _can_change_pending_approval(request.user, item):
        raise PermissionDenied
    if kind == "agreement":
        item.status = PendingAgreementApproval.STATUS_REJECTED
        item.reviewed_by = request.user
        item.reviewed_at = timezone.now()
        item.review_notes = request.POST.get("review_notes", "")
        item.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])
    elif kind == "payment":
        item.rejected = True
        item.approved = False
        item.status = PendingWhatsAppPayment.STATUS_REJECTED
        item.save(update_fields=["rejected", "approved", "status", "updated_at"])
        linked_media = PendingWhatsAppMedia.objects.filter(
            status=PendingWhatsAppMedia.STATUS_PENDING,
            purpose=PendingWhatsAppMedia.PURPOSE_PAYMENT,
        )
        if item.original_whatsapp_message_id:
            linked_media = linked_media.filter(
                original_whatsapp_message_id=item.original_whatsapp_message_id
            )
        elif item.screenshot and item.screenshot.name:
            linked_media = linked_media.filter(file=item.screenshot.name)
        else:
            linked_media = linked_media.none()
        linked_media.update(status=PendingWhatsAppMedia.STATUS_REJECTED, updated_at=timezone.now())
    elif kind == "media":
        media_items = PendingWhatsAppMedia.objects.filter(
            status=PendingWhatsAppMedia.STATUS_PENDING,
            batch_key=item.batch_key,
        ) if item.batch_key else PendingWhatsAppMedia.objects.filter(pk=item.pk)
        media_items.update(status=PendingWhatsAppMedia.STATUS_REJECTED, updated_at=timezone.now())
    elif kind == "maintenance":
        item.status = PendingWhatsAppMaintenance.STATUS_REJECTED
        item.save(update_fields=["status", "updated_at"])
    elif kind == "lease":
        item.status = "rejected"
        item.save(update_fields=["status", "updated_at"])
    elif kind == "family":
        item.status = PendingLeaseFamilyMemberSubmission.STATUS_REJECTED
        item.reviewed_by = request.user
        item.reviewed_at = timezone.now()
        item.review_notes = request.POST.get("review_notes", "")
        item.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes", "updated_at"])
    elif kind == "police":
        item.status = PendingPoliceVerificationSubmission.STATUS_REJECTED
        item.reviewed_by = request.user
        item.reviewed_at = timezone.now()
        item.notes = "\n".join(part for part in [item.notes, request.POST.get("review_notes", "")] if part).strip()
        item.save(update_fields=["status", "reviewed_by", "reviewed_at", "notes"])
    else:
        raise Http404("Unknown pending approval type.")
    messages.success(request, "Pending item rejected.")
    ajax_response = _pending_ajax_response(request, "Pending item rejected.")
    if ajax_response:
        return ajax_response
    return embed_redirect(request, "core:pending_approvals")


def _annotate_dashboard_lease_financials(queryset):
    money_field = DecimalField(max_digits=12, decimal_places=2)
    zero = Value(Decimal("0.00"), output_field=money_field)
    today = timezone.localdate()

    active_history_monthly_payment = (
        LeaseRenewal.objects.filter(
            lease_id=OuterRef("pk"),
            start_date__lte=today,
            end_date__gte=today,
        )
        .annotate(
            total=(
                Coalesce(F("monthly_rent"), zero)
                + Coalesce(F("society_maintenance"), zero)
                + Coalesce(F("water_charges"), zero)
                + Coalesce(F("internet_charges"), zero)
            )
        )
        .order_by("-renewal_number", "-id")
        .values("total")[:1]
    )

    invoice_total = (
        Invoice.objects.filter(lease_id=OuterRef("pk"))
        .values("lease_id")
        .annotate(total=Coalesce(Sum("amount"), zero))
        .values("total")[:1]
    )

    payment_total = (
        Payment.objects.filter(lease_id=OuterRef("pk"))
        .values("lease_id")
        .annotate(
            total=Coalesce(
                Sum(
                    Case(
                        When(
                            detail__isnull=False,
                            then=F("detail__lease_amount"),
                        ),
                        default=F("amount"),
                        output_field=money_field,
                    )
                ),
                zero,
            )
        )
        .values("total")[:1]
    )

    def security_total(tx_type):
        return (
            SecurityDepositTransaction.objects.filter(
                lease_id=OuterRef("pk"),
                type=tx_type,
            )
            .values("lease_id")
            .annotate(total=Coalesce(Sum("amount"), zero))
            .values("total")[:1]
        )

    return (
        queryset.annotate(
            invoice_total=Coalesce(Subquery(invoice_total, output_field=money_field), zero),
            payment_total=Coalesce(Subquery(payment_total, output_field=money_field), zero),
            security_paid_total=Coalesce(
                Subquery(security_total("PAYMENT"), output_field=money_field),
                zero,
            ),
        )
        .annotate(
            list_balance=F("invoice_total") - F("payment_total"),
            list_security_due=Greatest(
                Coalesce(F("security_deposit"), zero)
                - F("security_paid_total"),
                zero,
                output_field=money_field,
            ),
            list_monthly_payment=Coalesce(
                Subquery(active_history_monthly_payment, output_field=money_field),
                Coalesce(F("monthly_rent"), zero)
                + Coalesce(F("society_maintenance"), zero)
                + Coalesce(F("water_charges"), zero)
                + Coalesce(F("internet_charges"), zero),
                output_field=money_field,
            ),
        )
    )


@login_required
def dashboard(request):
    today = timezone.now().date()
    thirty_days_ago = today - timedelta(days=30)
    lease_ending_cutoff = today + timedelta(days=40)
    recently_ended_cutoff = today - timedelta(days=40)

    total_properties = Property.objects.count()
    total_units = Unit.objects.count()
    active_lease = Lease.objects.filter(
        unit_id=models.OuterRef("pk"),
        start_date__lte=today,
        end_date__gte=today,
    ).exclude(status__in=["ended", "terminated"])
    active_lease_history = LeaseRenewal.objects.filter(
        lease__unit_id=models.OuterRef("pk"),
        start_date__lte=today,
        end_date__gte=today,
    )
    units_with_occupancy = Unit.objects.annotate(
        has_active_lease=models.Exists(active_lease),
        has_active_lease_history=models.Exists(active_lease_history),
    )
    occupied_units = units_with_occupancy.filter(
        models.Q(has_active_lease=True) | models.Q(has_active_lease_history=True)
    ).count()
    vacant_units = (
        units_with_occupancy.select_related("property", "interest_type")
        .filter(has_active_lease=False, has_active_lease_history=False)
        .exclude(status="maintenance")
        .order_by("property__property_name", "unit_number")[:10]
    )
    vacancy_rate = ((total_units - occupied_units) /
                    total_units * 100) if total_units > 0 else 0

    total_tenants = Tenant.objects.filter(is_active=True).count()

    total_rent = Invoice.objects.filter(
        description__contains='Monthly Rent',
        issue_date__range=[thirty_days_ago, today]
    ).aggregate(total=models.Sum('amount'))['total'] or 0

    total_payments = Payment.objects.filter(
        payment_date__range=[thirty_days_ago, today]
    ).aggregate(total=models.Sum('amount'))['total'] or 0

    total_expenses = Expense.objects.filter(
        date__range=[thirty_days_ago, today]
    ).aggregate(total=models.Sum('amount'))['total'] or 0

    recent_payments = list(
        Payment.objects.select_related(
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
        )
        .order_by("-payment_date", "-id")[:5]
    )
    recent_lease_ids = [
        payment.lease_id for payment in recent_payments if payment.lease_id
    ]

    invoice_totals = {
        row["lease_id"]: row["total"] or 0
        for row in (
            Invoice.objects.filter(lease_id__in=recent_lease_ids)
            .values("lease_id")
            .annotate(total=models.Sum("amount"))
        )
    }
    payment_totals = {
        row["lease_id"]: row["total"] or 0
        for row in (
            Payment.objects.filter(lease_id__in=recent_lease_ids)
            .values("lease_id")
            .annotate(total=models.Sum("amount"))
        )
    }

    for payment in recent_payments:
        payment.dashboard_balance = (
            invoice_totals.get(payment.lease_id, 0)
            - payment_totals.get(payment.lease_id, 0)
        )

    upcoming_invoices = (
        Invoice.objects.select_related(
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
        )
        .filter(due_date__gte=today, status__in=["unpaid", "partially_paid"])
        .order_by("due_date", "id")[:5]
    )

    meter_online_minutes = online_threshold_minutes()
    meter_offline_cutoff = timezone.now() - timedelta(minutes=meter_online_minutes)
    offline_meter_readings = (
        LiveReading.objects.select_related(
            "meter",
            "meter__unit",
            "meter__unit__property",
        )
        .filter(
            meter__is_active=True,
            ts__lt=meter_offline_cutoff,
        )
        .order_by("ts", "meter__unit__property__property_name", "meter__unit__unit_number")[:50]
    )

    dashboard_lease_base = Lease.objects.select_related(
        "tenant",
        "unit",
        "unit__property",
    ).only(
        "id",
        "tenant_id",
        "unit_id",
        "start_date",
        "end_date",
        "monthly_rent",
        "society_maintenance",
        "water_charges",
        "internet_charges",
        "security_deposit",
        "status",
        "tenant__id",
        "tenant__first_name",
        "tenant__last_name",
        "tenant__phone",
        "unit__id",
        "unit__property_id",
        "unit__unit_number",
        "unit__property__id",
        "unit__property__property_name",
    )
    dashboard_leases = _annotate_dashboard_lease_financials(dashboard_lease_base)

    ending_soon_leases = (
        dashboard_leases.filter(
            models.Q(status="active", end_date__lte=lease_ending_cutoff)
            | models.Q(status__in=["ended", "inactive"], end_date__gte=recently_ended_cutoff)
        )
        .order_by("end_date", "unit__property__property_name", "unit__unit_number")[:10]
    )

    lease_balances = (
        dashboard_leases.filter(list_balance__gt=0)
        .order_by("-list_balance", "unit__property__property_name", "unit__unit_number")[:10]
    )

    recent_expenses = (
        Expense.objects.select_related("property", "unit", "category")
        .prefetch_related("receipts", "distributions__unit")
        .order_by("-date", "-pk")[:10]
    )

    context = {
        'total_properties': total_properties,
        'TODAY': today,
        'total_units': total_units,
        'occupied_units': occupied_units,
        'vacancy_rate': round(vacancy_rate, 2),
        'vacant_units': vacant_units,
        'total_tenants': total_tenants,
        'total_rent': total_rent,
        'total_payments': total_payments,
        'total_expenses': total_expenses,
        'net_income': total_payments - total_expenses,
        'recent_payments': recent_payments,
        'recent_invoices': Invoice.objects.order_by('-issue_date')[:5],
        'upcoming_invoices': upcoming_invoices,
        'offline_meter_readings': offline_meter_readings,
        'meter_online_minutes': meter_online_minutes,
        'ending_soon_leases': ending_soon_leases,
        'recent_expenses': recent_expenses,
        'lease_balances': lease_balances,
    }
    return render(request, 'dashboard.html', context)


class SettingsView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    # <— moved from tms_config/...
    template_name = "core/settings.html"
    form_class = GlobalSettingsForm
    success_url = reverse_lazy("core:settings")          # <— update namespace

    def test_func(self):
        return self.request.user.is_staff or self.request.user.is_superuser

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        # If you use django-solo, keep get_solo(); otherwise fallback to first-or-create:
        try:
            instance = GlobalSettings.get_solo()
        except AttributeError:
            instance, _ = GlobalSettings.objects.get_or_create(pk=1)
        kw["instance"] = instance
        return kw

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Settings saved.")
        return super().form_valid(form)
# core/views.py
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.utils.text import slugify
from .models import PaymentMethod


@require_POST
def payment_method_quick_add(request):
    """
    Quick add a payment method.
    Expects 'name' in POST, returns JSON {id, name}.
    """
    name = (request.POST.get('name') or '').strip()
    if not name:
        return HttpResponseBadRequest("Missing name")

    code = slugify(name) or 'method'
    # ensure unique code
    base_code = code
    i = 1
    while PaymentMethod.objects.filter(code=code).exists():
        i += 1
        code = f"{base_code}-{i}"

    pm = PaymentMethod.objects.create(
        name=name,
        code=code,
        is_active=True,
        sort_order=50,  # default
    )
    return JsonResponse({
        "id": pm.id,
        "name": pm.name,
    })


@require_POST
def payment_method_quick_edit(request):
    """
    Quick edit the name of an existing payment method.
    Expects 'id' and 'name' in POST.
    """
    try:
        pm_id = int(request.POST.get('id'))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid id")

    name = (request.POST.get('name') or '').strip()
    if not name:
        return HttpResponseBadRequest("Missing name")

    try:
        pm = PaymentMethod.objects.get(pk=pm_id)
    except PaymentMethod.DoesNotExist:
        return HttpResponseBadRequest("Payment method not found")

    pm.name = name
    pm.save(update_fields=['name'])

    return JsonResponse({
        "id": pm.id,
        "name": pm.name,
    })
# core/views.py

from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from .models import PaymentMethod


def payment_method_get(request, pk):
    pm = get_object_or_404(PaymentMethod, pk=pk)
    return JsonResponse({
        "id": pm.id,
        "name": pm.name,
        "code": pm.code,
        "sort_order": pm.sort_order,
        "is_active": pm.is_active,
    })


@require_POST
def payment_method_toggle(request, pk):
    pm = get_object_or_404(PaymentMethod, pk=pk)
    pm.is_active = not pm.is_active
    pm.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def payment_method_save(request):
    pm_id = request.POST.get("id")
    name = request.POST.get("name", "").strip()
    code = request.POST.get("code", "").strip() or slugify(name)
    sort_order = int(request.POST.get("sort_order", "50"))
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")

    if pm_id:
        pm = get_object_or_404(PaymentMethod, pk=pm_id)
    else:
        pm = PaymentMethod()

    pm.name = name
    pm.code = code
    pm.sort_order = sort_order
    pm.is_active = is_active
    pm.save()

    return JsonResponse({"ok": True})

# core/views.py
from django.shortcuts import render, redirect
from django.contrib import messages

from .models import GlobalSettings, PaymentMethod
from .forms import GlobalSettingsForm
from leases.models import LeaseDocumentCategory, LeaseRelationshipType
from django.conf import settings
# core/views.py
from django.views.generic import FormView
from django.urls import reverse_lazy
from django.contrib import messages
from django.core.cache import cache

from .models import GlobalSettings, PaymentMethod
from .forms import GlobalSettingsForm


class SettingsView(FormView):
    template_name = "core/settings.html"
    form_class = GlobalSettingsForm
    success_url = reverse_lazy("core:settings")

    def get_form_kwargs(self):
        """
        Use the singleton GlobalSettings instance.
        """
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = GlobalSettings.get_solo()
        return kwargs

    def form_valid(self, form):
        form.save()
        cache.delete("core.global_settings")
        cache.delete("core.enable_debug_toolbar")
        messages.success(self.request, "Settings saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        """
        Add payment_methods so settings.html can render the list.
        """
        ctx = super().get_context_data(**kwargs)
        form = ctx.get("form")
        if form:
            ctx["settings_field_groups"] = [
                (title, icon, [form[name] for name in names if name in form.fields])
                for title, icon, names in form.FIELD_GROUPS
            ]
        ctx["payment_methods"] = PaymentMethod.objects.order_by(
            "sort_order", "name"
        )
        building_types = list(BuildingType.objects.order_by("sort_order", "name"))
        ctx["building_types"] = building_types
        ctx["move_out_charge_building_types"] = [
            building_type for building_type in building_types if building_type.is_active
        ]
        doc_cat_sort = self.request.GET.get("doc_cat_sort")
        ctx["doc_cat_sort"] = doc_cat_sort
        doc_cat_ordering = ("name", "sort_order") if doc_cat_sort == "name" else ("sort_order", "name")
        ctx["lease_document_categories"] = LeaseDocumentCategory.objects.order_by(*doc_cat_ordering)
        ctx["lease_relationship_types"] = LeaseRelationshipType.objects.order_by("name")
        ctx["tenant_income_brackets"] = TenantIncomeBracket.objects.order_by(
            "sort_order", "name"
        )
        ctx["tenant_occupation_options"] = TenantOccupationOption.objects.order_by(
            "sort_order", "name"
        )
        try:
            from whatsapp.services.ai_config import get_whatsapp_ai_config
            from whatsapp.services.whatsapp import WhatsAppService

            ai_config = get_whatsapp_ai_config()
            whatsapp_config = WhatsAppService().configuration_status()
        except Exception:
            ai_config = None
            whatsapp_config = {}
        ctx["whatsapp_ai_status"] = {
            "openai_key_configured": bool(getattr(settings, "OPENAI_API_KEY", "")),
            "whatsapp_token_configured": bool(getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")),
            "whatsapp_api_ok": bool(whatsapp_config.get("ok")),
            "whatsapp_missing": whatsapp_config.get("missing", []),
            "celery_broker": getattr(settings, "CELERY_BROKER_URL", ""),
            "runtime": "Celery" if ai_config and ai_config.use_celery else "Local thread fallback",
            "provider": getattr(ai_config, "provider", ""),
            "ocr_provider": getattr(ai_config, "ocr_provider", ""),
        }
        return ctx


@login_required
@require_POST
def unit_move_out_charge_update(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({"ok": False, "error": "Staff access required."}, status=403)
    unit = get_object_or_404(Unit, pk=pk)
    try:
        inspection_amount = Decimal(
            (request.POST.get("inspection_incomplete_charge") or "0").replace(",", "")
        )
        key_amount = Decimal(
            (request.POST.get("key_card_not_returned_charge") or "0").replace(",", "")
        )
    except (ArithmeticError, ValueError):
        return JsonResponse({"ok": False, "error": "Enter valid charge amounts."}, status=400)
    if inspection_amount < 0 or key_amount < 0:
        return JsonResponse({"ok": False, "error": "Charge amounts cannot be negative."}, status=400)
    unit.inspection_incomplete_charge = inspection_amount
    unit.key_card_not_returned_charge = key_amount
    unit.save(update_fields=["inspection_incomplete_charge", "key_card_not_returned_charge"])
    return JsonResponse({"ok": True})


@login_required
@require_POST
@transaction.atomic
def building_type_move_out_charge_update(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({"ok": False, "error": "Staff access required."}, status=403)
    building_type = get_object_or_404(BuildingType, pk=pk)
    try:
        inspection_amount = Decimal(
            (request.POST.get("inspection_incomplete_charge") or "0").replace(",", "")
        )
        key_amount = Decimal(
            (request.POST.get("key_card_not_returned_charge") or "0").replace(",", "")
        )
    except (ArithmeticError, ValueError):
        return JsonResponse({"ok": False, "error": "Enter valid charge amounts."}, status=400)
    if inspection_amount < 0 or key_amount < 0:
        return JsonResponse({"ok": False, "error": "Charge amounts cannot be negative."}, status=400)
    building_type.inspection_incomplete_charge = inspection_amount
    building_type.key_card_not_returned_charge = key_amount
    building_type.save(
        update_fields=[
            "inspection_incomplete_charge",
            "key_card_not_returned_charge",
        ]
    )
    lead_interest = TenantInterestType.objects.filter(building_type=building_type).first()
    if lead_interest:
        lead_interest.inspection_incomplete_charge = inspection_amount
        lead_interest.key_card_not_returned_charge = key_amount
        lead_interest.save(
            update_fields=[
                "inspection_incomplete_charge",
                "key_card_not_returned_charge",
            ]
        )
    return JsonResponse({"ok": True, "name": building_type.name})


TENANT_REFERENCE_MODELS = {
    "income": TenantIncomeBracket,
    "occupation": TenantOccupationOption,
}


def _tenant_reference_model(kind):
    model = TENANT_REFERENCE_MODELS.get(kind)
    if model is None:
        raise Http404("Unknown tenant reference type")
    return model


def _require_settings_staff(request):
    return request.user.is_staff or request.user.is_superuser


@login_required
@require_POST
def tenant_reference_create(request, kind):
    if not _require_settings_staff(request):
        return JsonResponse({"ok": False, "error": "Staff access required."}, status=403)
    model = _tenant_reference_model(kind)
    name = " ".join((request.POST.get("name") or "").strip().split())
    try:
        sort_order = max(int(request.POST.get("sort_order") or 50), 0)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Enter a valid sort order."}, status=400)
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
    if model.objects.filter(name__iexact=name).exists():
        return JsonResponse({"ok": False, "error": "That value already exists."}, status=400)
    item = model.objects.create(
        name=name,
        sort_order=sort_order,
        is_active=request.POST.get("is_active", "1") == "1",
    )
    return JsonResponse({"ok": True, "id": item.pk})


@login_required
@require_POST
def tenant_reference_inline_update(request, kind, pk):
    if not _require_settings_staff(request):
        return JsonResponse({"ok": False, "error": "Staff access required."}, status=403)
    model = _tenant_reference_model(kind)
    item = get_object_or_404(model, pk=pk)
    field = request.POST.get("field")
    value = request.POST.get("value", "")
    if field == "name":
        value = " ".join(value.strip().split())
        if not value:
            return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
        if model.objects.filter(name__iexact=value).exclude(pk=item.pk).exists():
            return JsonResponse({"ok": False, "error": "That value already exists."}, status=400)
    elif field == "sort_order":
        try:
            value = max(int(value), 0)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Enter a valid sort order."}, status=400)
    elif field == "is_active":
        value = str(value).lower() in {"1", "true", "yes", "on"}
    else:
        return JsonResponse({"ok": False, "error": "Unknown field."}, status=400)
    setattr(item, field, value)
    item.save(update_fields=[field])
    return JsonResponse({"ok": True, "value": getattr(item, field)})


@login_required
@require_POST
def tenant_reference_delete(request, kind, pk):
    if not _require_settings_staff(request):
        return JsonResponse({"ok": False, "error": "Staff access required."}, status=403)
    model = _tenant_reference_model(kind)
    item = get_object_or_404(model, pk=pk)
    item.delete()
    return JsonResponse({"ok": True})


@login_required
def building_type_get(request, pk):
    building_type = get_object_or_404(BuildingType, pk=pk)
    return JsonResponse(
        {
            "id": building_type.id,
            "name": building_type.name,
            "code": building_type.code,
            "sort_order": building_type.sort_order,
            "is_active": building_type.is_active,
        }
    )


@login_required
@require_POST
@transaction.atomic
def building_type_toggle(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({"ok": False, "error": "Staff access required."}, status=403)
    building_type = get_object_or_404(BuildingType, pk=pk)
    building_type.is_active = not building_type.is_active
    building_type.save(update_fields=["is_active"])
    TenantInterestType.objects.filter(building_type=building_type).update(
        is_active=building_type.is_active
    )
    return JsonResponse({"ok": True, "is_active": building_type.is_active})


@login_required
@require_POST
@transaction.atomic
def building_type_save(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({"ok": False, "error": "Staff access required."}, status=403)
    building_type_id = request.POST.get("id")
    name = (request.POST.get("name") or "").strip()
    code = (request.POST.get("code") or "").strip() or slugify(name)
    try:
        sort_order = int(request.POST.get("sort_order", "50"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Enter a valid sort order."}, status=400)
    if not name or not code:
        return JsonResponse({"ok": False, "error": "Name and code are required."}, status=400)
    duplicate = BuildingType.objects.filter(models.Q(name__iexact=name) | models.Q(code=code))
    if building_type_id:
        duplicate = duplicate.exclude(pk=building_type_id)
    if duplicate.exists():
        return JsonResponse({"ok": False, "error": "Building type name or code already exists."}, status=400)
    building_type = (
        get_object_or_404(BuildingType, pk=building_type_id)
        if building_type_id
        else BuildingType()
    )
    lead_interest = (
        TenantInterestType.objects.filter(building_type=building_type).first()
        if building_type.pk
        else None
    )
    if lead_interest is None:
        lead_interest = TenantInterestType.objects.filter(code=code).first()
    building_type.name = name
    building_type.code = code
    building_type.sort_order = max(sort_order, 0)
    building_type.is_active = request.POST.get("is_active", "1") == "1"
    building_type.save()
    lead_interest = lead_interest or TenantInterestType()
    lead_interest.building_type = building_type
    lead_interest.name = building_type.name
    lead_interest.code = building_type.code
    lead_interest.sort_order = building_type.sort_order
    lead_interest.is_active = building_type.is_active
    lead_interest.inspection_incomplete_charge = building_type.inspection_incomplete_charge
    lead_interest.key_card_not_returned_charge = building_type.key_card_not_returned_charge
    lead_interest.save()
    return JsonResponse({"ok": True, "id": building_type.pk})


@login_required
@require_POST
def test_whatsapp_pending_request_alert(request):
    config = GlobalSettings.get_solo()
    raw_numbers = (
        request.POST.get("whatsapp_pending_request_staff_numbers")
        or config.whatsapp_pending_request_staff_numbers
        or ""
    )
    numbers = _split_whatsapp_staff_numbers(raw_numbers)
    if not numbers:
        messages.error(request, "Add at least one pending request staff WhatsApp number before testing.")
        return redirect(f"{reverse('core:settings')}#settings-group-whatsapp-twilio")

    from whatsapp.services.whatsapp import WhatsAppService

    service = WhatsAppService(created_by=request.user)
    sent = 0
    failed = []
    body = (
        "Test WhatsApp pending request alert from TMS.\n\n"
        "If you received this, pending request notifications are configured."
    )
    for number in numbers:
        result = service.send_text(number, body)
        if result.get("ok"):
            sent += 1
        else:
            failed.append(f"{number}: {result.get('error') or 'failed'}")

    if sent:
        messages.success(request, f"Test pending request alert sent to {sent} number(s).")
    if failed:
        messages.error(request, "Some test alerts failed: " + " | ".join(failed[:3]))
    return redirect(f"{reverse('core:settings')}#settings-group-whatsapp-twilio")


def _split_whatsapp_staff_numbers(raw_numbers):
    cleaned = str(raw_numbers or "").replace(";", ",").replace("\n", ",")
    numbers = []
    seen = set()
    for item in cleaned.split(","):
        number = item.strip()
        if number and number not in seen:
            seen.add(number)
            numbers.append(number)
    return numbers


def lease_document_category_get(request, pk):
    category = get_object_or_404(LeaseDocumentCategory, pk=pk)
    return JsonResponse({
        "id": category.id,
        "name": category.name,
        "code": category.code,
        "sort_order": category.sort_order,
        "is_active": category.is_active,
    })


@require_POST
def lease_document_category_toggle(request, pk):
    category = get_object_or_404(LeaseDocumentCategory, pk=pk)
    category.is_active = not category.is_active
    category.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def lease_document_category_save(request):
    category_id = request.POST.get("id")
    name = (request.POST.get("name") or "").strip()
    code = (request.POST.get("code") or "").strip() or slugify(name)
    try:
        sort_order = int(request.POST.get("sort_order", "50"))
    except ValueError:
        sort_order = 50
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")
    if not code:
        return HttpResponseBadRequest("Code required")

    if category_id:
        category = get_object_or_404(LeaseDocumentCategory, pk=category_id)
    else:
        category = LeaseDocumentCategory()

    category.name = name
    category.code = code
    category.sort_order = sort_order
    category.is_active = is_active
    category.save()
    return JsonResponse({"ok": True})


@require_POST
def lease_document_category_inline_update(request, pk):
    category = get_object_or_404(LeaseDocumentCategory, pk=pk)
    field = request.POST.get("field")
    value = (request.POST.get("value") or "").strip()
    if field == "name":
        if not value:
            return HttpResponseBadRequest("Name required")
        category.name = value
    elif field == "code":
        if not value:
            return HttpResponseBadRequest("Code required")
        category.code = value
    elif field == "sort_order":
        try:
            category.sort_order = int(value or 0)
        except ValueError:
            return HttpResponseBadRequest("Invalid sort order")
    elif field == "is_active":
        category.is_active = value in ("1", "true", "on", "yes")
    else:
        return HttpResponseBadRequest("Invalid field")
    category.save(update_fields=[field])
    return JsonResponse({"ok": True})


def tenant_interest_type_get(request, pk):
    interest_type = get_object_or_404(TenantInterestType, pk=pk)
    return JsonResponse({
        "id": interest_type.id,
        "name": interest_type.name,
        "code": interest_type.code,
        "sort_order": interest_type.sort_order,
        "is_active": interest_type.is_active,
    })


@require_POST
def tenant_interest_type_toggle(request, pk):
    interest_type = get_object_or_404(TenantInterestType, pk=pk)
    interest_type.is_active = not interest_type.is_active
    interest_type.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def tenant_interest_type_save(request):
    interest_id = request.POST.get("id")
    name = (request.POST.get("name") or "").strip()
    code = (request.POST.get("code") or "").strip() or slugify(name)
    try:
        sort_order = int(request.POST.get("sort_order", "50"))
    except ValueError:
        sort_order = 50
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")
    if not code:
        return HttpResponseBadRequest("Code required")

    if interest_id:
        interest_type = get_object_or_404(TenantInterestType, pk=interest_id)
    else:
        interest_type = TenantInterestType()

    interest_type.name = name
    interest_type.code = code
    interest_type.sort_order = sort_order
    interest_type.is_active = is_active
    interest_type.save()
    return JsonResponse({"ok": True})


def lease_relationship_type_get(request, pk):
    relationship_type = get_object_or_404(LeaseRelationshipType, pk=pk)
    return JsonResponse({
        "id": relationship_type.id,
        "name": relationship_type.name,
        "code": relationship_type.code,
        "sort_order": relationship_type.sort_order,
        "is_active": relationship_type.is_active,
    })


@require_POST
def lease_relationship_type_toggle(request, pk):
    relationship_type = get_object_or_404(LeaseRelationshipType, pk=pk)
    relationship_type.is_active = not relationship_type.is_active
    relationship_type.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def lease_relationship_type_save(request):
    relationship_id = request.POST.get("id")
    name = (request.POST.get("name") or "").strip()
    code = (request.POST.get("code") or "").strip() or slugify(name)
    try:
        sort_order = int(request.POST.get("sort_order", "50"))
    except ValueError:
        sort_order = 50
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")
    if not code:
        return HttpResponseBadRequest("Code required")

    if relationship_id:
        relationship_type = get_object_or_404(LeaseRelationshipType, pk=relationship_id)
    else:
        relationship_type = LeaseRelationshipType()

    relationship_type.name = name
    relationship_type.code = code
    relationship_type.sort_order = sort_order
    relationship_type.is_active = is_active
    relationship_type.save()
    return JsonResponse({"ok": True})


@require_POST
def lease_relationship_type_inline_update(request, pk):
    relationship_type = get_object_or_404(LeaseRelationshipType, pk=pk)
    field = request.POST.get("field")
    value = (request.POST.get("value") or "").strip()
    if field == "name":
        if not value:
            return HttpResponseBadRequest("Name required")
        relationship_type.name = value
    elif field == "code":
        if not value:
            return HttpResponseBadRequest("Code required")
        relationship_type.code = value
    elif field == "is_active":
        relationship_type.is_active = value in ("1", "true", "on", "yes")
    else:
        return HttpResponseBadRequest("Invalid field")
    relationship_type.save(update_fields=[field])
    return JsonResponse({"ok": True})


from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from .forms import BackupRestoreForm, BackupSettingsForm, BackupUploadForm
from .backup_utils import (
    choices_for,
    create_code_backup,
    create_db_backup,
    create_full_backup,
    create_media_backup,
    backup_storage_summary,
    delete_backup,
    detect_uploaded_backup_type,
    list_backups,
    load_backup_settings,
    prune_old_backups,
    protected_backup_ids,
    purge_old_backups,
    restore_database,
    restore_full,
    restore_media,
    save_backup_settings,
    save_uploaded_backup,
)


class BackupCenterView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = "core/backup_center.html"

    def test_func(self):
        return self.request.user.is_superuser

    def _context(self, settings_form=None):
        config = load_backup_settings()
        backups = list_backups(config)
        for serial_number, backup in enumerate(backups, start=1):
            backup.serial_number = serial_number
        keep_count = max(int(config.get("retention_count") or 3), 1)
        protected_ids = protected_backup_ids(backups, keep_count=keep_count)
        for backup in backups:
            backup.is_protected = backup.id in protected_ids
        selected_backup = (
            self.request.GET.get("selected_backup")
            or self.request.GET.get("selected_db")
            or self.request.GET.get("selected_media")
            or self.request.GET.get("selected_full")
        )
        restore_choices = [("", "----------")]
        restore_choices.extend(
            (
                backup.id,
                f"S.N {backup.serial_number} · {backup.get_backup_type_display()} · {backup.name} "
                f"({backup.created_at:%Y-%m-%d %H:%M})",
            )
            for backup in backups
            if backup.file_exists and backup.backup_type in {"db", "media", "full"}
        )
        return {
            "backup_settings_form": settings_form or BackupSettingsForm(initial=config),
            "restore_form": BackupRestoreForm(
                backup_choices=restore_choices,
                initial={"backup_id": selected_backup},
            ),
            "backup_upload_form": BackupUploadForm(),
            "backups": backups,
            "backup_storage": backup_storage_summary(config),
            "backup_help_modals": [
                (
                    "retentionHelpModal",
                    "Retention Count",
                    "How many newest backup files to keep overall. Automatic deletion and the Purge button both use this number.",
                ),
                (
                    "mysqldumpHelpModal",
                    "mysqldump Path",
                    "The server executable used to export MySQL database backups. Use mysqldump when it is available on PATH, or provide its absolute executable path.",
                ),
                (
                    "mysqlHelpModal",
                    "mysql Path",
                    "The server executable used to import SQL backups during database restore. Use mysql when it is available on PATH, or provide its absolute executable path.",
                ),
            ],
            "fresh_reset_scope": {
                "profile_name": "tms_safe",
                "profile_description": "Fresh reset is disabled until a TMS-specific reset profile is configured.",
                "wipe_total_rows": 0,
                "keep_total_rows": 0,
            },
        }

    def get(self, request):
        return render(request, self.template_name, self._context())

    def post(self, request):
        action = request.POST.get("action")
        config = load_backup_settings()
        try:
            if action == "save_backup_settings":
                form = BackupSettingsForm(request.POST)
                if form.is_valid():
                    save_backup_settings(form.cleaned_data)
                    messages.success(request, "Backup settings saved.")
                    return embed_redirect(request, "core:backup_center")
                return render(request, self.template_name, self._context(settings_form=form))

            if action == "upload_backup":
                form = BackupUploadForm(request.POST, request.FILES)
                if form.is_valid():
                    backup_type = detect_uploaded_backup_type(form.cleaned_data["backup_file"])
                    uploaded = save_uploaded_backup(
                        config,
                        backup_type,
                        form.cleaned_data["backup_file"],
                    )
                    messages.success(
                        request,
                        f"{backup_type.title()} backup detected and uploaded: {uploaded.name}",
                    )
                    return embed_redirect(
                        request,
                        f"{reverse('core:backup_center')}?"
                        f"selected_backup={quote(uploaded.name, safe='')}#restore-backup"
                    )
                else:
                    messages.error(request, "Upload failed. Choose a valid backup file.")
                return embed_redirect(request, "core:backup_center")

            if action == "backup_db":
                created = create_db_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Database backup created: {created.name}")
            elif action == "backup_media":
                created = create_media_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Media backup created: {created.name}")
            elif action == "backup_code":
                created = create_code_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Code backup created: {created.name}")
            elif action == "backup_full":
                created = create_full_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Full backup created: {created.name}")
            elif action == "purge_old_backups":
                if request.POST.get("confirm_text") != "PURGE":
                    messages.error(request, "Backup purge was not confirmed.")
                else:
                    result = purge_old_backups(config)
                    messages.success(
                        request,
                        f"Purged {len(result['deleted'])} old backup file(s). "
                        f"Reclaimed {result['reclaimed_bytes'] / (1024 * 1024):.2f} MB. "
                        f"The newest {max(int(config.get('retention_count') or 3), 1)} backup file(s) were kept.",
                    )
            elif action == "restore_smart":
                backups = list_backups(config)
                for serial_number, backup in enumerate(backups, start=1):
                    backup.serial_number = serial_number
                restore_choices = [("", "----------")]
                restore_choices.extend(
                    (backup.id, backup.name)
                    for backup in backups
                    if backup.file_exists and backup.backup_type in {"db", "media", "full"}
                )
                form = BackupRestoreForm(request.POST, backup_choices=restore_choices)
                if not form.is_valid() or form.cleaned_data["confirm_text"] != "RESTORE":
                    messages.error(request, "Type RESTORE exactly before restoring the selected backup.")
                else:
                    backup = next(
                        item for item in backups if item.id == form.cleaned_data["backup_id"]
                    )
                    if backup.backup_type == "db":
                        safety = create_db_backup({**config, "enable_db_backup": True})
                        restore_database(config, backup.id)
                    elif backup.backup_type == "media":
                        safety = create_media_backup({**config, "enable_media_backup": True})
                        restore_media(config, backup.id)
                    else:
                        safety = create_full_backup({**config, "enable_full_backup": True})
                        restore_full(config, backup.id)
                    logout(request)
                    messages.success(
                        request,
                        f"{backup.get_backup_type_display()} restore completed. "
                        f"Safety backup created first: {safety.name}. Please log in again.",
                    )
                    return redirect(settings.LOGIN_URL)
            elif action == "restore_db":
                form = BackupRestoreForm(request.POST, backup_choices=choices_for(list_backups(config), "db"))
                if not form.is_valid() or form.cleaned_data["confirm_text"] != "RESTORE DB":
                    messages.error(request, "Type RESTORE DB exactly before restoring the database.")
                else:
                    safety = create_db_backup({**config, "enable_db_backup": True})
                    restore_database(config, form.cleaned_data["backup_id"])
                    logout(request)
                    messages.success(request, f"Database restore completed. Safety backup created first: {safety.name}. Please log in again.")
                    return redirect(settings.LOGIN_URL)
            elif action == "restore_media":
                form = BackupRestoreForm(request.POST, backup_choices=choices_for(list_backups(config), "media"))
                if not form.is_valid() or form.cleaned_data["confirm_text"] != "RESTORE MEDIA":
                    messages.error(request, "Type RESTORE MEDIA exactly before restoring media.")
                else:
                    safety = create_media_backup({**config, "enable_media_backup": True})
                    restore_media(config, form.cleaned_data["backup_id"])
                    logout(request)
                    messages.success(request, f"Media restore completed. Safety backup created first: {safety.name}. Please log in again.")
                    return redirect(settings.LOGIN_URL)
            elif action == "restore_full":
                form = BackupRestoreForm(request.POST, backup_choices=choices_for(list_backups(config), "full"))
                if not form.is_valid() or form.cleaned_data["confirm_text"] != "RESTORE FULL":
                    messages.error(request, "Type RESTORE FULL exactly before restoring a full backup.")
                else:
                    safety = create_full_backup({**config, "enable_full_backup": True})
                    restore_full(config, form.cleaned_data["backup_id"])
                    logout(request)
                    messages.success(request, f"Full restore completed. Safety backup created first: {safety.name}. Please log in again.")
                    return redirect(settings.LOGIN_URL)
            elif action == "fresh_reset":
                messages.error(request, "Fresh reset is intentionally disabled until a TMS reset profile is configured.")
            else:
                messages.error(request, "Unknown backup action.")
        except Exception as exc:
            messages.error(request, f"Backup action failed: {exc}")

        return embed_redirect(request, "core:backup_center")


from django.http import FileResponse


class BackupDownloadView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def get(self, request, backup_id):
        backup = next((item for item in list_backups(load_backup_settings()) if item.id == backup_id), None)
        if not backup:
            raise Http404("Backup not found")
        return FileResponse(open(backup.display_path, "rb"), as_attachment=True, filename=backup.id)


class BackupDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, backup_id):
        try:
            delete_backup(load_backup_settings(), backup_id)
        except Exception as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=400)
        return JsonResponse({"success": True})


from django.http import Http404, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from .forms import SuggestionReplyForm, SuggestionTicketForm
from .suggestion_store import (
    STATUS_CHOICES,
    TYPE_CHOICES,
    add_reply,
    create_ticket,
    delete_ticket,
    get_ticket,
    list_tickets,
    update_status,
)


@login_required
def suggestion_list(request):
    selected_status = request.GET.get("status")
    if selected_status is None:
        selected_status = "PENDING"
    selected_type = request.GET.get("type", "")
    tickets = list_tickets(status=selected_status, ticket_type=selected_type)
    return render(request, "core/suggestion_list.html", {
        "tickets": tickets,
        "status_choices": STATUS_CHOICES,
        "type_choices": TYPE_CHOICES,
        "selected_status": selected_status,
        "selected_type": selected_type,
    })


@login_required
def suggestion_create(request):
    if request.method == "POST":
        form = SuggestionTicketForm(request.POST)
        if form.is_valid():
            ticket = create_ticket(
                form.cleaned_data,
                request.user,
                files=request.FILES.getlist("photos"),
            )
            messages.success(request, "Suggestion saved.")
            return embed_redirect(request, "core:suggestion_detail", pk=ticket.id)
    else:
        form = SuggestionTicketForm()
    return render(request, "core/suggestion_form.html", {"form": form})


@login_required
def suggestion_detail(request, pk):
    ticket = get_ticket(pk)
    if not ticket:
        raise Http404("Suggestion not found")

    if request.method == "POST":
        form = SuggestionReplyForm(request.POST)
        selected_status = request.POST.get("status") if request.user.is_staff or request.user.is_superuser else None
        if form.is_valid():
            message = (form.cleaned_data.get("message") or "").strip()
            photos = request.FILES.getlist("photos")
            if message or photos or selected_status:
                add_reply(ticket.id, message, request.user, status=selected_status, files=photos)
                messages.success(request, "Reply saved.")
                return embed_redirect(request, "core:suggestion_detail", pk=ticket.id)
            messages.error(request, "Reply, image, or status change is required.")
    else:
        form = SuggestionReplyForm()

    ticket = get_ticket(pk)
    return render(request, "core/suggestion_detail.html", {
        "ticket": ticket,
        "form": form,
        "status_choices": STATUS_CHOICES,
    })


@login_required
@require_POST
def suggestion_status_update(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseForbidden("Not allowed")
    new_status = request.POST.get("status")
    if new_status not in dict(STATUS_CHOICES):
        return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)
    ticket = update_status(pk, new_status)
    if not ticket:
        return JsonResponse({"ok": False, "error": "Suggestion not found."}, status=404)
    return JsonResponse({"ok": True, "status": ticket.status})


@login_required
@require_POST
def suggestion_delete(request, pk):
    ticket = get_ticket(pk)
    if not ticket:
        return JsonResponse(
            {"success": False, "error": "Suggestion not found."},
            status=404,
        )
    can_delete = request.user.is_staff or request.user.is_superuser
    can_delete = can_delete or ticket.user_name_snapshot == request.user.get_username()
    if not can_delete:
        return JsonResponse(
            {"success": False, "error": "Permission denied."},
            status=403,
        )
    if not delete_ticket(pk):
        return JsonResponse(
            {"success": False, "error": "Suggestion not found."},
            status=404,
        )
    return JsonResponse({"success": True})
