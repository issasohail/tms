from __future__ import annotations
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    UserCreationForm,
    UserChangeForm,
)
from django.contrib.auth.models import Group, Permission
from properties.models import Property
from django.core.exceptions import ValidationError
from core.utils.text import add_auto_titlecase_class

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

    def clean(self):
        try:
            return super().clean()
        except ValidationError:
            username = (self.data.get("username") or "").strip()
            password = self.data.get("password") or ""
            pending_user = Account._default_manager.filter(
                username__iexact=username,
                is_active=False,
                is_staff=False,
                is_superuser=False,
            ).first()
            if pending_user and pending_user.check_password(password):
                raise ValidationError(
                    "Your registration is awaiting administrator approval.",
                    code="inactive",
                )
            raise


class AccountCreationForm(UserCreationForm):
    website = forms.CharField(
        required=False,
        label="Website",
        widget=forms.TextInput(
            attrs={"autocomplete": "off", "tabindex": "-1"}
        ),
    )

    class Meta(UserCreationForm.Meta):
        model = Account
        fields = ("username", "email", "first_name",
                  "last_name", "whatsapp_number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        add_auto_titlecase_class(self.fields)

    def clean_website(self):
        value = (self.cleaned_data.get("website") or "").strip()
        if value:
            raise forms.ValidationError("Registration could not be submitted.")
        return ""

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = False
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
        return user


class AccountChangeForm(UserChangeForm):
    # Hide raw password hash field on the profile page
    password = None

    class Meta:
        model = Account
        fields = ("first_name", "last_name", "email", "whatsapp_number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        add_auto_titlecase_class(self.fields)


class AccountAccessForm(forms.ModelForm):
    all_properties = forms.BooleanField(
        label="Access all properties",
        required=False,
        help_text="If unchecked, access is limited to the selected properties.",
    )
    properties = forms.ModelMultipleChoiceField(
        queryset=Property.objects.order_by("property_name"),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select form-select-sm select2"}),
    )
    password1 = forms.CharField(label="Password", required=False, widget=forms.PasswordInput(attrs={"class": "form-control form-control-sm"}))
    password2 = forms.CharField(label="Reconfirm Password", required=False, widget=forms.PasswordInput(attrs={"class": "form-control form-control-sm"}))
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by("name"),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select form-select-sm select2"}),
    )

    class Meta:
        model = Account
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "whatsapp_number",
            "groups",
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

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        add_auto_titlecase_class(self.fields)
        if self.instance.pk:
            self.fields["properties"].initial = self.instance.property_access.values_list("property_id", flat=True)
            self.fields["all_properties"].initial = self.instance.has_perm("accounts.access_all_properties")
        if actor and not actor.is_superuser and not actor.has_perm("accounts.assign_staff_status"):
            self.fields.pop("is_staff", None)
        if actor and not actor.is_superuser and not actor.has_perm("accounts.manage_roles"):
            self.fields.pop("groups", None)
        if actor and not actor.is_superuser and not actor.has_perm("accounts.manage_property_access"):
            self.fields.pop("properties", None)
            self.fields.pop("all_properties", None)
        elif actor and not actor.is_superuser and "properties" in self.fields:
            if not actor.has_perm("accounts.access_all_properties"):
                allowed_ids = actor.property_access.values_list("property_id", flat=True)
                self.fields["properties"].queryset = Property.objects.filter(pk__in=allowed_ids).order_by("property_name")

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


class GroupAccessForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.select_related("content_type").order_by(
            "content_type__app_label",
            "content_type__model",
            "codename",
        ),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Group
        fields = ("name", "permissions")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        add_auto_titlecase_class(self.fields)


def permission_groups(actor=None):
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
            "whatsapp",
        ])
        .order_by("content_type__app_label", "content_type__model", "codename")
    )
    if actor is not None and not actor.is_superuser:
        allowed_names = actor.get_all_permissions()
        allowed_ids = []
        for name in allowed_names:
            if "." not in name:
                continue
            app_label, codename = name.split(".", 1)
            allowed_ids.extend(
                perms.filter(content_type__app_label=app_label, codename=codename).values_list("id", flat=True)
            )
        perms = perms.filter(id__in=allowed_ids)
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
