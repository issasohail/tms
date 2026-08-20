from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models
from django.core.cache import cache
from django.utils import timezone
from core.utils.text import normalize_title_fields
from core.model_fields import NormalizedPhoneField


class AccountManager(BaseUserManager):
    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError("The Username field is required")
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(username, password, **extra_fields)


class Account(AbstractBaseUser, PermissionsMixin):
    username = models.CharField(max_length=150, unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    whatsapp_number = NormalizedPhoneField(max_length=32, blank=True)
    email = models.EmailField(unique=True)
    other = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = AccountManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        permissions = [
            ("manage_roles", "Can manage user roles and groups"),
            ("grant_account_permissions", "Can grant account permissions"),
            ("manage_property_access", "Can manage staff property access"),
            ("impersonate_account", "Can impersonate other accounts"),
            ("assign_staff_status", "Can assign staff status"),
            ("access_all_properties", "Can access all properties"),
        ]

    def __str__(self):
        return self.username

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    def save(self, *args, **kwargs):
        normalize_title_fields(self, ("first_name", "last_name"))
        result = super().save(*args, **kwargs)
        cache.delete("core.settings_whatsapp_account_choices")
        return result

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        cache.delete("core.settings_whatsapp_account_choices")
        return result


class AccountPropertyAccess(models.Model):
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="property_access",
    )
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="account_access",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("account", "property"),
                name="accounts_account_property_access_unique",
            )
        ]
        ordering = ("property__property_name", "property_id")

    def __str__(self):
        return f"{self.account} -> {self.property}"
