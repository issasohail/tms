from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from maintenance.models import MaintenanceRequest

from .forms import HandymanCategoryForm, HandymanProfileForm, MaintenanceHandymanAssignmentForm
from .models import HandymanCategory, HandymanProfile
from .services import assign_handyman


class HandymanListView(LoginRequiredMixin, ListView):
    model = HandymanProfile
    template_name = "handyman/handyman_list.html"
    context_object_name = "handymen"
    paginate_by = 50

    def get_queryset(self):
        qs = HandymanProfile.objects.prefetch_related("categories").annotate(
            rating_avg=Avg("ratings__rating"),
            ratings_total=Count("ratings", distinct=True),
            completed_jobs_total=Count("assignments", filter=Q(assignments__status="completed"), distinct=True),
        )
        q = (self.request.GET.get("q") or "").strip()
        category = self.request.GET.get("category")
        preferred = self.request.GET.get("preferred")
        active = self.request.GET.get("active")
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(phone__icontains=q) | Q(whatsapp_number__icontains=q))
        if category:
            qs = qs.filter(categories__id=category)
        if preferred == "1":
            qs = qs.filter(is_preferred=True)
        if active == "1":
            qs = qs.filter(is_active=True)
        elif active == "0":
            qs = qs.filter(is_active=False)
        return qs.order_by("-is_preferred", "full_name").distinct()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["categories"] = HandymanCategory.objects.filter(is_active=True)
        ctx["filters"] = self.request.GET
        return ctx


class HandymanCreateView(LoginRequiredMixin, CreateView):
    model = HandymanProfile
    form_class = HandymanProfileForm
    template_name = "handyman/handyman_form.html"

    def get_success_url(self):
        return self.object.get_absolute_url()


class HandymanUpdateView(LoginRequiredMixin, UpdateView):
    model = HandymanProfile
    form_class = HandymanProfileForm
    template_name = "handyman/handyman_form.html"

    def get_success_url(self):
        return self.object.get_absolute_url()


class HandymanDetailView(LoginRequiredMixin, DetailView):
    model = HandymanProfile
    template_name = "handyman/handyman_detail.html"
    context_object_name = "handyman"

    def get_queryset(self):
        return HandymanProfile.objects.prefetch_related(
            "categories",
            "assignments__maintenance_request__unit__property",
            "assignments__attachments",
            "ratings__maintenance_request",
        )


@login_required
def category_settings(request):
    form = HandymanCategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Handyman category saved.")
        redirect_url = f"{reverse_lazy('handyman:category_settings')}?embed=1" if request.GET.get("embed") == "1" else reverse_lazy("handyman:category_settings")
        return redirect(redirect_url)
    sort = request.GET.get("sort")
    ordering = ("name", "sort_order") if sort == "name" else ("sort_order", "name")
    return render(
        request,
        "handyman/category_settings.html",
        {
            "form": form,
            "categories": HandymanCategory.objects.order_by(*ordering),
            "sort": sort,
            "base_template": "handyman/embedded_base.html" if request.GET.get("embed") == "1" else "base.html",
            "is_embedded": request.GET.get("embed") == "1",
        },
    )


@login_required
@require_POST
def category_inline_update(request, pk):
    category = get_object_or_404(HandymanCategory, pk=pk)
    field = request.POST.get("field")
    value = (request.POST.get("value") or "").strip()
    if field == "name":
        if not value:
            return HttpResponseBadRequest("Name required")
        category.name = value
    elif field == "sort_order":
        try:
            category.sort_order = int(value or 0)
        except ValueError:
            return HttpResponseBadRequest("Invalid sort order")
    elif field == "is_active":
        category.is_active = value in ("1", "true", "on", "yes")
    else:
        return HttpResponseBadRequest("Invalid field")
    category.save(update_fields=[field])
    return JsonResponse({"ok": True})


@login_required
@require_POST
def handyman_inline_update(request, pk):
    handyman = get_object_or_404(HandymanProfile, pk=pk)
    field = request.POST.get("field")
    value = request.POST.get("value")
    if field in {"phone", "whatsapp_number"}:
        setattr(handyman, field, (value or "").strip())
        handyman.save(update_fields=[field, "updated_at"])
        return JsonResponse({"ok": True, "value": getattr(handyman, field) or "-"})
    if field == "categories":
        ids = request.POST.getlist("value")
        categories = HandymanCategory.objects.filter(pk__in=ids, is_active=True)
        handyman.categories.set(categories)
        labels = [category.name for category in handyman.categories.all()]
        return JsonResponse({"ok": True, "value": ", ".join(labels) or "-"})
    if field == "is_preferred":
        handyman.is_preferred = str(value).lower() in {"1", "true", "yes", "on"}
        handyman.save(update_fields=["is_preferred", "updated_at"])
        return JsonResponse({"ok": True, "value": "Preferred" if handyman.is_preferred else "-"})
    if field == "is_active":
        handyman.is_active = str(value).lower() in {"1", "true", "yes", "on"}
        handyman.save(update_fields=["is_active", "updated_at"])
        return JsonResponse({"ok": True, "value": "Active" if handyman.is_active else "Inactive"})
    return JsonResponse({"ok": False, "error": "Unsupported field."}, status=400)


@login_required
@require_POST
def assign_to_maintenance(request, request_id):
    request_obj = get_object_or_404(MaintenanceRequest, pk=request_id)
    form = MaintenanceHandymanAssignmentForm(request.POST)
    if form.is_valid():
        assignment = assign_handyman(
            request_obj,
            form.cleaned_data["handyman"],
            assigned_by=request.user,
            notes=form.cleaned_data.get("notes", ""),
            status=form.cleaned_data.get("status"),
        )
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "ok": True,
                "handyman": assignment.handyman.full_name,
                "status": assignment.get_status_display(),
                "assigned_at": assignment.assigned_at.strftime("%Y-%m-%d"),
            })
        messages.success(request, f"Assigned to {assignment.handyman.full_name}.")
    else:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "Please select a valid handyman."}, status=400)
        messages.error(request, "Please select a valid handyman.")
    return redirect(request_obj.get_absolute_url())
