import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.decorators import user_passes_test
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Max, Prefetch, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from weasyprint import HTML

from invoices.models import Invoice, InvoiceItem, ItemCategory
from leases.models import LeaseDocument
from properties.models import Property, Unit
from .forms import (
    InspectionApplianceFormSet,
    InspectionCategoryForm,
    InspectionDamageChargeFormSet,
    InspectionDetailFormSet,
    InspectionItemForm,
    InspectionKeyFormSet,
    InspectionMeterReadingFormSet,
    InspectionPhotoForm,
    InspectionStatusForm,
    InspectionTemplateForm,
    InspectionTypeForm,
    LeaseInspectionCreateForm,
    LeaseInspectionHeaderForm,
)
from .models import Lease
from .models_inspections import (
    InspectionCategory,
    InspectionDamageCharge,
    InspectionDetail,
    InspectionItem,
    InspectionPhoto,
    InspectionStatus,
    InspectionTemplate,
    InspectionType,
    LeaseInspection,
)


SETTINGS_CONFIG = {
    "types": {
        "model": InspectionType,
        "form": InspectionTypeForm,
        "title": "Inspection Types",
        "columns": ["name", "display_order", "active"],
    },
    "categories": {
        "model": InspectionCategory,
        "form": InspectionCategoryForm,
        "title": "Inspection Categories",
        "columns": ["name", "display_order", "active"],
    },
    "statuses": {
        "model": InspectionStatus,
        "form": InspectionStatusForm,
        "title": "Inspection Statuses",
        "columns": ["name", "badge_color", "display_order", "active"],
    },
    "items": {
        "model": InspectionItem,
        "form": InspectionItemForm,
        "title": "Inspection Items",
        "columns": ["category", "item_name", "display_order", "required", "allow_photos", "allow_damage_cost", "allow_notes", "active"],
    },
    "templates": {
        "model": InspectionTemplate,
        "form": InspectionTemplateForm,
        "title": "Inspection Templates",
        "columns": ["name", "display_order", "active"],
    },
}


def can_manage_inspection_settings(user):
    return user.is_authenticated and (
        user.is_staff
        or user.is_superuser
        or user.has_perm("core.view_globalsettings")
    )


def _setting_config(kind):
    config = SETTINGS_CONFIG.get(kind)
    if not config:
        raise Http404("Unknown inspection setting.")
    return config


@login_required
@user_passes_test(can_manage_inspection_settings)
def inspection_settings_list(request, kind):
    if kind == "all":
        config = {"title": "Inspection Settings"}
    else:
        config = _setting_config(kind)
    rows = config.get("model").objects.all() if config.get("model") else []
    if kind == "items":
        rows = rows.select_related("category")
    elif kind == "templates":
        rows = rows.prefetch_related("items")
    return render(
        request,
        "leases/inspection_settings_list.html",
        {
            "kind": kind,
            "config": config,
            "rows": rows,
            "types": InspectionType.objects.all(),
            "categories": InspectionCategory.objects.all(),
            "statuses": InspectionStatus.objects.all(),
            "items": InspectionItem.objects.select_related("category").all(),
            "templates": InspectionTemplate.objects.prefetch_related("items__category").all(),
        },
    )


