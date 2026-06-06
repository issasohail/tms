import mimetypes
import os
from datetime import date, timedelta
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
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from core.models import GlobalSettings
from core.utils.text import smart_title
from leases.models import Lease
from leases.whatsapp import build_whatsapp_url
from properties.models import Property, Unit

from .forms import MaintenanceRequestForm, MaintenanceRequestMediaForm
from .models import (
    MAINTENANCE_FILE_EXTENSIONS,
    MaintenanceCategory,
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


def _request_tenant(request_obj):
    return request_obj.lease_tenant


def _clean_filter_value(value):
    return "" if value in {None, "", "__all__"} else value


def _date_filter_range(key):
    today = timezone.localdate()
    if key == "today":
        return today, today
    if key == "yesterday":
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    if key == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if key == "last_week":
        end = today - timedelta(days=today.weekday() + 1)
        return end - timedelta(days=6), end
    if key == "this_month":
        start = today.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, next_month - timedelta(days=1)
    if key == "last_month":
        this_month = today.replace(day=1)
        end = this_month - timedelta(days=1)
        return end.replace(day=1), end
    if key == "this_quarter":
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=quarter_month, day=1)
        next_quarter = (start.replace(day=28) + timedelta(days=95)).replace(day=1)
        next_quarter = next_quarter.replace(month=((next_quarter.month - 1) // 3) * 3 + 1)
        return start, next_quarter - timedelta(days=1)
    if key == "last_quarter":
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        this_quarter = today.replace(month=quarter_month, day=1)
        end = this_quarter - timedelta(days=1)
        start_month = ((end.month - 1) // 3) * 3 + 1
        return end.replace(month=start_month, day=1), end
    if key == "this_year":
        return today.replace(month=1, day=1), today.replace(month=12, day=31)
    if key == "last_year":
        last_year = today.year - 1
        return date(last_year, 1, 1), date(last_year, 12, 31)
    return None, None


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
    tenant = _request_tenant(item)
    tenant_first_name = getattr(tenant, "first_name", "") if tenant else ""
    unit = item.unit
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
        "reported_date_value": item.reported_date.strftime("%Y-%m-%d") if item.reported_date else "",
        "resolved_date": item.resolved_date.strftime("%b %d, %Y") if item.resolved_date else "",
        "resolved_date_value": item.resolved_date.strftime("%Y-%m-%d") if item.resolved_date else "",
        "building": str(item.building or "-"),
        "building_id": getattr(item.building, "pk", "") or "",
        "unit": getattr(unit, "unit_number", "") or str(unit or ""),
        "unit_id": item.unit_id or "",
        "tenant": str(tenant or "-"),
        "tenant_first_name": tenant_first_name or str(tenant or "-"),
        "tenant_phone": _tenant_phone(tenant),
        "category_id": item.category_ref_id or "",
        "category": str(item.category_ref or item.category or ""),
        "media_count": item.media.filter(is_active=True).count(),
        "detail_url": reverse("maintenance:request_detail", args=[item.pk]),
        "edit_url": reverse("maintenance:request_edit", args=[item.pk]),
        "delete_url": reverse("maintenance:request_delete", args=[item.pk]),
        "update_url": reverse("maintenance:request_inline_update", args=[item.pk]),
        "upload_url": reverse("maintenance:request_media_upload", args=[item.pk]),
        "whatsapp_url": reverse("maintenance:request_whatsapp", args=[item.pk]),
        "public_media_url": request.build_absolute_uri(
            reverse("maintenance:public_media_share", args=[token])
        ),
    }


def _lease_label(lease):
    tenant = lease.tenant
    return getattr(tenant, "first_name", "") or _tenant_name(tenant) or str(tenant)


def _unit_label(unit, active_lease=None):
    label = str(unit)
    if active_lease and getattr(active_lease, "tenant", None):
        label = f"{label} - {_tenant_name(active_lease.tenant)}"
    return label


def _active_leases_qs():
    return Lease.objects.select_related("tenant", "unit", "unit__property").filter(status="active")


def _category_json(category):
    return {
        "id": category.pk,
        "text": category.name,
        "name": category.name,
        "request_count": category.requests.count(),
        "is_active": category.is_active,
        "requests_url": reverse("maintenance:category_requests", args=[category.pk]),
        "update_url": reverse("maintenance:category_update", args=[category.pk]),
        "delete_url": reverse("maintenance:category_delete", args=[category.pk]),
    }


def _active_categories():
    return MaintenanceCategory.objects.filter(is_active=True).order_by("sort_order", "name")


def _all_categories():
    return MaintenanceCategory.objects.all().order_by("sort_order", "name")


def _current_user_whatsapp_phone(request):
    return (
        getattr(request.user, "whatsapp_number", "")
        or getattr(request.user, "phone", "")
        or ""
    )


def _unit_options(property_id=None):
    units = Unit.objects.select_related("property").order_by("property__property_name", "unit_number")
    if property_id:
        units = units.filter(property_id=property_id)
    active_by_unit = {
        lease.unit_id: lease
        for lease in _active_leases_qs().filter(unit_id__in=units.values("id")).order_by("unit_id", "-start_date", "-id")
    }
    return [
        {
            "id": unit.pk,
            "text": _unit_label(unit, active_by_unit.get(unit.pk)),
            "property_id": unit.property_id,
        }
        for unit in units
    ]


def _lease_options(property_id=None, unit_id=None):
    leases = _active_leases_qs().order_by("tenant__first_name", "tenant__last_name", "unit__unit_number")
    if property_id:
        leases = leases.filter(unit__property_id=property_id)
    if unit_id:
        leases = leases.filter(unit_id=unit_id)
    return [
        {
            "id": lease.pk,
            "text": _lease_label(lease),
            "property_id": lease.unit.property_id,
            "unit_id": lease.unit_id,
            "tenant_id": lease.tenant_id,
            "tenant_name": _tenant_name(lease.tenant),
        }
        for lease in leases[:300]
    ]


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
        MaintenanceRequest.objects.select_related("unit", "unit__property"),
        pk=request_id,
    ), ""


def _maintenance_whatsapp_message(request, request_obj):
    tenant = _request_tenant(request_obj)
    lines = [
        "Maintenance Request",
        f"Property / Building: {request_obj.building or '-'}",
        f"Unit: {request_obj.unit or '-'}",
        f"Tenant: {_tenant_name(tenant) or '-'}",
        f"Tenant Phone: {_tenant_phone(tenant) or '-'}",
        f"Title: {request_obj.title}",
        f"Description: {request_obj.description or '-'}",
        f"Request Date: {request_obj.reported_date:%Y-%m-%d}" if request_obj.reported_date else "Request Date: -",
    ]
    if request_obj.media.filter(is_active=True).exists():
        token = _sign_media_token(request_obj.pk)
        media_url = request.build_absolute_uri(
            reverse("maintenance:public_media_share", args=[token])
        )
        lines.extend([
            f"Files / Photos: {media_url}",
            "This media link expires in 3 days.",
        ])
    else:
        lines.append("Files / Photos: No photos available")
    return "\n".join(lines)


def _fill_request_relationships(request_obj):
    return request_obj


class MaintenanceRequestListView(LoginRequiredMixin, ListView):
    model = MaintenanceRequest
    template_name = "maintenance/request_list.html"
    context_object_name = "requests"
    paginate_by = 50

    def get_queryset(self):
        qs = (
            MaintenanceRequest.objects
            .select_related("unit", "unit__property", "assigned_to", "category_ref")
            .prefetch_related("media")
            .annotate(active_media_count=Count("media", filter=Q(media__is_active=True)))
        )
        raw_status = self.request.GET.get("status")
        status = _clean_filter_value(raw_status)
        priority = self.request.GET.get("priority")
        building = _clean_filter_value(self.request.GET.get("building"))
        unit = _clean_filter_value(self.request.GET.get("unit"))
        category = _clean_filter_value(self.request.GET.get("category"))
        date_filter = _clean_filter_value(self.request.GET.get("date_filter"))
        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")
        q = self.request.GET.get("q")
        if status:
            qs = qs.filter(status=status)
        elif raw_status != "__all__":
            qs = qs.exclude(status="completed")
        if priority:
            qs = qs.filter(priority=priority)
        if building:
            qs = qs.filter(unit__property_id=building)
        if unit:
            qs = qs.filter(unit_id=unit)
        if category:
            qs = qs.filter(category_ref_id=category)
        start_date, end_date = _date_filter_range(date_filter)
        if date_from:
            try:
                start_date = date.fromisoformat(date_from)
            except ValueError:
                start_date = start_date
        if date_to:
            try:
                end_date = date.fromisoformat(date_to)
            except ValueError:
                end_date = end_date
        if start_date:
            qs = qs.filter(reported_date__gte=start_date)
        if end_date:
            qs = qs.filter(reported_date__lte=end_date)
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(description__icontains=q)
                | Q(unit__leases__tenant__first_name__icontains=q)
                | Q(unit__leases__tenant__last_name__icontains=q)
                | Q(unit__unit_number__icontains=q)
            ).distinct()
        return qs.order_by("-reported_date", "-id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = MaintenanceRequest.STATUS_CHOICES
        ctx["priority_choices"] = MaintenanceRequest.PRIORITY_CHOICES
        ctx["buildings"] = Property.objects.order_by("property_name")
        ctx["units"] = Unit.objects.select_related("property").order_by("property__property_name", "unit_number")
        ctx["leases"] = _active_leases_qs().order_by("tenant__first_name", "tenant__last_name", "unit__unit_number")[:300]
        ctx["categories"] = _active_categories()
        ctx["date_filter_choices"] = [
            ("today", "Today"),
            ("yesterday", "Yesterday"),
            ("this_week", "This Week"),
            ("last_week", "Last Week"),
            ("this_month", "This Month"),
            ("last_month", "Last Month"),
            ("this_quarter", "This Quarter"),
            ("last_quarter", "Last Quarter"),
            ("this_year", "This Year"),
            ("last_year", "Last Year"),
        ]
        ctx["filters"] = self.request.GET
        return ctx


@login_required
def category_manage(request):
    return render(
        request,
        "maintenance/category_list.html",
        {"categories": _all_categories().annotate(request_count=Count("requests"))},
    )


@login_required
def category_request_list(request, pk):
    category = get_object_or_404(MaintenanceCategory, pk=pk)
    requests_qs = (
        MaintenanceRequest.objects
        .filter(category_ref=category)
        .select_related("unit", "unit__property")
        .prefetch_related("media")
        .annotate(active_media_count=Count("media", filter=Q(media__is_active=True)))
        .order_by("-reported_date", "-id")
    )
    return render(
        request,
        "maintenance/category_request_list.html",
        {
            "category": category,
            "requests": requests_qs,
        },
    )


class MaintenanceRequestDetailView(LoginRequiredMixin, DetailView):
    model = MaintenanceRequest
    template_name = "maintenance/request_detail.html"
    context_object_name = "request_obj"

    def get_queryset(self):
        return MaintenanceRequest.objects.select_related(
            "unit", "unit__property", "assigned_to", "created_by", "updated_by"
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
        new_status = form.cleaned_data.get("status")
        if old_status != new_status and new_status != "completed" and not (form.cleaned_data.get("admin_notes") or "").strip():
            form.add_error("admin_notes", "Please add notes when changing status.")
            return self.form_invalid(form)
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
            if _is_ajax(request):
                return JsonResponse({"success": False, "error": "Delete was not confirmed."}, status=400)
            messages.error(request, "Please confirm before deleting the maintenance request.")
            return redirect(self.get_object().get_absolute_url())
        if _is_ajax(request):
            self.object = self.get_object()
            self.object.delete()
            return JsonResponse({"success": True})
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
@require_GET
def request_quick_add_related(request):
    property_id = request.GET.get("property_id") or ""
    unit_id = request.GET.get("unit_id") or ""
    lease_id = request.GET.get("lease_id") or ""

    selected_lease = None
    selected_unit = None
    if lease_id:
        selected_lease = _active_leases_qs().filter(pk=lease_id).first()
        if selected_lease:
            property_id = str(selected_lease.unit.property_id)
            unit_id = str(selected_lease.unit_id)
    if unit_id:
        selected_unit = Unit.objects.select_related("property").filter(pk=unit_id).first()
        if selected_unit and not property_id:
            property_id = str(selected_unit.property_id)

    current_lease = selected_lease
    if selected_unit and not current_lease:
        current_lease = (
            _active_leases_qs()
            .filter(unit=selected_unit)
            .order_by("-start_date", "-id")
            .first()
        )

    return JsonResponse({
        "success": True,
        "property_id": property_id,
        "unit_id": unit_id,
        "lease_id": str(current_lease.pk) if current_lease else "",
        "tenant_id": str(current_lease.tenant_id) if current_lease else "",
        "tenant_name": _tenant_name(current_lease.tenant) if current_lease else "",
        "units": _unit_options(property_id=property_id or None),
        "leases": _lease_options(property_id=property_id or None, unit_id=unit_id or None),
    })


@login_required
def category_list_json(request):
    return JsonResponse({
        "success": True,
        "categories": [_category_json(category) for category in _active_categories()],
    })


@login_required
@require_POST
def category_create(request):
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"success": False, "error": "Category name is required."}, status=400)
    category, created = MaintenanceCategory.objects.get_or_create(
        name=smart_title(name),
        defaults={"is_active": True},
    )
    if not category.is_active:
        category.is_active = True
        category.save(update_fields=["is_active", "updated_at"])
    return JsonResponse({
        "success": True,
        "created": created,
        "category": _category_json(category),
        "categories": [_category_json(item) for item in _active_categories()],
        "all_categories": [_category_json(item) for item in _all_categories()],
    })


