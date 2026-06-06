from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from properties.models import Property, Unit

from .forms import MaintenanceRequestForm, MaintenanceRequestMediaForm
from .models import (
    MaintenanceRequest,
    MaintenanceRequestMedia,
    MaintenanceRequestStatusLog,
)


class MaintenanceRequestListView(LoginRequiredMixin, ListView):
    model = MaintenanceRequest
    template_name = "maintenance/request_list.html"
    context_object_name = "requests"
    paginate_by = 50

    def get_queryset(self):
        qs = (
            MaintenanceRequest.objects
            .select_related("tenant", "building", "unit", "lease", "assigned_to")
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
        response = super().form_valid(form)
        MaintenanceRequestStatusLog.objects.create(
            request=self.object,
            old_status="",
            new_status=self.object.status,
            changed_by=self.request.user,
            notes="Request created.",
        )
        self._save_uploads()
        messages.success(self.request, "Maintenance request created.")
        return response

    def _save_uploads(self):
        for upload in self.request.FILES.getlist("files"):
            MaintenanceRequestMedia.objects.create(
                request=self.object,
                file=upload,
                original_filename=upload.name,
                uploaded_by=self.request.user,
            )


class MaintenanceRequestUpdateView(LoginRequiredMixin, UpdateView):
    model = MaintenanceRequest
    form_class = MaintenanceRequestForm
    template_name = "maintenance/request_form.html"

    def form_valid(self, form):
        old_status = MaintenanceRequest.objects.filter(pk=self.object.pk).values_list("status", flat=True).first()
        form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        if old_status != self.object.status:
            MaintenanceRequestStatusLog.objects.create(
                request=self.object,
                old_status=old_status or "",
                new_status=self.object.status,
                changed_by=self.request.user,
                notes=form.cleaned_data.get("admin_notes") or "",
            )
        self._save_uploads()
        messages.success(self.request, "Maintenance request updated.")
        return response

    def _save_uploads(self):
        for upload in self.request.FILES.getlist("files"):
            MaintenanceRequestMedia.objects.create(
                request=self.object,
                file=upload,
                original_filename=upload.name,
                uploaded_by=self.request.user,
            )


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
        messages.error(request, "Please confirm before deleting the maintenance file.")
        return redirect(request_obj.get_absolute_url())
    media.is_active = False
    media.save(update_fields=["is_active"])
    messages.success(request, "Maintenance file removed from active view.")
    return redirect(request_obj.get_absolute_url())
