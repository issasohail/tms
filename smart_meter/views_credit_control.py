from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from smart_meter.forms import MeterCreditAccountForm
from smart_meter.models import LiveReading, Meter, MeterCreditAccount, MeterCreditAudit
from smart_meter.services.credit_control import (
    activate_credit_account,
    clear_enforcement_hold,
    clear_notification_mute,
    deactivate_credit_account,
    evaluate_credit_account,
    resolve_effective_limit,
    set_enforcement_hold,
    set_notification_mute,
)


def _require(request, codename):
    if not request.user.has_perm(f"smart_meter.{codename}"):
        raise PermissionDenied


def _active_installation(meter):
    return (
        meter.installations.filter(is_active=True, end_date__isnull=True)
        .select_related("lease", "lease__tenant", "unit", "unit__property")
        .order_by("-start_date", "-id")
        .first()
    )


def _until_from_post(request):
    raw = (request.POST.get("until") or "").strip()
    if not raw:
        return None
    value = parse_datetime(raw)
    if value and timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value


@login_required
@permission_required("smart_meter.view_meter_credit_details", raise_exception=True)
@require_http_methods(["GET", "POST"])
def credit_control(request, pk):
    meter = get_object_or_404(
        Meter.objects.select_related("unit", "unit__property"),
        pk=pk,
    )
    installation = _active_installation(meter)
    account = None
    if installation:
        account = (
            MeterCreditAccount.objects.filter(
                meter=meter,
                installation=installation,
                lease=installation.lease,
            )
            .select_related("lease", "lease__tenant", "installation")
            .order_by("-created_at")
            .first()
        )

    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "save":
                _require(request, "change_meter_credit_settings")
                if not installation:
                    raise ValueError("No active meter installation is available.")
                if not installation.lease_id:
                    raise ValueError("The active meter installation has no lease.")
                form = MeterCreditAccountForm(request.POST, instance=account)
                if form.is_valid():
                    with transaction.atomic():
                        account = form.save(commit=False)
                        account.meter = meter
                        account.installation = installation
                        account.lease = installation.lease
                        limit, explanation = resolve_effective_limit(account)
                        account.deposit_reference_amount = installation.lease.electricity_security_deposit
                        account.effective_credit_limit = limit
                        account.limit_explanation = explanation
                        account.full_clean()
                        account.save()
                        MeterCreditAudit.objects.create(
                            action_type="settings_update",
                            meter=meter,
                            installation=installation,
                            lease=installation.lease,
                            tenant=getattr(installation.lease, "tenant", None),
                            credit_account=account,
                            user=request.user,
                            source="manual",
                            reason="Credit-control settings saved from staff UI.",
                        )
                    messages.success(request, "Credit-control settings saved.")
                    return redirect("smart_meter:credit_control", pk=meter.pk)
            else:
                form = MeterCreditAccountForm(instance=account)
                if not account:
                    raise ValueError("Save credit-control settings before using this action.")
                reason = (request.POST.get("reason") or "").strip()
                if action == "activate":
                    _require(request, "activate_meter_credit")
                    activate_credit_account(
                        account,
                        user=request.user,
                        reason=reason or "Activated from Credit Control page.",
                    )
                    messages.success(request, "Credit control activated safely.")
                elif action == "deactivate":
                    deactivate_credit_account(
                        account.pk,
                        user=request.user,
                        reason=reason or "Deactivated from Credit Control page.",
                    )
                    messages.success(request, "Credit control deactivated.")
                elif action == "evaluate":
                    _require(request, "change_meter_credit_settings")
                    result = evaluate_credit_account(account.pk, source="manual")
                    messages.success(
                        request,
                        "Evaluation complete. "
                        f"Exposure: Rs.{result.exposure:.2f}; "
                        f"Limit: Rs.{account.effective_credit_limit:.2f}; "
                        f"Usage: {result.percent_used:.2f}%; State: {result.state}.",
                    )
                elif action == "mute":
                    set_notification_mute(
                        account.pk,
                        user=request.user,
                        reason=reason,
                        until=_until_from_post(request),
                        period=request.POST.get("period", ""),
                    )
                    messages.success(request, "Credit notifications muted.")
                elif action == "clear_mute":
                    clear_notification_mute(account.pk, user=request.user, reason=reason)
                    messages.success(request, "Notification mute cleared.")
                elif action == "hold":
                    set_enforcement_hold(
                        account.pk,
                        user=request.user,
                        reason=reason,
                        until=_until_from_post(request),
                        period=request.POST.get("period", ""),
                    )
                    messages.success(request, "Temporary enforcement hold applied.")
                elif action == "clear_hold":
                    clear_enforcement_hold(account.pk, user=request.user, reason=reason)
                    messages.success(request, "Enforcement hold cleared and account reevaluated.")
                else:
                    raise ValueError("Unknown credit-control action.")
                return redirect("smart_meter:credit_control", pk=meter.pk)
        except (ValueError, PermissionError) as exc:
            messages.error(request, f"Credit-control action failed: {exc}")
            form = MeterCreditAccountForm(request.POST, instance=account) if action == "save" else MeterCreditAccountForm(instance=account)
    else:
        form = MeterCreditAccountForm(instance=account)

    computed_limit = None
    computed_explanation = "Save settings to calculate the effective credit limit."
    if account:
        computed_limit, computed_explanation = resolve_effective_limit(account)
    latest_reading = LiveReading.objects.filter(meter=meter).first()
    allowlisted = meter.pk in set(getattr(settings, "METER_CREDIT_ALLOWED_METER_IDS", ()) or ())
    safety = {
        "evaluation": bool(getattr(settings, "METER_ENABLE_AUTOMATIC_CREDIT_EVALUATION", False)),
        "cutoff": bool(getattr(settings, "METER_ENABLE_AUTOMATIC_CUTOFF", False)),
        "restore": bool(getattr(settings, "METER_ENABLE_AUTOMATIC_RESTORE", False)),
        "allowlisted": allowlisted,
        "emergency_stop": bool(getattr(settings, "METER_EMERGENCY_STOP", False)),
    }
    return render(request, "smart_meter/credit_control.html", {
        "meter": meter,
        "installation": installation,
        "lease": installation.lease if installation else None,
        "tenant": getattr(getattr(installation, "lease", None), "tenant", None),
        "account": account,
        "form": form,
        "latest_reading": latest_reading,
        "computed_limit": computed_limit,
        "computed_explanation": computed_explanation,
        "safety": safety,
    })
