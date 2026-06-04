from __future__ import annotations
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    UserCreationForm,
    UserChangeForm,
)
from django.contrib.auth.models import Permission

Account = get_user_model()


class LoginForm(AuthenticationForm):
    """
    Simple wrapper around Django's AuthenticationForm so you can customize
    labels/widgets/placeholders as needed.
    """
    username = forms.CharField(
        label="Username",
        widget=forms.TextInput(
            attrs={"autofocus": True, "placeholder": "Username"}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"placeholder": "Password"}),
    )


class AccountCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = Account
        fields = ("username", "email", "first_name",
                  "last_name", "whatsapp_number")


class AccountChangeForm(UserChangeForm):
    # Hide raw password hash field on the profile page
    password = None

    class Meta:
        model = Account
        fields = ("first_name", "last_name", "email", "whatsapp_number")


class AccountAccessForm(forms.ModelForm):
    password1 = forms.CharField(required=False, widget=forms.PasswordInput(attrs={"class": "form-control form-control-sm"}))
    password2 = forms.CharField(required=False, widget=forms.PasswordInput(attrs={"class": "form-control form-control-sm"}))

    class Meta:
        model = Account
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "whatsapp_number",
            "is_active",
            "is_staff",
        )
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "first_name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "last_name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "email": forms.EmailInput(attrs={"class": "form-control form-control-sm"}),
            "whatsapp_number": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_staff": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 or password2:
            if password1 != password2:
                raise forms.ValidationError("Passwords do not match.")
        if not self.instance.pk and not password1:
            raise forms.ValidationError("Password is required for a new user.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)
        if commit:
            user.save()
            self.save_m2m()
        return user


def permission_groups():
    perms = (
        Permission.objects
        .select_related("content_type")
        .filter(content_type__app_label__in=[
            "accounts",
            "properties",
            "tenants",
            "leases",
            "payments",
            "invoices",
            "expenses",
            "utilities",
            "maintenance",
            "smart_meter",
            "reports",
            "core",
        ])
        .order_by("content_type__app_label", "content_type__model", "codename")
    )
    grouped = {}
    for perm in perms:
        codename = perm.codename
        action = codename.split("_", 1)[0]
        if action not in {"view", "add", "change", "delete"}:
            continue
        ct = perm.content_type
        key = (ct.app_label, ct.model)
        grouped.setdefault(key, {
            "app_label": ct.app_label,
            "model": ct.model,
            "label": ct.model.replace("_", " ").title(),
            "permissions": {},
        })
        grouped[key]["permissions"][action] = perm
    return grouped.values()
