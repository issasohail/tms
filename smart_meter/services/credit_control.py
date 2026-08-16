"""Observation-first smart-meter credit-control calculations and policy transitions."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from invoices.models import Invoice
from payments.models import PaymentDetail
from smart_meter.models import (
    LiveReading,
    MeterCreditAccount,
    MeterCreditAudit,
    MeterEvaluationRequest,
)

logger = logging.getLogger(__name__)
MONEY = Decimal("0.01")
KWH = Decimal("0.001")
END_KWH_RE = re.compile(r"end\s*unit\s*=\s*([0-9]+(?:\.[0-9]+)?)", re.I)


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def bool_setting(name, default=False):
    value = getattr(settings, name, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def resolve_effective_limit(account: MeterCreditAccount) -> tuple[Decimal, str]:
    fixed = _money(account.fixed_credit_limit)
    deposit = _money(getattr(account.lease, "electricity_security_deposit", 0))
    deposit_derived = _money(deposit * _decimal(account.deposit_percentage) / Decimal("100"))
    source = account.credit_limit_source
    if source == "deposit_percent":
        value, explanation = deposit_derived, f"{account.deposit_percentage}% of electricity security deposit {deposit}"
    elif source == "lower_of":
        value = min(fixed, deposit_derived)
        explanation = f"lower of fixed {fixed} and electricity-security-derived {deposit_derived}"
    elif source == "lease_override" and account.lease_override_limit is not None:
        value = _money(account.lease_override_limit)
        explanation = f"lease override {value}"
    else:
        value, explanation = fixed, f"fixed limit {fixed}"
    return value, explanation


def _electric_invoice_rows(account: MeterCreditAccount):
    """Return invoice rows/items that belong to this meter's electricity billing.

    The existing system stores meter identity and billed end reading in the electricity
    InvoiceItem description.  We use that established format to avoid a parallel ledger.
    """
    meter_token = f"Meter#={account.meter.meter_number}"
    return (
        Invoice.objects.filter(lease_id=account.lease_id)
        .exclude(lifecycle_status__in=("cancelled", "void"))
        .exclude(status="cancelled")
        .filter(items__description__icontains=meter_token)
        .distinct()
        .prefetch_related("items")
        .order_by("issue_date", "id")
    )


def electricity_outstanding_and_last_billed_kwh(account: MeterCreditAccount) -> tuple[Decimal, Decimal | None]:
    outstanding = Decimal("0.00")
    last_billed = None
    meter_token = f"Meter#={account.meter.meter_number}"
    for invoice in _electric_invoice_rows(account):
        electric_total = Decimal("0.00")
        end_values = []
        for item in invoice.items.all():
            desc = item.description or ""
            if meter_token.lower() not in desc.lower():
                continue
            electric_total += _money(item.amount)
            match = END_KWH_RE.search(desc)
            if match:
                end_values.append(_decimal(match.group(1)).quantize(KWH))
        if electric_total <= 0:
            continue
        # Existing accounting allocation is invoice-level.  Cap the electric share
        # by the invoice's current outstanding amount so payments are not double-counted.
        outstanding += min(electric_total, _money(invoice.outstanding_balance))
        if end_values:
            candidate = max(end_values)
            last_billed = candidate if last_billed is None else max(last_billed, candidate)
    explicitly_paid = explicit_electricity_payments(account)
    return _money(max(outstanding - explicitly_paid, Decimal("0.00"))), last_billed


def explicit_electricity_payments(account: MeterCreditAccount) -> Decimal:
    """Return lease payments explicitly allocated to this meter's electricity.

    Electricity allocation is a subset of PaymentDetail.lease_amount, so it does
    not change invoice totals. It tells credit control which part of a partial
    invoice payment should reduce electricity exposure instead of relying on the
    conservative electricity-last fallback.
    """
    amounts = PaymentDetail.objects.filter(
        payment__lease_id=account.lease_id,
        electricity_meter_id=account.meter_id,
        electricity_amount__gt=0,
        lease_amount__gt=0,
    ).values_list("electricity_amount", flat=True)
    return _money(sum(amounts, Decimal("0.00")))


def current_rate(account: MeterCreditAccount) -> Decimal | None:
    from smart_meter.rates import resolve_electricity_rate

    rate = resolve_electricity_rate(
        meter=account.meter,
        lease=account.lease,
    ).rate
    return rate if rate > 0 else None


def notification_muted(account: MeterCreditAccount, now=None) -> bool:
    now = now or timezone.now()
    if account.notifications_muted_for_period == "indefinite":
        return True
    if account.notifications_muted_for_period == "current_month":
        stamp = account.notification_muted_at or account.updated_at
        return bool(stamp and (stamp.year, stamp.month) == (now.year, now.month))
    return bool(account.notifications_muted_until and account.notifications_muted_until > now)


def enforcement_held(account: MeterCreditAccount, now=None) -> bool:
    now = now or timezone.now()
    if account.enforcement_hold_for_period == "indefinite":
        return True
    if account.enforcement_hold_for_period == "current_month":
        stamp = account.enforcement_hold_at or account.updated_at
        return bool(stamp and (stamp.year, stamp.month) == (now.year, now.month))
    return bool(account.enforcement_hold_until and account.enforcement_hold_until > now)


def request_credit_evaluation(meter, live_reading=None):
    """Debounced, fail-open queue insert called only after reading persistence succeeds."""
    if not bool_setting("METER_ENABLE_AUTOMATIC_CREDIT_EVALUATION", False):
        return None
    if meter.billing_mode != "credit_controlled":
        return None
    account_exists = MeterCreditAccount.objects.filter(meter=meter, is_enabled=True).exists()
    if not account_exists:
        return None
    defaults = {
        "latest_reading_id": getattr(live_reading, "pk", None),
        "reading_timestamp": getattr(live_reading, "ts", None) or timezone.now(),
        "last_error": "",
    }
    pending = MeterEvaluationRequest.objects.filter(meter=meter, status="pending").order_by("-created_at").first()
    if pending:
        for key, value in defaults.items():
            setattr(pending, key, value)
        pending.save(update_fields=["latest_reading_id", "reading_timestamp", "last_error", "updated_at"])
        return pending
    return MeterEvaluationRequest.objects.create(meter=meter, status="pending", **defaults)


def activate_credit_account(account: MeterCreditAccount, *, user=None, reason=""):
    with transaction.atomic():
        account = MeterCreditAccount.objects.select_for_update().select_related("meter", "installation", "lease").get(pk=account.pk)
        if account.meter.billing_mode != "credit_controlled":
            raise ValueError("Meter billing mode must be credit_controlled before activation")
        if account.meter.is_check_meter:
            raise ValueError("Audit/check meters cannot use credit control")
        inst = account.installation
        if not inst.is_active or inst.end_date is not None or inst.meter_id != account.meter_id or inst.lease_id != account.lease_id:
            raise ValueError("Credit account installation/lease is not the active meter installation")
        live = LiveReading.objects.filter(meter=account.meter).first()
        if not live or live.total_energy is None:
            raise ValueError("A current cumulative meter reading is required for activation")
        rate = current_rate(account)
        if rate is None:
            raise ValueError("Electricity tariff/rate is missing")
        limit, explanation = resolve_effective_limit(account)
        if limit <= 0:
            if account.credit_limit_source in {"deposit_percent", "lower_of"}:
                raise ValueError(
                    "Electricity security deposit is zero. Enter an electricity "
                    "security deposit or select a valid fixed credit limit before activation."
                )
            raise ValueError("Effective credit limit must be greater than zero")
        outstanding, last_billed = electricity_outstanding_and_last_billed_kwh(account)
        checkpoint = max(_decimal(live.total_energy), last_billed or Decimal("0"))
        now = timezone.now()
        account.deposit_reference_amount = _money(
            getattr(account.lease, "electricity_security_deposit", 0)
        )
        account.effective_credit_limit = limit
        account.limit_explanation = explanation
        account.activated_at = now
        account.activation_reading_kwh = _decimal(live.total_energy).quantize(KWH)
        account.checkpoint_reading_kwh = checkpoint.quantize(KWH)
        account.checkpoint_at = now
        account.starting_tariff = rate
        account.previous_unpaid_electricity = outstanding
        account.payments_applied = explicit_electricity_payments(account)
        account.current_exposure = outstanding
        account.policy_snapshot = {
            "credit_limit_source": account.credit_limit_source,
            "fixed_credit_limit": str(account.fixed_credit_limit),
            "deposit_percentage": str(account.deposit_percentage),
            "deposit_reference_amount": str(account.deposit_reference_amount),
            "effective_credit_limit": str(limit),
            "warning_threshold_percent": str(account.warning_threshold_percent),
            "final_warning_threshold_percent": str(account.final_warning_threshold_percent),
            "cutoff_threshold_percent": str(account.cutoff_threshold_percent),
            "reconnect_threshold_percent": str(account.reconnect_threshold_percent),
            "tariff": str(rate),
            "tariff_history_supported": False,
        }
        account.is_enabled = True
        account.enforcement_state = "normal"
        account.data_quality_reason = ""
        account.save()
        MeterCreditAudit.objects.create(
            action_type="activate", meter=account.meter, installation=inst, lease=account.lease,
            tenant=getattr(account.lease, "tenant", None), credit_account=account,
            exposure_after=account.current_exposure, user=user, source="manual", reason=reason,
            metadata={"policy_snapshot": account.policy_snapshot},
        )
        return account


@dataclass
class EvaluationResult:
    account_id: int
    valid: bool
    state: str
    exposure: Decimal
    percent_used: Decimal
    reason: str = ""


def _state_for(account: MeterCreditAccount) -> tuple[str, int]:
    if enforcement_held(account):
        return "manual_hold", account.last_warning_level
    pct = account.percent_used
    if pct >= account.cutoff_threshold_percent:
        return "cutoff_eligible", max(account.last_warning_level, 2)
    if pct >= account.final_warning_threshold_percent:
        return "warning_2", max(account.last_warning_level, 2)
    if pct >= account.warning_threshold_percent:
        return "warning_1", max(account.last_warning_level, 1)
    return "normal", 0


def evaluate_credit_account(account_id: int, *, dry_run=False, source="scheduled") -> EvaluationResult:
    with transaction.atomic():
        account = (
            MeterCreditAccount.objects.select_for_update()
            .select_related("meter", "installation", "lease", "lease__tenant")
            .get(pk=account_id)
        )
        before_state = account.enforcement_state
        before_exposure = account.current_exposure
        if not account.is_enabled or account.meter.billing_mode != "credit_controlled":
            return EvaluationResult(account.pk, False, before_state, before_exposure, account.percent_used, "account not active credit-controlled")
        inst = account.installation
        if not inst.is_active or inst.end_date is not None or inst.meter_id != account.meter_id or inst.lease_id != account.lease_id:
            return _data_error(account, "installation_mismatch", "active installation/lease no longer matches", dry_run, before_state, before_exposure, source)
        if getattr(account.lease, "status", "") != "active":
            return _data_error(account, "data_review_required", "lease is not active", dry_run, before_state, before_exposure, source)
        if account.meter.is_check_meter:
            return _data_error(account, "data_review_required", "audit/check meter", dry_run, before_state, before_exposure, source)
        live = LiveReading.objects.filter(meter=account.meter).first()
        if not live or live.total_energy is None:
            return _data_error(account, "stale_reading", "live cumulative reading missing", dry_run, before_state, before_exposure, source)
        age = timezone.now() - live.ts
        if age.total_seconds() > account.stale_after_minutes * 60:
            return _data_error(account, "stale_reading", f"latest reading is {int(age.total_seconds()/60)} minutes old", dry_run, before_state, before_exposure, source)
        current = _decimal(live.total_energy).quantize(KWH)
        base = account.checkpoint_reading_kwh
        if base is None:
            return _data_error(account, "data_review_required", "checkpoint reading missing", dry_run, before_state, before_exposure, source)
        outstanding, last_billed = electricity_outstanding_and_last_billed_kwh(account)
        if last_billed is not None and last_billed > base:
            base = last_billed.quantize(KWH)
        if current < base:
            return _data_error(account, "reading_reset_detected", f"cumulative reading {current} below checkpoint {base}", dry_run, before_state, before_exposure, source)
        delta = (current - base).quantize(KWH)
        if delta > account.max_consumption_jump_kwh:
            return _data_error(account, "data_review_required", f"consumption jump {delta} kWh exceeds configured maximum", dry_run, before_state, before_exposure, source)
        rate = current_rate(account)
        if rate is None:
            return _data_error(account, "tariff_missing", "electricity tariff/rate missing", dry_run, before_state, before_exposure, source)
        limit, explanation = resolve_effective_limit(account)
        if limit <= 0:
            return _data_error(
                account,
                "data_review_required",
                "electricity security deposit produces a zero effective credit limit",
                dry_run,
                before_state,
                before_exposure,
                source,
            )
        account.deposit_reference_amount = _money(
            getattr(account.lease, "electricity_security_deposit", 0)
        )
        account.effective_credit_limit = limit
        account.limit_explanation = explanation
        unbilled = _money(delta * rate)
        exposure = _money(outstanding + unbilled)
        account.accrued_usage_amount = unbilled
        account.previous_unpaid_electricity = outstanding
        account.payments_applied = explicit_electricity_payments(account)
        account.current_exposure = exposure
        account.last_evaluated_reading_kwh = current
        account.last_evaluated_at = timezone.now()
        account.data_quality_reason = ""
        account.enforcement_state, account.last_warning_level = _state_for(account)
        if not dry_run:
            account.save()
            MeterCreditAudit.objects.create(
                action_type="evaluate", meter=account.meter, installation=account.installation,
                lease=account.lease, tenant=getattr(account.lease, "tenant", None), credit_account=account,
                previous_state=before_state, new_state=account.enforcement_state,
                exposure_before=before_exposure, exposure_after=exposure, source=source,
                metadata={"reading_kwh": str(current), "checkpoint_kwh": str(base), "unbilled_amount": str(unbilled), "electricity_outstanding": str(outstanding), "rate": str(rate)},
            )
            # Payments or credits can make a previously queued automatic OFF obsolete.
            cutoff_amount = account.effective_credit_limit * account.cutoff_threshold_percent / Decimal("100")
            if exposure < cutoff_amount:
                from smart_meter.models import MeterCommand
                from smart_meter.services.command_lifecycle import ACTIVE
                MeterCommand.objects.filter(
                    meter=account.meter, command_type="relay", desired_state="off",
                    source__in=("credit_control", "system"), status__in=ACTIVE,
                ).update(status="cancelled", cancelled_at=timezone.now(), cancelled_reason="exposure recalculated below cutoff threshold")
            account_pk = account.pk
            state_after = account.enforcement_state
            def _after_commit():
                try:
                    from smart_meter.services.notifications import maybe_send_credit_notification
                    from smart_meter.services.enforcement import automatic_enforcement
                    refreshed = MeterCreditAccount.objects.select_related("meter", "installation", "lease", "lease__tenant").get(pk=account_pk)
                    if state_after != before_state:
                        maybe_send_credit_notification(refreshed, state_after)
                    automatic_enforcement(account_pk)
                except Exception:
                    logger.exception("meter_credit_post_evaluation_action_failed account=%s", account_pk)
            transaction.on_commit(_after_commit)
        return EvaluationResult(account.pk, True, account.enforcement_state, exposure, account.percent_used, "")


def _data_error(account, state, reason, dry_run, before_state, before_exposure, source):
    if not dry_run:
        account.enforcement_state = state
        account.data_quality_reason = reason
        account.last_evaluated_at = timezone.now()
        account.save(update_fields=["enforcement_state", "data_quality_reason", "last_evaluated_at", "updated_at"])
        MeterCreditAudit.objects.create(
            action_type="evaluation_blocked", meter=account.meter, installation=account.installation,
            lease=account.lease, tenant=getattr(account.lease, "tenant", None), credit_account=account,
            previous_state=before_state, new_state=state, exposure_before=before_exposure,
            exposure_after=account.current_exposure, source=source, reason=reason,
        )
    return EvaluationResult(account.pk, False, state, account.current_exposure, account.percent_used, reason)


def process_evaluation_request(request_id, *, dry_run=False):
    with transaction.atomic():
        req = MeterEvaluationRequest.objects.select_for_update().get(pk=request_id)
        if req.status == "done" and not dry_run:
            return None
        req.status = "processing"
        req.attempts += 1
        if not dry_run:
            req.save(update_fields=["status", "attempts", "updated_at"])
    try:
        account = MeterCreditAccount.objects.filter(meter_id=req.meter_id, is_enabled=True).order_by("-created_at").first()
        result = evaluate_credit_account(account.pk, dry_run=dry_run, source="reading") if account else None
        if not dry_run:
            MeterEvaluationRequest.objects.filter(pk=req.pk).update(status="done", processed_at=timezone.now(), last_error="")
        return result
    except Exception as exc:
        if not dry_run:
            MeterEvaluationRequest.objects.filter(pk=req.pk).update(status="failed", processed_at=timezone.now(), last_error=str(exc))
        raise


def _require_credit_permission(user, codename):
    if user is None or not getattr(user, "has_perm", lambda _p: False)(f"smart_meter.{codename}"):
        raise PermissionError(f"Permission smart_meter.{codename} is required")


def deactivate_credit_account(account_id, *, user, reason):
    _require_credit_permission(user, "deactivate_meter_credit")
    if not reason:
        raise ValueError("A reason is required")
    with transaction.atomic():
        account = (
            MeterCreditAccount.objects.select_for_update()
            .select_related("meter", "installation", "lease")
            .get(pk=account_id)
        )
        if not account.is_enabled:
            return account
        previous_state = account.enforcement_state
        account.is_enabled = False
        account.active_installation_key = None
        account.enforcement_state = "normal"
        account.data_quality_reason = ""
        account.save(update_fields=[
            "is_enabled", "active_installation_key", "enforcement_state",
            "data_quality_reason", "updated_at",
        ])
        from smart_meter.models import MeterCommand
        from smart_meter.services.command_lifecycle import ACTIVE

        MeterCommand.objects.filter(
            meter=account.meter,
            source__in=("credit_control", "system"),
            status__in=ACTIVE,
        ).update(
            status="cancelled",
            cancelled_at=timezone.now(),
            cancelled_reason="credit control deactivated",
        )
        MeterCreditAudit.objects.create(
            action_type="deactivate",
            meter=account.meter,
            installation=account.installation,
            lease=account.lease,
            tenant=getattr(account.lease, "tenant", None),
            credit_account=account,
            previous_state=previous_state,
            new_state=account.enforcement_state,
            user=user,
            source="manual",
            reason=reason,
        )
        return account


def set_notification_mute(account_id, *, user, reason, until=None, period=""):
    _require_credit_permission(user, "mute_meter_credit_notifications")
    if not reason:
        raise ValueError("A reason is required")
    if period not in {"", "current_month", "indefinite"}:
        raise ValueError("Unsupported mute period")
    if period == "indefinite" and not getattr(user, "is_superuser", False):
        raise PermissionError("Indefinite notification mute requires elevated permission")
    with transaction.atomic():
        account = MeterCreditAccount.objects.select_for_update().get(pk=account_id)
        before = {"until": str(account.notifications_muted_until or ""), "period": account.notifications_muted_for_period}
        account.notifications_muted_until = until if not period else None
        account.notifications_muted_for_period = period
        account.notification_mute_reason = reason
        account.notifications_muted_by = user
        account.notification_muted_at = timezone.now()
        account.save(update_fields=["notifications_muted_until", "notifications_muted_for_period", "notification_mute_reason", "notifications_muted_by", "notification_muted_at", "updated_at"])
        MeterCreditAudit.objects.create(action_type="notification_mute", meter=account.meter, installation=account.installation, lease=account.lease, credit_account=account, user=user, source="manual", reason=reason, metadata={"before": before, "until": str(until or ""), "period": period})
        return account


def clear_notification_mute(account_id, *, user, reason):
    _require_credit_permission(user, "mute_meter_credit_notifications")
    if not reason:
        raise ValueError("A reason is required")
    with transaction.atomic():
        account = MeterCreditAccount.objects.select_for_update().get(pk=account_id)
        account.notifications_muted_until = None
        account.notifications_muted_for_period = ""
        account.notification_mute_reason = ""
        account.notifications_muted_by = None
        account.notification_muted_at = None
        account.save(update_fields=["notifications_muted_until", "notifications_muted_for_period", "notification_mute_reason", "notifications_muted_by", "notification_muted_at", "updated_at"])
        MeterCreditAudit.objects.create(action_type="notification_mute_cleared", meter=account.meter, installation=account.installation, lease=account.lease, credit_account=account, user=user, source="manual", reason=reason)
        return account


def set_enforcement_hold(account_id, *, user, reason, until=None, period=""):
    _require_credit_permission(user, "hold_meter_credit_enforcement")
    if not reason:
        raise ValueError("A reason is required")
    if period not in {"", "current_month", "indefinite"}:
        raise ValueError("Unsupported hold period")
    if period == "indefinite" and not getattr(user, "is_superuser", False):
        raise PermissionError("Indefinite enforcement hold requires elevated permission")
    with transaction.atomic():
        account = MeterCreditAccount.objects.select_for_update().get(pk=account_id)
        account.enforcement_hold_until = until if not period else None
        account.enforcement_hold_for_period = period
        account.enforcement_hold_reason = reason
        account.enforcement_hold_by = user
        account.enforcement_hold_at = timezone.now()
        account.enforcement_state = "manual_hold"
        account.save(update_fields=["enforcement_hold_until", "enforcement_hold_for_period", "enforcement_hold_reason", "enforcement_hold_by", "enforcement_hold_at", "enforcement_state", "updated_at"])
        # An automatic OFF that has not been executed must not survive a new hold.
        from smart_meter.services.command_lifecycle import ACTIVE
        from smart_meter.models import MeterCommand
        now = timezone.now()
        MeterCommand.objects.filter(meter=account.meter, command_type="relay", desired_state="off", source__in=("credit_control", "system"), status__in=ACTIVE).update(status="cancelled", cancelled_at=now, cancelled_reason="enforcement hold activated")
        MeterCreditAudit.objects.create(action_type="enforcement_hold", meter=account.meter, installation=account.installation, lease=account.lease, credit_account=account, user=user, source="manual", reason=reason, metadata={"until": str(until or ""), "period": period})
        return account


def clear_enforcement_hold(account_id, *, user, reason):
    _require_credit_permission(user, "hold_meter_credit_enforcement")
    if not reason:
        raise ValueError("A reason is required")
    with transaction.atomic():
        account = MeterCreditAccount.objects.select_for_update().get(pk=account_id)
        account.enforcement_hold_until = None
        account.enforcement_hold_for_period = ""
        account.enforcement_hold_reason = ""
        account.enforcement_hold_by = None
        account.enforcement_hold_at = None
        account.save(update_fields=["enforcement_hold_until", "enforcement_hold_for_period", "enforcement_hold_reason", "enforcement_hold_by", "enforcement_hold_at", "updated_at"])
        MeterCreditAudit.objects.create(action_type="enforcement_hold_cleared", meter=account.meter, installation=account.installation, lease=account.lease, credit_account=account, user=user, source="manual", reason=reason)
    # Re-evaluate immediately after a hold is released.
    return evaluate_credit_account(account_id, source="manual")


def reset_credit_checkpoint(account_id, *, user, reason, reading_kwh=None):
    _require_credit_permission(user, "change_meter_credit_settings")
    if not reason:
        raise ValueError("A reason is required")
    with transaction.atomic():
        account = MeterCreditAccount.objects.select_for_update().select_related("meter").get(pk=account_id)
        live = LiveReading.objects.filter(meter=account.meter).first()
        value = _decimal(reading_kwh if reading_kwh is not None else getattr(live, "total_energy", None))
        if value < 0:
            raise ValueError("Checkpoint reading cannot be negative")
        previous = account.checkpoint_reading_kwh
        account.checkpoint_reading_kwh = value.quantize(KWH)
        account.checkpoint_at = timezone.now()
        account.data_quality_reason = ""
        account.enforcement_state = "normal"
        account.save(update_fields=["checkpoint_reading_kwh", "checkpoint_at", "data_quality_reason", "enforcement_state", "updated_at"])
        MeterCreditAudit.objects.create(action_type="checkpoint_reset", meter=account.meter, installation=account.installation, lease=account.lease, credit_account=account, user=user, source="manual", reason=reason, metadata={"previous": str(previous), "new": str(account.checkpoint_reading_kwh)})
        return account
