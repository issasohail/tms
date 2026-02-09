from django.db.models import Q  # ensure this import exists at top
from django.http import JsonResponse
from payments.models import Payment  # adjust if your model is named differently
from django.http import JsonResponse, Http404
from django.views.decorators.http import require_POST
import requests
from django.urls import reverse
from collections import defaultdict
from decimal import Decimal
from utils.pdf_export import PDFTableExport, TableExport
from django.shortcuts import get_object_or_404
from django.views.generic import (
    ListView, DetailView, CreateView, UpdateView, DeleteView
)
from django_tables2 import SingleTableView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.http import HttpResponse, JsonResponse
from django import forms
from django.template.defaulttags import register
from django.utils import timezone
from django.db.models import Sum
from django.views.decorators.http import require_GET
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Q

from payments.forms import PaymentForm
from payments.tables import PaymentTable
from notifications.utils import send_payment_receipt
from django.urls import reverse_lazy
from utils.pdf_export import handle_export
from django.http import HttpResponse
from django.template.loader import get_template
from django.shortcuts import redirect
from io import BytesIO
from django.conf import settings
from properties.models import Property, Unit  # Add this import at the top
from tenants.models import Tenant  # Ensure this is imported
from leases.models import Lease  # Ensure this is imported
from invoices.models import Invoice
import os
from django.shortcuts import get_object_or_404, redirect, render
# payments/views.py
from django.http import HttpResponse
from django.template.loader import render_to_string
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration
from django.conf import settings
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from utils.pdf_export import PDFTableExport
from utils.pdf_export import PaymentReceiptPDF  # Direct import
from datetime import datetime
from reportlab.lib.pagesizes import letter, portrait, landscape
from io import BytesIO
from reportlab.platypus import Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

from django.views.decorators.cache import never_cache
from leases.models import Lease
from django.db.models import F
# class PaymentListView(ListView):
from payments.pdf_utils import generate_payment_pdf  # Instead of render_to_pdf
import logging
from django.templatetags.static import static
from django.core.mail import EmailMessage
from django.http import JsonResponse, HttpResponseBadRequest
from django.contrib.auth.decorators import login_required
import os
from invoices.services import security_deposit_totals
from decimal import Decimal
from django.db import transaction
from django.views.decorators.http import require_POST

from django.db.models import Q
from django.db import transaction
from decimal import Decimal
from invoices.models import SecurityDepositTransaction
from payments.models import Payment, PaymentAllocation
from payments.services import rebuild_allocation
# payments.py (top imports)
from payments.forms import PaymentAllocationForm
from payments.models import PaymentAllocation
from django.db.models import Sum, Case, When, F, DecimalField
from django.db.models.functions import Coalesce
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin

from leases.models import Lease
from tenants.models import Tenant
from properties.models import Property

from payments.models import Payment
from payments.forms import PaymentForm
from payments.services.allocation import rebuild_allocation  # ✅ the service function

logger = logging.getLogger(__name__)