@login_required
@user_passes_test(can_manage_inspection_settings)
def inspection_settings_create(request, kind):
    config = _setting_config(kind)
    form = config["form"](request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{config['title']} saved.")
        return redirect("leases:inspection_settings", kind=kind)
    return render(
        request,
        "leases/inspection_settings_form.html",
        {"kind": kind, "config": config, "form": form},
    )


@login_required
@user_passes_test(can_manage_inspection_settings)
def inspection_settings_edit(request, kind, pk):
    config = _setting_config(kind)
    obj = get_object_or_404(config["model"], pk=pk)
    form = config["form"](request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{config['title']} updated.")
        return redirect("leases:inspection_settings", kind=kind)
    return render(
        request,
        "leases/inspection_settings_form.html",
        {"kind": kind, "config": config, "form": form, "object": obj},
    )


def _bool_from_post(value):
    return str(value).lower() in ("1", "true", "yes", "on")


@login_required
@user_passes_test(can_manage_inspection_settings)
@require_POST
def inspection_settings_inline_update(request, kind, pk):
    config = _setting_config(kind)
    obj = get_object_or_404(config["model"], pk=pk)
    field = request.POST.get("field")
    value = request.POST.get("value", "")
    allowed = set(config["columns"])
    if kind == "templates":
        allowed = {"name", "description", "display_order", "active"}
    if field not in allowed:
        return JsonResponse({"ok": False, "error": "Invalid field."}, status=400)
    if field in {"active", "required", "allow_photos", "allow_damage_cost", "allow_notes"}:
        value = _bool_from_post(value)
    elif field == "display_order":
        value = int(value or 0)
    elif field == "category":
        obj.category = get_object_or_404(InspectionCategory, pk=value)
        obj.save(update_fields=["category"])
        return JsonResponse({"ok": True})
    elif field == "badge_color":
        value = (value or "secondary").strip().lower()
    setattr(obj, field, value)
    obj.save(update_fields=[field])
    return JsonResponse({"ok": True})


@login_required
@user_passes_test(can_manage_inspection_settings)
@require_POST
def inspection_template_items_update(request, pk):
    template = get_object_or_404(InspectionTemplate, pk=pk)
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
    item_ids = data.get("item_ids") or []
    items = list(InspectionItem.objects.filter(pk__in=item_ids, active=True))
    item_map = {str(item.pk): item for item in items}
    ordered_items = [item_map[str(item_id)] for item_id in item_ids if str(item_id) in item_map]
    template.items.set(ordered_items)
    template.item_order = [item.pk for item in ordered_items]
    template.save(update_fields=["item_order"])
    return JsonResponse({"ok": True, "count": len(ordered_items)})


def _inspection_queryset():
    return (
        LeaseInspection.objects.select_related(
            "lease",
            "property",
            "unit",
            "tenant",
            "inspection_type",
            "inspection_template",
            "inspector",
            "approved_by",
        )
        .prefetch_related(
            Prefetch("details", queryset=InspectionDetail.objects.prefetch_related("photos")),
            "meter_readings",
            "keys",
            "appliances",
            "damage_charges__invoice",
        )
    )


@login_required
@permission_required("leases.view_leaseinspection", raise_exception=True)
def lease_inspection_list(request, lease_id):
    selected_lease = request.GET.get("lease")
    if selected_lease and str(selected_lease) != str(lease_id):
        return redirect("leases:lease_inspection_list", lease_id=selected_lease)
    lease = get_object_or_404(
        Lease.objects.select_related("tenant", "unit", "unit__property"),
        pk=lease_id,
    )
    inspections = _inspection_queryset().filter(lease=lease)
    return render(
        request,
        "leases/inspection_list.html",
        {"lease": lease, "inspections": inspections, **_lease_filter_context(request)},
    )


@login_required
@permission_required("leases.add_leaseinspection", raise_exception=True)
@transaction.atomic
def lease_inspection_create(request, lease_id):
    lease = get_object_or_404(
        Lease.objects.select_related("tenant", "unit", "unit__property"),
        pk=lease_id,
    )
    form = LeaseInspectionCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        inspection = form.save(commit=False)
        inspection.lease = lease
        inspection.property = lease.unit.property
        inspection.unit = lease.unit
        inspection.tenant = lease.tenant
        inspection.created_by = request.user
        inspection.save()
        inspection.snapshot_template_items()
        default_status = form.cleaned_data.get("default_status")
        if default_status:
            inspection.details.update(
                status_name=default_status.name,
                status_badge_color=default_status.badge_color,
            )
        inspection.add_audit("created", request.user)
        messages.success(request, "Inspection sheet created from the selected template.")
        return redirect("leases:inspection_detail", inspection_id=inspection.pk)
    return render(
        request,
        "leases/inspection_create.html",
        {"lease": lease, "form": form},
    )


def _summary_for(inspection):
    rows = inspection.details.values("status_name", "status_badge_color").annotate(total=Count("id"))
    return {
        "total_items": inspection.details.count(),
        "completed_percent": inspection.completion_percent,
        "statuses": rows,
    }


def _status_payload(status):
    if not status:
        return {"name": "", "badge_color": ""}
    return {"name": status.name, "badge_color": status.badge_color}


def _detail_payload(detail):
    return {
        "id": detail.pk,
        "category": detail.category,
        "item_name": detail.item_name,
        "status_name": detail.status_name,
        "status_badge_color": detail.status_badge_color,
        "remarks": detail.remarks,
        "damage_cost": str(detail.damage_cost or Decimal("0.00")),
        "display_order": detail.display_order,
        "photo_count": detail.photos.count() if hasattr(detail, "photos") else 0,
    }


def _inspection_pdf_bytes(request, inspection):
    html_string = render_to_string(
        "leases/inspection_pdf.html",
        {"inspection": inspection, "printed_at": timezone.now()},
        request=request,
    )
    return HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()


def _lease_filter_context(request):
    active_only = request.GET.get("active_only", "1") != "0"
    selected_property = request.GET.get("property") or ""
    selected_unit = request.GET.get("unit") or ""
    selected_lease = request.GET.get("lease") or ""
    leases_qs = Lease.objects.select_related("tenant", "unit", "unit__property").order_by(
        "tenant__first_name", "unit__unit_number"
    )
    if active_only:
        leases_qs = leases_qs.filter(status="active")
    if selected_property:
        leases_qs = leases_qs.filter(unit__property_id=selected_property)
    if selected_unit:
        leases_qs = leases_qs.filter(unit_id=selected_unit)
    units_qs = Unit.objects.select_related("property").order_by("property__property_name", "unit_number")
    if selected_property:
        units_qs = units_qs.filter(property_id=selected_property)
    return {
        "filter_properties": Property.objects.order_by("property_name"),
        "filter_units": units_qs,
        "filter_leases": leases_qs,
        "filter_selected_property": str(selected_property),
        "filter_selected_unit": str(selected_unit),
        "filter_selected_lease": str(selected_lease),
        "filter_active_only": active_only,
    }


@login_required
@permission_required("leases.view_leaseinspection", raise_exception=True)
def inspection_detail(request, inspection_id):
    inspection = get_object_or_404(_inspection_queryset(), pk=inspection_id)
    return render(
        request,
        "leases/inspection_detail.html",
        {
            "inspection": inspection,
            "summary": _summary_for(inspection),
            "photo_form": InspectionPhotoForm(),
            "statuses": InspectionStatus.objects.filter(active=True).order_by("display_order", "name"),
            "categories": InspectionCategory.objects.filter(active=True).order_by("display_order", "name"),
            "items": InspectionItem.objects.filter(active=True, category__active=True).select_related("category").order_by("category__display_order", "display_order", "item_name"),
            **_lease_filter_context(request),
        },
    )


@login_required
@permission_required("leases.change_leaseinspection", raise_exception=True)
@transaction.atomic
def inspection_edit(request, inspection_id):
    inspection = get_object_or_404(_inspection_queryset(), pk=inspection_id)
    header_form = LeaseInspectionHeaderForm(request.POST or None, instance=inspection)
    detail_formset = InspectionDetailFormSet(request.POST or None, instance=inspection, prefix="details")
    meter_formset = InspectionMeterReadingFormSet(request.POST or None, instance=inspection, prefix="meters")
    key_formset = InspectionKeyFormSet(request.POST or None, instance=inspection, prefix="keys")
    appliance_formset = InspectionApplianceFormSet(request.POST or None, instance=inspection, prefix="appliances")
    damage_formset = InspectionDamageChargeFormSet(request.POST or None, instance=inspection, prefix="damages")

    if request.method == "POST":
        forms_valid = all(
            [
                header_form.is_valid(),
                detail_formset.is_valid(),
                meter_formset.is_valid(),
                key_formset.is_valid(),
                appliance_formset.is_valid(),
                damage_formset.is_valid(),
            ]
        )
        if forms_valid:
            old = LeaseInspection.objects.get(pk=inspection.pk)
            obj = header_form.save(commit=False)
            now = timezone.now()
            if obj.tenant_signature and not old.tenant_signature:
                obj.tenant_signed_at = now
            if obj.inspector_signature and not old.inspector_signature:
                obj.inspector_signed_at = now
            if obj.manager_signature and not old.manager_signature:
                obj.manager_signed_at = now
            obj.save()
            detail_formset.save()
            meter_formset.save()
            key_formset.save()
            appliance_formset.save()
            damage_formset.save()
            obj.add_audit("edited", request.user)
            messages.success(request, "Inspection sheet updated.")
            return redirect("leases:inspection_detail", inspection_id=obj.pk)

    return render(
        request,
        "leases/inspection_edit.html",
        {
            "inspection": inspection,
            "header_form": header_form,
            "detail_formset": detail_formset,
            "meter_formset": meter_formset,
            "key_formset": key_formset,
            "appliance_formset": appliance_formset,
            "damage_formset": damage_formset,
        },
    )


@login_required
@permission_required("leases.change_leaseinspection", raise_exception=True)
@require_POST
def inspection_photo_add(request, detail_id):
    detail = get_object_or_404(InspectionDetail.objects.select_related("inspection"), pk=detail_id)
    if not detail.allow_photos:
        messages.error(request, "Photos are not enabled for this item.")
        return redirect("leases:inspection_detail", inspection_id=detail.inspection_id)
    files = request.FILES.getlist("image")
    if files:
        photos = []
        caption = request.POST.get("caption", "")
        for uploaded in files:
            photo = InspectionPhoto.objects.create(
                detail=detail,
                image=uploaded,
                caption=caption,
                uploaded_by=request.user,
            )
            photos.append({"id": photo.pk, "url": photo.image.url, "caption": photo.caption})
        detail.inspection.add_audit("photo_added", request.user, {"detail_id": detail.pk})
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "photos": photos})
        messages.success(request, f"{len(photos)} inspection file(s) uploaded.")
    else:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "Photo upload failed."}, status=400)
        messages.error(request, "Photo upload failed.")
    return redirect("leases:inspection_detail", inspection_id=detail.inspection_id)


