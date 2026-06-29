import mimetypes
import os
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.files.storage import default_storage
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from core.models import GlobalSettings
from leases.models import Lease
from leases.whatsapp import build_whatsapp_url
from properties.models import Property, Unit
from tenants.models import Tenant

from .forms import MaintenanceRequestForm, MaintenanceRequestMediaForm
from .models import (
    MAINTENANCE_FILE_EXTENSIONS,
    MaintenanceRequest,
    MaintenanceRequestMedia,
    MaintenanceRequestStatusLog,
)

MAINTENANCE_SHARE_SALT = "maintenance-request-media-share"
MAINTENANCE_SHARE_MAX_AGE = 3 * 24 * 60 * 60


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _safe_extension(filename):
    return os.path.splitext(filename or "")[1].lower().lstrip(".")


def _save_request_uploads(request, request_obj, description=""):
    uploaded = []
    skipped = []
    files = request.FILES.getlist("files") or request.FILES.getlist("file")
    for upload in files:
        ext = _safe_extension(upload.name)
        if ext not in MAINTENANCE_FILE_EXTENSIONS:
            skipped.append(upload.name)
            continue
        media = MaintenanceRequestMedia.objects.create(
            request=request_obj,
            file=upload,
            description=description[:255],
            original_filename=upload.name[:255],
            uploaded_by=request.user,
        )
        uploaded.append(media)
    return uploaded, skipped


def _tenant_name(tenant):
    return tenant.get_full_name() if tenant else ""


def _tenant_phone(tenant):
    return (
        getattr(tenant, "phone", None)
        or getattr(tenant, "phone2", None)
        or getattr(tenant, "phone3", None)
        or ""
    )


def _media_json(request, media):
    token = _sign_media_token(media.request_id)
    return {
        "id": media.pk,
        "filename": media.display_filename,
        "original_filename": media.original_filename,
        "description": media.description,
        "is_image": media.is_image,
        "is_video": media.is_video,
        "is_pdf": media.is_pdf,
        "url": request.build_absolute_uri(
            reverse("maintenance:public_media_file", args=[token, media.pk])
        ),
        "description_url": reverse("maintenance:media_description_update", args=[media.pk]),
        "delete_url": reverse("maintenance:media_delete", args=[media.pk]),
    }


def _request_json(request, item):
    tenant = item.tenant
    token = _sign_media_token(item.pk)
    return {
        "id": item.pk,
        "title": item.title,
        "description": item.description,
        "status": item.status,
        "status_display": item.get_status_display(),
        "priority": item.priority,
        "priority_display": item.get_priority_display(),
        "cost": str(item.cost),
        "cost_display": f"{item.cost:,.0f}",
        "reported_date": item.reported_date.strftime("%b %d, %Y") if item.reported_date else "",
        "building": str(item.building or "-"),
        "unit": str(item.unit or ""),
        "tenant": str(tenant or "-"),
        "tenant_phone": _tenant_phone(tenant),
        "media_count": item.media.filter(is_active=True).count(),
        "detail_url": reverse("maintenance:request_detail", args=[item.pk]),
        "delete_url": reverse("maintenance:request_delete", args=[item.pk]),
        "update_url": reverse("maintenance:request_inline_update", args=[item.pk]),
        "upload_url": reverse("maintenance:request_media_upload", args=[item.pk]),
        "whatsapp_url": reverse("maintenance:request_whatsapp", args=[item.pk]),
        "public_media_url": request.build_absolute_uri(
            reverse("maintenance:public_media_share", args=[token])
        ),
    }


def _sign_media_token(request_id):
    return signing.TimestampSigner(salt=MAINTENANCE_SHARE_SALT).sign(str(request_id))


def _request_from_share_token(token):
    try:
        request_id = signing.TimestampSigner(salt=MAINTENANCE_SHARE_SALT).unsign(
            token,
            max_age=MAINTENANCE_SHARE_MAX_AGE,
        )
    except signing.SignatureExpired:
        return None, "expired"
    except signing.BadSignature:
        return None, "invalid"
    return get_object_or_404(
        MaintenanceRequest.objects.select_related("tenant", "building", "unit"),
        pk=request_id,
    ), ""


