from django.contrib import admin
from .models import Report  # This is the critical missing import


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('title', 'created_by', 'created_at')
    exclude = ('created_by',)  # Hide from admin form

    def save_model(self, request, obj, form, change):
        if not obj.pk:  # Only set user during creation
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
