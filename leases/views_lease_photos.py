# leases/views_lease_photos.py
from .services.export_lease_photos_pdf import export_lease_photos_pdf
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse, FileResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
import mimetypes
import os
from urllib.parse import quote as urlquote

from .models import Lease
from .models_lease_photos import LeaseMedia, _folder_name_for_lease
import logging
logger = logging.getLogger(__name__)
# --- RENDER HELPERS -------------------------------------------------

# leases/views_lease_photos.py

from django.http import HttpResponse
from django.template.loader import render_to_string


# leases/views_lease_photos.py
import inspect

def _safe_export_call(lease, layout=None):
    """
    Call export_lease_photos_pdf() and pass layout only if supported.
    Returns (name, fileobj) or (None, None).
    """
    try:
        from .services.export_lease_photos_pdf import export_lease_photos_pdf
    except Exception:
        return (None, None)

    try:
        sig = inspect.signature(export_lease_photos_pdf)
        if "layout" in sig.parameters:
            return export_lease_photos_pdf(lease, layout=layout)
        else:
            return export_lease_photos_pdf(lease)
    except TypeError:
        # defensive: some wrappers raise TypeError differently
        try:
            return export_lease_photos_pdf(lease)
        except Exception:
            return (None, None)

def _export_pdf_for_lease(lease, layout: str | None = None):
    try:
        from .services.export_lease_photos_pdf import export_lease_photos_pdf
    except Exception:
        return

    if not layout:
        from django.conf import settings as dj_settings
        layout = getattr(dj_settings, "LEASE_PHOTOS_PDF_LAYOUT", "4up")

    # Prefer calling with layout if your service supports it; else ignore
    try:
        res = export_lease_photos_pdf(lease, layout=layout)
    except TypeError:
        res = export_lease_photos_pdf(lease, layout=layout)

    if not res:
        return
    name, fileobj = res
    if not fileobj:
        return

    folder = _folder_name_for_lease(lease)
    base_dir = f"leases/lease_photos/{folder}"
    pdf_path = f"{base_dir}/{folder}.pdf"

    from django.core.files.storage import default_storage
    from django.core.files.base import ContentFile

    try:
        pdf_bytes = fileobj.read()
        # overwrite canonical PDF
        if default_storage.exists(pdf_path):
            try:
                default_storage.delete(pdf_path)
            except Exception:
                pass
        default_storage.save(pdf_path, ContentFile(pdf_bytes))

        # remove any older PDFs with different names (cleanup)
        if default_storage.exists(base_dir):
            _, filenames = default_storage.listdir(base_dir)
            for fn in filenames:
                if fn.lower().endswith(".pdf") and fn != f"{folder}.pdf":
                    try:
                        default_storage.delete(f"{base_dir}/{fn}")
                    except Exception:
                        pass
    except Exception:
        pass

def _grid_response(request, lease):
    html = render_to_string(
        "leases/_photos_grid.html",
        {"lease": lease, "media": lease.media.all().order_by("-created_at")},
        request=request,
    )
    resp = HttpResponse(html)               # <-- wrap in HttpResponse
    resp["HX-Trigger"] = '{"photos:changed": true}'
    return resp

