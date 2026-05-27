from django.db.models import Case, DecimalField, Exists, OuterRef, When
from django.db.models.functions import Coalesce
from django.db.models.functions import Replace
from django.db.models import F, Value
import re
from django.apps import apps
from django.db.models import Q
from utils.pdf_export import PDFTableExport, TableExport
from django_tables2 import SingleTableView
from properties.models import Property, Unit
from .models import PotentialTenantLead, Tenant, TenantRegistrationSubmission
from .tables import TenantTable
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
from .forms import PotentialTenantLeadForm, TenantForm, TenantPublicRegistrationForm, TenantRegistrationSubmissionReviewForm
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
from invoices.models import Invoice, SecurityDepositTransaction  # Add this import
from payments.models import Payment
from decimal import Decimal
from django.db.models import Q
from utils.pdf_export import handle_export
from django_tables2 import SingleTableView
from django.contrib.auth.mixins import LoginRequiredMixin
from django_tables2.export.views import ExportMixin
from django.db.models import Prefetch
from django.db.models import Sum, Q
from datetime import datetime
from django.utils.dateparse import parse_date
from django.db.models import Sum
from decimal import Decimal
from django.db.models import Sum, Q
from django.utils import timezone
from datetime import timedelta
from django_tables2 import SingleTableView
from .tables import LedgerTable  # We'll create this later
from django.contrib.auth.decorators import login_required
from django.db.models import Subquery, OuterRef, CharField, Value
from django.db.models.functions import Concat
import logging
from django.conf import settings
from django.urls import reverse_lazy
from django.urls import NoReverseMatch  # Add this import
from django.urls import reverse
from django.shortcuts import get_object_or_404
from django.http import Http404
from django.contrib import messages
import django_tables2 as tables
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
# tenants/views.py
from django.views.generic import ListView
from .models import Tenant
from .tables import TenantDetailTable
from django.db import models
from datetime import date
from django.http import HttpResponse
import csv
from django.template.loader import render_to_string
from weasyprint import HTML
import openpyxl
from io import BytesIO
from django.utils import timezone
from django.db.models import Q
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.drawing.image import Image
from leases.tables import TenantLeaseTable   # NEW
from django_tables2 import RequestConfig
from django.http import HttpResponse
from invoices.models import Invoice
from payments.models import Payment
from django.db.models import Sum
from django.shortcuts import redirect
from core.models import GlobalSettings
from leases.models import WhatsAppTemplate
from leases.whatsapp import build_whatsapp_url, render_unit_whatsapp_template
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.views.decorators.http import require_POST

TENANT_REGISTRATION_MAX_AGE = 60 * 60 * 24 * 7
TENANT_REGISTRATION_SALT = "tenants.registration-link"


