from django.core.mail import EmailMessage
from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django_tables2 import SingleTableView
from .models import Tenant
from django.db.models import Sum
from .tables import TenantTable
from django.http import JsonResponse
from properties.forms import PropertyForm, UnitForm
from .forms import TenantForm
from leases.models import Lease
from .forms import LeaseForm
from django.http import HttpResponse
from reportlab.pdfgen import canvas
from io import BytesIO
from django.template.loader import render_to_string
from weasyprint import HTML
import tempfile
from leases.models import Lease
from django.views.generic import DetailView
from django.shortcuts import render
from properties.models import Property as PropertyModel
from leases.models import Lease
from properties.models import Property, Unit
from django_tables2 import SingleTableView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Subquery, OuterRef
from django.utils import timezone
from invoices.models import Invoice  # Add this import
from payments.models import Payment
from decimal import Decimal
from django.db.models import Q
from utils.pdf_export import handle_export
from django_tables2 import SingleTableView
from django.contrib.auth.mixins import LoginRequiredMixin
from django_tables2.export.views import ExportMixin


class TenantListView(LoginRequiredMixin, ExportMixin, SingleTableView):
    model = Tenant
    template_name = 'tenants/tenant_list.html'
    table_class = TenantTable
    context_object_name = 'tenants'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related(
            'leases',
            'leases__unit',
            'leases__unit__property'
        )

        # Apply filters
        search_query = self.request.GET.get('search')
        property_id = self.request.GET.get('property')
        unit_id = self.request.GET.get('unit')
        status = self.request.GET.get('status')

        if search_query:
            queryset = queryset.filter(
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(phone__icontains=search_query)
            )

        if property_id:
            queryset = queryset.filter(leases__unit__property_id=property_id)

        if unit_id:
            queryset = queryset.filter(leases__unit_id=unit_id)

        if status:
            queryset = queryset.filter(status=status)

        return queryset.order_by('first_name', 'last_name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        property_id = self.request.GET.get('property')

        context['all_properties'] = Property.objects.all().order_by(
            'property_name')
        context['filtered_units'] = (
            Unit.objects.filter(
                property_id=property_id).order_by('unit_number')
            if property_id else []
        )
        return context

    def get(self, request, *args, **kwargs):
        resp = handle_export(request, self.get_table(), 'tenants')
        return resp or super().get(request, *args, **kwargs)


class TenantDetailView(LoginRequiredMixin, DetailView):
    model = Tenant
    template_name = 'tenants/tenant_detail.html'
    context_object_name = 'tenant'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.object

        # Get active lease if available
        lease = tenant.leases.filter(status='active').first()
        tenant.lease = lease

        # Safely initialize all values
        invoices = Invoice.objects.none()
        payments = Payment.objects.none()
        total_invoices = 0
        total_payments = 0
        tenant.current_balance = 0

        if lease:
            invoices = Invoice.objects.filter(
                lease=lease
            ).select_related('lease').order_by('issue_date')

            payments = Payment.objects.filter(
                lease=lease
            ).select_related('lease').order_by('-payment_date')

            total_invoices = invoices.aggregate(
                total=Sum('amount'))['total'] or 0
            total_payments = payments.aggregate(
                total=Sum('amount'))['total'] or 0
            tenant.current_balance = total_invoices - total_payments

        context.update({
            'invoices': invoices,
            'payments': payments,
            'total_invoices': total_invoices,
            'total_payments': total_payments,
        })
        print(f"Tenant: {tenant}")
        print(
            f"Active leases: {tenant.leases.filter(status='active').exists()}")
        print(f"Found lease: {tenant.lease}")
        return context


class TenantCreateView(LoginRequiredMixin, CreateView):
    model = Tenant
    form_class = TenantForm
    template_name = 'tenants/tenant_form.html'
    success_url = reverse_lazy('tenants:tenant_list')

    def get_initial(self):
        """Set initial values from URL parameters"""
        initial = super().get_initial()
        unit_id = self.request.GET.get('unit')
        if unit_id:
            initial['unit'] = unit_id
        return initial

    def form_valid(self, form):
        """Add success message when form is valid"""
        messages.success(self.request, 'Tenant was created successfully!')
        return super().form_valid(form)


class TenantUpdateView(LoginRequiredMixin, UpdateView):
    model = Tenant
    form_class = TenantForm
    # Reuse the same template as create view
    template_name = 'tenants/tenant_form.html'
    success_url = reverse_lazy('tenants:tenant_list')

    def form_valid(self, form):
        """Add success message when form is valid"""
        messages.success(self.request, 'Tenant was updated successfully!')
        return super().form_valid(form)


class TenantDeleteView(LoginRequiredMixin, DeleteView):
    model = Tenant
    template_name = 'tenants/tenant_confirm_delete.html'
    success_url = reverse_lazy('tenants:tenant_list')

    def delete(self, request, *args, **kwargs):
        """Add success message when tenant is deleted"""
        messages.success(request, 'Tenant was deleted successfully!')
        return super().delete(request, *args, **kwargs)


def create_tenant(request):
    if request.method == 'POST':
        form = TenantForm(request.POST)
        if form.is_valid():
            tenant = form.save()
            # Get unit from form data if available
            unit = form.cleaned_data.get(
                'unit') if 'unit' in form.cleaned_data else None

            if unit:  # Only create lease if unit is provided
                Lease.objects.create(
                    tenant=tenant,
                    unit=unit,
                    start_date=timezone.now().date(),
                    # ... other lease fields
                )
            return redirect('success_url')
    else:
        form = TenantForm()
    return render(request, 'tenant_form.html', {'form': form})


def generate_agreement(request, lease_id):
    lease = get_object_or_404(Lease, pk=lease_id)
    html_string = render_to_string(
        'leases/agreement_template.html', {'lease': lease})

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Lease_{lease.id}.pdf"'

    HTML(string=html_string).write_pdf(response)
    return response


# views.py


class BalanceDetailView(DetailView):
    template_name = 'balance_detail.html'

    def get_object(self):
        if 'tenant_id' in self.kwargs:
            return Tenant.objects.get(pk=self.kwargs['tenant_id'])
        return Lease.objects.get(pk=self.kwargs['lease_id'])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()

        if isinstance(obj, Tenant):
            context['invoices'] = Invoice.objects.filter(lease__tenant=obj)
            context['payments'] = Payment.objects.filter(lease__tenant=obj)
        else:
            context['invoices'] = obj.invoices.all()
            context['payments'] = obj.payments.all()

        return context


def print_tenant_view(request):
    tenant_ids = request.GET.get('ids', '').split(',')
    tenants = Tenant.objects.filter(id__in=tenant_ids)
    return render(request, 'admin/tenant_print.html', {'tenants': tenants})


def ledger_pdf(request, tenant_id):
    """Generate PDF ledger for tenant"""
    tenant = get_object_or_404(Tenant, pk=tenant_id)

    try:
        # Get transactions using your TenantLedgerView
        transactions = TenantLedgerView().get_queryset()

        # Calculate financials
        total_paid = sum(t['amount'] for t in transactions if t['amount'] > 0)
        total_owed = sum(-t['amount'] for t in transactions if t['amount'] < 0)
        balance = total_paid - total_owed

        context = {
            'tenant': tenant,
            'transactions': transactions,
            'total_paid': total_paid,
            'total_owed': total_owed,
            'balance': balance,
            'date': timezone.now().date(),
        }

        # Render HTML template
        html_string = render_to_string('leases/ledger_pdf.html', context)

        # Add CSS styling (optional - can also be in the template)
        css_string = """
        body { font-family: Arial; font-size: 12px; }
        h1 { color: #333; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        .header { display: flex; justify-content: space-between; }
        .totals { margin-top: 20px; font-weight: bold; }
        """

        # Generate PDF
        html = HTML(string=html_string)
        pdf_content = html.write_pdf(stylesheets=[CSS(string=css_string)])

        # Create response
        response = HttpResponse(pdf_content, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename=tenant_{tenant.id}_ledger_{timezone.now().date()}.pdf'
        return response

    except Exception as e:
        # Log the error (you can use logging module)
        print(f"PDF generation error: {str(e)}")
        return HttpResponse(f"Failed to generate PDF: {str(e)}", status=500)


# leases/views.py or tenants/views.py


def send_ledger(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)

    # Generate PDF (similar to your ledger_pdf view)
    context = {'tenant': tenant}
    html_string = render_to_string('tenants/ledger_pdf.html', context)
    pdf = HTML(string=html_string).write_pdf()

    # Create email
    email = EmailMessage(
        subject=f"Rent Ledger for {tenant.name}",
        body="Please find attached your rent ledger.",
        from_email="your@email.com",
        to=[tenant.email],
    )
    email.attach(f"ledger_{tenant_id}.pdf", pdf, "application/pdf")
    email.send()

    return HttpResponse("Ledger sent successfully")


class TenantLedgerView(LoginRequiredMixin, ListView):
    """View for tenant financial ledger"""
    template_name = 'tenants/lease_ledger.html'
    context_object_name = 'transactions'

    def get_queryset(self):
        tenant = get_object_or_404(Tenant, pk=self.kwargs['pk'])
        transactions = []
        balance = Decimal('0.00')

        # Get all invoices and payments
        invoices = Invoice.objects.filter(lease__tenant=tenant)
        payments = Payment.objects.filter(lease__tenant=tenant)

        # Process invoices
        for invoice in invoices:
            balance -= invoice.amount
            transactions.append({
                'date': invoice.issue_date,
                'type': 'Invoice',
                'description': invoice.description,
                'amount': -invoice.amount,
                'balance': balance,
                'object': invoice
            })

        # Process payments
        for payment in payments:
            balance += payment.amount
            transactions.append({
                'date': payment.payment_date,
                'type': 'Payment',
                'description': payment.reference_number,
                'amount': payment.amount,
                'balance': balance,
                'object': payment
            })

        # Sort by date
        return sorted(transactions, key=lambda x: x['date'])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = get_object_or_404(Tenant, pk=self.kwargs['pk'])
        context['tenant'] = tenant
        return context


def get_units_by_property(request):
    property_id = request.GET.get('property_id')
    units = Unit.objects.filter(
        property_id=property_id).order_by('unit_number')
    data = {
        'units': [{
            'id': u.id,
            'unit_number': u.unit_number
        } for u in units]
    }
    return JsonResponse(data)
