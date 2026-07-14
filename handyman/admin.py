from django.contrib import admin
from core.utils.identity import format_phone

from .models import (
    HandymanCategory,
    HandymanJobAttachment,
    HandymanProfile,
    HandymanRating,
    MaintenanceHandymanAssignment,
)


class MaintenanceHandymanAssignmentInline(admin.TabularInline):
    model = MaintenanceHandymanAssignment
    extra = 0


@admin.register(HandymanCategory)
class HandymanCategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    search_fields = ("name",)


@admin.register(HandymanProfile)
class HandymanProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "display_phone_formatted", "is_preferred", "is_active", "average_rating", "completed_jobs_count")
    list_filter = ("is_active", "is_preferred", "categories")
    search_fields = ("full_name", "phone", "whatsapp_number")
    filter_horizontal = ("categories",)
    inlines = [MaintenanceHandymanAssignmentInline]

    @admin.display(description="Phone")
    def display_phone_formatted(self, obj):
        return format_phone(obj.display_phone)


@admin.register(MaintenanceHandymanAssignment)
class MaintenanceHandymanAssignmentAdmin(admin.ModelAdmin):
    list_display = ("id", "maintenance_request", "handyman", "status", "is_current", "assigned_at")
    list_filter = ("status", "is_current", "assigned_at")
    search_fields = ("maintenance_request__title", "handyman__full_name")


@admin.register(HandymanJobAttachment)
class HandymanJobAttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "assignment", "attachment_type", "original_filename", "source", "uploaded_at")
    list_filter = ("attachment_type", "source", "uploaded_at")


@admin.register(HandymanRating)
class HandymanRatingAdmin(admin.ModelAdmin):
    list_display = ("id", "handyman", "maintenance_request", "tenant_name_snapshot", "rating", "created_at")
    list_filter = ("rating", "created_at")
    search_fields = ("handyman__full_name", "tenant_name_snapshot", "job_title_snapshot")
