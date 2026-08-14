from __future__ import annotations
import hashlib
import logging

from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout as auth_logout
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import Group, Permission
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.cache import cache
from django.db import transaction
from django.db.models import ProtectedError
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.debug import sensitive_post_parameters

from .whatsapp_password_reset import (
    resolve_whatsapp_password_reset_token,
    whatsapp_password_reset_request_url,
)

from .forms import (
    LoginForm,
    AccountCreationForm,
    AccountChangeForm,
    AccountAccessForm,
    GroupAccessForm,
    permission_groups,
)

Account = get_user_model()
security_logger = logging.getLogger("security.accounts")
SIGNUP_RATE_LIMIT = 5
SIGNUP_RATE_WINDOW_SECONDS = 15 * 60
PERMISSION_ACTIONS = [
    ("view", "View"),
    ("add", "Add"),
    ("change", "Update"),
    ("delete", "Delete"),
]


class LoginView(auth_views.LoginView):
    form_class = LoginForm
    template_name = "accounts/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["whatsapp_password_reset_url"] = whatsapp_password_reset_request_url()
        return context

    def get_default_redirect_url(self):
        if self.request.path_info.startswith("/tms/"):
            return "/tms/"
        return super().get_default_redirect_url()


class LogoutView(auth_views.LogoutView):
    http_method_names = ["get", "post", "options"]
    template_name = "accounts/logout.html"

    def get(self, request, *args, **kwargs):
        auth_logout(request)
        messages.success(request, "You have been logged out.")
        return redirect(f"{settings.MARKETING_PUBLIC_BASE_URL.rstrip('/')}/")

    def post(self, request, *args, **kwargs):
        auth_logout(request)
        messages.success(request, "You have been logged out.")
        return redirect(f"{settings.MARKETING_PUBLIC_BASE_URL.rstrip('/')}/")


@sensitive_post_parameters("new_password1", "new_password2")
@require_http_methods(["GET", "POST"])
def whatsapp_password_reset_confirm(request, token):
    if request.method == "POST":
        with transaction.atomic():
            user = resolve_whatsapp_password_reset_token(token, for_update=True)
            form = SetPasswordForm(user, request.POST) if user else None
            if form and form.is_valid():
                form.save()
                security_logger.info("WhatsApp password reset completed account_id=%s", user.pk)
                messages.success(request, "Your password has been updated. You can now log in.")
                return redirect("accounts:login")
    else:
        user = resolve_whatsapp_password_reset_token(token)
        form = SetPasswordForm(user) if user else None
    return render(
        request,
        "accounts/password_reset_confirm.html",
        {
            "form": form,
            "validlink": bool(user),
            "whatsapp_password_reset_url": whatsapp_password_reset_request_url(),
        },
    )

@require_http_methods(["GET", "POST"])
def signup(request):
    if request.method == "POST":
        remote_address = request.META.get("REMOTE_ADDR", "unknown")
        address_hash = hashlib.sha256(remote_address.encode("utf-8")).hexdigest()
        rate_key = f"accounts:signup-attempts:{address_hash}"
        if cache.add(rate_key, 1, SIGNUP_RATE_WINDOW_SECONDS):
            attempt_count = 1
        else:
            try:
                attempt_count = cache.incr(rate_key)
            except ValueError:
                cache.set(rate_key, 1, SIGNUP_RATE_WINDOW_SECONDS)
                attempt_count = 1
        form = AccountCreationForm(request.POST)
        if attempt_count > SIGNUP_RATE_LIMIT:
            form.add_error(
                None,
                "Too many registration attempts. Please try again in 15 minutes.",
            )
            return render(
                request,
                "accounts/signup.html",
                {"form": form},
                status=429,
            )
        if form.is_valid():
            user = form.save()
            security_logger.info(
                "registration_submitted target_user_id=%s username=%s ip_hash=%s",
                user.pk,
                user.username,
                address_hash,
            )
            messages.success(
                request,
                "Your registration has been submitted for approval.",
            )
            return redirect("login")
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


def _has_account_perm(user, action):
    return user.is_authenticated and (
        user.is_superuser or user.has_perm(f"accounts.{action}_account")
    )


def _account_perm_required(request, action, message="You do not have permission to manage users."):
    if _has_account_perm(request.user, action):
        return None
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        raise PermissionDenied(message)
    messages.error(request, message)
    return redirect("dashboard:home")


@login_required
def user_access_list(request):
    denied = _account_perm_required(request, "view")
    if denied:
        return denied
    users = Account.objects.order_by("username")
    return render(request, "accounts/user_access_list.html", {"users": users})


@login_required
@require_http_methods(["GET", "POST"])
def user_access_create(request):
    denied = _account_perm_required(request, "add")
    if denied:
        return denied
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
    denied = _account_perm_required(request, "change")
    if denied:
        return denied
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


