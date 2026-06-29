from django.contrib import admin

from .models import (
    MaintenanceCategory,
    MaintenanceRequest,
    MaintenanceRequestMedia,
    MaintenanceRequestStatusLog,
)


class MaintenanceRequestMediaInline(admin.TabularInline):
    model = MaintenanceRequestMedia
    extra = 0


class MaintenanceRequestStatusLogInline(admin.TabularInline):
    model = MaintenanceRequestStatusLog
    extra = 0
    readonly_fields = ("old_status", "new_status", "changed_by", "changed_at", "notes")
    can_delete = False


@admin.register(MaintenanceCategory)
class MaintenanceCategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    search_fields = ("name",)


@admin.register(MaintenanceRequest)
class MaintenanceRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id", "title", "building_name", "unit", "lease_tenant", "status",
        "source", "priority", "reported_date", "resolved_date", "assigned_to",
    )
    list_filter = ("status", "source", "priority", "category_ref", "unit__property")
    search_fields = ("title", "description", "unit__unit_number", "unit__leases__tenant__first_name", "unit__leases__tenant__last_name")
    inlines = [MaintenanceRequestMediaInline, MaintenanceRequestStatusLogInline]

    @admin.display(description="Building")
    def building_name(self, obj):
        return obj.building or "-"

    @admin.display(description="Tenant")
    def lease_tenant(self, obj):
        return obj.lease_tenant or "-"


@admin.register(MaintenanceRequestMedia)
class MaintenanceRequestMediaAdmin(admin.ModelAdmin):
    list_display = ("id", "request", "original_filename", "uploaded_by", "uploaded_at", "is_active")
    list_filter = ("is_active", "uploaded_at")


@admin.register(MaintenanceRequestStatusLog)
class MaintenanceRequestStatusLogAdmin(admin.ModelAdmin):
    list_display = ("id", "request", "old_status", "new_status", "changed_by", "changed_at")
    list_filter = ("new_status", "changed_at")

# Register your models here.
