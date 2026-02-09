# expenses/admin.py
from django.contrib import admin
from .models import ExpenseDistribution


@admin.register(ExpenseDistribution)
class ExpenseDistributionAdmin(admin.ModelAdmin):
    list_display = ['expense', 'unit', 'amount']
    list_select_related = ['unit__property']  # For better performance
