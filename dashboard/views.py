from django.shortcuts import render
from django.utils import timezone
from django.db.models import Sum, Q, Max
from datetime import timedelta
from decimal import Decimal
from tenants.models import Tenant
from leases.models import Lease
from payments.models import Payment
from invoices.models import Invoice
from properties.models import Unit, Property
from expenses.models import Expense  # Make sure you have this model


def dashboard(request):
    # Basic counts
    total_properties = Property.objects.count()
    total_units = Unit.objects.count()
    occupied_units = Lease.objects.filter(
        status='active',
        end_date__gte=timezone.now().date()
    ).count()
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
        due_date__gte=timezone.now().date(),
        due_date__lte=timezone.now().date() + timedelta(days=15),
        status__in=['unpaid', 'partially_paid']
    ).order_by('due_date')[:4]

    # Leases ending soon (within 40 days)
    ending_soon_leases = Lease.objects.select_related(
        'tenant', 'unit__property'
    ).filter(
        end_date__gte=timezone.now().date(),
        end_date__lte=timezone.now().date() + timedelta(days=40),
        status='active'
    ).order_by('end_date')

    # Vacant units
    occupied_unit_ids = Lease.objects.filter(
        status='active',
        end_date__gte=timezone.now().date()
    ).values_list('unit_id', flat=True)

    vacant_units = Unit.objects.select_related('property').exclude(
        id__in=occupied_unit_ids
    ).order_by('property__property_name', 'unit_number')

    # Recent expenses (last 10)
    recent_expenses = Expense.objects.select_related(
        'property').order_by('-date')[:4]

    # Tenant balances (using lease balances)
    active_tenants = Tenant.objects.filter(
        is_active=True,
        leases__status='active',
        leases__end_date__gte=timezone.now().date()
    ).distinct()

    tenant_balances = []
    for tenant in active_tenants:
        active_lease = tenant.leases.filter(
            status='active',
            end_date__gte=timezone.now().date()
        ).first()

        if active_lease:
            last_payment = Payment.objects.filter(
                lease=active_lease
            ).order_by('-payment_date').first()

            if tenant.pk:  # Only include tenants with valid ID
                tenant_balances.append({
                    'id': tenant.pk,
                    'full_name': tenant.get_full_name(),
                    'balance': active_lease.get_balance,
                    'last_payment_date': last_payment.payment_date if last_payment else None,
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
