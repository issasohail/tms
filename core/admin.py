from django.contrib import admin

# Register your models here.
# core/admin.py
from django.contrib import admin
from .models import GlobalSettings, PaymentMethod


@admin.register(GlobalSettings)
class GlobalSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        # Enforce singleton
        return not GlobalSettings.objects.exists()


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_active", "sort_order", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "code"]
    ordering = ["sort_order", "name"]