@login_required
@permission_required("leases.view_leaseinspection", raise_exception=True)
def inspection_photo_download(request, photo_id):
    photo = get_object_or_404(InspectionPhoto, pk=photo_id)
    if not photo.image:
        raise Http404("Photo not found.")
    return FileResponse(photo.image.open("rb"), as_attachment=True, filename=photo.image.name.split("/")[-1])


@login_required
@permission_required("leases.delete_inspectionphoto", raise_exception=True)
@require_POST
def inspection_photo_delete(request, photo_id):
    photo = get_object_or_404(InspectionPhoto.objects.select_related("detail__inspection"), pk=photo_id)
    inspection_id = photo.detail.inspection_id
    photo.delete()
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "photo_id": photo_id})
    messages.success(request, "Inspection photo deleted.")
    return redirect("leases:inspection_detail", inspection_id=inspection_id)


@login_required
@permission_required("leases.change_leaseinspection", raise_exception=True)
@require_POST
def inspection_public_link(request, inspection_id):
    inspection = get_object_or_404(LeaseInspection, pk=inspection_id)
    days = int(request.POST.get("days") or 7)
    inspection.public_is_active = True
    inspection.public_expires_at = timezone.now() + timezone.timedelta(days=days)
    inspection.save(update_fields=["public_is_active", "public_expires_at", "updated_at"])
    inspection.add_audit("public_link_generated", request.user, {"days": days})
    messages.success(request, "Public inspection link generated.")
    return redirect("leases:inspection_detail", inspection_id=inspection.pk)


