from .models import Lease
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models_pcr import PropertyConditionReport, PCRPhoto
from .models_pcr_video import PCRVideo
from .serializers import PCRSerializer, PCRPhotoSerializer, PCRVideoSerializer
from .services.pcr_export import export_pcr_to_pdf


class PCRViewSet(viewsets.ModelViewSet):
    queryset = PropertyConditionReport.objects.all()
    serializer_class = PCRSerializer

    @action(detail=True, methods=["post"])
    def lock_and_export(self, request, pk=None):
        pcr = self.get_object()
        if pcr.locked:
            return Response({"detail": "Already locked."}, status=400)
        # Optionally: validate all videos processing_status == "ok"

        def stream_url_for_video(v):
            return v.encoded_mp4.url if v.encoded_mp4 else v.file.url
        pdf = export_pcr_to_pdf(pcr, stream_url_for_video)
        pcr.locked = True
        pcr.save(update_fields=["locked"])
        # Also attach to Lease if you like:
        if hasattr(pcr.lease, "condition_report_pdf"):
            pcr.lease.condition_report_pdf = pdf
            pcr.lease.save(update_fields=["condition_report_pdf"])
        return Response(self.get_serializer(pcr).data, status=200)


class PCRPhotoViewSet(viewsets.ModelViewSet):
    queryset = PCRPhoto.objects.all()
    serializer_class = PCRPhotoSerializer


class PCRVideoViewSet(viewsets.ModelViewSet):
    queryset = PCRVideo.objects.all()
    serializer_class = PCRVideoSerializer

# leases/views_pcr.py


def _get_or_create_pcr(lease):
    pcr, _ = PropertyConditionReport.objects.get_or_create(lease=lease)
    return pcr


@login_required
def pcr_gallery(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    pcr = _get_or_create_pcr(lease)
    return render(request, "leases/_pcr_gallery.html", {"lease": lease, "pcr": pcr})


@login_required
@require_POST
def pcr_photo_upload(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    pcr = _get_or_create_pcr(lease)
    files = request.FILES.getlist(
        "photos[]") or request.FILES.getlist("photos") or []
    room = request.POST.get("room", "")  # optional shared room label
    comment = request.POST.get("comment", "")
    if not files:
        return HttpResponseBadRequest("No files")
    for f in files:
        PCRPhoto.objects.create(pcr=pcr, image=f, room=room, comment=comment)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def pcr_photo_delete(request, photo_id):
    photo = get_object_or_404(PCRPhoto, pk=photo_id)
    lease_id = photo.pcr.lease_id
    photo.delete()
    return JsonResponse({"ok": True, "lease_id": lease_id})