class PaymentListView(SingleTableView):
    model = Payment
    table_class = PaymentTable
    template_name = 'payments/payment_list.html'
    context_object_name = 'payments'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            'lease__tenant', 'lease__unit', 'lease__unit__property')

        # Get filter parameters
        property_id = self.request.GET.get('property')
        tenant_id = self.request.GET.get('tenant')
        unit_id = self.request.GET.get('unit')
        status = self.request.GET.get('status')
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        date_range = self.request.GET.get('date_range')
        include_inactive = self.request.GET.get('include_inactive') == 'on'

        # Apply property filter
        if property_id:
            queryset = queryset.filter(lease__unit__property_id=property_id)

        if tenant_id:
            queryset = queryset.filter(lease__tenant_id=tenant_id)

        if unit_id:
            queryset = queryset.filter(lease__unit_id=unit_id)

        if status:
            queryset = queryset.filter(status=status)

        # Handle date range presets
        today = timezone.now().date()

        if date_range and date_range != 'all':
            if date_range == 'today':
                queryset = queryset.filter(payment_date=today)
            elif date_range == 'yesterday':
                yesterday = today - timezone.timedelta(days=1)
                queryset = queryset.filter(payment_date=yesterday)
            elif date_range == 'this_week':
                start_of_week = today - \
                    timezone.timedelta(days=today.weekday())
                end_of_week = start_of_week + timezone.timedelta(days=6)
                queryset = queryset.filter(
                    payment_date__range=[start_of_week, end_of_week])
            elif date_range == 'this_month':
                start_of_month = today.replace(day=1)
                end_of_month = (start_of_month + timezone.timedelta(days=32)
                                ).replace(day=1) - timezone.timedelta(days=1)
                queryset = queryset.filter(
                    payment_date__range=[start_of_month, end_of_month])
            elif date_range == 'this_year':
                start_of_year = today.replace(month=1, day=1)
                end_of_year = today.replace(month=12, day=31)
                queryset = queryset.filter(
                    payment_date__range=[start_of_year, end_of_year])
        else:
            # Apply manual date range filters if no preset is selected
            if date_range != 'all':
                if start_date:
                    try:
                        queryset = queryset.filter(
                            payment_date__gte=start_date)
                    except ValueError:
                        pass
                if end_date:
                    try:
                        queryset = queryset.filter(payment_date__lte=end_date)
                    except ValueError:
                        pass
        # Filter out inactive leases if not requested
        if not include_inactive:
            queryset = queryset.filter(lease__status='active')

        queryset = queryset.order_by('-payment_date')

        # Efficient balance annotation
        lease_ids = queryset.values_list('lease_id', flat=True).distinct()
        invoice_totals = Invoice.objects.filter(lease_id__in=lease_ids).values(
            'lease_id').annotate(total=Sum('amount'))
        

        payment_totals = (
            Payment.objects.filter(lease_id__in=lease_ids)
            .values("lease_id")
            .annotate(
                total=Coalesce(
                    Sum(
                        Case(
                            When(allocation__isnull=False, then=F("allocation__lease_amount")),
                            default=F("amount"),
                            output_field=DecimalField(max_digits=12, decimal_places=2),
                        )
                    ),
                    0,
                )
            )
        )


        invoice_map = {entry['lease_id']: entry['total']
                       or 0 for entry in invoice_totals}
        payment_map = {entry['lease_id']: entry['total']
                       or 0 for entry in payment_totals}

        for payment in queryset:
            lease_id = payment.lease_id
            total_invoiced = invoice_map.get(lease_id, 0)
            total_paid = payment_map.get(lease_id, 0)
            payment.lease_balance = round(total_invoiced - total_paid, 2)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Get all properties for dropdown
        context['all_properties'] = Property.objects.all()

        # Get the filtered queryset
        queryset = self.get_queryset()

        # Calculate total amount for the filtered payments
        total_amount = queryset.aggregate(total=Sum('amount'))['total'] or 0

        # Get filtered units based on selected property
        property_id = self.request.GET.get('property')
        if property_id:
            context['filtered_units'] = Unit.objects.filter(
                property_id=property_id)
        else:
            context['filtered_units'] = Unit.objects.none()

        # Get all tenants ordered by first name
        context['tenant_list'] = Tenant.objects.all().order_by(
            'first_name', 'last_name')
        context['unit_list'] = Unit.objects.select_related(
            'property').all().order_by('unit_number')

        # Add current filter values to context
        context['current_property'] = self.request.GET.get('property', '')
        context['current_unit'] = self.request.GET.get('unit', '')
        context['current_tenant'] = self.request.GET.get('tenant', '')
        context['include_inactive'] = self.request.GET.get(
            'include_inactive', '') == 'on'

        # Add total amount to context
        context['total_amount'] = total_amount

        # Add export formats to context
        context['export_formats'] = self.table_class.Meta.export_formats

        return context

    def get_table(self, **kwargs):
        table = super().get_table(**kwargs)
        # Pass the request to the table for export title generation
        table.request = self.request
        return table

    def get(self, request, *args, **kwargs):
        # Handle AJAX requests for total amount
        if request.GET.get('ajax') == '1':
            queryset = self.get_queryset()
            total_amount = queryset.aggregate(
                total=Sum('amount'))['total'] or 0
            return JsonResponse({
                'total_amount': float(total_amount)
            })

        # Handle export requests
        self.object_list = self.get_queryset()
        table = self.get_table()

        start = self.request.GET.get('start_date')
        end = self.request.GET.get('end_date')

        if start and end:
            title = f"Payment Report from {start} to {end}"
        else:
            title = "Payment Report for All"

        export_response = handle_export(
            request,
            table,
            export_name='payments',
            title=title
        )

        if export_response:
            return export_response

        return super().get(request, *args, **kwargs)