def _public_detail_from_token(token, detail_id=None):
    inspection = get_object_or_404(_inspection_queryset(), public_token=token)
    if not inspection.public_link_valid:
        raise Http404("Inspection link expired.")
    if detail_id is None:
        return inspection, None
    return inspection, get_object_or_404(InspectionDetail, pk=detail_id, inspection=inspection)


def public_inspection_sign(request, token):
    inspection = get_object_or_404(_inspection_queryset(), public_token=token)
    if not inspection.public_link_valid:
        return render(request, "leases/public_inspection_expired.html", status=410)
    if request.method == "POST":
        inspection.tenant_comments = request.POST.get("tenant_comments", "").strip()
        inspection.tenant_signature = request.POST.get("tenant_signature", "").strip()
        if inspection.tenant_signature and not inspection.tenant_signed_at:
            inspection.tenant_signed_at = timezone.now()
        inspection.public_is_active = False
        inspection.save(update_fields=["tenant_comments", "tenant_signature", "tenant_signed_at", "public_is_active", "updated_at"])
        inspection.add_audit("tenant_public_signed")
        return render(request, "leases/public_inspection_signed.html", {"inspection": inspection})
    return render(
        request,
        "leases/public_inspection_sign.html",
        {
            "inspection": inspection,
            "statuses": InspectionStatus.objects.filter(active=True).order_by("display_order", "name"),
        },
    )


