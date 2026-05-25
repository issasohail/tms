from django.shortcuts import render
from django.utils import timezone
from django.db.models import Case, DecimalField, F, Max, Sum, Value, When
from django.db.models.functions import Coalesce
from datetime import timedelta
from decimal import Decimal
from tenants.models import Tenant
from leases.models import Lease
from payments.models import Payment
from invoices.models import Invoice
from properties.models import Unit, Property
from expenses.models import Expense  # Make sure you have this model


def dashboard(request):
    today = timezone.now().date()
    # Basic counts
    total_properties = Property.objects.count()
    total_units = Unit.objects.count()
    occupied_unit_ids = Lease.objects.filter(
        status='active',
        start_date__lte=today,
        end_date__gte=today
    ).values_list('unit_id', flat=True).distinct()
    occupied_units = occupied_unit_ids.count()
    vacancy_rate = round(((total_units - occupied_units) /
                         total_units * 100) if total_units > 0 else 0, 1)
    total_tenants = Tenant.objects.filter(is_active=True).count()

    # Income calculations (last 30 days)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    net_income = Payment.objects.filter(
        payment_date__gte=thirty_days_ago
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # Recent payments (last 4)
    recent_payments = Payment.objects.select_related(
        'lease__tenant', 'lease__unit__property'
    ).order_by('-payment_date')[:4]

    # Upcoming invoices (due in next 15 days)
    upcoming_invoices = Invoice.objects.select_related(
        'lease__tenant', 'lease__unit__property'
    ).filter(
        due_date__gte=today,
        due_date__lte=today + timedelta(days=15),
        status__in=['unpaid', 'partially_paid']
    ).order_by('due_date')[:4]

    # Leases ending soon (within 40 days)
    ending_soon_leases = Lease.objects.select_related(
        'tenant', 'unit__property'
    ).filter(
        end_date__gte=today,
        end_date__lte=today + timedelta(days=40),
        status='active'
    ).order_by('end_date')

    # Vacant units
    vacant_units = Unit.objects.select_related('property').filter(
        status='vacant'
    ).exclude(id__in=occupied_unit_ids).order_by('property__property_name', 'unit_number')

    # Recent expenses (last 10)
    recent_expenses = Expense.objects.select_related(
        'property').order_by('-date')[:4]

    # Tenant balances without per-tenant lease/payment lookups.
    active_leases = list(
        Lease.objects.select_related('tenant', 'unit__property')
        .filter(
            tenant__is_active=True,
            status='active',
            start_date__lte=today,
            end_date__gte=today,
        )
        .order_by('tenant_id', '-start_date', '-id')
    )
    lease_ids = [lease.id for lease in active_leases]

    invoice_totals = {
        row['lease_id']: row['total'] or Decimal('0.00')
        for row in Invoice.objects.filter(lease_id__in=lease_ids)
        .values('lease_id')
        .annotate(total=Coalesce(Sum('amount'), Value(Decimal('0.00'), output_field=DecimalField())))
    }
    payment_totals = {
        row['lease_id']: {
            'total': row['total'] or Decimal('0.00'),
            'last_payment_date': row['last_payment_date'],
        }
        for row in Payment.objects.filter(lease_id__in=lease_ids)
        .values('lease_id')
        .annotate(
            total=Coalesce(
                Sum(
                    Case(
                        When(allocation__isnull=False, then=F('allocation__lease_amount')),
                        default=F('amount'),
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    )
                ),
                Value(Decimal('0.00'), output_field=DecimalField()),
            ),
            last_payment_date=Max('payment_date'),
        )
    }

    tenant_balances = []
    seen_tenants = set()
    for active_lease in active_leases:
        tenant = active_lease.tenant
        if tenant.pk in seen_tenants:
            continue
        seen_tenants.add(tenant.pk)
        payments_info = payment_totals.get(active_lease.id, {})
        balance = invoice_totals.get(active_lease.id, Decimal('0.00')) - payments_info.get('total', Decimal('0.00'))
        tenant_balances.append({
            'id': tenant.pk,
            'full_name': tenant.get_full_name(),
            'balance': balance,
            'last_payment_date': payments_info.get('last_payment_date'),
            'current_lease': active_lease,
        })

    # Sort by balance descending
    tenant_balances.sort(key=lambda x: x['balance'], reverse=True)

    context = {
        'total_properties': total_properties,
        'total_units': total_units,
        'occupied_units': occupied_units,
        'vacancy_rate': vacancy_rate,
        'total_tenants': total_tenants,
        'net_income': net_income,
        'recent_payments': recent_payments,
        'upcoming_invoices': upcoming_invoices,
        'ending_soon_leases': ending_soon_leases,
        'vacant_units': vacant_units,
        'recent_expenses': recent_expenses,
        'tenant_balances': tenant_balances[:10],  # Only show top 10
    }

    return render(request, 'dashboard/dashboard.html', context)
