# leases/views_pcr_export.py
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import Lease
from .models_pcr import PropertyConditionReport
from .services.export_photos_pdf import export_photos_pdf


@login_required
def export_photos_to_pdf_and_attach(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    pcr, _ = PropertyConditionReport.objects.get_or_create(lease=lease)

    if not pcr.photos.exists():
        messages.error(request, "No photos to export.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    pdf_file = export_photos_pdf(pcr)  # returns a ContentFile with name
    # Save directly to the Lease field you added:
    lease.condition_photos_signed.save(pdf_file.name, pdf_file, save=True)

    messages.success(request, "Exported photos to PDF and attached to lease.")
    return redirect(request.META.get("HTTP_REFERER", "/"))