@require_POST
def public_inspection_detail_update_ajax(request, token, detail_id):
    inspection, detail = _public_detail_from_token(token, detail_id)
    field = request.POST.get("field")
    value = (request.POST.get("value") or "").strip()
    if field == "status":
        status = InspectionStatus.objects.filter(pk=value, active=True).first() if value else None
        detail.status_name = status.name if status else ""
        detail.status_badge_color = status.badge_color if status else ""
    elif field == "remarks":
        detail.remarks = value
    else:
        return JsonResponse({"ok": False, "error": "Invalid field."}, status=400)
    detail.save(update_fields=["status_name", "status_badge_color", "remarks"])
    inspection.add_audit("public_detail_updated", extra={"detail_id": detail.pk, "field": field})
    return JsonResponse({"ok": True, "detail": _detail_payload(detail)})


@require_POST
def public_inspection_photo_add_ajax(request, token, detail_id):
    inspection, detail = _public_detail_from_token(token, detail_id)
    if not detail.allow_photos:
        return JsonResponse({"ok": False, "error": "Photos are not enabled for this item."}, status=400)
    files = request.FILES.getlist("image")
    if not files:
        return JsonResponse({"ok": False, "error": "Photo upload failed."}, status=400)
    caption = request.POST.get("caption", "")
    photos = []
    for uploaded in files:
        photo = InspectionPhoto.objects.create(detail=detail, image=uploaded, caption=caption)
        photos.append({"id": photo.pk, "url": photo.image.url, "caption": photo.caption})
    inspection.add_audit("public_photo_added", extra={"detail_id": detail.pk})
    return JsonResponse({"ok": True, "photos": photos})


@login_required
@permission_required("leases.change_leaseinspection", raise_exception=True)
@require_POST
def inspection_approve(request, inspection_id):
    inspection = get_object_or_404(LeaseInspection, pk=inspection_id)
    inspection.status = LeaseInspection.STATUS_APPROVED
    inspection.approved_by = request.user
    inspection.approved_at = timezone.now()
    inspection.public_is_active = False
    if request.POST.get("manager_signature"):
        inspection.manager_signature = request.POST.get("manager_signature")
        inspection.manager_signed_at = inspection.manager_signed_at or timezone.now()
    inspection.save()
    inspection.add_audit("approved", request.user)
    messages.success(request, "Inspection approved.")
    return redirect("leases:inspection_detail", inspection_id=inspection.pk)