class TenantListView(SingleTableView):
    model = Tenant
    table_class = TenantTable
    template_name = 'tenants/tenant_list.html'
    paginate_by = 40
    context_object_name = 'tenants'
    export_formats = ['csv', 'xlsx', 'pdf']

    def get_queryset(self):
        today = timezone.now().date()

        # Optimized prefetch query
        active_leases = Prefetch(
            'leases',
            queryset=Lease.objects.filter(
                status='active',
                start_date__lte=today,
                end_date__gte=today
            ).select_related('unit__property').order_by('-start_date'),
            to_attr='active_leases'
        )

        queryset = super().get_queryset().prefetch_related(active_leases)

        # Apply filters
        filters = {}
        if self.request.GET.get('property'):
            filters['leases__unit__property_id'] = self.request.GET.get(
                'property')
        if self.request.GET.get('unit'):
            filters['leases__unit_id'] = self.request.GET.get('unit')
        if not self.request.GET.get('include_inactive'):
            filters['is_active'] = True

        if filters:
            queryset = queryset.filter(**filters)

        return queryset.distinct().order_by('first_name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['all_properties'] = Property.objects.all().order_by(
            'property_name')

        # Get filtered units based on selected property
        property_id = self.request.GET.get('property')
        context['filtered_units'] = Unit.objects.filter(
            property_id=property_id
        ).order_by('unit_number') if property_id else Unit.objects.none()

        # Add current filter values to context
        context['current_property'] = property_id
        context['current_unit'] = self.request.GET.get('unit')
        context['phone_search'] = self.request.GET.get('phone', '')
        context['include_inactive'] = bool(
            self.request.GET.get('include_inactive'))

        # Add export formats to context
        context['export_formats'] = self.table_class.Meta.export_formats
        tenants = context.get('object_list', [])
        for tenant in tenants:
            tenant.ledger_url = ''
            if tenant.current_lease:  # Using the property we added
                try:
                    tenant.ledger_url = reverse(
                        'tenants:lease_ledger',
                        kwargs={'lease_id': tenant.current_lease.id}
                    )
                except NoReverseMatch:
                    pass

        return context

    def get(self, request, *args, **kwargs):
        # Handle export requests
        export_response = self.handle_export(request)
        if export_response:
            return export_response
        return super().get(request, *args, **kwargs)

    def tenant_list(request):
        include_inactive = request.GET.get('include_inactive', False) == 'on'

        queryset = Tenant.objects.all()

        if not include_inactive:
            # Only show tenants with active leases
            queryset = queryset.filter(leases__status='active').distinct()

    def handle_export(self, request):
        """Handle export functionality"""
        self.object_list = self.get_queryset()
        table = self.get_table()
        export_name = "tenants_list"
        return handle_export(request, table, export_name)


    def create_export(self, export_format):
        queryset = self.get_queryset()
        filename = f'tenants_{timezone.now().strftime("%Y%m%d_%H%M%S")}'

        if export_format == 'csv':
            # Your existing CSV export logic
            # ...
            pass

        elif export_format == 'xlsx':
            # Your existing Excel export logic
            # ...
            pass

        elif export_format == 'pdf':
            # Get pagination data
            page_obj = self.get_page_obj(queryset)

            html_string = render_to_string(
                'tenants/tenant_list_pdf.html',
                {
                    'tenants': page_obj.object_list,
                    'page_obj': page_obj,
                    'is_paginated': True,
                    'date': timezone.now().strftime('%Y-%m-%d')
                }
            )

            html = HTML(string=html_string,
                        base_url=self.request.build_absolute_uri())
            pdf = html.write_pdf()

            response = HttpResponse(pdf, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{filename}.pdf"'
            return response

        return None

    def get_page_obj(self, queryset):
        paginator = self.get_paginator(
            queryset,
            self.paginate_by,
            orphans=self.get_paginate_orphans(),
            allow_empty_first_page=self.get_allow_empty()
        )
        page_kwarg = self.page_kwarg
        page = self.kwargs.get(
            page_kwarg) or self.request.GET.get(page_kwarg) or 1
        return paginator.page(page)

    def get(self, request, *args, **kwargs):
        export_format = request.GET.get('export')
        if export_format in self.export_formats:
            return self.create_export(export_format)
        return super().get(request, *args, **kwargs)


def tenant_registration_token(tenant):
    return signing.dumps({"tenant_id": tenant.pk}, salt=TENANT_REGISTRATION_SALT)


def _tenant_from_registration_token(token):
    data = signing.loads(
        token,
        salt=TENANT_REGISTRATION_SALT,
        max_age=TENANT_REGISTRATION_MAX_AGE,
    )
    return get_object_or_404(Tenant, pk=data["tenant_id"])


def tenant_public_registration_update(request, token):
    try:
        tenant = _tenant_from_registration_token(token)
    except SignatureExpired:
        return render(request, "tenants/public_registration_expired.html", status=410)
    except BadSignature:
        raise Http404("Invalid registration link")

    initial = {
        "first_name": tenant.first_name,
        "last_name": tenant.last_name,
        "email": tenant.email,
        "phone": tenant.phone,
        "phone2": tenant.phone2,
        "address": tenant.address,
        "emergency_contact_name": tenant.emergency_contact_name,
        "emergency_contact_phone": tenant.emergency_contact_phone,
        "emergency_contact_relation": tenant.emergency_contact_relation,
        "number_of_family_member": tenant.number_of_family_member,
    }
    if request.method == "POST":
        form = TenantPublicRegistrationForm(request.POST)
        if form.is_valid():
            TenantRegistrationSubmission.objects.create(
                tenant=tenant,
                submitted_data=form.cleaned_data,
            )
            return render(request, "tenants/public_registration_submitted.html", {"tenant": tenant})
    else:
        form = TenantPublicRegistrationForm(initial=initial)
    return render(request, "tenants/public_registration_form.html", {"tenant": tenant, "form": form})


class TenantRegistrationSubmissionListView(LoginRequiredMixin, ListView):
    model = TenantRegistrationSubmission
    template_name = "tenants/registration_submission_list.html"
    context_object_name = "submissions"


class TenantRegistrationSubmissionDetailView(LoginRequiredMixin, DetailView):
    model = TenantRegistrationSubmission
    template_name = "tenants/registration_submission_detail.html"
    context_object_name = "submission"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = TenantRegistrationSubmissionReviewForm(instance=self.object)
        context["registration_link"] = self.request.build_absolute_uri(
            reverse("tenants:tenant_public_registration", args=[tenant_registration_token(self.object.tenant)])
        )
        return context


@login_required
@require_POST
def tenant_registration_submission_review(request, pk):
    submission = get_object_or_404(TenantRegistrationSubmission, pk=pk)
    form = TenantRegistrationSubmissionReviewForm(request.POST, instance=submission)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.reviewed_by = request.user
        obj.reviewed_at = timezone.now()
        obj.save()
        if obj.status == "approved":
            tenant = obj.tenant
            allowed = [
                "first_name", "last_name", "email", "phone", "phone2", "address",
                "emergency_contact_name", "emergency_contact_phone",
                "emergency_contact_relation", "number_of_family_member",
            ]
            for field in allowed:
                setattr(tenant, field, obj.submitted_data.get(field, getattr(tenant, field)))
            tenant.save(update_fields=allowed + ["updated_at"])
            messages.success(request, "Tenant registration update approved and applied.")
        else:
            messages.success(request, "Tenant registration submission updated.")
    else:
        messages.error(request, "Could not update registration submission.")
    return redirect("tenants:registration_submission_detail", pk=submission.pk)


def get_units_by_property(request):
    property_id = request.GET.get('property_id')
    units = Unit.objects.filter(
        property_id=property_id).order_by('unit_number')
    data = {
        'units': [{'id': unit.id, 'unit_number': unit.unit_number} for unit in units]
    }
    return JsonResponse(data)


class PotentialTenantLeadListView(LoginRequiredMixin, ListView):
    model = PotentialTenantLead
    template_name = "tenants/lead_list.html"
    context_object_name = "leads"
    paginate_by = 40

    def get_queryset(self):
        qs = PotentialTenantLead.objects.select_related(
            "interested_building", "interested_unit"
        )
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = PotentialTenantLead.STATUS_CHOICES
        context["current_status"] = self.request.GET.get("status", "")
        return context


class PotentialTenantLeadCreateView(LoginRequiredMixin, CreateView):
    model = PotentialTenantLead
    form_class = PotentialTenantLeadForm
    template_name = "tenants/lead_form.html"
    success_url = reverse_lazy("tenants:lead_list")

    def form_valid(self, form):
        messages.success(self.request, "Lead added.")
        return super().form_valid(form)


class PotentialTenantLeadUpdateView(LoginRequiredMixin, UpdateView):
    model = PotentialTenantLead
    form_class = PotentialTenantLeadForm
    template_name = "tenants/lead_form.html"
    success_url = reverse_lazy("tenants:lead_list")

    def form_valid(self, form):
        messages.success(self.request, "Lead updated.")
        return super().form_valid(form)


class PotentialTenantLeadDetailView(LoginRequiredMixin, DetailView):
    model = PotentialTenantLead
    template_name = "tenants/lead_detail.html"
    context_object_name = "lead"


@login_required
def lead_vacancy_whatsapp(request, pk):
    lead = get_object_or_404(PotentialTenantLead, pk=pk)
    unit = lead.interested_unit
    if not unit:
        messages.error(request, "Select an interested unit before sending a vacancy message.")
        return redirect("tenants:lead_detail", pk=lead.pk)

    settings_obj = GlobalSettings.get_solo()
    template, rendered_message = render_unit_whatsapp_template(
        WhatsAppTemplate.TEMPLATE_VACANCY,
        unit,
        request=request,
    )
    phone = lead.whatsapp_number or lead.phone
    whatsapp_url = build_whatsapp_url(
        phone,
        rendered_message,
        country_code=getattr(settings_obj, "country_code", "+92"),
    )
    return render(request, "tenants/lead_vacancy_whatsapp.html", {
        "lead": lead,
        "unit": unit,
        "template": template,
        "message_text": rendered_message,
        "phone": phone,
        "whatsapp_url": whatsapp_url,
    })


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

        total_invoices = Invoice.objects.filter(
            lease__tenant=tenant
        ).aggregate(total=Sum('amount'))['total'] or 0

        total_payments = Payment.objects.filter(
            lease__tenant=tenant
        ).aggregate(total=Sum('amount'))['total'] or 0

        total_invoices_all = Invoice.objects.filter(
            lease__tenant=tenant
        ).aggregate(total=Sum('amount'))['total'] or 0

        total_payments_all = Payment.objects.filter(
            lease__tenant=tenant
        ).aggregate(total=Sum('amount'))['total'] or 0

        tenant.current_balance = total_invoices_all - \
            total_payments_all  # <-- keep this as the source of truth

        # Defaults for the “active lease” tables
        invoices = Invoice.objects.none()
        payments = Payment.objects.none()
        total_invoices_active = 0
        total_payments_active = 0

        # If there is an active lease, load its items for the table ONLY,
        # but DO NOT override tenant.current_balance anymore.
        if lease:
            invoices = (Invoice.objects
                        .filter(lease=lease)
                        .select_related('lease')
                        .order_by('issue_date'))
            payments = (Payment.objects
                        .filter(lease=lease)
                        .select_related('lease')
                        .order_by('-payment_date'))

            total_invoices_active = invoices.aggregate(
                total=Sum('amount'))['total'] or 0
            total_payments_active = payments.aggregate(
                total=Sum('amount'))['total'] or 0

        leases_qs = (
            Lease.objects
            .filter(tenant=tenant)
            .select_related('unit__property')
            .order_by('-start_date', '-id')
        )
        # After you compute tenant.current_balance in TenantDetailView
        context['all_leases'] = leases_qs
        context['leases_total_balance'] = tenant.current_balance  # <-- add this

        if lease:
            invoices = Invoice.objects.filter(
                lease=lease
            ).select_related('lease').order_by('issue_date')

            payments = Payment.objects.filter(
                lease=lease
            ).select_related('lease').order_by('-payment_date')

            total_invoices_active = invoices.aggregate(
                total=Sum('amount'))['total'] or 0
            total_payments_active = payments.aggregate(
                total=Sum('amount'))['total'] or 0

        context.update({
            'invoices': invoices,
            'payments': payments,
            # active-lease totals for that section
            'total_invoices': total_invoices_active,
            # active-lease totals for that section
            'total_payments': total_payments_active,
            'all_leases': leases_qs,                  # for the partial loop
            # <-- expose tenant-wide balance explicitly
            'current_balance': tenant.current_balance
        })
        context['registration_link'] = self.request.build_absolute_uri(
            reverse('tenants:tenant_public_registration', args=[tenant_registration_token(tenant)])
        )

        def get_object(self, queryset=None):
            tenant = super().get_object(queryset)
            print(
                f"Retrieved tenant: ID={tenant.pk}, Name={tenant.first_name} {tenant.last_name}")
            return tenant

        print(f"Tenant: {tenant}")
        print(
            f"Active leases: {tenant.leases.filter(status='active').exists()}")
        print(f"Found lease: {tenant.lease}")
        return context

    def get_object(self, queryset=None):
        # Get the tenant object
        tenant = super().get_object(queryset)
        # Debug print
        print(
            f"Tenant PK: {tenant.pk}, Name: {tenant.first_name} {tenant.last_name}")
        return tenant


class TenantCreateView(LoginRequiredMixin, CreateView):
    model = Tenant
    form_class = TenantForm
    template_name = 'tenants/tenant_form.html'

    def get_success_url(self):
        return reverse('tenants:tenant_detail', kwargs={'pk': self.object.pk})

    def get_initial(self):
        """Set initial values from URL parameters"""
        initial = super().get_initial()
        unit_id = self.request.GET.get('unit')
        if unit_id:
            initial['unit'] = unit_id
        return initial

    def form_valid(self, form):
        """Add success message when form is valid"""

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


def create_export(self, export_format):
    queryset = self.get_queryset()
    filename = f'tenants_{timezone.now().strftime("%Y%m%d_%H%M%S")}'

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'

        writer = csv.writer(response)
        writer.writerow(['ID', 'Name', 'Phone', 'Property',
                        'Unit', 'Rent', 'Balance'])

        for tenant in queryset:
            lease = tenant.current_lease
            writer.writerow([
                tenant.id,
                f"{tenant.first_name} {tenant.last_name}",
                tenant.phone,
                lease.unit.property.property_name if lease else '',
                lease.unit.unit_number if lease else '',
                lease.total_payment if lease else '',
                lease.get_balance if lease else ''
            ])
        return response

    elif export_format == 'xlsx':
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{filename}.xlsx"'

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tenants"

        headers = ['ID', 'Name', 'Phone',
                   'Property', 'Unit', 'Rent', 'Balance']
        ws.append(headers)

        for tenant in queryset:
            lease = tenant.current_lease
            ws.append([
                tenant.id,
                f"{tenant.first_name} {tenant.last_name}",
                tenant.phone,
                lease.unit.property.property_name if lease else '',
                lease.unit.unit_number if lease else '',
                lease.total_payment if lease else '',
                lease.get_balance if lease else ''
            ])

        buffer = BytesIO()
        wb.save(buffer)
        response.write(buffer.getvalue())
        return response

    elif export_format == 'pdf':
        html_string = render_to_string(
            'tenants/tenant_list_pdf.html',
            {
                'tenants': queryset,
                'date': timezone.now().strftime('%Y-%m-%d')
            }
        )

        html = HTML(string=html_string,
                    base_url=self.request.build_absolute_uri())
        pdf = html.write_pdf()

        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}.pdf"'
        return response

    return None


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


