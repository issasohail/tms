from django.contrib import admin
from .models import Utility


@admin.register(Utility)
class UtilityAdmin(admin.ModelAdmin):
    list_display = ('property', 'utility_type', 'amount',
                    'billing_date', 'due_date', 'distribution_method')
    list_filter = ('utility_type', 'distribution_method', 'property')
    search_fields = ('property__name', 'description')
    date_hierarchy = 'billing_date'
    ordering = ('-billing_date',)