@login_required
@permission_required("leases.view_leaseinspection", raise_exception=True)
def inspection_pdf(request, inspection_id):
    inspection = get_object_or_404(_inspection_queryset(), pk=inspection_id)
    pdf = _inspection_pdf_bytes(request, inspection)
    filename = f"inspection-{inspection.pk}-{inspection.inspection_date}.pdf"
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@permission_required("leases.change_leaseinspection", raise_exception=True)
@require_POST
def inspection_pdf_attach(request, inspection_id):
    inspection = get_object_or_404(_inspection_queryset(), pk=inspection_id)
    pdf = _inspection_pdf_bytes(request, inspection)
    date_part = timezone.localdate().strftime("%Y%m%d")
    filename = f"inspection-{inspection.pk}-{slugify(str(inspection.inspection_type))}-{date_part}.pdf"
    document = LeaseDocument(
        lease=inspection.lease,
        display_name=f"{inspection.inspection_type} Inspection #{inspection.pk}",
        category="property_condition_report",
        description=f"Inspection sheet generated from inspection #{inspection.pk}.",
        uploaded_by=request.user,
    )
    document.file.save(filename, ContentFile(pdf), save=True)
    inspection.add_audit("pdf_attached_to_lease_documents", request.user, {"document_id": document.pk})
    messages.success(request, "Inspection PDF attached to lease documents.")
    return redirect("leases:inspection_detail", inspection_id=inspection.pk)


@login_required
@permission_required("leases.change_leaseinspection", raise_exception=True)
@require_POST
def inspection_detail_update_ajax(request, detail_id):
    detail = get_object_or_404(InspectionDetail.objects.select_related("inspection"), pk=detail_id)
    field = request.POST.get("field")
    value = (request.POST.get("value") or "").strip()
    if field == "status":
        status = InspectionStatus.objects.filter(pk=value, active=True).first() if value else None
        detail.status_name = status.name if status else ""
        detail.status_badge_color = status.badge_color if status else ""
    elif field == "remarks":
        detail.remarks = value
    elif field == "damage_cost":
        detail.damage_cost = Decimal(value or "0.00")
    else:
        return JsonResponse({"ok": False, "error": "Invalid field."}, status=400)
    detail.save(update_fields=["status_name", "status_badge_color", "remarks", "damage_cost"])
    detail.inspection.add_audit("detail_ajax_updated", request.user, {"detail_id": detail.pk, "field": field})
    return JsonResponse({"ok": True, "detail": _detail_payload(detail)})


@login_required
@permission_required("leases.change_leaseinspection", raise_exception=True)
@require_POST
def inspection_mark_all_ajax(request, inspection_id):
    inspection = get_object_or_404(LeaseInspection, pk=inspection_id)
    status = get_object_or_404(InspectionStatus, pk=request.POST.get("status_id"), active=True)
    inspection.details.update(status_name=status.name, status_badge_color=status.badge_color)
    inspection.add_audit("mark_all_status", request.user, _status_payload(status))
    return JsonResponse({"ok": True, "status": _status_payload(status), "completion_percent": inspection.completion_percent})


@login_required
@permission_required("leases.change_leaseinspection", raise_exception=True)
@require_POST
def inspection_category_create_ajax(request):
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Category name is required."}, status=400)
    category, _ = InspectionCategory.objects.get_or_create(
        name=name,
        defaults={"display_order": (InspectionCategory.objects.count() + 1) * 10, "active": True},
    )
    return JsonResponse({"ok": True, "category": {"id": category.pk, "name": category.name}})


@login_required
@permission_required("leases.change_leaseinspection", raise_exception=True)
@require_POST
def inspection_item_create_ajax(request):
    category_id = request.POST.get("category_id")
    item_name = (request.POST.get("item_name") or "").strip()
    if not category_id or not item_name:
        return JsonResponse({"ok": False, "error": "Category and item are required."}, status=400)
    category = get_object_or_404(InspectionCategory, pk=category_id)
    item, _ = InspectionItem.objects.get_or_create(
        category=category,
        item_name=item_name,
        defaults={
            "display_order": (InspectionItem.objects.filter(category=category).count() + 1) * 10,
            "required": False,
            "allow_photos": True,
            "allow_damage_cost": True,
            "allow_notes": True,
            "active": True,
        },
    )
    return JsonResponse({"ok": True, "item": {"id": item.pk, "name": item.item_name, "category_id": category.pk}})