def _maintenance_whatsapp_message(request, request_obj):
    tenant = request_obj.tenant
    token = _sign_media_token(request_obj.pk)
    media_url = request.build_absolute_uri(
        reverse("maintenance:public_media_share", args=[token])
    )
    return "\n".join([
        "Maintenance Request",
        f"Property / Building: {request_obj.building or '-'}",
        f"Unit: {request_obj.unit or '-'}",
        f"Tenant: {_tenant_name(tenant) or '-'}",
        f"Tenant Phone: {_tenant_phone(tenant) or '-'}",
        f"Title: {request_obj.title}",
        f"Description: {request_obj.description or '-'}",
        f"Request Date: {request_obj.reported_date:%Y-%m-%d}" if request_obj.reported_date else "Request Date: -",
        f"Files / Photos: {media_url}",
        "This media link expires in 3 days.",
    ])


def _fill_request_relationships(request_obj):
    lease = getattr(request_obj, "lease", None)
    if lease:
        if not request_obj.unit_id and getattr(lease, "unit_id", None):
            request_obj.unit = lease.unit
        if not request_obj.tenant_id and getattr(lease, "tenant_id", None):
            request_obj.tenant = lease.tenant
    if request_obj.unit_id and not request_obj.building_id:
        unit = getattr(request_obj, "unit", None) or Unit.objects.select_related("property").filter(
            pk=request_obj.unit_id
        ).first()
        if unit:
            request_obj.building = unit.property