logger = logging.getLogger(__name__)


@login_required
def send_ledger(request, lease_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

    lease = get_object_or_404(Lease, pk=lease_id)
    tenant = lease.tenant

    if not tenant.email:
        return JsonResponse({'status': 'error', 'message': 'Tenant has no email address'}, status=400)

    try:
        # Generate PDF using the lease-based view
        view = LeaseLedgerView()
        view.request = request
        view.kwargs = {'lease_id': lease_id}
        transactions = view.get_queryset()

        context = {
            'lease': lease,
            'tenant': tenant,
            'transactions': transactions,
            'current_balance': lease.get_balance(),
            'current_start_date': request.GET.get('start_date'),
            'current_end_date': request.GET.get('end_date')
        }

        html_string = render_to_string(
            'tenants/ledger_pdf_export.html', context)
        pdf = HTML(string=html_string).write_pdf()

        # Create email
        subject = f"Rent Ledger for {tenant.get_full_name()}"
        body = render_to_string('tenants/ledger_email_body.txt', {
            'tenant': tenant,
            'lease': lease,
            'current_balance': transactions[-1]['balance'] if transactions else 0,
            'start_date': request.GET.get('start_date'),
            'end_date': request.GET.get('end_date')
        })

        email = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[tenant.email],
            reply_to=[settings.DEFAULT_FROM_EMAIL]
        )
        email.attach(
            f"ledger_{tenant.id}_{timezone.now().date()}.pdf",
            pdf,
            "application/pdf"
        )
        email.send()

        return JsonResponse({
            'status': 'success',
            'message': 'Ledger sent successfully',
            'email': tenant.email
        })

    except Exception as e:
        logger.error(f"Failed to send ledger: {str(e)}", exc_info=True)
        return JsonResponse({
            'status': 'error',
            'message': f'Failed to send ledger: {str(e)}'
        }, status=500)

