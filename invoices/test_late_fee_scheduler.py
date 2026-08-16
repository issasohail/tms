from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import GlobalSettings
from invoices.late_fees import approve_pending_late_fee, run_due_late_fee_reminders
from invoices.models import Invoice, InvoiceItem, InvoiceLateFeeReminder, ItemCategory
from leases.models import Lease
from properties.models import Property, Unit
from tenants.models import Tenant


class LateFeeSchedulerTests(TestCase):
    def setUp(self):
        settings_obj = GlobalSettings.get_solo()
        settings_obj.late_fee_enabled = True
        settings_obj.late_fee_auto_send_reminders = True
        settings_obj.late_fee_auto_apply = True
        settings_obj.late_fee_type = "fixed"
        settings_obj.late_fee_amount = Decimal("500.00")
        settings_obj.late_fee_grace_days = 5
        settings_obj.late_fee_reminder_interval_days = 5
        settings_obj.late_fee_max_reminders = 3
        settings_obj.late_fee_automation_start_date = date(2026, 8, 1)
        settings_obj.late_fee_skip_current_month = False
        settings_obj.late_fee_staff_summary_enabled = False
        settings_obj.save()
        tenant = Tenant.objects.create(first_name="Late", last_name="Tenant", cnic="1234512345671", phone="03000000000")
        prop = Property.objects.create(property_name="Late Test", owner_name="Owner", owner_cnic="1234512345672", type="apartment", property_type="apartment", total_units=1)
        unit = Unit.objects.create(property=prop, unit_number="L-1")
        self.lease = Lease.objects.create(tenant=tenant, unit=unit, start_date=date(2026, 1, 1), end_date=date(2027, 1, 1), monthly_rent=Decimal("20000.00"), status="active")
        self.invoice = Invoice.objects.create(lease=self.lease, issue_date=date(2026, 8, 1), due_date=date(2026, 8, 1), amount=Decimal("20000.00"), status="overdue")
        rent = ItemCategory.objects.create(name="Late Test Rent")
        InvoiceItem.objects.create(invoice=self.invoice, category=rent, description="Principal rent", amount=Decimal("20000.00"))

    @patch("invoices.late_fees._send_late_fee_whatsapp", return_value={"ok": True})
    def test_exact_schedule_no_early_and_missed_manual_catchup(self, _send):
        first = run_due_late_fee_reminders(source="manual", today=date(2026, 8, 6))
        self.assertEqual(first["processed"], 1)
        early = run_due_late_fee_reminders(source="manual", today=date(2026, 8, 7))
        self.assertEqual(early["processed"], 0)
        missed = run_due_late_fee_reminders(source="manual", today=date(2026, 8, 12))
        self.assertEqual(missed["processed"], 1)
        self.assertEqual(list(self.invoice.late_fee_reminders.order_by("reminder_number").values_list("reminder_number", flat=True)), [1, 2])

    @patch("invoices.late_fees._send_late_fee_whatsapp", return_value={"ok": True})
    def test_scheduler_manual_scheduler_is_idempotent(self, _send):
        for source in ("auto", "manual", "auto"):
            run_due_late_fee_reminders(source=source, today=date(2026, 8, 6))
        self.assertEqual(InvoiceLateFeeReminder.objects.count(), 1)
        self.assertEqual(self.invoice.items.filter(category__name="Late Fee").count(), 1)

    @patch("invoices.late_fees._send_late_fee_whatsapp", return_value={"ok": True})
    def test_percentage_does_not_compound(self, _send):
        settings_obj = GlobalSettings.get_solo()
        settings_obj.late_fee_type = "percent"
        settings_obj.late_fee_percent = Decimal("5.00")
        settings_obj.save()
        run_due_late_fee_reminders(source="auto", today=date(2026, 8, 6))
        run_due_late_fee_reminders(source="auto", today=date(2026, 8, 11))
        fees = list(self.invoice.late_fee_reminders.order_by("reminder_number").values_list("fee_amount", flat=True))
        self.assertEqual(fees, [Decimal("1000.00"), Decimal("1000.00")])

    @patch("invoices.late_fees._send_late_fee_whatsapp", return_value={"ok": True})
    def test_pending_paid_maximum_and_dry_run(self, _send):
        settings_obj = GlobalSettings.get_solo()
        settings_obj.late_fee_auto_apply = False
        settings_obj.late_fee_max_reminders = 1
        settings_obj.save()
        dry = run_due_late_fee_reminders(source="auto", today=date(2026, 8, 6), dry_run=True)
        self.assertEqual(dry["processed"], 1)
        self.assertEqual(InvoiceLateFeeReminder.objects.count(), 0)
        result = run_due_late_fee_reminders(source="auto", today=date(2026, 8, 6))
        self.assertEqual(result["fees_pending"], 1)
        again = run_due_late_fee_reminders(source="auto", today=date(2026, 8, 30))
        self.assertEqual(again["processed"], 0)
        self.invoice.status = "paid"
        self.invoice.save(update_fields=["status"])
        self.assertEqual(run_due_late_fee_reminders(source="manual", today=date(2026, 9, 1))["processed"], 0)

    def test_batch_excludes_old_invoices_and_reports_property_and_unit(self):
        Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 7, 1),
            due_date=date(2026, 7, 31),
            amount=Decimal("20000.00"),
            status="overdue",
        )

        result = run_due_late_fee_reminders(
            source="auto", today=date(2026, 8, 6), dry_run=True
        )

        self.assertEqual(result["excluded_before_start"], 1)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["details"][0]["property_name"], "Late Test")
        self.assertEqual(result["details"][0]["unit_name"], "L-1")
        self.assertEqual(result["details"][0]["due_date"], "2026-08-01")

    @patch("invoices.late_fees._send_late_fee_whatsapp", return_value={"ok": True})
    def test_zero_amount_invoice_is_never_sent_or_charged(self, send):
        zero_invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 8, 2),
            due_date=date(2026, 8, 2),
            amount=Decimal("0.00"),
            status="overdue",
        )

        result = run_due_late_fee_reminders(
            source="manual", today=date(2026, 8, 12)
        )

        self.assertEqual(result["excluded_zero_amount"], 1)
        self.assertNotIn(zero_invoice.pk, [item["invoice_id"] for item in result["details"]])
        self.assertFalse(zero_invoice.late_fee_reminders.exists())

    @patch("invoices.late_fees._send_late_fee_whatsapp", return_value={"ok": True})
    def test_current_month_setting_delays_but_does_not_cancel_reminder(self, _send):
        settings_obj = GlobalSettings.get_solo()
        settings_obj.late_fee_skip_current_month = True
        settings_obj.save(update_fields=["late_fee_skip_current_month"])

        august = run_due_late_fee_reminders(source="manual", today=date(2026, 8, 6))
        september = run_due_late_fee_reminders(source="manual", today=date(2026, 9, 1))

        self.assertEqual(august["processed"], 0)
        self.assertEqual(august["excluded_current_month"], 1)
        self.assertEqual(september["processed"], 1)

    @patch("invoices.late_fees._send_late_fee_whatsapp", return_value={"ok": True})
    def test_invoice_hold_blocks_reminder_and_fee_until_following_day(self, _send):
        self.invoice.late_fee_hold_until = date(2026, 8, 10)
        self.invoice.late_fee_hold_reason = "Tenant promised payment"
        self.invoice.save(update_fields=["late_fee_hold_until", "late_fee_hold_reason"])

        held = run_due_late_fee_reminders(source="manual", today=date(2026, 8, 6))
        released = run_due_late_fee_reminders(source="manual", today=date(2026, 8, 11))

        self.assertEqual(held["processed"], 0)
        self.assertEqual(held["excluded_on_hold"], 1)
        self.assertEqual(released["processed"], 1)

    @patch("invoices.late_fees._send_staff_late_fee_summary", return_value={"ok": True})
    @patch("invoices.late_fees._send_late_fee_whatsapp", return_value={"ok": True})
    def test_enabled_staff_summary_is_sent_after_processed_run(self, _tenant_send, staff_send):
        settings_obj = GlobalSettings.get_solo()
        settings_obj.late_fee_staff_summary_enabled = True
        settings_obj.save(update_fields=["late_fee_staff_summary_enabled"])

        result = run_due_late_fee_reminders(source="auto", today=date(2026, 8, 6))

        self.assertTrue(result["staff_summary_sent"])
        staff_send.assert_called_once()

    def test_control_screen_can_place_and_clear_invoice_hold(self):
        user = get_user_model().objects.create_superuser(
            username="late-control", password="pass", email="control@example.com"
        )
        self.client.force_login(user)

        old_invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 7, 1),
            due_date=date(2026, 7, 31),
            amount=Decimal("1200.00"),
            status="overdue",
        )

        page = self.client.get(reverse("invoices:late_fee_pending_queue"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Before sending")
        self.assertContains(page, self.invoice.invoice_number)
        self.assertContains(page, old_invoice.invoice_number)
        self.assertContains(page, "before the automation start date")

        response = self.client.post(
            reverse("invoices:set_invoice_late_fee_hold", args=[self.invoice.pk]),
            {"hold_until": "2026-08-20", "reason": "Promised payment"},
        )
        self.assertEqual(response.status_code, 302)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.late_fee_hold_until, date(2026, 8, 20))

        response = self.client.post(
            reverse("invoices:clear_invoice_late_fee_hold", args=[self.invoice.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.invoice.refresh_from_db()
        self.assertIsNone(self.invoice.late_fee_hold_until)

    @patch("invoices.management.commands.send_late_fee_reminders.run_due_late_fee_reminders")
    def test_command_lists_invoice_property_unit_and_start_boundary(self, run_service):
        run_service.return_value = {
            "reason": "",
            "automation_start_date": date(2026, 8, 16),
            "excluded_before_start": 124,
            "details": [{
                "invoice_number": "INV-1",
                "property_name": "Late Test",
                "unit_name": "L-1",
                "due_date": "2026-08-16",
                "reminder_number": 1,
            }],
            "examined": 1,
            "due": 1,
            "processed": 1,
            "fees_applied": 0,
            "fees_pending": 0,
            "failed": 0,
            "skipped": 0,
        }
        output = StringIO()

        call_command("send_late_fee_reminders", "--dry-run", stdout=output)

        text = output.getvalue()
        self.assertIn("Older overdue invoices excluded: 124", text)
        self.assertIn(
            "Invoice #INV-1 | Late Test | Unit L-1 | Due 2026-08-16",
            text,
        )

    def test_command_lists_excluded_invoice_details_without_processing(self):
        old_invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 7, 1),
            due_date=date(2026, 7, 31),
            amount=Decimal("12340.00"),
            status="overdue",
        )
        settings_obj = GlobalSettings.get_solo()
        settings_obj.late_fee_automation_start_date = date(2026, 8, 1)
        settings_obj.save(update_fields=["late_fee_automation_start_date"])
        zero_invoice = Invoice.objects.create(
            lease=self.lease,
            issue_date=date(2026, 7, 2),
            due_date=date(2026, 7, 31),
            amount=Decimal("0.00"),
            status="overdue",
        )
        output = StringIO()

        call_command("send_late_fee_reminders", "--list-excluded", stdout=output)

        text = output.getvalue()
        self.assertIn(old_invoice.invoice_number, text)
        self.assertIn("2026-07-01 | 2026-07-31", text)
        self.assertIn("Late Test | L-1 | Late Tenant", text)
        self.assertNotIn(zero_invoice.invoice_number, text)
        self.assertIn("Total excluded: 1", text)

    def test_hold_also_blocks_approval_of_an_already_pending_fee(self):
        reminder = InvoiceLateFeeReminder.objects.create(
            invoice=self.invoice,
            reminder_number=1,
            status=InvoiceLateFeeReminder.STATUS_FEE_PENDING,
            fee_amount=Decimal("500.00"),
        )
        self.invoice.late_fee_hold_until = date(2026, 8, 20)
        self.invoice.save(update_fields=["late_fee_hold_until"])

        with patch("invoices.late_fees.timezone.localdate", return_value=date(2026, 8, 16)):
            result = approve_pending_late_fee(reminder)

        self.assertFalse(result["ok"])
        self.assertIn("on hold", result["reason"])

    @patch(
        "invoices.management.commands.send_late_fee_reminders.scheduler_time_is_due",
        return_value=False,
    )
    @patch(
        "invoices.management.commands.send_late_fee_reminders.run_due_late_fee_reminders"
    )
    def test_systemd_mode_skips_outside_configured_time(self, run_service, _is_due):
        output = StringIO()

        call_command("send_late_fee_reminders", "--scheduled", stdout=output)

        run_service.assert_not_called()
        self.assertIn("Configured Pakistan time", output.getvalue())

    @patch("invoices.late_fees.run_due_late_fee_reminders")
    def test_invoice_list_button_uses_shared_batch_service(self, run_service):
        run_service.return_value = {"examined": 1, "processed": 0, "fees_applied": 0, "fees_pending": 0, "failed": 0}
        user = get_user_model().objects.create_superuser(
            username="late-staff", password="pass", email="late@example.com"
        )
        self.client.force_login(user)
        response = self.client.post(reverse("invoices:apply_late_fees"))
        self.assertEqual(response.status_code, 302)
        run_service.assert_called_once()