class MaintenanceRequestListView(LoginRequiredMixin, ListView):
    model = MaintenanceRequest
    template_name = "maintenance/request_list.html"
    context_object_name = "requests"
    paginate_by = 50

    def get_queryset(self):
        qs = (
            MaintenanceRequest.objects
            .select_related("tenant", "building", "unit", "lease", "assigned_to")
            .prefetch_related("media")
            .annotate(active_media_count=Count("media", filter=Q(media__is_active=True)))
        )
        status = self.request.GET.get("status")
        priority = self.request.GET.get("priority")
        building = self.request.GET.get("building")
        unit = self.request.GET.get("unit")
        q = self.request.GET.get("q")
        if status:
            qs = qs.filter(status=status)
        if priority:
            qs = qs.filter(priority=priority)
        if building:
            qs = qs.filter(building_id=building)
        if unit:
            qs = qs.filter(unit_id=unit)
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(description__icontains=q)
                | Q(tenant__first_name__icontains=q)
                | Q(tenant__last_name__icontains=q)
                | Q(unit__unit_number__icontains=q)
            )
        return qs.order_by("-reported_date", "-id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = MaintenanceRequest.STATUS_CHOICES
        ctx["priority_choices"] = MaintenanceRequest.PRIORITY_CHOICES
        ctx["buildings"] = Property.objects.order_by("property_name")
        ctx["units"] = Unit.objects.select_related("property").order_by("property__property_name", "unit_number")
        ctx["tenants"] = Tenant.objects.filter(is_active=True).order_by("first_name", "last_name")
        ctx["leases"] = Lease.objects.select_related("tenant", "unit", "unit__property").order_by("-start_date", "-id")[:300]
        ctx["filters"] = self.request.GET
        return ctx


class MaintenanceRequestDetailView(LoginRequiredMixin, DetailView):
    model = MaintenanceRequest
    template_name = "maintenance/request_detail.html"
    context_object_name = "request_obj"

    def get_queryset(self):
        return MaintenanceRequest.objects.select_related(
            "tenant", "building", "unit", "lease", "assigned_to", "created_by", "updated_by"
        ).prefetch_related("media", "status_logs")


class MaintenanceRequestCreateView(LoginRequiredMixin, CreateView):
    model = MaintenanceRequest
    form_class = MaintenanceRequestForm
    template_name = "maintenance/request_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        _fill_request_relationships(form.instance)
        response = super().form_valid(form)
        MaintenanceRequestStatusLog.objects.create(
            request=self.object,
            old_status="",
            new_status=self.object.status,
            changed_by=self.request.user,
            notes="Request created.",
        )
        uploaded, skipped = _save_request_uploads(self.request, self.object)
        if skipped:
            messages.warning(self.request, f"Skipped unsupported file(s): {', '.join(skipped)}")
        messages.success(self.request, "Maintenance request created.")
        return response


class MaintenanceRequestUpdateView(LoginRequiredMixin, UpdateView):
    model = MaintenanceRequest
    form_class = MaintenanceRequestForm
    template_name = "maintenance/request_form.html"

    def form_valid(self, form):
        old_status = MaintenanceRequest.objects.filter(pk=self.object.pk).values_list("status", flat=True).first()
        form.instance.updated_by = self.request.user
        _fill_request_relationships(form.instance)
        response = super().form_valid(form)
        if old_status != self.object.status:
            MaintenanceRequestStatusLog.objects.create(
                request=self.object,
                old_status=old_status or "",
                new_status=self.object.status,
                changed_by=self.request.user,
                notes=form.cleaned_data.get("admin_notes") or "",
            )
        uploaded, skipped = _save_request_uploads(self.request, self.object)
        if skipped:
            messages.warning(self.request, f"Skipped unsupported file(s): {', '.join(skipped)}")
        messages.success(self.request, "Maintenance request updated.")
        return response


class MaintenanceRequestDeleteView(LoginRequiredMixin, DeleteView):
    model = MaintenanceRequest
    template_name = "maintenance/request_confirm_delete.html"
    success_url = reverse_lazy("maintenance:request_list")

    def post(self, request, *args, **kwargs):
        if request.POST.get("confirm_delete") != "yes":
            messages.error(request, "Please confirm before deleting the maintenance request.")
            return redirect(self.get_object().get_absolute_url())
        messages.success(request, "Maintenance request deleted.")
        return super().post(request, *args, **kwargs)


class MaintenanceMediaUpdateView(LoginRequiredMixin, UpdateView):
    model = MaintenanceRequestMedia
    form_class = MaintenanceRequestMediaForm
    template_name = "maintenance/media_form.html"

    def get_success_url(self):
        return self.object.request.get_absolute_url()


@login_required
@require_POST
def maintenance_media_delete(request, pk):
    media = get_object_or_404(MaintenanceRequestMedia, pk=pk)
    request_obj = media.request
    if request.POST.get("confirm_delete") != "yes":
        if _is_ajax(request):
            return JsonResponse({"success": False, "error": "Delete was not confirmed."}, status=400)
        messages.error(request, "Please confirm before deleting the maintenance file.")
        return redirect(request_obj.get_absolute_url())
    media.is_active = False
    media.save(update_fields=["is_active"])
    if _is_ajax(request):
        return JsonResponse({
            "success": True,
            "media_count": request_obj.media.filter(is_active=True).count(),
        })
    messages.success(request, "Maintenance file removed from active view.")
    return redirect(request_obj.get_absolute_url())


@login_required
@require_POST
def request_quick_add(request):
    title = (request.POST.get("title") or "").strip()
    if not title:
        return JsonResponse({"success": False, "error": "Title is required."}, status=400)
    status = request.POST.get("status") or "new"
    priority = request.POST.get("priority") or "normal"
    if status not in dict(MaintenanceRequest.STATUS_CHOICES):
        return JsonResponse({"success": False, "error": "Invalid status."}, status=400)
    if priority not in dict(MaintenanceRequest.PRIORITY_CHOICES):
        return JsonResponse({"success": False, "error": "Invalid priority."}, status=400)
    try:
        cost = Decimal(request.POST.get("cost") or "0")
    except InvalidOperation:
        return JsonResponse({"success": False, "error": "Invalid cost."}, status=400)

    lease = None
    if request.POST.get("lease"):
        lease = Lease.objects.select_related("tenant", "unit", "unit__property").filter(
            pk=request.POST.get("lease")
        ).first()
    unit_id = request.POST.get("unit") or getattr(lease, "unit_id", None)
    tenant_id = request.POST.get("tenant") or getattr(lease, "tenant_id", None)
    building_id = request.POST.get("building") or getattr(getattr(lease, "unit", None), "property_id", None)
    if unit_id and not building_id:
        building_id = Unit.objects.filter(pk=unit_id).values_list("property_id", flat=True).first()

    item = MaintenanceRequest.objects.create(
        building_id=building_id or None,
        unit_id=unit_id or None,
        lease=lease,
        tenant_id=tenant_id or None,
        title=title,
        description=(request.POST.get("description") or "").strip(),
        status=status,
        priority=priority,
        cost=cost,
        created_by=request.user,
        updated_by=request.user,
    )
    MaintenanceRequestStatusLog.objects.create(
        request=item,
        old_status="",
        new_status=item.status,
        changed_by=request.user,
        notes="Request created from quick add.",
    )
    uploaded, skipped = _save_request_uploads(request, item)
    data = _request_json(request, item)
    data["media"] = [_media_json(request, media) for media in uploaded]
    return JsonResponse({"success": True, "request": data, "skipped": skipped})


@login_required
@require_POST
def request_inline_update(request, pk):
    item = get_object_or_404(MaintenanceRequest, pk=pk)
    field = request.POST.get("field")
    value = request.POST.get("value", "")
    if field not in {"title", "description", "status", "cost"}:
        return JsonResponse({"success": False, "error": "Unsupported field."}, status=400)

    old_status = item.status
    if field == "status":
        if value not in dict(MaintenanceRequest.STATUS_CHOICES):
            return JsonResponse({"success": False, "error": "Invalid status."}, status=400)
        item.status = value
    elif field == "cost":
        try:
            cost = Decimal(value or "0")
        except InvalidOperation:
            return JsonResponse({"success": False, "error": "Invalid cost."}, status=400)
        if cost < 0:
            return JsonResponse({"success": False, "error": "Cost cannot be negative."}, status=400)
        item.cost = cost
    elif field == "title":
        item.title = value.strip()
        if not item.title:
            return JsonResponse({"success": False, "error": "Title is required."}, status=400)
    else:
        item.description = value.strip()

    item.updated_by = request.user
    item.save()
    if field == "status" and old_status != item.status:
        MaintenanceRequestStatusLog.objects.create(
            request=item,
            old_status=old_status or "",
            new_status=item.status,
            changed_by=request.user,
            notes="Inline status update.",
        )
    return JsonResponse({"success": True, "request": _request_json(request, item)})


@login_required
@require_POST
def request_media_upload(request, pk):
    item = get_object_or_404(MaintenanceRequest, pk=pk)
    description = (request.POST.get("description") or "").strip()
    uploaded, skipped = _save_request_uploads(request, item, description=description)
    if not uploaded and not skipped:
        return JsonResponse({"success": False, "error": "Please choose at least one file."}, status=400)
    return JsonResponse({
        "success": True,
        "media": [_media_json(request, media) for media in uploaded],
        "skipped": skipped,
        "media_count": item.media.filter(is_active=True).count(),
    })


@login_required
@require_POST
def media_description_update(request, pk):
    media = get_object_or_404(MaintenanceRequestMedia, pk=pk, is_active=True)
    media.description = (request.POST.get("description") or "").strip()[:255]
    media.save(update_fields=["description"])
    return JsonResponse({"success": True, "media": _media_json(request, media)})


@login_required
def request_whatsapp(request, pk):
    item = get_object_or_404(
        MaintenanceRequest.objects.select_related("tenant", "building", "unit"),
        pk=pk,
    )
    message = _maintenance_whatsapp_message(request, item)
    settings_obj = GlobalSettings.get_solo()
    whatsapp_url = build_whatsapp_url(
        _tenant_phone(item.tenant),
        message,
        country_code=getattr(settings_obj, "country_code", "+92"),
    )
    return JsonResponse({
        "success": True,
        "message": message,
        "whatsapp_url": whatsapp_url,
        "phone": _tenant_phone(item.tenant),
    })


def public_media_share(request, token):
    item, token_error = _request_from_share_token(token)
    if token_error:
        return render(
            request,
            "maintenance/public_media_share.html",
            {"expired": True, "invalid": token_error == "invalid"},
            status=410,
        )
    media_files = item.media.filter(is_active=True).order_by("uploaded_at", "id")
    return render(
        request,
        "maintenance/public_media_share.html",
        {
            "request_obj": item,
            "media_files": media_files,
            "token": token,
            "expires_days": 3,
        },
    )


def public_media_file(request, token, media_id):
    item, token_error = _request_from_share_token(token)
    if token_error:
        return render(
            request,
            "maintenance/public_media_share.html",
            {"expired": True, "invalid": token_error == "invalid"},
            status=410,
        )
    media = get_object_or_404(MaintenanceRequestMedia, pk=media_id, request=item, is_active=True)
    if not media.file or not default_storage.exists(media.file.name):
        return HttpResponseBadRequest("File missing")
    fh = default_storage.open(media.file.name, "rb")
    filename = media.original_filename or media.display_filename
    content_type, _ = mimetypes.guess_type(filename)
    return FileResponse(
        fh,
        as_attachment=False,
        filename=filename,
        content_type=content_type or "application/octet-stream",
    )
