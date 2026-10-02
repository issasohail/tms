from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from pypdf import PdfReader

from leases.models import Lease
from properties.models import Property, Unit
from smart_meter.models import Meter, MeterCommand, MeterPrepaidPilot, MeterPrepaidRecharge
from tenants.models import Tenant


class RefillAuditTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="refill-auditor", password="test", email="audit@example.com"
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Audit Property", owner_name="Owner",
            owner_cnic="6110112345601", type="apartment",
            property_type="apartment", total_units=1,
        )
        self.unit = Unit.objects.create(property=self.property, unit_number="A-1")
        self.tenant = Tenant.objects.create(
            first_name="Current", last_name="Tenant",
            cnic="6110112345602", phone="03001234567",
        )
        today = timezone.localdate()
        Lease.objects.create(
            tenant=self.tenant, unit=self.unit, status="active",
            start_date=today - timedelta(days=5), end_date=today + timedelta(days=30),
            monthly_rent=Decimal("10000.00"),
        )
        self.meter = Meter.objects.create(
            meter_number="260305519998", unit=self.unit,
            billing_mode="prepaid_pilot",
        )
        self.pilot = MeterPrepaidPilot.objects.create(meter=self.meter, status="active_test")

    def _record(self, order, transaction_status, command_status, command_type="prepaid_recharge"):
        transaction = MeterPrepaidRecharge.objects.create(
            pilot=self.pilot, transaction_id=order, amount=Decimal("1200.00"),
            before_balance=Decimal("100.00"),
            after_balance=Decimal("1300.00") if transaction_status == "verified" else None,
            status=transaction_status, created_by=self.user,
            reconciliation_note="Audit test detail",
        )
        command = MeterCommand.objects.create(
            meter=self.meter, meter_number=self.meter.meter_number,
            frame_hex="AA", command_type=command_type, status=command_status,
            idempotency_key=f"prepaid-order:{order}", initiated_by=self.user.username,
            reason="Audit test reason", max_attempts=1,
            acknowledged_at=timezone.now() if command_status in {"acknowledged", "verified"} else None,
            verified_at=timezone.now() if command_status == "verified" else None,
        )
        return transaction, command

    def test_page_shows_current_tenant_status_and_safe_resend(self):
        self._record("0000000000000001", "verified", "verified")
        self._record("0000000000000002", "failed", "failed", "prepaid_refund")
        self._record("0000000000000003", "uncertain", "acknowledged")

        response = self.client.get(reverse("smart_meter:refill_audit"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Refill Audit")
        self.assertContains(response, self.meter.meter_number)
        self.assertContains(response, "Current Tenant")
        self.assertContains(response, "Order serial")
        self.assertContains(response, "Resend", count=1)
        self.assertContains(response, "Do not resend", count=1)
        self.assertContains(response, 'data-operation="refund"')

    def test_filters_and_exports_preserve_audit_headings_and_footers(self):
        self._record("0000000000000004", "verified", "verified")
        query = {
            "period": "custom",
            "from_date": timezone.localdate().isoformat(),
            "to_date": timezone.localdate().isoformat(),
            "property": str(self.property.pk),
            "unit": str(self.unit.pk),
            "meter": str(self.meter.pk),
        }
        page = self.client.get(reverse("smart_meter:refill_audit"), query)
        self.assertEqual(page.context["page_obj"].paginator.count, 1)
        self.assertContains(page, "Custom dates")

        excel = self.client.get(reverse("smart_meter:refill_audit_export", args=["xlsx"]), query)
        self.assertEqual(excel.status_code, 200)
        book = load_workbook(filename=__import__("io").BytesIO(excel.content))
        sheet = book["Refill Audit"]
        self.assertEqual(sheet["A1"].value, "Refill Audit")
        self.assertIn("Property: Audit Property", sheet["A2"].value)
        self.assertEqual(sheet.oddFooter.right.text, "Page &P of &N")
        self.assertIn("Generated", sheet.oddFooter.left.text)

        for export_format, content_type in (("pdf", "application/pdf"), ("jpg", "image/jpeg")):
            exported = self.client.get(
                reverse("smart_meter:refill_audit_export", args=[export_format]), query
            )
            self.assertEqual(exported.status_code, 200)
            self.assertEqual(exported["Content-Type"], content_type)
            self.assertGreater(len(exported.content), 100)
            if export_format == "pdf":
                text = "\n".join(
                    page.extract_text() or ""
                    for page in PdfReader(__import__("io").BytesIO(exported.content)).pages
                )
                self.assertIn("Refill Audit", text)
                self.assertIn("Generated", text)
                self.assertIn("Page 1 of 1", text)