def _public_registration_target(pk):
    target = get_object_or_404(Account, pk=pk)
    if target.is_staff or target.is_superuser:
        raise PermissionDenied(
            "Staff and superuser accounts must be managed through the user editor."
        )
    return target


@login_required
@require_POST
def user_registration_approve(request, pk):
    denied = _account_perm_required(request, "change")
    if denied:
        return denied
    target = _public_registration_target(pk)
    if not target.is_active:
        target.is_active = True
        target.save(update_fields=["is_active"])
    security_logger.info(
        "registration_approved target_user_id=%s username=%s actor_user_id=%s actor_username=%s",
        target.pk,
        target.username,
        request.user.pk,
        request.user.get_username(),
    )
    messages.success(request, f"Approved registration for {target.username}.")
    return redirect("accounts:user_access_list")


@login_required
@require_POST
def user_registration_reject(request, pk):
    denied = _account_perm_required(request, "change")
    if denied:
        return denied
    target = _public_registration_target(pk)
    if target.is_active:
        target.is_active = False
        target.save(update_fields=["is_active"])
    security_logger.info(
        "registration_rejected target_user_id=%s username=%s actor_user_id=%s actor_username=%s",
        target.pk,
        target.username,
        request.user.pk,
        request.user.get_username(),
    )
    messages.success(request, f"Registration for {target.username} remains inactive.")
    return redirect("accounts:user_access_list")


@login_required
@require_POST
def user_permission_autosave(request, pk):
    denied = _account_perm_required(request, "change")
    if denied:
        return denied
    user = get_object_or_404(Account, pk=pk)
    selected_ids = [pk for pk in request.POST.getlist("permissions") if pk.isdigit()]
    permissions = Permission.objects.filter(id__in=selected_ids)
    user.user_permissions.set(permissions)
    return JsonResponse({"ok": True, "saved_count": permissions.count()})


@login_required
@require_POST
def user_access_delete(request, pk):
    denied = _account_perm_required(request, "delete")
    if denied:
        return denied
    user = get_object_or_404(Account, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "You cannot delete the account you are currently using.")
        return redirect("accounts:user_access_list")
    username = user.username
    try:
        user.delete()
    except ProtectedError:
        messages.error(request, f"{username} cannot be deleted because related records protect it.")
    else:
        messages.success(request, f"Deleted user {username}.")
    return redirect("accounts:user_access_list")


@login_required
def group_access_list(request):
    if not _staff_required(request.user):
        messages.error(request, "You do not have permission to manage groups.")
        return redirect("dashboard:home")
    groups = (
        Group.objects
        .prefetch_related("permissions", "user_set")
        .order_by("name")
    )
    return render(request, "accounts/group_access_list.html", {"groups": groups})


def _selected_permission_ids(form, group):
    if form.is_bound:
        return {int(pk) for pk in form.data.getlist("permissions") if pk.isdigit()}
    if group.pk:
        return set(group.permissions.values_list("id", flat=True))
    return set()


@login_required
@require_http_methods(["GET", "POST"])
def group_access_create(request):
    if not _staff_required(request.user):
        messages.error(request, "You do not have permission to manage groups.")
        return redirect("dashboard:home")
    group = Group()
    form = GroupAccessForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Group created.")
        return redirect("accounts:group_access_list")
    return render(request, "accounts/group_access_form.html", {
        "form": form,
        "managed_group": group,
        "permission_groups": permission_groups(),
        "permission_actions": PERMISSION_ACTIONS,
        "selected_permissions": _selected_permission_ids(form, group),
        "is_create": True,
    })


@login_required
@require_http_methods(["GET", "POST"])
def group_access_update(request, pk):
    if not _staff_required(request.user):
        messages.error(request, "You do not have permission to manage groups.")
        return redirect("dashboard:home")
    group = get_object_or_404(Group, pk=pk)
    form = GroupAccessForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Group access updated.")
        return redirect("accounts:group_access_list")
    return render(request, "accounts/group_access_form.html", {
        "form": form,
        "managed_group": group,
        "permission_groups": permission_groups(),
        "permission_actions": PERMISSION_ACTIONS,
        "selected_permissions": _selected_permission_ids(form, group),
        "is_create": False,
    })


@login_required
@require_POST
def group_access_delete(request, pk):
    if not _staff_required(request.user):
        messages.error(request, "You do not have permission to manage groups.")
        return redirect("dashboard:home")
    group = get_object_or_404(Group, pk=pk)
    name = group.name
    group.delete()
    messages.success(request, f"Deleted group {name}.")
    return redirect("accounts:group_access_list")


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
