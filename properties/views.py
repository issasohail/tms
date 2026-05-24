from utils.pdf_export import PDFTableExport, TableExport
from .tables import PropertyTable
from .models import Property
from django.utils.timezone import now
from django.http import HttpResponse
from django.views.generic import ListView
from leases.models import Lease
from utils.pdf_export import handle_export
from utils.pdf_export import PDFTableExport
from .tables import PropertyTable, UnitTable
from .forms import PropertyForm, UnitForm
from .models import Property, Unit
from datetime import datetime
import logging
from django_tables2.export.export import TableExport
from django_tables2.export.views import ExportMixin
from django_tables2 import SingleTableView
from django.db.models import Prefetch
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.views.generic import ListView, CreateView, DetailView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import JsonResponse, HttpResponseRedirect
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.views.decorators.http import require_POST
from core.models import GlobalSettings
from leases.models import WhatsAppTemplate
from leases.whatsapp import build_whatsapp_url, render_unit_whatsapp_template
from django.views.generic import CreateView, UpdateView, DetailView, DeleteView
from django_filters.views import FilterView
from django_tables2.views import SingleTableMixin
from django.views import View

from .models import PropertyMedia, Unit, UnitMedia, Property
from .filters import UnitFilter
from .tables import UnitTable
from .forms import UnitForm

import json

logger = logging.getLogger(__name__)
UNIT_MEDIA_SHARE_MAX_AGE = 60 * 60 * 48
UNIT_MEDIA_SHARE_SALT = "properties.unit-media-share"


@csrf_exempt
def unit_inline_update(request):
    if request.method == "POST":
        data = json.loads(request.body)
        unit_id = data.get("id")
        field = data.get("field")
        value = data.get("value")

        try:
            unit = Unit.objects.get(pk=unit_id)
            setattr(unit, field, value)
            unit.save()
            return JsonResponse({"success": True, "new_value": getattr(unit, field)})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)}, status=400)


logger = logging.getLogger(__name__)


