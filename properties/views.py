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
from django.http import JsonResponse, HttpResponseRedirect
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, UpdateView, DetailView, DeleteView
from django_filters.views import FilterView
from django_tables2.views import SingleTableMixin
from django.views import View

from .models import Unit, Property
from .filters import UnitFilter
from .tables import UnitTable
from .forms import UnitForm

import json

logger = logging.getLogger(__name__)


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
        context['units'] = self.object.units.all()
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
        return context


def unit_detail(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    return render(request, 'properties/unit_detail.html', {'unit': unit})


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