@login_required
@permission_required("leases.change_leaseinspection", raise_exception=True)
@require_POST
def inspection_row_add_ajax(request, inspection_id):
    inspection = get_object_or_404(LeaseInspection, pk=inspection_id)
    category = get_object_or_404(InspectionCategory, pk=request.POST.get("category_id"), active=True)
    item = get_object_or_404(InspectionItem, pk=request.POST.get("item_id"), category=category, active=True)
    next_order = (inspection.details.aggregate(max_order=Max("display_order")).get("max_order") or 0) + 10
    detail = InspectionDetail.objects.create(
        inspection=inspection,
        category=category.name,
        item_name=item.item_name,
        display_order=next_order,
        required=item.required,
        allow_photos=item.allow_photos,
        allow_damage_cost=item.allow_damage_cost,
        allow_notes=item.allow_notes,
    )
    inspection.add_audit("detail_row_added", request.user, {"detail_id": detail.pk})
    return JsonResponse({"ok": True, "detail": _detail_payload(detail)})


@login_required
@permission_required("leases.view_leaseinspection", raise_exception=True)
def inspection_compare(request, lease_id):
    lease = get_object_or_404(Lease.objects.select_related("tenant", "unit", "unit__property"), pk=lease_id)
    inspections = _inspection_queryset().filter(lease=lease)
    left_id = request.GET.get("left")
    right_id = request.GET.get("right")
    left = inspections.filter(pk=left_id).first() if left_id else inspections.filter(inspection_type__name__icontains="Move In").first()
    right = inspections.filter(pk=right_id).first() if right_id else inspections.filter(inspection_type__name__icontains="Move Out").first()
    rows = []
    if left and right:
        left_map = {(d.category.lower(), d.item_name.lower()): d for d in left.details.all()}
        right_map = {(d.category.lower(), d.item_name.lower()): d for d in right.details.all()}
        keys = sorted(set(left_map) | set(right_map))
        for category, item in keys:
            lrow = left_map.get((category, item))
            rrow = right_map.get((category, item))
            same = (getattr(lrow, "status_name", "") or "") == (getattr(rrow, "status_name", "") or "")
            damaged = any(
                word in ((getattr(rrow, "status_name", "") or "").lower())
                for word in ["damage", "missing", "repair"]
            )
            diff = "No Change" if same else ("Damaged" if damaged else "Changed")
            rows.append({"left": lrow, "right": rrow, "difference": diff})
    return render(
        request,
        "leases/inspection_compare.html",
        {"lease": lease, "inspections": inspections, "left": left, "right": right, "rows": rows},
    )


@login_required
@permission_required("invoices.add_invoice", raise_exception=True)
@require_POST
@transaction.atomic
def inspection_generate_damage_invoice(request, inspection_id):
    inspection = get_object_or_404(LeaseInspection.objects.select_related("lease"), pk=inspection_id)
    charges = list(
        inspection.damage_charges.filter(
            charge_tenant=True,
            generate_invoice=True,
            invoice__isnull=True,
            repair_cost__gt=Decimal("0.00"),
        )
    )
    if not charges:
        messages.info(request, "No pending damage charges selected for invoice generation.")
        return redirect("leases:inspection_detail", inspection_id=inspection.pk)

    category, _ = ItemCategory.objects.get_or_create(name="Damage Charges")
    invoice = Invoice.objects.create(
        lease=inspection.lease,
        issue_date=timezone.localdate(),
        due_date=timezone.localdate(),
        status="sent",
        description=f"Damage charges from inspection #{inspection.pk}",
        notes=f"Generated from {inspection.inspection_type.name} inspection dated {inspection.inspection_date}.",
    )
    for charge in charges:
        InvoiceItem.objects.create(
            invoice=invoice,
            category=category,
            description=charge.damage_description[:200],
            amount=charge.repair_cost,
        )
        charge.invoice = invoice
        charge.save(update_fields=["invoice"])
    inspection.add_audit("damage_invoice_generated", request.user, {"invoice_id": invoice.pk})
    messages.success(request, f"Damage invoice #{invoice.invoice_number} generated.")
    return redirect("invoices:invoice_detail", pk=invoice.pk)
