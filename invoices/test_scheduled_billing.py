from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from core.models import GlobalSettings
from invoices.services import run_scheduled_monthly_billing


class ScheduledBillingTests(TestCase):
    def setUp(self):
        self.settings_obj = GlobalSettings.get_solo()
        self.settings_obj.automatic_monthly_billing = True
        self.settings_obj.monthly_billing_day = 2
        self.settings_obj.save()

    @patch("invoices.services.run_monthly_billing_engine")
    def test_before_date_does_nothing(self, engine):
        result = run_scheduled_monthly_billing(today=date(2026, 9, 1))
        self.assertFalse(result["processed"])
        engine.assert_not_called()

    @patch("invoices.services.run_monthly_billing_engine")
    def test_scheduled_date_and_missed_date_run_current_period(self, engine):
        engine.return_value = {"processed": True, "created": 3}
        for today in (date(2026, 9, 2), date(2026, 9, 4)):
            result = run_scheduled_monthly_billing(today=today)
            self.assertTrue(result["processed"])
        self.assertEqual(engine.call_count, 2)
        self.assertEqual(engine.call_args.args[0], date(2026, 9, 1))

    @patch("invoices.services.run_monthly_billing_engine")
    def test_changed_day_and_disabled_setting(self, engine):
        self.settings_obj.monthly_billing_day = 5
        self.settings_obj.save(update_fields=["monthly_billing_day"])
        run_scheduled_monthly_billing(today=date(2026, 9, 2))
        engine.assert_not_called()
        self.settings_obj.automatic_monthly_billing = False
        self.settings_obj.save(update_fields=["automatic_monthly_billing"])
        result = run_scheduled_monthly_billing(today=date(2026, 9, 5))
        self.assertIn("disabled", result["reason"].lower())
        engine.assert_not_called()

    @patch("invoices.services.run_monthly_billing_engine")
    def test_dry_run_is_delegated_without_writes(self, engine):
        engine.return_value = {"processed": False, "dry_run": True, "active_leases": 4}
        result = run_scheduled_monthly_billing(today=date(2026, 9, 2), dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertTrue(engine.call_args.kwargs["dry_run"])

    @patch(
        "invoices.management.commands.run_scheduled_billing.scheduler_time_is_due",
        return_value=False,
    )
    @patch(
        "invoices.management.commands.run_scheduled_billing.run_scheduled_monthly_billing"
    )
    def test_systemd_mode_skips_outside_configured_time(self, run_service, _is_due):
        output = StringIO()

        call_command("run_scheduled_billing", "--scheduled", stdout=output)

        run_service.assert_not_called()
        self.assertIn("Configured Pakistan time", output.getvalue())
