from django.contrib import admin, messages

from punjab_estamp.services.sync import PunjabEStampSyncError, sync_district_tehsils

from .models import (
    LeaseEStampWorkflow,
    PunjabEStampDistrict,
    PunjabEStampPurpose,
    PunjabEStampRelation,
    PunjabEStampTehsil,
)


@admin.register(PunjabEStampDistrict)
class PunjabEStampDistrictAdmin(admin.ModelAdmin):
    list_display = ("name", "portal_value", "active", "sort_order", "last_synced_at")
    actions = ("sync_selected_tehsils",)
    list_editable = ("active", "sort_order")
    search_fields = ("name", "portal_value")
    ordering = ("sort_order", "name")

    @admin.action(description="Sync Tehsils from Punjab for selected Districts")
    def sync_selected_tehsils(self, request, queryset):
        succeeded = 0
        failed = []
        for district in queryset.filter(active=True).order_by("sort_order", "name"):
            try:
                sync_district_tehsils(district)
            except PunjabEStampSyncError as exc:
                failed.append(f"{district.name}: {exc}")
            else:
                succeeded += 1
        if succeeded:
            self.message_user(
                request,
                f"Synchronized Tehsils for {succeeded} District(s).",
                level=messages.SUCCESS,
            )
        if failed:
            self.message_user(
                request,
                "Some Districts could not be synchronized: " + " | ".join(failed),
                level=messages.WARNING,
            )


@admin.register(PunjabEStampTehsil)
class PunjabEStampTehsilAdmin(admin.ModelAdmin):
    list_display = ("name", "district", "portal_value", "active", "sort_order")
    list_filter = ("district", "active")
    list_editable = ("active", "sort_order")
    search_fields = ("name", "portal_value", "district__name")
    ordering = ("district__sort_order", "sort_order", "name")


@admin.register(PunjabEStampRelation)
class PunjabEStampRelationAdmin(admin.ModelAdmin):
    list_display = ("name", "portal_value", "active", "sort_order")
    list_editable = ("active", "sort_order")
    search_fields = ("name", "portal_value")
    ordering = ("sort_order", "name")


@admin.register(PunjabEStampPurpose)
class PunjabEStampPurposeAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "portal_value",
        "denomination",
        "default_continuation_sheets",
        "active",
        "is_default_for_lease",
        "sort_order",
    )
    list_editable = ("active", "is_default_for_lease", "sort_order")
    search_fields = ("name", "portal_value")
    ordering = ("sort_order", "name")


@admin.register(LeaseEStampWorkflow)
class LeaseEStampWorkflowAdmin(admin.ModelAdmin):
    list_display = (
        "lease",
        "lease_history",
        "status",
        "challan_number",
        "psid",
        "stamp_number",
        "updated_at",
    )
    list_filter = ("status",)
    search_fields = (
        "lease__id",
        "challan_number",
        "psid",
        "stamp_number",
        "lease__tenant__first_name",
        "lease__tenant__last_name",
    )
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("lease", "lease_history", "purpose", "lease_document")