# tenants/views.py


# tenants/views.py


class LeaseLedgerView(LoginRequiredMixin, SingleTableView):
    table_class = LedgerTable
    template_name = 'tenants/lease_ledger.html'
    context_object_name = 'table'

    def dispatch(self, request, *args, **kwargs):
        Lease = apps.get_model('leases', 'Lease')
        self.lease = get_object_or_404(Lease, pk=kwargs['lease_id'])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        transactions = []
        balance = Decimal('0.00')

        # Process invoices
        for index, invoice in enumerate(self.lease.invoices.all().order_by('issue_date')):
            transactions.append({
                'index': index,
                'date': invoice.issue_date,
                'type': 'Invoice',
                'description': invoice.description,
                'amount': -invoice.amount,
                'balance': None
            })

        # Process payments
        for index, payment in enumerate(self.lease.payments.all().order_by('payment_date'), start=len(transactions)):
            transactions.append({
                'index': index,
                'date': payment.payment_date,
                'type': 'Payment',
                'description': payment.reference_number or f"Payment",
                'amount': payment.amount,
                'balance': None
            })

        return sorted(transactions, key=lambda x: x['date'])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'lease': self.lease,
            'tenant': self.lease.tenant,
            'current_balance': self.lease.get_balance(),
            'export_formats': ['csv', 'xlsx', 'pdf']
        })
        return context