@login_required
def photos_grid(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    return _grid_response(request, lease)   # <-- return the HttpResponse

# --- PAGES ----------------------------------------------------------


@login_required
def photos_page(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    return render(request, "leases/photos_page.html", {"lease": lease})



# --- ADD / UPDATE / DELETE -----------------------------------------
@login_required
@require_POST
def photo_add(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    title = (request.POST.get("title") or "").strip()[:120]
    description = (request.POST.get("description") or "").strip()[:300]

    # optional layout from form (see §5)
    pdf_layout = (request.POST.get("pdf_layout") or "").strip().lower()
    if pdf_layout not in {"1up", "2up", "4up"}:
        pdf_layout = None  # fall back to settings later

    files = (
        request.FILES.getlist("photos[]")
        or request.FILES.getlist("photos")
        or request.FILES.getlist("images")
        or request.FILES.getlist("file")
    )
    if not files:
        return HttpResponseBadRequest("No files uploaded")

    for f in files:
        lm = LeaseMedia(lease=lease, title=title, description=description)
        lm.file = f
        lm.save()               # processes -> stamped /photos + /thumbs
        lm.refresh_from_db()

    # Build the PDF **once** after all files are saved
    try:
        _export_pdf_for_lease(lease, layout=pdf_layout)
    except Exception as e:
        # swallow export errors so adding photos never 500s
        logger.exception("PDF export after batch add failed: %s", e)

        pass

    lease.refresh_from_db()
    return _grid_response(request, lease)



@login_required
@require_POST
def photo_update(request, photo_id):
    p = LeaseMedia.objects.select_related(
            "lease", "lease__unit", "lease__unit__property"
        ).get(pk=photo_id)

    p.title = (request.POST.get("title") or "").strip()[:120]
    p.description = (request.POST.get("description") or "").strip()[:300]
    p.save(update_fields=["title", "description"])

    if p.media_type == "image":
        p.reburn_from_stamped()

    try:
        _export_pdf_for_lease(p.lease, layout=None)
    except Exception:
        pass


    p.refresh_from_db()
    html = render_to_string("leases/_photo_card.html", {"p": p}, request=request)
    resp = HttpResponse(html)
    resp["HX-Trigger"] = '{"photos:changed": true}'
    return resp



@login_required
@require_POST
def photo_delete(request, photo_id):
    p = get_object_or_404(LeaseMedia, pk=photo_id)
    lease = p.lease
    p.delete()
    lease.refresh_from_db()
    return _grid_response(request, lease)

# Deleted photos views (unchanged logic)


@login_required
def deleted_photos_view(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    base_path = f"leases/lease_photos/{_folder_name_for_lease(lease)}/deleted_photos"
    files = []
    if default_storage.exists(base_path):
        _, filenames = default_storage.listdir(base_path)
        for f in filenames:
            files.append(
                {"name": f, "url": default_storage.url(f"{base_path}/{f}")})
    return render(request, "leases/deleted_photos.html", {"lease": lease, "files": files})


@login_required
def deleted_photos_delete(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    names = request.POST.getlist("files")
    base_path = f"leases/lease_photos/{_folder_name_for_lease(lease)}/deleted_photos"
    for n in names:
        path = f"{base_path}/{n}"
        if default_storage.exists(path):
            default_storage.delete(path)
    return redirect("deleted_photos_view", lease_id=lease_id)


@login_required
def deleted_photos_delete_all(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    base_path = f"leases/lease_photos/{_folder_name_for_lease(lease)}/deleted_photos"
    if default_storage.exists(base_path):
        _, filenames = default_storage.listdir(base_path)
        for f in filenames:
            default_storage.delete(f"{base_path}/{f}")
    return redirect("deleted_photos_view", lease_id=lease_id)

# ----------------------------------------------------------------------
# PDF EXPORT (unchanged)
# ----------------------------------------------------------------------

# leases/views_lease_photos.py

def _normalize_layout_param(request):
    """
    Accept ?layout=1up|2up|4up OR ?ppg=1|2|4 and normalize to '1up'/'2up'/'4up'.
    Falls back to settings.LEASE_PHOTOS_PDF_LAYOUT if invalid/missing.
    """
    from django.conf import settings as dj_settings

    raw = (request.GET.get("layout") or request.GET.get("ppg") or "").strip().lower()
    m = {"1": "1up", "2": "2up", "4": "4up",
         "1up": "1up", "2up": "2up", "4up": "4up"}
    layout = m.get(raw)
    if layout not in {"1up", "2up", "4up"}:
        layout = getattr(dj_settings, "LEASE_PHOTOS_PDF_LAYOUT", "4up")
    return layout


@login_required
def photos_export_pdf(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    layout = _normalize_layout_param(request)

    # Save/export to the lease (server-side copy)
    _export_pdf_for_lease(lease, layout=layout)

    messages.success(request, f"Exported photos PDF ({layout}) saved to lease.")
    return redirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def photos_export_pdf_stream(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    layout = _normalize_layout_param(request)

    # Call your exporter (layout-aware if supported)
    try:
        name, fileobj = _safe_export_call(lease, layout=layout)
    except TypeError:
        name, fileobj = _safe_export_call(lease, layout=layout)  # backward-compatible

    if not fileobj:
        messages.error(request, "No photos to export.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    pdf_bytes = fileobj.read()
    disposition = "attachment" if request.GET.get("download") else "inline"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'{disposition}; filename="{name}"; filename*=UTF-8\'\'{urlquote(name)}'
    )
    return response



# ----------------------------------------------------------------------
# SINGLE MEDIA VIEWER
# ----------------------------------------------------------------------


@login_required
def photo_viewer(request, photo_id):
    p = get_object_or_404(LeaseMedia, pk=photo_id)
    return render(request, "leases/photo_viewer.html", {"p": p, "lease": p.lease})

# ----------------------------------------------------------------------
# FRIENDLY DOWNLOAD
# ----------------------------------------------------------------------


@login_required
def photo_download(request, photo_id):
    """Force a browser download with a friendly filename."""
    p = get_object_or_404(LeaseMedia, pk=photo_id)
    path = p.file.name
    if not default_storage.exists(path):
        return HttpResponseBadRequest("File missing")

    fh = default_storage.open(path, "rb")
    ext = os.path.splitext(path)[1] or ".jpg"
    friendly = p.build_friendly_filename(force_ext=ext)
    resp = FileResponse(fh, as_attachment=True, filename=friendly)
    ctype, _ = mimetypes.guess_type(friendly)
    if ctype:
        resp["Content-Type"] = ctype
    return resp
