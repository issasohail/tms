from __future__ import annotations
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.views.decorators.http import require_POST

from .forms import LoginForm, AccountCreationForm, AccountChangeForm, AccountAccessForm, permission_groups

Account = get_user_model()
PERMISSION_ACTIONS = [
    ("view", "View"),
    ("add", "Add"),
    ("change", "Update"),
    ("delete", "Delete"),
]


class LoginView(auth_views.LoginView):
    form_class = LoginForm
    template_name = "accounts/login.html"


class LogoutView(auth_views.LogoutView):
    template_name = "accounts/logout.html"


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("accounts:profile")

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
            return redirect("accounts:profile")
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
            return redirect("accounts:profile")
    else:
        form = AccountChangeForm(instance=request.user)
    return render(request, "accounts/profile.html", {"form": form})


def _staff_required(user):
    return user.is_authenticated and user.is_staff


@login_required
def user_access_list(request):
    if not _staff_required(request.user):
        messages.error(request, "You do not have permission to manage users.")
        return redirect("dashboard:home")
    users = Account.objects.order_by("username")
    return render(request, "accounts/user_access_list.html", {"users": users})


@login_required
@require_http_methods(["GET", "POST"])
def user_access_create(request):
    if not _staff_required(request.user):
        messages.error(request, "You do not have permission to manage users.")
        return redirect("dashboard:home")
    user = Account()
    form = AccountAccessForm(request.POST or None, instance=user)
    selected_permissions = set()
    if request.method == "POST" and form.is_valid():
        user = form.save()
        selected_ids = request.POST.getlist("permissions")
        user.user_permissions.set(Permission.objects.filter(id__in=selected_ids))
        messages.success(request, "User created.")
        return redirect("accounts:user_access_list")
    return render(request, "accounts/user_access_form.html", {
        "form": form,
        "managed_user": user,
        "permission_groups": permission_groups(),
        "permission_actions": PERMISSION_ACTIONS,
        "selected_permissions": selected_permissions,
        "is_create": True,
    })


@login_required
@require_http_methods(["GET", "POST"])
def user_access_update(request, pk):
    if not _staff_required(request.user):
        messages.error(request, "You do not have permission to manage users.")
        return redirect("dashboard:home")
    user = get_object_or_404(Account, pk=pk)
    form = AccountAccessForm(request.POST or None, instance=user)
    selected_permissions = set(user.user_permissions.values_list("id", flat=True))
    if request.method == "POST" and form.is_valid():
        user = form.save()
        selected_ids = request.POST.getlist("permissions")
        user.user_permissions.set(Permission.objects.filter(id__in=selected_ids))
        messages.success(request, "User access updated.")
        return redirect("accounts:user_access_list")
    return render(request, "accounts/user_access_form.html", {
        "form": form,
        "managed_user": user,
        "permission_groups": permission_groups(),
        "permission_actions": PERMISSION_ACTIONS,
        "selected_permissions": selected_permissions,
        "is_create": False,
    })


@login_required
@require_POST
def impersonate_start(request, pk):
    if not _staff_required(request.user):
        messages.error(request, "You do not have permission to impersonate users.")
        return redirect("dashboard:home")
    target = get_object_or_404(Account, pk=pk, is_active=True)
    if target.pk == request.user.pk:
        messages.info(request, "You are already using this account.")
        return redirect("accounts:user_access_list")
    request.session["impersonator_user_id"] = request.user.pk
    request.session["impersonate_user_id"] = target.pk
    messages.success(request, f"Now viewing as {target.username}.")
    return redirect("dashboard:home")


@login_required
def impersonate_stop(request):
    request.session.pop("impersonate_user_id", None)
    request.session.pop("impersonator_user_id", None)
    messages.success(request, "Stopped impersonating.")
    return redirect("accounts:user_access_list")
