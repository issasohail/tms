from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Count, Prefetch, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from weasyprint import HTML

from invoices.models import Invoice, InvoiceItem, ItemCategory
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


def _setting_config(kind):
    config = SETTINGS_CONFIG.get(kind)
    if not config:
        raise Http404("Unknown inspection setting.")
    return config


@login_required
@permission_required("leases.view_inspectiontemplate", raise_exception=True)
def inspection_settings_list(request, kind):
    config = _setting_config(kind)
    rows = config["model"].objects.all()
    if kind == "items":
        rows = rows.select_related("category")
    elif kind == "templates":
        rows = rows.prefetch_related("items")
    return render(
        request,
        "leases/inspection_settings_list.html",
        {"kind": kind, "config": config, "rows": rows},
    )


@login_required
@permission_required("leases.add_inspectiontemplate", raise_exception=True)
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
@permission_required("leases.change_inspectiontemplate", raise_exception=True)
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
    lease = get_object_or_404(
        Lease.objects.select_related("tenant", "unit", "unit__property"),
        pk=lease_id,
    )
    inspections = _inspection_queryset().filter(lease=lease)
    return render(
        request,
        "leases/inspection_list.html",
        {"lease": lease, "inspections": inspections},
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
        if not inspection.inspector_id:
            inspection.inspector = request.user
        inspection.save()
        inspection.snapshot_template_items()
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
    form = InspectionPhotoForm(request.POST, request.FILES)
    if form.is_valid():
        photo = form.save(commit=False)
        photo.detail = detail
        photo.uploaded_by = request.user
        photo.save()
        detail.inspection.add_audit("photo_added", request.user, {"detail_id": detail.pk})
        messages.success(request, "Inspection photo uploaded.")
    else:
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
    return render(request, "leases/public_inspection_sign.html", {"inspection": inspection})


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
    html_string = render_to_string(
        "leases/inspection_pdf.html",
        {"inspection": inspection, "printed_at": timezone.now()},
        request=request,
    )
    pdf = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
    filename = f"inspection-{inspection.pk}-{inspection.inspection_date}.pdf"
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


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