class PaymentDetailView(LoginRequiredMixin, DetailView):
    model = Payment
    template_name = 'payments/payment_detail.html'
    context_object_name = 'payment'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        payment = self.object
        lease = getattr(payment, "lease", None)

        if lease:
            sec_totals = security_deposit_totals(lease)
        else:
            sec_totals = {
                "required": 0,
                "paid_in": 0,
                "refunded": 0,
                "damages": 0,
                "balance_to_collect": 0,
                "currently_held": 0,
            }

        ctx["sec_totals"] = sec_totals
        ctx["allocation"] = getattr(self.object, "allocation", None)

        return ctx
    



def _dec(v, default="0.00"):
    try:
        return Decimal(str(v or default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


class PaymentCreateView(LoginRequiredMixin, CreateView):
    model = Payment
    form_class = PaymentForm
    template_name = "payments/payment_form.html"

    def get_success_url(self):
        return reverse_lazy("payments:payment_detail", kwargs={"pk": self.object.pk})

    def get_lease(self):
        lease_id = (
            self.request.POST.get("lease")
            or self.request.GET.get("lease")
            or self.request.GET.get("lease_id")
        )
        if lease_id:
            return get_object_or_404(Lease, id=lease_id)
        return None

    def get_initial(self):
        initial = super().get_initial()
        lease = self.get_lease()
        if lease:
            initial.update({
                "lease": lease,
                "amount": lease.get_balance,
                "payment_date": timezone.now().date(),
            })
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        lease = self.get_lease()
        if lease:
            kwargs["lease"] = lease
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        include_inactive = str(
            self.request.GET.get("include_inactive", "")
            or self.request.POST.get("include_inactive", "")
        ).lower() in ("on", "true", "1", "yes")

        selected_lease_id = (
            self.request.POST.get("lease")
            or self.request.GET.get("lease")
            or self.request.GET.get("lease_id")
        )

        lease_qs = Lease.objects.all()
        if not include_inactive:
            if selected_lease_id:
                lease_qs = lease_qs.filter(Q(status="active") | Q(pk=selected_lease_id))
            else:
                lease_qs = lease_qs.filter(status="active")

        form.fields["lease"].queryset = lease_qs

        if selected_lease_id:
            try:
                form.fields["lease"].initial = lease_qs.model.objects.only("id").get(pk=selected_lease_id).pk
            except lease_qs.model.DoesNotExist:
                pass

        return form

    @transaction.atomic
    def form_valid(self, form):
        allocation_form = PaymentAllocationForm(self.request.POST)

        if not allocation_form.is_valid():
            return self.form_invalid(form)
    
        # 1) Save Payment
        payment = form.save(commit=False)
        resolved_lease = self.get_lease() or payment.lease
        if not resolved_lease:
            form.add_error("lease", "Please select a lease.")
            return self.form_invalid(form)

        payment.lease = resolved_lease
        payment.save()
        self.object = payment

        # 2) Read allocation fields from POST
        mode = (self.request.POST.get("allocation_mode") or "LEASE").upper()
        sec_type = (self.request.POST.get("security_type") or "PAYMENT").upper()

        lease_amt = _dec(self.request.POST.get("lease_amount"), "0.00")
        sec_amt   = _dec(self.request.POST.get("security_amount"), "0.00")

        total = lease_amt + sec_amt

        if total != payment.amount:
            form.add_error(None, f"Allocation total ({total}) must equal Payment amount ({payment.amount}).")
            return self.form_invalid(form)

        # Normalize by mode
        if mode == "LEASE":
            lease_amt = payment.amount
            sec_amt = Decimal("0.00")
        elif mode == "SECURITY":
            lease_amt = Decimal("0.00")
            sec_amt = payment.amount
        else:  # SPLIT
            # if user didn't fill lease/security amounts properly, fall back to "all lease"
            if lease_amt <= 0 and sec_amt <= 0:
                lease_amt = payment.amount
                sec_amt = Decimal("0.00")

            # Ensure split does not exceed total; if it does, clamp security
            total = _dec(payment.amount, "0.00")
            if lease_amt + sec_amt > total:
                sec_amt = max(total - lease_amt, Decimal("0.00"))

        # 3) Rebuild derived records (allocation + security tx)
        rebuild_allocation(
            payment=payment,
            lease_amount=lease_amt,
            security_amount=sec_amt,
            security_type=sec_type,
        )

        messages.success(self.request, "Payment recorded successfully.")
        return redirect(payment.get_absolute_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # recent payments selector
        size = 10
        try:
            s = int(self.request.GET.get("recent_size", "10"))
            size = s if s in (10, 20, 50) else 10
        except ValueError:
            size = 10

        context["recent_size"] = size
        context["recent_size_options"] = [10, 20, 50]
        context["recent_payments"] = Payment.objects.all().order_by("-id")[:size]
        context["allocation_form"] = PaymentAllocationForm(initial={
            "allocation_mode": "LEASE",
            "lease_amount": "0.00",
            "security_amount": "0.00",
            "security_type": "PAYMENT",
        })
        include_inactive = self.request.GET.get("include_inactive") == "on"
        if include_inactive:
            context["active_tenants"] = Tenant.objects.all().distinct().order_by("first_name")
            context["tenants"] = Tenant.objects.all().distinct().order_by("first_name")
        else:
            context["active_tenants"] = Tenant.objects.filter(leases__status="active").distinct().order_by("first_name")
            context["tenants"] = Tenant.objects.filter(leases__status="active").distinct().order_by("first_name")

        context["properties"] = Property.objects.all().order_by("property_name")
        context["today"] = timezone.now().date()
        context["nocache"] = timezone.now().timestamp()

        return context

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response


class PaymentUpdateView(LoginRequiredMixin, UpdateView):
    model = Payment
    form_class = PaymentForm
    template_name = "payments/payment_form.html"

    def get_success_url(self):
        return reverse_lazy("payments:payment_detail", kwargs={"pk": self.object.pk})

    @transaction.atomic
    def form_valid(self, form):
        payment = form.save()
        self.object = payment

        # Read allocation fields (same as create)
        mode = (self.request.POST.get("allocation_mode") or "LEASE").upper()
        sec_type = (self.request.POST.get("security_type") or "PAYMENT").upper()

        lease_amt = _dec(self.request.POST.get("lease_amount"), "0.00")
        sec_amt   = _dec(self.request.POST.get("security_amount"), "0.00")

        total = lease_amt + sec_amt
        if total != payment.amount:
            form.add_error(None, f"Allocation total ({total}) must equal Payment amount ({payment.amount}).")
            return self.form_invalid(form)


        if mode == "LEASE":
            lease_amt = payment.amount
            sec_amt = Decimal("0.00")
        elif mode == "SECURITY":
            lease_amt = Decimal("0.00")
            sec_amt = payment.amount
        else:  # SPLIT
            total = _dec(payment.amount, "0.00")
            if lease_amt + sec_amt > total:
                sec_amt = max(total - lease_amt, Decimal("0.00"))

        rebuild_allocation(
            payment=payment,
            lease_amount=lease_amt,
            security_amount=sec_amt,
            security_type=sec_type,
        )

        messages.success(self.request, "Payment updated successfully.")
        return redirect(payment.get_absolute_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # existing context code (if any)...

        payment = self.object
        alloc = getattr(payment, "allocation", None)

        if alloc:
            context["allocation_form"] = PaymentAllocationForm(instance=alloc)
        else:
            # if allocation row doesn't exist, still show the UI with defaults
            context["allocation_form"] = PaymentAllocationForm(initial={
                "allocation_mode": "LEASE",
                "lease_amount": str(payment.amount or "0.00"),
                "security_amount": "0.00",
                "security_type": "PAYMENT",
            })

        return context


@require_GET
def get_filtered_leases(request):
    tenant_id = request.GET.get('tenant_id')
    property_id = request.GET.get('property_id')
    unit_id = request.GET.get('unit_id')
    lease_id = request.GET.get(
        'lease') or request.GET.get('lease_id')
    include_inactive = str(request.GET.get(
        'include_inactive', '')).lower() in ('on', 'true', '1', 'yes')

    def cut(s, n):
        s = (s or "").strip()
        return (s[:n] + "…") if len(s) > n else s

    qs = Lease.objects.all().select_related('tenant', 'unit', 'unit__property')

    # If specific lease requested (e.g., from URL), always return it even if inactive
    if lease_id:
        qs = qs.filter(id=lease_id)
    else:
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)
        if property_id:
            qs = qs.filter(unit__property_id=property_id)
        if unit_id:
            qs = qs.filter(unit_id=unit_id)
        if not include_inactive:
            # Adjust this line if your model uses a different flag than 'status'
            qs = qs.filter(status='active')

    leases_data = []
    for l in qs.order_by('tenant__first_name', 'tenant__last_name'):
        tenant_full = l.tenant.get_full_name()
        tenant_short = cut(tenant_full, 20)   # 20 chars for Tenant
        prop_full = l.unit.property.property_name
        prop_short = cut(prop_full, 8)       # 8 chars for Property
        unit_no = l.unit.unit_number
        bal = float(l.get_balance)
        bal_fmt = "{:,.2f}".format(bal)
        sec_bal = float(l.security_balance_to_collect)
        sec_bal_fmt = "{:,.2f}".format(sec_bal)

        # What shows in the dropdown (single, compact line)
        text = f"{tenant_short} | {prop_short}-{unit_no} | Bal: {bal_fmt} | Sec. Bal: {sec_bal_fmt}"

        leases_data.append({
            "id": l.id,
            "text": text,
            # metadata you may already use elsewhere:
            "tenant_id": l.tenant_id,
            "tenant": tenant_full,
            "property": prop_full,
            "unit": str(unit_no),
            "unit_id": l.unit_id,
            "balance": bal_fmt,
            "sec_balance": sec_bal_fmt,

            # raw numeric values (recommended)
            "balance_raw": float(bal),
            "sec_balance_raw": float(sec_bal),
            
            "status": getattr(l, "status", ""),
        })

    return JsonResponse({"leases": leases_data})


def payment_create(request):
    if request.method == 'POST':
        form = PaymentForm(request.POST)
        if form.is_valid():
            payment = form.save()
            # Redirect to detail page
            return redirect(payment.get_absolute_url())
    else:
        form = PaymentForm()

    return render(request, 'payments/payment_form.html', {'form': form})



class PaymentDeleteView(LoginRequiredMixin, DeleteView):
    model = Payment
    template_name = 'payments/payment_confirm_delete.html'
    success_url = reverse_lazy('payments:cash_ledger')

    def delete(self, request, *args, **kwargs):
        messages.success(request, 'Payment deleted successfully.')
        return super().delete(request, *args, **kwargs)


def send_receipt(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id)

    if request.method == 'POST':
        # Generate PDF
        html_string = render_to_string('payments/payment_pdf.html', {
            'payment': payment,
            'STATIC_URL': settings.STATIC_URL,
        })

        font_config = FontConfiguration()
        html = HTML(string=html_string, base_url=request.build_absolute_uri())
        pdf_bytes = html.write_pdf(
            stylesheets=[settings.STATIC_ROOT + '/css/pdf.css'],
            font_config=font_config,
            presentational_hints=True,
            size=(4.25*72, 11*72)
        )

        if request.POST.get('send_email'):
            # Create email with PDF attachment
            subject = f'Payment Receipt #{payment.id}'
            body = render_to_string(
                'payments/receipt_email.html', {'payment': payment})

            email = EmailMessage(
                subject,
                body,
                settings.DEFAULT_FROM_EMAIL,
                [payment.lease.tenant.email],
            )
            email.content_subtype = "html"

            # Attach PDF
            email.attach(
                f'payment_receipt_{payment.id}.pdf',
                pdf_bytes,
                'application/pdf'
            )

            email.send()

            payment.receipt_sent = True
            payment.receipt_sent_via = 'email'
            payment.save()
            messages.success(request, 'Receipt sent via email successfully')
            return redirect('payments:payment_detail', pk=payment.id)

        if request.POST.get('print'):
            # Return PDF for printing
            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = 'inline; filename="payment_receipt.pdf"'
            return response

    return redirect('payments:payment_detail', pk=payment.id)


@register.filter
def model_type(value):
    return value.__class__._meta.model_name


@api_view(['GET'])
@require_GET
def invoice_list(request):
    lease_id = request.GET.get('lease')
    invoices = Invoice.objects.filter(
        lease_id=lease_id).order_by('-issue_date')
    data = [{
        'id': invoice.id,
        'invoice_number': invoice.invoice_number,
        'amount': invoice.amount,
        'status': invoice.get_status_display()
    } for invoice in invoices]
    return Response(data)

# this is is working fine and it is downloading the file. it is downlading the file from utils/pdf_export.py, but has restricted format.


def payment_pdf_view1(request, pk):
    try:
        print("=== Starting PDF generation ===")  # Debug
        payment = get_object_or_404(Payment, pk=pk)
        print(f"Payment found: {payment}")  # Debug

        # Generate PDF using the new class
        from utils.pdf_export import PaymentReceiptPDF
        print("PaymentReceiptPDF imported successfully")  # Debug

        pdf, filename = PaymentReceiptPDF.generate(payment, request)
        print(f"PDF generated, filename: {filename}")  # Debug
        print(f"PDF size: {len(pdf) if pdf else 0} bytes")  # Debug

        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    except Exception as e:
        logger.error(f"Failed to generate PDF: {str(e)}", exc_info=True)
        print(f"!!! PDF generation error: {str(e)}")  # Debug
        return HttpResponse("Failed to generate PDF", status=500)


logger = logging.getLogger(__name__)

# this is working fine. it is printing using weasyprint and using payment_pdf.html (weasy)


def payment_pdf_view2(request, pk):
    try:
        payment = get_object_or_404(Payment, pk=pk)

        # Render HTML template
        context = {
            'payment': payment,
            'base_url': request.build_absolute_uri('/'),
            'STATIC_URL': settings.STATIC_URL,
        }
        html_string = render_to_string('payments/payment_pdf.html', context)

        # Create HTML object
        html = HTML(
            string=html_string,
            base_url=request.build_absolute_uri('/')
        )

        # Use either inline CSS or external CSS file
        # Option 1: Inline CSS
        css = CSS(string='''
            body { font-family: Arial; font-size: 10pt; margin: 0; padding: 20px; }
            table { width: 100%; border-collapse: collapse; margin: 15px 0; }
            th, td { padding: 8px; border: 1px solid #ddd; }
            th { background-color: #f5f5f5; }
        ''')

        # Option 2: External CSS (uncomment if using)
        # css = CSS(filename=os.path.join(settings.STATIC_ROOT, 'css/pdf.css'))

        # Generate PDF with minimal parameters
        pdf_bytes = html.write_pdf(
            stylesheets=[css],
            font_config=FontConfiguration()
        )

        # Create HTTP response
        filename = f"payment_receipt_{payment.id}.pdf"
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    except Exception as e:
        logger.error(f"Failed to generate PDF: {str(e)}", exc_info=True)
        return HttpResponse(f"Failed to generate PDF: {str(e)}", status=500)


logger = logging.getLogger(__name__)


def payment_pdf_view(request, pk):
    try:
        payment = get_object_or_404(Payment, pk=pk)

        # Get absolute URL for static files
        static_url = request.build_absolute_uri(static(''))

        context = {
            'payment': payment,
            'STATIC_URL': static_url,
            'base_url': request.build_absolute_uri('/'),
        }

        html_string = render_to_string('payments/payment_pdf.html', context)

        # Create HTML object with proper base URL
        html = HTML(
            string=html_string,
            base_url=request.build_absolute_uri('/')
        )

        # CSS options - use either inline or external CSS
        # Option 1: Inline CSS (recommended for PDF consistency)
        css = CSS(string='''
            /* Add any additional CSS overrides here if needed */
            body {
                font-family: Arial, sans-serif !important;
            }
            .payment-table td {
                padding: 6px 8px !important;
            }
        ''')

        # Option 2: External CSS (uncomment if needed)
        # css = CSS(filename=os.path.join(settings.STATIC_ROOT, 'css/pdf.css'))

        # Generate PDF
        pdf_bytes = html.write_pdf(
            stylesheets=[css],
            font_config=FontConfiguration(),
            presentational_hints=True  # Helps with some HTML5/CSS3 features
        )

        filename = f"payment_receipt_{payment.id}.pdf"
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    except Exception as e:
        logger.error(f"Failed to generate PDF: {str(e)}", exc_info=True)
        return HttpResponse(f"Failed to generate PDF: {str(e)}", status=500)


@login_required
def send_payment_email(request, pk):
    if request.method != 'POST':
        return HttpResponseBadRequest("Invalid request")

    payment = get_object_or_404(Payment, pk=pk)
    tenant = payment.lease.tenant

    if not tenant.email:
        return JsonResponse({'status': 'error', 'message': 'Tenant email not found'}, status=400)

    # Build the absolute URL to the existing PDF download
    pdf_url = request.build_absolute_uri(
        reverse('payments:payment_pdf', args=[payment.pk]))

    try:
        pdf_response = requests.get(pdf_url, cookies=request.COOKIES)
        pdf_response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch PDF: {str(e)}")
        return JsonResponse({'status': 'error', 'message': 'Failed to fetch PDF'}, status=500)

    # Email subject and body
    subject = f"Payment Receipt for {tenant.first_name} - {payment.payment_date.strftime('%b %d, %Y')}"
    body = render_to_string(
        'payments/email_receipt_body.txt', {'payment': payment})

    try:
        # Compose email
        email = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[tenant.email],
        )

        email.attach(
            f"receipt_{payment.reference_number or payment.id}.pdf",
            pdf_response.content,
            'application/pdf'
        )

        # Send email and get result
        # This will raise exceptions
        email_sent = email.send(fail_silently=False)

        if email_sent == 1:
            return JsonResponse({'status': 'success', 'message': f'Email sent successfully to {tenant.email}'})
        else:
            logger.error(f"Email failed to send. Return value: {email_sent}")
            return JsonResponse({'status': 'error', 'message': 'Email failed to send'}, status=500)

    except Exception as e:
        logger.error(f"Failed to send email: {str(e)}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': f'Failed to send email: {str(e)}'}, status=500)


@require_POST
def send_payment_notification(request):
    payment_id = request.POST.get('payment_id')
    action = request.POST.get('action')  # whatsapp, sms, or email

    try:
        payment = Payment.objects.get(pk=payment_id)

        # Implement your notification logic here
        # This is a placeholder - implement actual notification sending
        success = True
        message = f"Payment notification sent via {action}"

        if success:
            return JsonResponse({'status': 'success', 'message': message})
        else:
            return JsonResponse({'status': 'error', 'message': 'Failed to send notification'}, status=400)

    except Payment.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Payment not found'}, status=404)


def get_units_by_property(request):
    property_id = request.GET.get('property_id')
    units = Unit.objects.filter(
        property_id=property_id).order_by('unit_number')
    data = {
        'units': [{
            'id': unit.id,
            'unit_number': unit.unit_number
        } for unit in units]
    }
    return JsonResponse(data)


def build_payment_receipt_message(request, pay):
    lease = getattr(pay, "lease", None)
    tenant = getattr(lease, "tenant", None)
    unit = getattr(lease, "unit", None)
    prop = getattr(unit, "property", None)

    # parts used in the detail template
    first_name = getattr(tenant, "first_name", "") or "Customer"
    property_name = getattr(prop, "property_name", "") or ""
    unit_number = getattr(unit, "unit_number", "") or ""
    start_date = getattr(lease, "start_date", None)
    end_date = getattr(lease, "end_date", None)

    payment_date = getattr(pay, "payment_date", None)
    amount = getattr(pay, "amount", 0) or 0
    balance = lease.get_balance if lease else 0

    # 🔹 NEW: security deposit summary using your helper
    sec_required = 0
    sec_balance_to_collect = 0
    sec_status = "Pending"

    if lease:
        totals = security_deposit_totals(lease)
        sec_required = totals["required"] or 0
        sec_balance_to_collect = totals["balance_to_collect"] or 0
        sec_status = "Paid" if sec_balance_to_collect <= 0 else "Pending"

    lines = [
        f"Dear {first_name},",
        f"*Payment received* for {property_name}.",
        f"Unit: {unit_number}",
        (
            f"Period: {start_date:%b %d, %Y} – {end_date:%b %d, %Y}"
            if start_date and end_date else ""
        ),
    ]

    # 🔹 Security deposit lines
    if sec_required:
        # Always show required + status
        lines.append(
            f"Security Deposit: Rs. {float(sec_required):,.2f} ({sec_status})"
        )
        # Only show balance if still pending
        if sec_balance_to_collect > 0:
            lines.append(
                f"*Security Deposit Balance: Rs. {float(sec_balance_to_collect):,.2f}*"
            )

    # Payment info
    if payment_date:
        lines.append(f"Date: {payment_date:%b %d, %Y}")

    lines.extend([
        f"*Amount: Rs. {float(amount):,.2f}*",
        f"Balance: Rs. {float(balance):,.2f}",
        "",
        "Thank you!",
    ])

    # Remove any empty strings
    return "\n".join([l for l in lines if l])


@login_required
@require_GET
def api_payment_receipt_whatsapp(request, pk: int):
    pay = (Payment.objects
           .select_related("lease", "lease__tenant", "lease__unit", "lease__unit__property")
           .filter(pk=pk).first())
    if not pay:
        raise Http404("Payment not found")

    phone = getattr(getattr(getattr(pay, "lease", None),
                    "tenant", None), "phone", "") or ""
    message = build_payment_receipt_message(request, pay)
    return JsonResponse({"phone": phone, "message": message, "payment_id": pay.pk})

class AllocationDetailView(LoginRequiredMixin, DetailView):
    model = PaymentAllocation
    template_name = "payments/payment_detail.html"
    context_object_name = "allocation"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        payment = self.object.payment
        lease = payment.lease

        from invoices.services import security_deposit_totals

        ctx.update({
            "payment": payment,
            "lease": lease,
            "sec_totals": security_deposit_totals(lease),
        })
        return ctx
"""
class AllocationPDFView(PDFTemplateView):
    template_name = "payments/allocation_pdf.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        alloc = get_object_or_404(PaymentAllocation, pk=self.kwargs["pk"])
        ctx["allocation"] = alloc
        ctx["payment"] = alloc.payment
        return ctx

        """   
from django.views import View
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from payments.models import PaymentAllocation
from utils.pdf_export import AllocationReceiptPDF


class AllocationPDFView(View):
    def get(self, request, pk):
        allocation = get_object_or_404(
            PaymentAllocation.objects.select_related(
                "payment",
                "payment__lease",
                "payment__lease__tenant",
                "payment__lease__unit",
                "payment__lease__unit__property",
            ),
            pk=pk
        )

        result = AllocationReceiptPDF.generate(allocation, request)
        if isinstance(result, tuple):
            pdf_bytes, filename = result
        else:
            pdf_bytes, filename = result, f"allocation_{allocation.id}.pdf"

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