class PropertyListView(SingleTableView):
    model = Property
    table_class = PropertyTable
    template_name = 'properties/property_list.html'
    ordering = ['-created_at']
    context_object_name = 'properties'

    def get_queryset(self):
        active_leases = Lease.objects.filter(
            status='active').select_related('tenant')
        return Property.objects.all().prefetch_related(
            'units',
            Prefetch('units__leases', queryset=active_leases,
                     to_attr='active_leases')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        return context

    def get(self, request, *args, **kwargs):
       # Handle export requests first
        if request.GET.get('_export'):
            table = self.get_table()
            export_name = f"properties_{datetime.now().strftime('%Y%m%d')}"
            return handle_export(request, table, export_name)

        # Normal GET request
        return super().get(request, *args, **kwargs)


class PropertyCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Property
    form_class = PropertyForm
    template_name = 'properties/property_form.html'
    success_message = "Property created successfully"
    success_url = reverse_lazy('properties:property_list')

    def form_valid(self, form):
        messages.success(self.request, 'Property created successfully.')
        return super().form_valid(form)


class PropertyDetailView(LoginRequiredMixin, DetailView):
    model = Property
    template_name = 'properties/property_detail.html'
    context_object_name = 'property'
    success_url = reverse_lazy('properties:property_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()
        units = self.object.units.all().order_by('unit_number')
        active_unit_ids = Lease.objects.filter(
            unit__property=self.object,
            status='active',
            start_date__lte=today,
            end_date__gte=today,
        ).values_list('unit_id', flat=True).distinct()

        context['units'] = units
        context['actual_total_units'] = units.count()
        context['configured_total_units'] = self.object.total_units
        context['occupied_units_count'] = units.filter(id__in=active_unit_ids).count()
        context['vacant_units_count'] = units.filter(
            status='vacant'
        ).exclude(id__in=active_unit_ids).count()
        context['maintenance_units_count'] = units.filter(status='maintenance').count()
        context['media_files'] = self.object.media_files.filter(is_active=True)[:6]
        return context


class PropertyUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Property
    form_class = PropertyForm
    template_name = 'properties/property_form.html'
    success_message = "Property updated successfully"
    success_url = reverse_lazy('properties:property_list')

    def form_valid(self, form):
        messages.success(self.request, 'Property updated successfully.')
        return super().form_valid(form)


class PropertyDeleteView(LoginRequiredMixin, DeleteView):
    model = Property
    template_name = 'properties/property_confirm_delete.html'
    success_url = reverse_lazy('properties:property_list')

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Property deleted successfully.')
        return super().delete(request, *args, **kwargs)


class UnitListView(SingleTableMixin, FilterView):
    model = Unit
    table_class = UnitTable
    template_name = "properties/unit_list.html"
    filterset_class = UnitFilter

    def get_queryset(self):
        queryset = super().get_queryset()
        property_id = self.request.GET.get('property')
        if property_id:
            queryset = queryset.filter(property_id=property_id)
        return queryset

    def get_table_data(self):
        return self.object_list

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['all_properties'] = Property.objects.all().order_by(
            'property_name')
        return context

    def get(self, request, *args, **kwargs):
        # Handle export requests
        export_response = self.handle_export(request)
        if export_response:
            return export_response
        return super().get(request, *args, **kwargs)

    def handle_export(self, request):
        """Handle export functionality"""
        self.object_list = self.get_queryset()
        table = self.get_table()
        export_name = "units_list"
        return handle_export(request, table, export_name)


class UnitDetailView(LoginRequiredMixin, DetailView):
    model = Unit
    template_name = 'properties/unit_detail.html'
    context_object_name = 'unit'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['units'] = self.object
        context['media_files'] = self.object.media_files.filter(is_active=True)[:6]
        return context


def unit_detail(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    media_files = unit.media_files.filter(is_active=True)[:6]
    return render(request, 'properties/unit_detail.html', {'unit': unit, 'media_files': media_files})


class UnitCreateView(CreateView):
    model = Unit
    form_class = UnitForm
    template_name = 'properties/unit_form.html'

    def get_success_url(self):
        messages.success(self.request, "Unit created successfully.")
        return reverse('properties:unit_list')


class UnitUpdateView(UpdateView):
    model = Unit
    form_class = UnitForm
    template_name = 'properties/unit_form.html'

    def get_success_url(self):
        messages.success(self.request, "Unit updated successfully.")
        return reverse('properties:unit_list')


class UnitDeleteView(DeleteView):
    model = Unit
    template_name = 'properties/unit_confirm_delete.html'

    def get_success_url(self):
        messages.success(self.request, "Unit deleted successfully.")
        return reverse('properties:unit_list')


@require_POST
def unit_inline_update(request):
    try:
        data = json.loads(request.body)
        unit_id = data.get('id')
        field = data.get('field')
        value = data.get('value')

        unit = get_object_or_404(Unit, pk=unit_id)
        setattr(unit, field, value)
        unit.save()
        return JsonResponse({'success': True, 'new_value': value})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def _next_media_sort(qs):
    return qs.count() + 1


def _upload_media_files(request, owner, media_model, owner_field):
    files = request.FILES.getlist("files") or request.FILES.getlist("photos")
    description = (request.POST.get("description") or "").strip()[:300]
    if not files:
        messages.error(request, "Please choose at least one file.")
        return 0

    created = 0
    active_qs = owner.media_files.filter(is_active=True)
    for file_obj in files:
        media = media_model(
            **{owner_field: owner},
            file=file_obj,
            description=description,
            sort_order=_next_media_sort(active_qs) + created,
            uploaded_by=request.user if request.user.is_authenticated else None,
            original_filename=getattr(file_obj, "name", "")[:255],
        )
        try:
            media.full_clean()
            media.save()
            created += 1
        except ValidationError as exc:
            messages.error(request, f"{getattr(file_obj, 'name', 'File')}: {exc.messages[0]}")
    if created:
        messages.success(request, f"Uploaded {created} file(s).")
    return created


@login_required
def unit_media_page(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    if request.method == "POST":
        _upload_media_files(request, unit, UnitMedia, "unit")
        return redirect("properties:unit_media", pk=unit.pk)
    media_files = unit.media_files.filter(is_active=True)
    return render(request, "properties/media_page.html", {
        "owner": unit,
        "owner_label": f"Unit {unit.unit_number}",
        "parent_label": unit.property.property_name,
        "media_files": media_files,
        "upload_url": reverse("properties:unit_media", args=[unit.pk]),
        "back_url": reverse("properties:unit_detail", args=[unit.pk]),
        "delete_url_name": "properties:unit_media_delete",
        "share_link_url": reverse("properties:unit_media_share_link", args=[unit.pk]),
    })


@login_required
def property_media_page(request, pk):
    property_obj = get_object_or_404(Property, pk=pk)
    if request.method == "POST":
        _upload_media_files(request, property_obj, PropertyMedia, "property")
        return redirect("properties:property_media", pk=property_obj.pk)
    media_files = property_obj.media_files.filter(is_active=True)
    return render(request, "properties/media_page.html", {
        "owner": property_obj,
        "owner_label": property_obj.property_name,
        "parent_label": "Property / Building",
        "media_files": media_files,
        "upload_url": reverse("properties:property_media", args=[property_obj.pk]),
        "back_url": reverse("properties:property_detail", args=[property_obj.pk]),
        "delete_url_name": "properties:property_media_delete",
    })


@login_required
@require_POST
def unit_media_delete(request, pk, media_id):
    unit = get_object_or_404(Unit, pk=pk)
    media = get_object_or_404(UnitMedia, pk=media_id, unit=unit, is_active=True)
    if request.POST.get("confirm_delete") != "yes":
        messages.error(request, "Media delete was not confirmed.")
    else:
        media.is_active = False
        media.save(update_fields=["is_active", "updated_at"])
        messages.success(request, "Media removed from active list.")
    return redirect("properties:unit_media", pk=unit.pk)


@login_required
@require_POST
def property_media_delete(request, pk, media_id):
    property_obj = get_object_or_404(Property, pk=pk)
    media = get_object_or_404(PropertyMedia, pk=media_id, property=property_obj, is_active=True)
    if request.POST.get("confirm_delete") != "yes":
        messages.error(request, "Media delete was not confirmed.")
    else:
        media.is_active = False
        media.save(update_fields=["is_active", "updated_at"])
        messages.success(request, "Media removed from active list.")
    return redirect("properties:property_media", pk=property_obj.pk)


def _sign_unit_media_token(unit_id):
    return signing.TimestampSigner(salt=UNIT_MEDIA_SHARE_SALT).sign(str(unit_id))


def _unit_from_share_token(token):
    try:
        unit_id = signing.TimestampSigner(salt=UNIT_MEDIA_SHARE_SALT).unsign(
            token,
            max_age=UNIT_MEDIA_SHARE_MAX_AGE,
        )
    except signing.SignatureExpired:
        raise Http404("This photo link has expired.")
    except signing.BadSignature:
        raise Http404("Invalid photo link.")
    return get_object_or_404(Unit, pk=unit_id)


@login_required
def unit_media_share_link(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    token = _sign_unit_media_token(unit.pk)
    share_url = request.build_absolute_uri(
        reverse("properties:unit_media_public_share", args=[token])
    )
    return render(request, "properties/unit_media_share_link.html", {
        "unit": unit,
        "token": token,
        "share_url": share_url,
        "expires_hours": 48,
        "back_url": reverse("properties:unit_media", args=[unit.pk]),
    })


def unit_media_public_share(request, token):
    unit = _unit_from_share_token(token)
    media_files = unit.media_files.filter(is_active=True, file_type="image")
    return render(request, "properties/unit_media_public_share.html", {
        "unit": unit,
        "token": token,
        "media_files": media_files,
    })


def unit_media_public_file(request, token, media_id):
    unit = _unit_from_share_token(token)
    media = get_object_or_404(
        UnitMedia,
        pk=media_id,
        unit=unit,
        is_active=True,
        file_type="image",
    )
    image_file = media.stamped_file or media.file
    if not image_file:
        raise Http404("File not found.")
    image_file.open("rb")
    return FileResponse(image_file, content_type="image/jpeg")


@login_required
def unit_vacancy_whatsapp(request, pk):
    unit = get_object_or_404(Unit.objects.select_related("property"), pk=pk)
    settings_obj = GlobalSettings.get_solo()
    template, rendered_message = render_unit_whatsapp_template(
        WhatsAppTemplate.TEMPLATE_VACANCY,
        unit,
        request=request,
    )
    phone = (request.GET.get("phone") or "").strip()
    whatsapp_url = build_whatsapp_url(
        phone,
        rendered_message,
        country_code=getattr(settings_obj, "country_code", "+92"),
    )
    return render(request, "properties/unit_vacancy_whatsapp.html", {
        "unit": unit,
        "template": template,
        "phone": phone,
        "message_text": rendered_message,
        "whatsapp_url": whatsapp_url,
    })
