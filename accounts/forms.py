from __future__ import annotations
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    UserCreationForm,
    UserChangeForm,
)

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
