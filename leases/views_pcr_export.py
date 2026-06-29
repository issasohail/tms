# leases/views_pcr_export.py
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from urllib.parse import quote as urlquote
from .models import Lease
from .models_pcr import PropertyConditionReport
from .services.export_photos_pdf import export_photos_docx, export_photos_pdf


def _download_response(file_obj, content_type):
    data = file_obj.read()
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = (
        f'attachment; filename="{file_obj.name}"; filename*=UTF-8\'\'{urlquote(file_obj.name)}'
    )
    return response


@login_required
def export_photos_to_pdf_and_attach(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    pcr, _ = PropertyConditionReport.objects.get_or_create(lease=lease)

    if not pcr.photos.filter(image__isnull=False).exists():
        messages.error(request, "No photos to export.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    pdf_file = export_photos_pdf(pcr)  # returns a ContentFile with name
    lease.condition_photos_signed.save(pdf_file.name, pdf_file, save=True)

    pdf_file.seek(0)
    return _download_response(pdf_file, "application/pdf")


@login_required
def export_photos_to_word(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    pcr, _ = PropertyConditionReport.objects.get_or_create(lease=lease)

    if not pcr.photos.filter(image__isnull=False).exists():
        messages.error(request, "No photos to export.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    docx_file = export_photos_docx(pcr)
    return _download_response(
        docx_file,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
