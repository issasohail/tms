import mimetypes
import os
from urllib.parse import quote as urlquote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import GlobalSettings
from leases.whatsapp import build_whatsapp_url
from .models import Lease, LeaseDocument, LeaseDocumentCategory, LeaseFileShareLink


def _safe_extension(filename):
    return os.path.splitext(filename or "")[1].lower().lstrip(".")


def _share_days():
    settings_obj = GlobalSettings.get_solo()
    return max(1, getattr(settings_obj, "lease_file_share_valid_days", 7) or 7)


def _active_documents(lease):
    return lease.documents.filter(is_active=True).select_related("uploaded_by", "lease_history")


@login_required
@require_POST
def lease_file_upload(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    category = request.POST.get("category") or "other"
    if not LeaseDocumentCategory.objects.filter(code=category, is_active=True).exists():
        category = "other"
    description = (request.POST.get("description") or "").strip()

    files = request.FILES.getlist("files") or request.FILES.getlist("file")
    if not files:
        messages.error(request, "Please choose at least one file.")
        if request.POST.get("redirect_to") == "edit":
            return redirect("leases:lease_update", pk=lease.pk)
        return redirect("leases:lease_detail", pk=lease.pk)

    uploaded = 0
    for upload in files:
        ext = _safe_extension(upload.name)
        if ext not in LeaseDocument.SAFE_EXTENSIONS:
            messages.error(request, f"{upload.name} was skipped: unsupported file type.")
            continue
        doc = LeaseDocument(
            lease=lease,
            lease_history=None,
            category=category,
            description=description,
            original_filename=upload.name[:255],
            display_name=upload.name[:255],
            uploaded_by=request.user,
        )
        doc.file = upload
        doc.save()
        uploaded += 1

    if uploaded:
        messages.success(request, f"Uploaded {uploaded} lease file(s).")
    if request.POST.get("redirect_to") == "edit":
        return redirect("leases:lease_update", pk=lease.pk)
    return redirect("leases:lease_detail", pk=lease.pk)


@login_required
def lease_file_serve(request, document_id, mode="view"):
    document = get_object_or_404(LeaseDocument, pk=document_id, is_active=True)
    if not document.file_exists:
        return HttpResponseBadRequest("File missing")
    fh = default_storage.open(document.file.name, "rb")
    filename = document.display_name or document.original_filename or os.path.basename(document.file.name)
    content_type, _ = mimetypes.guess_type(filename)
    response = FileResponse(
        fh,
        as_attachment=(mode == "download"),
        filename=filename,
        content_type=content_type or "application/octet-stream",
    )
    response["Content-Disposition"] += f"; filename*=UTF-8''{urlquote(filename)}"
    return response


@login_required
def lease_file_view(request, document_id):
    return lease_file_serve(request, document_id, "view")


@login_required
def lease_file_download(request, document_id):
    return lease_file_serve(request, document_id, "download")


@login_required
@require_POST
def lease_file_deactivate(request, document_id):
    document = get_object_or_404(LeaseDocument, pk=document_id, is_active=True)
    lease_pk = document.lease_id
    document.is_active = False
    document.save(update_fields=["is_active"])
    messages.success(request, "Lease file deleted.")
    return redirect("leases:lease_detail", pk=lease_pk)


@login_required
@require_POST
def lease_file_category_update(request, document_id):
    document = get_object_or_404(LeaseDocument, pk=document_id, is_active=True)
    category = request.POST.get("category") or "other"
    if not LeaseDocumentCategory.objects.filter(code=category, is_active=True).exists():
        return JsonResponse({"ok": False, "error": "Invalid category."}, status=400)
    document.category = category
    document.save(update_fields=["category"])
    return JsonResponse({"ok": True, "category": document.category, "category_label": document.category_label})


def _tenant_share_phone(lease):
    tenant = getattr(lease, "tenant", None)
    return (
        getattr(tenant, "phone", None)
        or getattr(tenant, "phone2", None)
        or getattr(tenant, "phone3", None)
        or ""
    )


def _redirect_to_whatsapp_or_detail(request, lease, share_url, label):
    tenant_name = lease.tenant.get_full_name() if getattr(lease, "tenant", None) else "Tenant"
    message = (
        f"Dear {tenant_name},\n\n"
        f"Please use this secure link to access your lease {label}:\n{share_url}\n\n"
        f"This link expires in {_share_days()} day(s)."
    )
    settings_obj = GlobalSettings.get_solo()
    whatsapp_url = build_whatsapp_url(
        _tenant_share_phone(lease),
        message,
        country_code=getattr(settings_obj, "country_code", "+92"),
    )
    if not whatsapp_url:
        messages.warning(request, f"Share link created, but tenant phone is missing: {share_url}")
        return redirect("leases:lease_detail", pk=lease.pk)
    return redirect(whatsapp_url)


@login_required
@require_POST
def lease_files_share_all(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    link = LeaseFileShareLink.objects.create(
        lease=lease,
        document=None,
        expires_at=timezone.now() + timezone.timedelta(days=_share_days()),
        created_by=request.user,
    )
    url = request.build_absolute_uri(reverse("public_lease_files_share_root", args=[link.token]))
    return _redirect_to_whatsapp_or_detail(request, lease, url, "files")


@login_required
@require_POST
def lease_file_share_one(request, document_id):
    document = get_object_or_404(LeaseDocument, pk=document_id, is_active=True)
    link = LeaseFileShareLink.objects.create(
        lease=document.lease,
        document=document,
        expires_at=timezone.now() + timezone.timedelta(days=_share_days()),
        created_by=request.user,
    )
    url = request.build_absolute_uri(reverse("public_file_share_root", args=[link.token]))
    return _redirect_to_whatsapp_or_detail(request, document.lease, url, f"file ({document.display_name})")


def _valid_share_or_template(token):
    link = get_object_or_404(LeaseFileShareLink.objects.select_related("lease", "document", "lease__tenant", "lease__unit", "lease__unit__property"), token=token)
    if not link.is_valid:
        return link, False
    return link, True


def public_lease_files_share(request, token):
    link, valid = _valid_share_or_template(token)
    if not valid:
        return render(request, "leases/public_file_share_expired.html", {"link": link}, status=410)
    docs = _active_documents(link.lease)
    return render(request, "leases/public_lease_files_share.html", {"link": link, "lease": link.lease, "documents": docs})


def public_lease_file_share(request, token):
    link, valid = _valid_share_or_template(token)
    if not valid:
        return render(request, "leases/public_file_share_expired.html", {"link": link}, status=410)
    if not link.document or not link.document.is_active:
        return HttpResponseForbidden("File is not available.")
    return render(request, "leases/public_lease_file_share.html", {"link": link, "lease": link.lease, "document": link.document})


def public_lease_file_download(request, token):
    link, valid = _valid_share_or_template(token)
    if not valid or not link.document or not link.document.is_active:
        return render(request, "leases/public_file_share_expired.html", {"link": link}, status=410)
    document = link.document
    if not document.file_exists:
        return HttpResponseBadRequest("File missing")
    fh = default_storage.open(document.file.name, "rb")
    filename = document.display_name or document.original_filename or os.path.basename(document.file.name)
    content_type, _ = mimetypes.guess_type(filename)
    return FileResponse(fh, as_attachment=True, filename=filename, content_type=content_type or "application/octet-stream")


def public_lease_shared_document_download(request, token, document_id):
    link, valid = _valid_share_or_template(token)
    if not valid or link.document_id is not None:
        return render(request, "leases/public_file_share_expired.html", {"link": link}, status=410)
    document = get_object_or_404(LeaseDocument, pk=document_id, lease=link.lease, is_active=True)
    if not document.file_exists:
        return HttpResponseBadRequest("File missing")
    fh = default_storage.open(document.file.name, "rb")
    filename = document.display_name or document.original_filename or os.path.basename(document.file.name)
    content_type, _ = mimetypes.guess_type(filename)
    return FileResponse(fh, as_attachment=True, filename=filename, content_type=content_type or "application/octet-stream")
