from __future__ import annotations
from django.views.decorators.http import require_http_methods
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.contrib.auth import views as auth_views

from .forms import LoginForm, AccountCreationForm, AccountChangeForm


class LoginView(auth_views.LoginView):
    form_class = LoginForm
    template_name = "accounts/login.html"


class LogoutView(auth_views.LogoutView):
    template_name = "accounts/logout.html"


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("profile")

    if request.method == "POST":
        form = AccountCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Auto-login after signup
            raw_password = form.cleaned_data.get("password1")
            user = authenticate(username=user.username, password=raw_password)
            if user:
                login(request, user)
            messages.success(
                request, "Welcome! Your account has been created.")
            return redirect("profile")
    else:
        form = AccountCreationForm()
    return render(request, "accounts/signup.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    if request.method == "POST":
        form = AccountChangeForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated.")
            return redirect("profile")
    else:
        form = AccountChangeForm(instance=request.user)
    return render(request, "accounts/profile.html", {"form": form})