@login_required
@require_POST
def category_update(request, pk):
    category = get_object_or_404(MaintenanceCategory, pk=pk)
    name = (request.POST.get("name") or "").strip()
    update_fields = ["updated_at"]
    if name:
        name = smart_title(name)
        if MaintenanceCategory.objects.exclude(pk=category.pk).filter(name__iexact=name).exists():
            return JsonResponse({"success": False, "error": "A category with this name already exists."}, status=400)
        category.name = name
        update_fields.append("name")
    if "is_active" in request.POST:
        category.is_active = request.POST.get("is_active") == "1"
        update_fields.append("is_active")
    if len(update_fields) == 1:
        return JsonResponse({"success": False, "error": "Nothing to update."}, status=400)
    category.save(update_fields=update_fields)
    MaintenanceRequest.objects.filter(category_ref=category).update(category=category.name)
    return JsonResponse({
        "success": True,
        "category": _category_json(category),
        "categories": [_category_json(item) for item in _all_categories()],
    })


@login_required
@require_POST
def category_delete(request, pk):
    category = get_object_or_404(MaintenanceCategory, pk=pk)
    category.is_active = False
    category.save(update_fields=["is_active", "updated_at"])
    return JsonResponse({
        "success": True,
        "categories": [_category_json(item) for item in _active_categories()],
        "all_categories": [_category_json(item) for item in _all_categories()],
    })


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
    unit_id = getattr(lease, "unit_id", None) or request.POST.get("unit")
    if unit_id and not lease:
        lease = (
            _active_leases_qs()
            .filter(unit_id=unit_id)
            .order_by("-start_date", "-id")
            .first()
        )
        unit_id = getattr(lease, "unit_id", None) or unit_id
    if not unit_id:
        return JsonResponse({"success": False, "error": "Please select a unit or lease tenant."}, status=400)
    category_ref_id = request.POST.get("category_ref") or None
    if category_ref_id and not str(category_ref_id).isdigit():
        category, _ = MaintenanceCategory.objects.get_or_create(
            name=smart_title(category_ref_id),
            defaults={"is_active": True},
        )
        category_ref_id = category.pk
    elif category_ref_id and not MaintenanceCategory.objects.filter(pk=category_ref_id, is_active=True).exists():
        category_ref_id = None

    item = MaintenanceRequest.objects.create(
        unit_id=unit_id or None,
        title=title,
        description=(request.POST.get("description") or "").strip(),
        category_ref_id=category_ref_id,
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
    notes = (request.POST.get("notes") or "").strip()
    if field not in {"title", "description", "status", "cost", "unit", "reported_date", "resolved_date", "category_ref"}:
        return JsonResponse({"success": False, "error": "Unsupported field."}, status=400)

    old_status = item.status
    if field == "status":
        if value not in dict(MaintenanceRequest.STATUS_CHOICES):
            return JsonResponse({"success": False, "error": "Invalid status."}, status=400)
        item.status = value
        if old_status != value and value == "completed":
            try:
                item.cost = Decimal(request.POST.get("cost") or item.cost or "0")
            except InvalidOperation:
                return JsonResponse({"success": False, "error": "Invalid cost."}, status=400)
        elif old_status != value and not notes:
            return JsonResponse({"success": False, "error": "Notes are required when changing status."}, status=400)
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
    elif field == "unit":
        unit = Unit.objects.select_related("property").filter(pk=value or None).first()
        if not unit:
            return JsonResponse({"success": False, "error": "Invalid unit."}, status=400)
        item.unit = unit
    elif field == "reported_date":
        try:
            item.reported_date = date.fromisoformat(value)
        except Exception:
            return JsonResponse({"success": False, "error": "Invalid date."}, status=400)
    elif field == "resolved_date":
        if value:
            try:
                item.resolved_date = date.fromisoformat(value)
            except Exception:
                return JsonResponse({"success": False, "error": "Invalid resolved date."}, status=400)
        else:
            item.resolved_date = None
    elif field == "category_ref":
        if value:
            category = MaintenanceCategory.objects.filter(pk=value, is_active=True).first()
            if not category:
                return JsonResponse({"success": False, "error": "Invalid category."}, status=400)
            item.category_ref = category
        else:
            item.category_ref = None
            item.category = ""
    else:
        item.description = value.strip()

    item.updated_by = request.user
    item.save()
    item = MaintenanceRequest.objects.select_related(
        "unit", "unit__property", "category_ref"
    ).prefetch_related("media").get(pk=item.pk)
    if field == "status" and old_status != item.status:
        MaintenanceRequestStatusLog.objects.create(
            request=item,
            old_status=old_status or "",
            new_status=item.status,
            changed_by=request.user,
            notes=notes or "Inline status update.",
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
        MaintenanceRequest.objects.select_related("unit", "unit__property").prefetch_related("media"),
        pk=pk,
    )
    message = _maintenance_whatsapp_message(request, item)
    settings_obj = GlobalSettings.get_solo()
    whatsapp_url = build_whatsapp_url(
        _current_user_whatsapp_phone(request),
        message,
        country_code=getattr(settings_obj, "country_code", "+92"),
    )
    return JsonResponse({
        "success": True,
        "message": message,
        "whatsapp_url": whatsapp_url,
        "phone": _current_user_whatsapp_phone(request),
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
