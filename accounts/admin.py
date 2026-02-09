from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Account


@admin.register(Account)
class AccountAdmin(UserAdmin):
    model = Account
    list_display = ("username", "email", "first_name", "last_name",
                    "whatsapp_number", "is_staff", "is_active")
    search_fields = ("username", "email", "first_name",
                     "last_name", "whatsapp_number")
    ordering = ("username",)
    fieldsets = (
        (None, {"fields": ("username", "email", "password")}),
        ("Personal info", {"fields": ("first_name",
         "last_name", "whatsapp_number", "other")}),
        ("Permissions", {"fields": ("is_active", "is_staff",
         "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "email", "password1", "password2", "is_staff", "is_active"),
        }),
    )