def tenant_search(request):
    search_term = request.GET.get('q', '').strip()

    if not search_term:
        return JsonResponse({'results': []})

    tenants = Tenant.objects.filter(
        Q(first_name__icontains=search_term) |
        Q(last_name__icontains=search_term) |
        Q(email__icontains=search_term)
    ).select_related('current_lease__unit__property').distinct()

    results = []
    for tenant in tenants:
        lease = tenant.current_lease
        if lease:
            results.append({
                'id': tenant.id,
                'text': f"{tenant.get_full_name()}",
                'property': lease.unit.property.property_name,
                'unit': lease.unit.unit_number,
                'balance': lease.get_balance_due()
            })

    return JsonResponse({'results': results})


class TenantLedgerView(LoginRequiredMixin, SingleTableView):
    table_class = LedgerTable
    template_name = 'tenants/tenant_ledger.html'
    context_object_name = 'table'

    def get_queryset(self):
        tenant_id = self.request.GET.get('tenant', self.kwargs.get('pk'))
        if not tenant_id:
            return []

        tenant = get_object_or_404(Tenant, pk=tenant_id)
        transactions = []
        balance = Decimal('0.00')

        # Get date filters
        date_range = self.request.GET.get('date_range', '')
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')

        # Convert string dates to date objects
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                start_date = None
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                end_date = None

        # Get transactions
        invoices = Invoice.objects.filter(lease__tenant=tenant)
        payments = Payment.objects.filter(lease__tenant=tenant)

        # Apply date filters
        if start_date:
            invoices = invoices.filter(issue_date__gte=start_date)
            payments = payments.filter(payment_date__gte=start_date)
        if end_date:
            invoices = invoices.filter(issue_date__lte=end_date)
            payments = payments.filter(payment_date__lte=end_date)

        # Process invoices
        for index, invoice in enumerate(invoices.order_by('issue_date')):
            transactions.append({
                'index': index,
                'transaction_date': invoice.issue_date,
                'type': 'Invoice',
                'description': invoice.description,
                'amount': -invoice.amount,
                'balance': None  # Will be calculated in the table
            })

        # Process payments
        for index, payment in enumerate(payments.order_by('payment_date'), start=len(transactions)):
            transactions.append({
                'index': index,
                'transaction_date': payment.payment_date,
                'type': 'Payment',
                'description': payment.reference_number or f"Payment {payment.id}",
                'amount': payment.amount,
                'balance': None  # Will be calculated in the table
            })

        # Sort all transactions by date
        transactions.sort(key=lambda x: x['transaction_date'])
        return transactions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant_id = self.request.GET.get('tenant', self.kwargs.get('pk'))
        tenant = get_object_or_404(Tenant, pk=tenant_id) if tenant_id else None
        lease = tenant.leases.filter(
            status='active').first() if tenant else None

        # Calculate current balance
        current_balance = Decimal('0.00')
        if tenant and lease:
            total_invoices = Invoice.objects.filter(
                lease=lease
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            total_payments = Payment.objects.filter(
                lease=lease
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            current_balance = total_payments - total_invoices

        context.update({
            'tenant': tenant,
            'lease': lease,
            'current_balance': current_balance,
            'current_date_range': self.request.GET.get('date_range', ''),
            'current_start_date': self.request.GET.get('start_date', ''),
            'current_end_date': self.request.GET.get('end_date', ''),
            'export_formats': ['csv', 'xlsx', 'pdf']
        })
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


# tenants/views.py (update_tenant_field)

CNIC_DIGITS = re.compile(r'\D+')


@csrf_exempt
@require_POST
def update_tenant_field(request, tenant_id):
    try:
        tenant = Tenant.objects.get(pk=tenant_id)
        field = request.POST.get('field')
        value = (request.POST.get('value') or '').strip()

        if field not in ['first_name', 'last_name', 'email', 'phone', 'cnic', 'address']:
            return JsonResponse({'success': False, 'error': 'Invalid field'})

        if field == 'cnic':
            digits = CNIC_DIGITS.sub('', value)
            if digits and len(digits) != 13:
                return JsonResponse({'success': False, 'error': 'CNIC must contain exactly 13 digits.'})

            # Duplicate check
            qs = (Tenant.objects
                  .annotate(cnic_digits_db=Replace(Replace(F('cnic'), Value('-'), Value('')), Value(' '), Value('')))
                  .exclude(pk=tenant.pk)
                  .filter(cnic_digits_db=digits))
            if digits and qs.exists():
                return JsonResponse({'success': False, 'error': 'A tenant with this CNIC already exists.'})

        setattr(tenant, field, value)
        tenant.save(update_fields=[field])
        return JsonResponse({'success': True})
    except Tenant.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Tenant not found'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

# at top of tenants/views.py


class TenantListView(LoginRequiredMixin, ExportMixin, SingleTableView):
    model = Tenant
    template_name = 'tenants/tenant_list.html'
    table_class = TenantTable
    context_object_name = 'tenants'
    paginate_by = 20
    export_formats = ['csv', 'xlsx', 'pdf']  # Add this line

    def get_queryset(self):
        show_inactive = self.request.GET.get('show_inactive') == 'on'
        lease_prefetch_queryset = Lease.objects.all() if show_inactive else Lease.objects.filter(status='active')
        active_leases = Prefetch(
            'leases',
            queryset=lease_prefetch_queryset
            .select_related('unit__property')
            .order_by('-start_date', '-id'),
            to_attr='active_leases',
        )
        queryset = super().get_queryset().prefetch_related(active_leases)

        tenant_id = self.request.GET.get('tenant')
        phone = self.request.GET.get('phone')
        property_id = self.request.GET.get('property')
        unit_id = self.request.GET.get('unit')

        # If a specific tenant is chosen, short-circuit
        if tenant_id:
            return (queryset
                    .filter(id=tenant_id)
                    .order_by('first_name', 'last_name'))

        if phone:
            queryset = queryset.filter(phone__icontains=phone)

        lease_filter = Lease.objects.filter(tenant_id=OuterRef('pk'))
        if not show_inactive:
            lease_filter = lease_filter.filter(status='active')

        if property_id:
            lease_filter = lease_filter.filter(unit__property_id=property_id)

        if unit_id:
            lease_filter = lease_filter.filter(unit_id=unit_id)

        # ✅ Default: only tenants who have at least one ACTIVE lease.
        #    When "show_inactive" is checked, show all tenants.
        if not show_inactive or property_id or unit_id:
            queryset = queryset.filter(Exists(lease_filter))

        return queryset.order_by('first_name', 'last_name')

    def _attach_lease_totals(self, tenants):
        leases = []
        for tenant in tenants:
            lease = tenant.current_lease
            if lease:
                leases.append(lease)

        lease_ids = [lease.id for lease in leases]
        if not lease_ids:
            return

        zero = Decimal('0.00')
        decimal_field = DecimalField(max_digits=12, decimal_places=2)
        zero_db = Value(zero, output_field=decimal_field)

        invoice_totals = {
            row['lease_id']: row['total'] or zero
            for row in (
                Invoice.objects
                .filter(lease_id__in=lease_ids)
                .values('lease_id')
                .annotate(total=Coalesce(Sum('amount'), zero_db))
            )
        }

        payment_totals = {
            row['lease_id']: row['total'] or zero
            for row in (
                Payment.objects
                .filter(lease_id__in=lease_ids)
                .values('lease_id')
                .annotate(
                    total=Coalesce(
                        Sum(
                            Case(
                                When(allocation__isnull=False, then=F('allocation__lease_amount')),
                                default=F('amount'),
                                output_field=decimal_field,
                            )
                        ),
                        zero_db,
                    )
                )
            )
        }

        security_rows = (
            SecurityDepositTransaction.objects
            .filter(lease_id__in=lease_ids, type__in=['PAYMENT', 'ADJUST'])
            .values('lease_id', 'type')
            .annotate(total=Coalesce(Sum('amount'), zero_db))
        )
        security_totals = {}
        for row in security_rows:
            security_totals.setdefault(row['lease_id'], {})[row['type']] = row['total'] or zero

        for lease in leases:
            balance = invoice_totals.get(lease.id, zero) - payment_totals.get(lease.id, zero)
            paid_in = security_totals.get(lease.id, {}).get('PAYMENT', zero)
            adjust = security_totals.get(lease.id, {}).get('ADJUST', zero)
            security_due = max((lease.security_deposit or zero) - paid_in - adjust, zero)
            lease._cached_get_balance = balance
            lease._cached_security_due = security_due

    def get_context_data(self, **kwargs):
        # This template renders its own HTML table, so skip SingleTableView's
        # unused table context and avoid a second pagination/count query.
        context = ListView.get_context_data(self, **kwargs)
        property_id = self.request.GET.get('property')
        page_tenants = list(context.get('object_list') or context.get('tenants') or [])
        self._attach_lease_totals(page_tenants)

        # Add all tenants for the tenant dropdown
        context['all_tenants'] = (
            Tenant.objects
            .only('id', 'first_name', 'last_name')
            .order_by('first_name', 'last_name')
        )

        # Add all properties for the property dropdown
        context['properties'] = (
            Property.objects
            .only('id', 'property_name')
            .order_by('property_name')
        )

        # Add all units (for when no property is selected)
        context['all_units'] = (
            Unit.objects
            .only('id', 'unit_number')
            .order_by('unit_number')
        )

        # Add filtered units (for when a property is selected)
        context['filtered_units'] = (
            Unit.objects.filter(
                property_id=property_id).only('id', 'unit_number').order_by('unit_number')
            if property_id else []
        )

        context['current_tenant'] = self.request.GET.get('tenant')
        context['current_phone'] = self.request.GET.get('phone')
        context['current_property'] = property_id
        context['current_unit'] = self.request.GET.get('unit')
        context['show_inactive'] = bool(self.request.GET.get('show_inactive'))

        return context

    def create_export(self, export_format):
        queryset = self.get_queryset()
        filename = f'tenants_{timezone.now().strftime("%Y%m%d_%H%M%S")}'

        try:
            if export_format == 'csv':
                response = HttpResponse(content_type='text/csv')
                response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'

                writer = csv.writer(response)
                writer.writerow([
                    'ID', 'First Name', 'Last Name', 'Phone',
                    'Property', 'Unit', 'Rent', 'Balance'
                ])

                for tenant in queryset:
                    lease = tenant.current_lease
                    writer.writerow([
                        tenant.id,
                        tenant.first_name,
                        tenant.last_name,
                        tenant.phone,
                        lease.unit.property.property_name if lease else '',
                        lease.unit.unit_number if lease else '',
                        lease.total_payment if lease else '',
                        lease.get_balance if lease else ''
                    ])
                return response

            elif export_format == 'xlsx':
                try:
                    output = BytesIO()
                    wb = openpyxl.Workbook()
                    ws = wb.active
                    ws.title = "Tenants"

                    # ===== HEADER FORMATTING =====
                    headers = [
                        'SN',
                        'First\nName', 'Last\nName',

                        'Phone',
                        'Email',
                        'Property\nName', 'Unit\nNo.',
                        'Lease\nEnd Date', 'Monthly\nRent', 'Current\nBalance',
                        'Family\nMembers', 'Gender',
                        'Emergency\nContact', 'Emergency\nPhone',
                        'Photo',
                        'CNIC\nFront', 'CNIC\nBack',
                        'Address\n(Full)',
                        'Notes\n(Detailed)',

                    ]

                    for col_num, header in enumerate(headers, 1):
                        cell = ws.cell(row=1, column=col_num, value=header)
                        cell.alignment = Alignment(
                            horizontal='center',
                            vertical='center',
                            wrap_text=True
                        )
                        cell.font = Font(bold=True, size=11)
                        cell.fill = PatternFill(
                            start_color="D3D3D3",  # Light gray
                            end_color="D3D3D3",
                            fill_type="solid"
                        )

                    # ===== COLUMN WIDTHS =====
                    col_widths = {
                        'A': 3,   'B': 12,  'C': 12,  'D': 12,  'E': 16,
                        'F': 15,  'G': 10,  'H': 12,  'I': 12,  'J': 15,
                        'K': 7,   'L': 8,   'M': 13,  'N': 12,  'O': 15,
                        'P': 22,  'Q': 22,  'R': 20,  'S': 20,
                    }
                    for col, width in col_widths.items():
                        ws.column_dimensions[col].width = width

                    # ===== ROW HEIGHTS =====
                    ws.row_dimensions[1].height = 30  # Header row
                    data_row_height = 72  # Data rows (for images)

                    # ===== CELL STYLES =====
                    center_style = Alignment(
                        horizontal='center',
                        vertical='center',
                        wrap_text=True
                    )
                    wrap_style = Alignment(
                        vertical='center',
                        wrap_text=True,
                        horizontal='left'
                    )

                    # ===== DATA POPULATION =====
                    for idx, tenant in enumerate(queryset, start=2):
                        lease = tenant.current_lease
                        ws.row_dimensions[idx].height = data_row_height

                        # Basic data
                        data = [
                            idx-1,  # SN
                            tenant.first_name,
                            tenant.last_name,
                            tenant.phone,
                            tenant.email,
                            lease.unit.property.property_name if lease else "",
                            lease.unit.unit_number if lease else "",
                            lease.end_date.strftime(
                                '%d-%b-%Y') if lease else "",  # More readable date
                            f"Rs. {lease.total_payment:,.2f}" if lease else "",
                            f"Rs. {lease.get_balance:,.2f}" if lease else "",
                            tenant.number_of_family_member,
                            tenant.get_gender_display(),
                            tenant.emergency_contact_name,
                            tenant.emergency_contact_phone,
                            "",  # Photo placeholder
                            "",  # CNIC Front placeholder
                            "",  # CNIC Back placeholder
                            tenant.address or "-",
                            tenant.notes or "-",

                        ]

                        # Apply styles to each cell
                        for col_num, value in enumerate(data, 1):
                            cell = ws.cell(
                                row=idx, column=col_num, value=value)
                            cell.alignment = wrap_style if col_num in [
                                12, 13] else center_style
                            if col_num in [17, 18]:  # Currency columns
                                cell.number_format = '#,##0.00'

                        # ===== IMAGE HANDLING =====
                        image_data = [
                            ('O', tenant.photo, 90, 90),      # Photo
                            # CNIC Front (adjusted ratio)
                            ('P', tenant.cnic_front, 150, 90),
                            # CNIC Back (adjusted ratio)
                            ('Q', tenant.cnic_back, 150, 90)
                        ]

                        for col, image, width, height in image_data:
                            if image and image.path:
                                try:
                                    img = Image(image.path)
                                    img.width = width
                                    img.height = height
                                    ws.add_image(img, f'{col}{idx}')
                                except Exception as e:
                                    ws[f'{col}{idx}'] = "Image"
                                    ws[f'{col}{idx}'].alignment = center_style

                    # ===== FINAL TOUCHES =====
                    ws.freeze_panes = 'A2'  # Freeze header row
                    wb.save(output)
                    output.seek(0)

                    response = HttpResponse(
                        output.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    )
                    response[
                        'Content-Disposition'] = f'attachment; filename="Tenants_Export_{timezone.now().strftime('%Y%m%d')}.xlsx"'
                    return response

                except Exception as e:
                    logger.error(
                        f"Excel export error: {str(e)}", exc_info=True)
                    messages.error(
                        self.request, "Failed to generate Excel file. Please try again.")
                    return redirect('tenants:tenant_list')

            elif export_format == 'pdf':
                # Get pagination data to match HTML view
                queryset = self.get_queryset()
                request = self.request

                # Build filter description
                filter_description = []

                # Property filter
                if property_id := request.GET.get('property'):
                    try:
                        property = Property.objects.get(id=property_id)
                        filter_description.append(
                            f"Property: {property.property_name}")
                    except Property.DoesNotExist:
                        pass

                # Unit filter
                if unit_id := request.GET.get('unit'):
                    try:
                        unit = Unit.objects.get(id=unit_id)
                        filter_description.append(f"Unit: {unit.unit_number}")
                    except Unit.DoesNotExist:
                        pass

                # Tenant filter
                if tenant_id := request.GET.get('tenant'):
                    try:
                        tenant = Tenant.objects.get(id=tenant_id)
                        filter_description.append(
                            f"Tenant: {tenant.first_name} {tenant.last_name}")
                    except Tenant.DoesNotExist:
                        pass

                # Phone filter
                if phone := request.GET.get('phone'):
                    filter_description.append(f"Phone: {phone}")

                # Inactive filter
                show_inactive = request.GET.get('show_inactive') == 'on'
                status_text = "Including Inactive" if show_inactive else "Active Only"
                filter_description.append(f"Status: {status_text}")

                # Combine all filters
                filter_text = " | ".join(
                    filter_description) if filter_description else "All Tenants"

                html_string = render_to_string(
                    'tenants/tenant_list_pdf.html',
                    {
                        'tenants': queryset,  # Full filtered list
                        'date': timezone.now().strftime('%Y-%m-%d'),
                        'filter_text': filter_text  # Make sure this is passed
                    }
                )

                html = HTML(
                    string=html_string,
                    base_url=self.request.build_absolute_uri()
                )
                pdf = html.write_pdf()

                response = HttpResponse(pdf, content_type='application/pdf')
                response['Content-Disposition'] = f'attachment; filename="{filename}.pdf"'
                return response
        except Exception as e:
            logger.error(f"Export failed: {str(e)}")
            messages.error(self.request, f"Export failed: {str(e)}")
            return redirect('tenants:tenant_list')

    def get(self, request, *args, **kwargs):
        export_format = request.GET.get('export')
        if export_format in self.export_formats:
            return self.create_export(export_format)
        return super().get(request, *args, **kwargs)
# tenants/views.py


def tenant_ajax_update(request):
    if request.method == 'POST' and request.headers.get('x-requested-with') == 'XMLHttpRequest':
        pk = request.POST.get('pk')
        name = request.POST.get('name')
        value = request.POST.get('value')

        if not all([pk, name, value]):
            return JsonResponse({'status': 'error', 'message': 'Missing required parameters'}, status=400)

        try:
            tenant = Tenant.objects.get(pk=pk)
            setattr(tenant, name, value)
            tenant.save()
            return JsonResponse({'status': 'success'})
        except Tenant.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Tenant not found'}, status=404)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)
