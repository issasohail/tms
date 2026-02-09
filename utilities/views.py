from django.views.generic import DetailView
from .forms import UtilityForm
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, UpdateView, DeleteView
from django.contrib.messages.views import SuccessMessageMixin
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from .models import Utility
from django.utils import timezone
from tenants.models import Tenant
from payments.models import Payment
from leases.models import Lease  # Ma
from django.urls import reverse
from properties.models import Property
from leases.models import Lease
from properties.models import Unit
from django_tables2 import SingleTableView
from .tables import UtilityTable
from utils.pdf_export import handle_export
from django.contrib.auth.mixins import LoginRequiredMixin
from django_tables2.export.views import ExportMixin
from django_tables2 import RequestConfig
from .models import Utility
from .tables import UtilityTable
from properties.models import Property
from django.shortcuts import render, get_object_or_404, redirect


class UtilityListView(LoginRequiredMixin, ExportMixin, SingleTableView):
    model = Utility
    table_class = UtilityTable
    template_name = 'utilities/utility_list.html'
    context_object_name = 'utilities'
    paginate_by = 10

    def get_queryset(self):
        queryset = super().get_queryset()
        property_id = self.request.GET.get('property')
        if property_id:
            queryset = queryset.filter(property_id=property_id)
        return queryset

    def get(self, request, *args, **kwargs):
        # Handle export requests
        export_response = self.handle_export(request)
        if export_response:
            return export_response
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['all_properties'] = Property.objects.all()

        # Add current filter value to context
        context['current_property'] = self.request.GET.get('property', '')

        # Add export formats to context
        context['export_formats'] = self.table_class.Meta.export_formats

        return context

    def handle_export(self, request):
        """Handle export functionality"""
        self.object_list = self.get_queryset()
        table = self.get_table()
        export_name = "utilities_list"
        return handle_export(request, table, export_name)


def utility_list_view(request):
    property_id = request.GET.get('property')
    utilities = Utility.objects.all()

    if property_id:
        utilities = utilities.filter(property_id=property_id)

    table = UtilityTable(utilities)
    RequestConfig(request, paginate={'per_page': 20}).configure(table)

    context = {
        'table': table,
        'property_list': Property.objects.all(),
        'selected_property': int(property_id) if property_id else None
    }
    return render(request, 'utilities/utilities_list.html', context)


class UtilityCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Utility
    form_class = UtilityForm
    template_name = 'utilities/utility_form.html'
    success_message = "Utility bill added successfully"
    success_url = reverse_lazy('utilities:utility_list')


class UtilityDetailView(LoginRequiredMixin, DetailView):
    model = Utility
    template_name = 'utilities/utility_detail.html'
    context_object_name = 'utility'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        utility = self.object

        # Explicitly add property to context
        context['property_name'] = utility.property.property_name if utility.property else "Not assigned"

        return context

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        utility = self.object

        # Verify property exists through multiple checks
        if utility.property_id:
            try:
                context['property'] = Property.objects.get(
                    pk=utility.property_id)
                context['property_url'] = reverse('properties:property_detail',
                                                  kwargs={'pk': utility.property_id})
            except Property.DoesNotExist:
                context['property'] = None
                context['property_url'] = None
        else:
            context['property'] = None
            context['property_url'] = None

        # Verify leases through unit->property relationship
        if utility.property:
            context['active_leases'] = Lease.objects.filter(
                unit__property_id=utility.property_id,
                start_date__lte=timezone.now(),
                end_date__gte=timezone.now()
            ).select_related('tenant', 'unit')
        else:
            context['active_leases'] = []

        return context


class UtilityUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Utility
    form_class = UtilityForm
    template_name = 'utilities/utility_form.html'
    success_message = "Utility bill updated successfully"
    success_url = reverse_lazy('utilities:utility_list')


class UtilityDeleteView(LoginRequiredMixin, DeleteView):
    model = Utility
    template_name = 'utilities/utility_confirm_delete.html'
    success_url = reverse_lazy('utilities:utility_list')


def distribute_utility(request, pk):
    utility = get_object_or_404(Utility, pk=pk)
    property = utility.property
    tenants = Tenant.objects.filter(property=property)

    if not tenants.exists():
        messages.error(request, "No tenants found for this property")
        return redirect('utilities:utility_detail', pk=utility.pk)

    if utility.distribution_method == 'equal':
        amount_per_tenant = utility.amount / tenants.count()
    elif utility.distribution_method == 'per_person':
        # Assuming each tenant represents one person
        amount_per_tenant = utility.amount / tenants.count()
    else:  # usage - would need meter readings in a real system
        amount_per_tenant = utility.amount / tenants.count()

    for tenant in tenants:
        # Create a payment record for each tenant
        Payment.objects.create(
            tenant=tenant,
            amount=amount_per_tenant,
            payment_date=utility.billing_date,
            payment_method='bank_transfer',
            status='pending',
            notes=f"Utility charge: {utility.get_utility_type_display()} - {utility.billing_date}"
        )

    messages.success(
        request, f"Utility charges distributed to {tenants.count()} tenants")
    return redirect('utilities:utility_detail', pk=utility.pk)
