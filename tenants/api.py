# tenants/api.py
from django.http import JsonResponse
from django.views import View
from .models import Tenant
from leases.models import Lease
from django.urls import reverse_lazy
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator


@method_decorator(login_required, name='dispatch')
class TenantLeasesAPI(View):
    def get(self, request, tenant_id):
        try:
            tenant = Tenant.objects.get(pk=tenant_id)
            leases = tenant.leases.all().order_by('-start_date')

            leases_data = []
            for lease in leases:
                leases_data.append({
                    'property_name': lease.unit.property.property_name if lease.unit else 'N/A',
                    'property_url': reverse_lazy('properties:property_detail', args=[lease.unit.property.id]) if lease.unit else '#',
                    'unit_number': lease.unit.unit_number if lease.unit else 'N/A',
                    'unit_url': reverse_lazy('properties:unit_detail', args=[lease.unit.id]) if lease.unit else '#',
                    'start_date': lease.start_date.strftime('%Y-%m-%d'),
                    'end_date': lease.end_date.strftime('%Y-%m-%d'),
                    'rent_amount': float(lease.monthly_rent),
                    'balance': float(lease.get_balance),
                    'status': lease.status,
                    'status_class': 'bg-success' if lease.status == 'active' else 'bg-secondary'
                })

            return JsonResponse({'leases': leases_data})

        except Tenant.DoesNotExist:
            return JsonResponse({'error': 'Tenant not found'}, status=404)
