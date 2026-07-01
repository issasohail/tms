from decimal import Decimal

from django.db import models


class LeaseLateFeeSettings(models.Model):
    LATE_FEE_TYPE_CHOICES = (
        ("fixed", "Fixed amount"),
        ("percent", "Percentage"),
    )

    lease = models.OneToOneField(
        "leases.Lease",
        on_delete=models.CASCADE,
        related_name="late_fee_settings",
    )
    override_enabled = models.BooleanField(
        default=False,
        help_text="Use custom late fee rules for this lease instead of global settings.",
    )
    late_fee_enabled = models.BooleanField(default=True)
    late_fee_type = models.CharField(
        max_length=10,
        choices=LATE_FEE_TYPE_CHOICES,
        default="fixed",
    )
    late_fee_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    late_fee_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    late_fee_grace_days = models.PositiveIntegerField(default=0)
    reminder_interval_days = models.PositiveIntegerField(default=5)
    late_fee_max_reminders = models.PositiveIntegerField(
        default=0,
        help_text="0 = unlimited reminders.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Lease late fee setting"
        verbose_name_plural = "Lease late fee settings"

    def __str__(self):
        return f"Late fee settings for lease #{self.lease_id}"


def get_effective_late_fee_settings(lease):
    from core.models import GlobalSettings

    settings_obj = GlobalSettings.get_solo()
    override = getattr(lease, "late_fee_settings", None)

    cfg = {
        "auto_apply": settings_obj.late_fee_auto_apply,
        "auto_send_reminders": settings_obj.late_fee_auto_send_reminders,
    }

    if override and override.override_enabled:
        cfg.update({
            "enabled": override.late_fee_enabled,
            "type": override.late_fee_type,
            "amount": override.late_fee_amount,
            "percent": override.late_fee_percent,
            "grace_days": override.late_fee_grace_days,
            "reminder_interval_days": override.reminder_interval_days,
            "max_reminders": override.late_fee_max_reminders,
            "source": "lease",
        })
    else:
        cfg.update({
            "enabled": settings_obj.late_fee_enabled,
            "type": settings_obj.late_fee_type,
            "amount": settings_obj.late_fee_amount,
            "percent": settings_obj.late_fee_percent,
            "grace_days": settings_obj.late_fee_grace_days,
            "reminder_interval_days": settings_obj.late_fee_reminder_interval_days,
            "max_reminders": settings_obj.late_fee_max_reminders,
            "source": "global",
        })
    return cfg
