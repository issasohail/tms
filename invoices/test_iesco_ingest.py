import json
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from invoices.models import IescoBillReading, IescoStandaloneMeter, Invoice, InvoiceItem
from leases.models import Lease
from properties.models import Property, Unit
from tenants.models import Tenant


@override_settings(IESCO_BILL_API_KEY="test-ingest-key")
class IescoBillIngestTests(TestCase):
    def setUp(self):
        self.url = reverse("invoices:iesco_bill_ingest")
        self.headers = {"HTTP_X_API_KEY": "test-ingest-key"}
        self.payload = {
            "reference_no": "17146151548911",
            "fetched_at": "2026-09-07T10:15:30+00:00",
            "consumer_id": "1234567890",
            "consumer_name": "Test Consumer",
            "address": "Test Consumer, Islamabad",
            "tariff_category": "A-1a(01)",
            "units": "27",
            "bill_month": "AUG 26",
            "reading_date": "28 AUG 26",
            "issue_date": "03 SEP 26",
            "due_date": "17 SEP 26",
            "grand_total": "3,682",
            "bill_history": [
                {
                    "month": "Jul26",
                    "units": "27",
                    "bill": "3682",
                    "payment": "6874",
                    "paid": True,
                }
            ],
            "current_month_paid": True,
            "raw_found": True,
        }

    def post(self, payload=None, **headers):
        return self.client.post(
            self.url,
            data=json.dumps(self.payload if payload is None else payload),
            content_type="application/json",
            **headers,
        )

    def test_missing_or_incorrect_api_key_is_unauthorized(self):
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.post(HTTP_X_API_KEY="wrong").status_code, 401)
        self.assertFalse(IescoBillReading.objects.exists())

    def test_missing_reference_number_is_bad_request(self):
        payload = dict(self.payload)
        payload.pop("reference_no")
        response = self.post(payload, **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IescoBillReading.objects.exists())

    def test_missing_bill_month_is_bad_request(self):
        payload = dict(self.payload)
        payload.pop("bill_month")
        response = self.post(payload, **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IescoBillReading.objects.exists())

    def test_malformed_or_non_object_json_is_bad_request(self):
        malformed = self.client.post(
            self.url,
            data=b'{"reference_no":',
            content_type="application/json",
            **self.headers,
        )
        non_object = self.post([], **self.headers)
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(non_object.status_code, 400)
        self.assertFalse(IescoBillReading.objects.exists())

    def test_valid_payload_creates_reading_with_payment_status(self):
        response = self.post(**self.headers)
        self.assertEqual(response.status_code, 201)
        reading = IescoBillReading.objects.get()
        self.assertEqual(reading.reference_no, self.payload["reference_no"])
        self.assertEqual(reading.consumer_name, "Test Consumer")
        self.assertEqual(reading.bill_month, "AUG 26")
        self.assertEqual(reading.grand_total, "3,682")
        self.assertEqual(reading.bill_history[0]["month"], "Jul26")
        self.assertIs(reading.current_month_paid, True)
        self.assertEqual(reading.payment_status_display, "Paid")

    def test_ingest_derives_current_bill_and_average_rate_from_total_less_arrears(self):
        payload = dict(
            self.payload,
            units="10",
            current_bill="649",
            arrears="0",
            grand_total="845",
        )

        response = self.post(payload, **self.headers)

        self.assertEqual(response.status_code, 201)
        reading = IescoBillReading.objects.get()
        self.assertEqual(reading.current_bill, "845")
        self.assertEqual(reading.current_bill_amount, Decimal("845"))
        self.assertEqual(reading.per_unit_rate, Decimal("84.5"))
        self.assertEqual(
            reading.per_unit_rate * reading.import_units,
            reading.grand_total_amount - reading.arrears_amount,
        )

    def test_duplicate_month_updates_without_resetting_received_at(self):
        self.post(**self.headers)
        original = IescoBillReading.objects.get()
        received_at = original.received_at
        payload = dict(self.payload, grand_total="4,000", current_month_paid=False)

        response = self.post(payload, **self.headers)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(IescoBillReading.objects.count(), 1)
        updated = IescoBillReading.objects.get()
        self.assertEqual(updated.grand_total, "4,000")
        self.assertIs(updated.current_month_paid, False)
        self.assertEqual(updated.received_at, received_at)

    def test_invalid_field_types_return_bad_request(self):
        response = self.post(
            dict(self.payload, consumer_name={"unexpected": "object"}),
            **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IescoBillReading.objects.exists())

    def test_get_is_method_not_allowed(self):
        response = self.client.get(self.url, **self.headers)
        self.assertEqual(response.status_code, 405)


class IescoBillFetchParserTests(TestCase):
    @patch("invoices.iesco_bill_fetch.time.sleep")
    @patch("invoices.iesco_bill_fetch.requests.post")
    def test_fetch_retries_one_temporary_tls_failure(self, post, sleep):
        from requests.exceptions import SSLError

        from invoices.iesco_bill_fetch import fetch_raw_html

        response = Mock(text="<html>bill</html>")
        response.raise_for_status.return_value = None
        post.side_effect = [SSLError("temporary EOF"), response]

        self.assertEqual(fetch_raw_html("17146151548911"), "<html>bill</html>")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_current_bill_paid_status_is_parsed_from_paid_amount(self):
        from invoices.iesco_bill_fetch import parse_bill

        html = """
            <span class="charges-bd-en">Current Bill</span>
            <span class="charges-bd-val">2,100</span>
            <span class="charges-bd-en">Arrears</span>
            <span class="charges-bd-val">1,582</span>
            <span class="charges-bd-en">Grand Total</span>
            <span class="charges-bd-val">3,682</span>
            <span class="right-panel-en">BILL MONTH</span>
            <div class="right-main-val">AUG 26</div>
            <span class="right-panel-en--due">DUE DATE</span>
            <div class="right-main-val--due">17 SEP 26</div>
            <div class="payable-card-paid-row">
              <span class="payable-card-paid-label">Amount Paid</span>
              <span class="payable-card-paid-val">3,682</span>
            </div>
            <div class="payable-card-paid-row">
              <span class="payable-card-paid-label">Payment Date</span>
              <span class="payable-card-paid-val">18-Sep-26</span>
            </div>
            <div class="history-row">
              <div class="history-cell">Jun26</div><div class="history-cell"></div>
              <div class="history-cell">67</div><div class="history-cell">3,056</div>
              <div class="history-cell">0</div>
            </div>
            <div class="history-row">
              <div class="history-cell">Jul26</div><div class="history-cell"></div>
              <div class="history-cell">27</div><div class="history-cell">3,682</div>
              <div class="history-cell">6,874</div>
            </div>
        """

        result = parse_bill(html, "17146151548911")

        self.assertTrue(result.raw_found)
        self.assertEqual(result.bill_history[-1]["month"], "Jul26")
        self.assertIs(result.bill_history[0]["paid"], False)
        self.assertIs(result.current_month_paid, True)
        self.assertEqual(result.amount_paid, "3,682")
        self.assertEqual(result.payment_date, "18-Sep-26")
        self.assertEqual(result.current_bill, "2,100")
        self.assertEqual(result.arrears, "1,582")

    def test_reference_number_must_be_fourteen_digits(self):
        from invoices.iesco_bill_fetch import parse_bill

        with self.assertRaisesMessage(ValueError, "exactly 14 digits"):
            parse_bill("", "123")

    def test_three_phase_net_meter_bill_uses_the_same_summary_parser(self):
        from invoices.iesco_bill_fetch import parse_bill

        html = """
            <span class="en-lbl">TARIFF CATEGORY</span><div class="val-space">Domestic</div>
            <div class="meter-info-cell"><div><span class="en-lbl">METER NO</span></div><div class="val-space">3-P I 123<br>I 123<br>E 123<br>E 123</div></div>
            <div class="meter-info-cell"><div><span class="en-lbl">MF</span></div><div class="val-space">IMP 1<br>IMP 1<br>EXP 1<br>EXP 1</div></div>
            <div class="meter-info-cell"><div><span class="en-lbl">PREVIOUS READING</span></div><div class="val-space">1<br>2<br>3<br>4</div></div>
            <div class="meter-info-cell"><div><span class="en-lbl">PRESENT READING</span></div><div class="val-space">2<br>3<br>4<br>4</div></div>
            <div class="meter-info-cell"><div><span class="en-lbl">UNITS</span></div><div class="val-space">431<br>172<br>489<br>0</div></div>
            <div class="meter-type">3-P NET METERING</div>
            <span class="charges-bd-en">Current Bill</span><span class="charges-bd-val">10,000</span>
            <span class="charges-bd-en">Arrears</span><span class="charges-bd-val">2,566</span>
            <span class="charges-bd-en">Grand Total</span><span class="charges-bd-val">12,566</span>
            <span class="right-panel-en">BILL MONTH</span><div class="right-main-val">AUG 26</div>
            <span class="right-panel-en--due">DUE DATE</span><div class="right-main-val--due">24 AUG 26</div>
        """

        result = parse_bill(html, "17146151548921")

        self.assertTrue(result.raw_found)
        self.assertEqual(result.units, "603")
        self.assertEqual(result.meter_type, "3-P")
        self.assertEqual(len(result.meter_readings), 4)
        self.assertEqual(result.meter_readings[0]["direction"], "import")
        self.assertEqual(result.meter_readings[1]["period"], "peak")
        self.assertEqual(result.meter_readings[2]["direction"], "export")
        self.assertEqual(result.meter_readings[2]["units"], "489")
        self.assertEqual(result.meter_readings[0]["meter_no"], "3-P I 123")
        self.assertEqual(result.meter_readings[0]["previous"], "1")
        self.assertEqual(result.meter_readings[0]["present"], "2")
        self.assertIs(result.current_month_paid, False)
        self.assertEqual(result.current_bill, "10,000")
        self.assertEqual(result.arrears, "2,566")
        self.assertEqual(result.grand_total, "12,566")
        self.assertEqual(result.bill_month, "AUG 26")


class IescoBillReadingListTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(
            username="iesco-list-admin",
            email="iesco-list-admin@example.com",
            password="test-pass",
        )
        self.client.force_login(user)
        property_obj = Property.objects.create(
            property_name="IESCO List Property",
            owner_name="Owner",
            owner_cnic="12345-1234567-1",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        Unit.objects.create(
            property=property_obj,
            unit_number="A-1",
            electric_meter_num="17146151548911",
        )

    def test_meter_without_reading_renders_no_reading_row(self):
        response = self.client.get(reverse("invoices:iesco_bill_reading_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "17146151548911")
        self.assertContains(response, "No reading")


class IescoBillWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="iesco-workflow-admin",
            email="iesco-workflow-admin@example.com",
            password="test-pass",
        )
        self.client.force_login(self.user)
        self.property = self._property("IESCO Active Property", "12345-1234567-2")
        self.other_property = self._property("IESCO Other Property", "12345-1234567-3")
        self.active_unit = Unit.objects.create(
            property=self.property,
            unit_number="A-1",
            electric_meter_num="17146151548911",
        )
        self.unleased_unit = Unit.objects.create(
            property=self.property,
            unit_number="A-2",
            electric_meter_num="17146151548912",
        )
        self.other_unit = Unit.objects.create(
            property=self.other_property,
            unit_number="B-1",
            electric_meter_num="17146151548913",
            iesco_bill_active=False,
        )
        tenant = Tenant.objects.create(
            first_name="IESCO",
            last_name="Tenant",
            cnic="12345-1234567-4",
        )
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=self.active_unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=Decimal("25000.00"),
            status="active",
        )
        self.standalone = IescoStandaloneMeter.objects.create(
            reference_no="17146151548914",
            description="Office common meter",
            is_active=True,
        )
        IescoStandaloneMeter.objects.create(
            reference_no="17146151548915",
            description="Inactive spare meter",
            is_active=False,
        )

    def _property(self, name, cnic):
        return Property.objects.create(
            property_name=name,
            owner_name="Owner",
            owner_cnic=cnic,
            type="residential",
            property_type="apartment",
            total_units=2,
        )

    def _payload(self, reference_no, **overrides):
        payload = {
            "reference_no": reference_no,
            "fetched_at": "2026-09-08T10:00:00+00:00",
            "consumer_id": "123456",
            "consumer_name": "Workflow Consumer",
            "address": "Islamabad",
            "tariff_category": "A-1a(01)",
            "units": "27",
            "bill_month": "AUG 26",
            "reading_date": "28 AUG 26",
            "issue_date": "03 SEP 26",
            "due_date": "17 SEP 26",
            "current_bill": "3,000",
            "arrears": "680",
            "grand_total": "3,680",
            "bill_history": [{"month": "Jul26", "paid": True}],
            "current_month_paid": True,
            "description": "",
        }
        payload.update(overrides)
        return payload

    def test_list_has_serial_property_filter_and_standalone_description(self):
        response = self.client.get(reverse("invoices:iesco_bill_reading_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Office common meter")
        self.assertContains(response, "Fetch All Active")
        self.assertContains(response, "PITC bill fetching should be run from the local Pakistan TMS")
        self.assertContains(response, "<th>#</th>", html=True)

        filtered = self.client.get(
            reverse("invoices:iesco_bill_reading_list"),
            {"property": self.property.pk},
        )
        self.assertContains(filtered, "17146151548911")
        self.assertNotContains(filtered, "17146151548913")
        self.assertNotContains(filtered, self.standalone.reference_no)

    def test_net_meter_totals_month_filter_detail_summary_and_csv_download(self):
        reading = IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            meter_type="3-P",
            units="603",
            meter_readings=[
                {"direction": "import", "period": "off_peak", "previous": "3879", "present": "4310", "units": "431"},
                {"direction": "import", "period": "peak", "previous": "1000", "present": "1172", "units": "172"},
                {"direction": "export", "period": "off_peak", "previous": "2000", "present": "2489", "units": "489"},
                {"direction": "export", "period": "peak", "previous": "0", "present": "0", "units": "0"},
            ],
            current_bill="12,566",
            arrears="0",
            grand_total="12,566",
            amount_paid="12,566",
            payment_date="16-Aug-26",
            current_month_paid=True,
        )

        listing = self.client.get(
            reverse("invoices:iesco_bill_reading_list"), {"bill_month": "AUG 26"}
        )
        self.assertContains(listing, "<th>Export</th>", html=True)
        self.assertContains(listing, "<th>Import</th>", html=True)
        self.assertContains(listing, "<th>Net</th>", html=True)
        self.assertContains(listing, "Rs. 20.84")
        self.assertContains(listing, "Rs. 12,566")
        self.assertContains(listing, "<th>Total</th>", html=True)
        self.assertContains(listing, "iesco-mobile-card")
        self.assertContains(listing, "iesco-tablet-view")
        self.assertContains(listing, "View Bill")
        self.assertContains(listing, "Off<br>Peak", html=True)
        self.assertContains(listing, "Imp OP 4310")
        self.assertContains(listing, "Exp OP 2489")
        self.assertContains(listing, "<th>Previous</th>", html=True)
        self.assertContains(listing, "<th>Current</th>", html=True)
        self.assertContains(listing, "Reading Date")
        self.assertNotContains(listing, "iesco-mobile-actions-label")
        self.assertContains(listing, ">View</a>")
        self.assertContains(
            listing,
            reverse("invoices:iesco_bill_reading_detail", args=[reading.reference_no]),
        )
        self.assertContains(
            listing,
            reverse("invoices:iesco_bill_pitc", args=[reading.reference_no]),
        )

        detail = self.client.get(
            reverse("invoices:iesco_bill_reading_detail", args=[reading.reference_no])
        )
        self.assertContains(detail, "Weighted average unit rate")
        self.assertContains(detail, "Off Peak")
        self.assertContains(detail, "16-Aug-26")

        export = self.client.get(reverse("invoices:iesco_bill_export"))
        self.assertEqual(export.status_code, 200)
        csv_text = export.content.decode("utf-8-sig")
        self.assertIn("meter_readings", csv_text)
        self.assertIn(reading.reference_no, csv_text)

    def test_three_phase_meter_without_export_registers_uses_simple_units(self):
        reading = IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            meter_type="3-P",
            units="73",
            meter_readings=[
                {"direction": "import", "period": "off_peak", "units": "73"},
                {"direction": "import", "period": "peak", "units": "0"},
            ],
            current_bill="1,440",
            grand_total="1,440",
        )

        self.assertFalse(reading.has_export_registers)
        self.assertEqual(reading.units_display, "73")

        listing = self.client.get(reverse("invoices:iesco_bill_reading_list"))
        self.assertNotContains(listing, "<th>Export</th>", html=True)

    def test_per_unit_rate_uses_grand_total_less_arrears(self):
        reading = IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            units="414",
            current_bill="20,000",
            arrears="21,213/2",
            grand_total="42,890",
        )

        self.assertEqual(reading.current_bill_display, "Rs. 21,677")
        self.assertEqual(reading.arrears_display, "Rs. 21,213")
        self.assertEqual(reading.grand_total_display, "Rs. 42,890")
        self.assertEqual(reading.per_unit_rate_display, "Rs. 52.36")

        listing = self.client.get(reverse("invoices:iesco_bill_reading_list"))
        self.assertContains(listing, "Current Bill")
        self.assertContains(listing, "Arrears")
        self.assertContains(listing, "Rs. 52.36")

    def test_single_register_reading_hides_redundant_import_label(self):
        IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            units="414",
            meter_readings=[
                {
                    "direction": "import",
                    "period": "off_peak",
                    "previous": "397.14",
                    "present": "810.69",
                    "units": "414",
                }
            ],
            current_bill="21,677",
            grand_total="21,677",
        )

        listing = self.client.get(reverse("invoices:iesco_bill_reading_list"))

        self.assertContains(listing, "397.14")
        self.assertContains(listing, "810.69")
        self.assertNotContains(listing, "Imp OP")

    def test_add_standalone_meter_and_reject_unit_assigned_reference(self):
        response = self.client.post(
            reverse("invoices:iesco_standalone_meter_add"),
            {
                "reference_no": "17146151548916",
                "description": "Generator room",
                "is_active": "on",
            },
            follow=True,
        )
        self.assertContains(response, "Standalone IESCO meter 17146151548916 added")
        self.assertTrue(
            IescoStandaloneMeter.objects.filter(
                reference_no="17146151548916", description="Generator room"
            ).exists()
        )

        response = self.client.post(
            reverse("invoices:iesco_standalone_meter_add"),
            {
                "reference_no": self.active_unit.electric_meter_num,
                "description": "Duplicate unit meter",
                "is_active": "on",
            },
            follow=True,
        )
        self.assertContains(response, "already assigned to a property unit")
        self.assertFalse(
            IescoStandaloneMeter.objects.filter(
                reference_no=self.active_unit.electric_meter_num
            ).exists()
        )

    def test_meter_numbers_can_be_edited_from_the_iesco_workflow(self):
        response = self.client.post(
            reverse("invoices:iesco_meter_edit", args=["unit", self.active_unit.pk]),
            {
                "reference_no": "17146151548916",
                "unit": self.active_unit.pk,
                "description": "",
                "is_active": "on",
            },
            follow=True,
        )
        self.assertContains(response, "IESCO meter updated to 17146151548916")
        self.active_unit.refresh_from_db()
        self.assertEqual(self.active_unit.electric_meter_num, "17146151548916")

        response = self.client.post(
            reverse("invoices:iesco_meter_edit", args=["standalone", self.standalone.pk]),
            {
                "reference_no": "17146151548917",
                "description": "Edited common meter",
                "is_active": "on",
            },
            follow=True,
        )
        self.assertContains(response, "IESCO meter updated to 17146151548917")
        self.standalone.refresh_from_db()
        self.assertEqual(self.standalone.reference_no, "17146151548917")
        self.assertEqual(self.standalone.description, "Edited common meter")

    def test_meter_can_move_between_unit_and_standalone_and_be_removed(self):
        target_unit = Unit.objects.create(
            property=self.property,
            unit_number="A-3",
            electric_meter_num="",
        )
        reading = IescoBillReading.objects.create(
            reference_no=self.standalone.reference_no,
            bill_month="JUL 26",
            grand_total="1,000",
        )

        response = self.client.post(
            reverse("invoices:iesco_meter_edit", args=["standalone", self.standalone.pk]),
            {
                "reference_no": self.standalone.reference_no,
                "unit": target_unit.pk,
                "description": "",
                "is_active": "on",
            },
            follow=True,
        )
        self.assertContains(response, "IESCO meter updated")
        target_unit.refresh_from_db()
        self.assertEqual(target_unit.electric_meter_num, reading.reference_no)
        self.assertFalse(IescoStandaloneMeter.objects.filter(pk=self.standalone.pk).exists())

        response = self.client.post(
            reverse("invoices:iesco_meter_edit", args=["unit", target_unit.pk]),
            {
                "reference_no": reading.reference_no,
                "unit": "",
                "description": "Detached solar meter",
                "is_active": "on",
            },
            follow=True,
        )
        self.assertContains(response, "IESCO meter updated")
        target_unit.refresh_from_db()
        self.assertEqual(target_unit.electric_meter_num, "")
        standalone = IescoStandaloneMeter.objects.get(reference_no=reading.reference_no)
        self.assertEqual(standalone.description, "Detached solar meter")

        response = self.client.post(
            reverse("invoices:iesco_meter_delete", args=["standalone", standalone.pk]),
            follow=True,
        )
        self.assertContains(response, "Saved bill history was preserved")
        self.assertFalse(IescoStandaloneMeter.objects.filter(pk=standalone.pk).exists())
        self.assertTrue(IescoBillReading.objects.filter(pk=reading.pk).exists())

    def test_month_filter_is_unique_and_pitc_link_posts_reference(self):
        IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            grand_total="1,000",
        )
        IescoBillReading.objects.create(
            reference_no=self.standalone.reference_no,
            bill_month="August 2026",
            grand_total="2,000",
        )
        listing = self.client.get(reverse("invoices:iesco_bill_reading_list"))
        self.assertEqual(
            listing.context["bill_month_options"],
            [{"value": "2026-08", "label": "August 2026"}],
        )
        self.assertContains(listing, "iescoFilterForm")
        self.assertContains(listing, "form.requestSubmit()")

        filtered = self.client.get(
            reverse("invoices:iesco_bill_reading_list"), {"bill_month": "2026-08"}
        )
        self.assertContains(filtered, self.active_unit.electric_meter_num)
        self.assertContains(filtered, self.standalone.reference_no)

        handoff = self.client.get(
            reverse("invoices:iesco_bill_pitc", args=[self.active_unit.electric_meter_num])
        )
        self.assertEqual(handoff.status_code, 200)
        self.assertContains(handoff, 'method="post"')
        self.assertContains(
            handoff,
            f'name="refno" value="{self.active_unit.electric_meter_num}"',
        )

    @patch("invoices.views_iesco.fetch_bill_payload")
    def test_single_fetch_saves_immediately_and_upserts_duplicates(self, fetch):
        fetch.return_value = self._payload(self.active_unit.electric_meter_num)
        fetch_url = reverse(
            "invoices:iesco_bill_fetch_one",
            args=[self.active_unit.electric_meter_num],
        )

        response = self.client.post(fetch_url, follow=True)
        self.assertContains(response, "list is now current")
        reading = IescoBillReading.objects.get()
        self.assertEqual(reading.grand_total, "3,680")
        listing = self.client.get(reverse("invoices:iesco_bill_reading_list"))
        self.assertContains(listing, "3,680")

        fetch.return_value = self._payload(
            self.active_unit.electric_meter_num, grand_total="4,000"
        )
        self.client.post(fetch_url)
        self.assertEqual(IescoBillReading.objects.count(), 1)
        reading.refresh_from_db()
        self.assertEqual(reading.grand_total, "4,000")

    @patch("invoices.views_iesco.fetch_bill_payload")
    def test_fetch_all_uses_iesco_active_units_and_active_standalone_meters(self, fetch):
        fetch.side_effect = lambda reference_no, description="": self._payload(
            reference_no, description=description
        )

        response = self.client.post(
            reverse("invoices:iesco_bill_fetch_all"), follow=True
        )

        self.assertEqual(response.status_code, 200)
        fetched_refs = {call.args[0] for call in fetch.call_args_list}
        self.assertEqual(
            fetched_refs,
            {
                self.active_unit.electric_meter_num,
                self.unleased_unit.electric_meter_num,
                self.standalone.reference_no,
            },
        )
        self.assertEqual(IescoBillReading.objects.count(), 3)

    @patch("invoices.views_iesco.fetch_bill_payload")
    def test_ajax_fetch_all_saves_one_meter_at_a_time(self, fetch):
        fetch.side_effect = lambda reference_no, description="": self._payload(
            reference_no, description=description
        )
        url = reverse("invoices:iesco_bill_fetch_all")

        started = self.client.post(url, {"ajax_action": "start"})

        self.assertEqual(started.status_code, 200)
        start_data = started.json()
        self.assertEqual(start_data["total"], 3)
        self.assertEqual(
            {source["reference_no"] for source in start_data["sources"]},
            {
                self.active_unit.electric_meter_num,
                self.unleased_unit.electric_meter_num,
                self.standalone.reference_no,
            },
        )
        active_source = next(
            source
            for source in start_data["sources"]
            if source["reference_no"] == self.active_unit.electric_meter_num
        )
        self.assertIn(self.property.property_name, active_source["label"])
        self.assertIn(self.active_unit.unit_number, active_source["label"])
        self.assertNotIn("iesco_bill_preview", self.client.session)

        first = self.client.post(
            url,
            {
                "ajax_action": "fetch",
                "reference_no": self.active_unit.electric_meter_num,
            },
        )
        self.assertTrue(first.json()["ok"])
        self.assertEqual(IescoBillReading.objects.count(), 1)

        second = self.client.post(
            url,
            {
                "ajax_action": "fetch",
                "reference_no": self.standalone.reference_no,
            },
        )
        self.assertTrue(second.json()["ok"])
        self.assertEqual(IescoBillReading.objects.count(), 2)
        self.assertEqual(len(self.client.session["iesco_bill_last_export_ids"]), 2)

    @patch("invoices.views_iesco.fetch_bill_payload")
    def test_bulk_fetch_skips_recent_successful_bill(self, fetch):
        IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            fetched_at=timezone.now(),
            bill_month="SEP 26",
            reading_date="08 SEP 26",
            units="100",
            current_bill="2,000",
            grand_total="2,000",
        )

        response = self.client.post(
            reverse("invoices:iesco_bill_fetch_all"),
            {
                "ajax_action": "fetch",
                "reference_no": self.active_unit.electric_meter_num,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["skipped"])
        self.assertIn("next_fetch", response.json())
        fetch.assert_not_called()

    def test_detail_demo_history_is_preview_only(self):
        IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            reading_date="08 AUG 26",
            units="151",
            current_bill="2,400",
            grand_total="2,400",
        )
        original_count = IescoBillReading.objects.count()

        response = self.client.get(
            reverse(
                "invoices:iesco_bill_reading_detail",
                args=[self.active_unit.electric_meter_num],
            ),
            {"demo": "1"},
        )

        self.assertContains(response, "Preview only")
        for month in ("APR 26", "MAY 26", "JUN 26", "JUL 26"):
            self.assertContains(response, month)
        self.assertEqual(IescoBillReading.objects.count(), original_count)

    def test_csv_import_saves_immediately_without_duplicates(self):
        csv_data = (
            "reference_no,bill_month,grand_total,description,current_month_paid,bill_history\n"
            "17146151548914,AUG 26,3680,Office common meter,true,[]\n"
        )
        upload = SimpleUploadedFile(
            "iesco.csv", csv_data.encode("utf-8"), content_type="text/csv"
        )
        response = self.client.post(
            reverse("invoices:iesco_bill_import"), {"file": upload}, follow=True
        )
        self.assertContains(response, "IESCO CSV imported")
        self.assertEqual(IescoBillReading.objects.count(), 1)
        reading = IescoBillReading.objects.get()

        export = self.client.get(reverse("invoices:iesco_bill_export"))
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("17146151548914", export.content.decode("utf-8-sig"))

        upload = SimpleUploadedFile(
            "iesco.csv", csv_data.encode("utf-8"), content_type="text/csv"
        )
        self.client.post(reverse("invoices:iesco_bill_import"), {"file": upload})
        self.assertEqual(IescoBillReading.objects.count(), 1)
        self.assertEqual(IescoBillReading.objects.get().pk, reading.pk)

    def test_csv_import_can_create_unknown_standalone_meter_immediately(self):
        csv_data = (
            "reference_no,bill_month,grand_total,description,current_month_paid,bill_history\n"
            "17146151548916,AUG 26,2500,New generator meter,false,[]\n"
        )
        upload = SimpleUploadedFile(
            "iesco.csv", csv_data.encode("utf-8"), content_type="text/csv"
        )

        self.client.post(reverse("invoices:iesco_bill_import"), {"file": upload})
        meter = IescoStandaloneMeter.objects.get(reference_no="17146151548916")
        self.assertEqual(meter.description, "New generator meter")
        self.assertTrue(meter.is_active)
        self.assertTrue(
            IescoBillReading.objects.filter(reference_no=meter.reference_no).exists()
        )

    def test_active_reference_csv_contains_only_active_fetch_sources(self):
        response = self.client.get(reverse("invoices:iesco_reference_export"))

        self.assertEqual(response.status_code, 200)
        csv_text = response.content.decode("utf-8-sig")
        self.assertIn("reference_no,description", csv_text)
        self.assertIn(self.active_unit.electric_meter_num, csv_text)
        self.assertIn(self.standalone.reference_no, csv_text)
        self.assertIn(self.unleased_unit.electric_meter_num, csv_text)
        self.assertNotIn(self.other_unit.electric_meter_num, csv_text)
        self.assertNotIn("17146151548915", csv_text)

    @patch("invoices.views_iesco.fetch_bill_payload")
    def test_reference_csv_fetches_saves_and_registers_unknown_meter(self, fetch):
        reference_no = "17146151548916"
        fetch.side_effect = lambda reference_no, description="": self._payload(
            reference_no, description=description
        )
        upload = SimpleUploadedFile(
            "active-references.csv",
            (
                "reference_no,description\n"
                f"{reference_no},Production common meter\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("invoices:iesco_reference_fetch"), {"file": upload}, follow=True
        )

        self.assertContains(response, "Reference list processed and bills saved")
        self.assertTrue(
            IescoBillReading.objects.filter(reference_no=reference_no).exists()
        )
        meter = IescoStandaloneMeter.objects.get(reference_no=reference_no)
        self.assertEqual(meter.description, "Production common meter")
        self.assertTrue(meter.is_active)

    def test_saved_bill_posts_to_invoice_only_once(self):
        reading = IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            units="27",
            meter_readings=[
                {
                    "direction": "import",
                    "period": "off_peak",
                    "previous": "100",
                    "present": "127",
                    "units": "27",
                }
            ],
            due_date="24 AUG 26",
            grand_total="3,680",
        )
        url = reverse("invoices:iesco_bill_post_to_invoice", args=[reading.pk])

        first = self.client.post(url, {"posting_month": "2026-08"}, follow=True)

        self.assertContains(first, "posted to invoice")
        reading.refresh_from_db()
        self.assertIsNotNone(reading.posted_at)
        self.assertIsNotNone(reading.posted_invoice_item_id)
        self.assertEqual(reading.posted_invoice_item.amount, Decimal("3680.00"))
        self.assertIn("Total units consumed 27", reading.posted_invoice_item.description)
        self.assertIn("Previous 100", reading.posted_invoice_item.description)
        self.assertIn("Current 127", reading.posted_invoice_item.description)
        self.assertIn("Due 24 AUG 26", reading.posted_invoice_item.description)

        self.client.post(url, {"posting_month": "2026-08"})
        self.assertEqual(InvoiceItem.objects.count(), 1)

    def test_paid_bill_shows_tenant_balance_and_generated_invoice_state(self):
        reading = IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            consumer_name="PITC Consumer",
            bill_month="AUG 26",
            current_bill="3,000",
            arrears="0",
            grand_total="3,000",
            current_month_paid=True,
        )
        list_url = reverse("invoices:iesco_bill_reading_list")

        before = self.client.get(list_url)
        row = next(
            item
            for item in before.context["meter_rows"]
            if item["reference_no"] == reading.reference_no
        )
        self.assertEqual(row["tenant_name"], "Iesco Tenant")
        self.assertContains(before, "PITC Consumer")
        self.assertContains(before, "Iesco Tenant")
        self.assertContains(before, "Balance:")
        self.assertContains(before, ">Invoice</button>")
        self.assertContains(
            before, reverse("leases:lease_detail", args=[self.lease.pk])
        )
        self.assertContains(
            before, reverse("leases:lease_ledger_by_pk", args=[self.lease.pk])
        )
        self.assertContains(before, "data-no-invoice")
        self.assertContains(before, "No WhatsApp phone number")

        self.client.post(
            reverse("invoices:iesco_bill_post_to_invoice", args=[reading.pk]),
            {"return_to": "list"},
        )

        after = self.client.get(list_url)
        reading.refresh_from_db()
        self.assertIsNotNone(reading.posted_invoice_item_id)
        self.assertContains(after, ">ReInvoice</button>")
        self.assertContains(
            after, reading.posted_invoice_item.invoice.invoice_number
        )
        self.assertContains(
            after,
            reverse(
                "invoices:invoice_detail",
                args=[reading.posted_invoice_item.invoice_id],
            ),
        )

    def test_invoice_adds_only_arrears_above_verified_prior_iesco_charge(self):
        july = IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="JUL 26",
            current_bill="21,195",
            arrears="0",
            grand_total="21,195",
        )
        self.client.post(
            reverse("invoices:iesco_bill_post_to_invoice", args=[july.pk])
        )
        july.refresh_from_db()
        self.assertEqual(july.posted_invoice_item.amount, Decimal("21200.00"))

        august = IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            current_bill="21,677",
            arrears="21,213",
            grand_total="42,890",
        )
        self.client.post(
            reverse("invoices:iesco_bill_post_to_invoice", args=[august.pk])
        )

        august.refresh_from_db()
        self.assertEqual(august.posted_invoice_item.amount, Decimal("21690.00"))
        self.assertIn(
            "prior billed credit Rs. 21,200.00",
            august.posted_invoice_item.description,
        )
        self.assertIn(
            "new arrears/penalty Rs. 13.00",
            august.posted_invoice_item.description,
        )

    def test_invoice_excludes_unverified_arrears_without_prior_matching_item(self):
        reading = IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            current_bill="21,677",
            arrears="21,213",
            grand_total="42,890",
        )

        self.client.post(
            reverse("invoices:iesco_bill_post_to_invoice", args=[reading.pk])
        )

        reading.refresh_from_db()
        self.assertEqual(reading.posted_invoice_item.amount, Decimal("21680.00"))
        self.assertIn(
            "excluded because no prior matching IESCO invoice was verified",
            reading.posted_invoice_item.description,
        )

    def test_changed_bill_amount_requires_confirmation_before_invoice_update(self):
        reading = IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            current_bill="3,680",
            arrears="0",
            grand_total="3,680",
        )
        url = reverse("invoices:iesco_bill_post_to_invoice", args=[reading.pk])
        self.client.post(url)
        reading.refresh_from_db()
        item = reading.posted_invoice_item

        reading.current_bill = "4,000"
        reading.grand_total = "4,000"
        reading.save(update_fields=["current_bill", "grand_total"])
        review = self.client.post(url)

        self.assertContains(review, "Confirm IESCO invoice update")
        self.assertContains(review, "Existing Amount")
        item.refresh_from_db()
        self.assertEqual(item.amount, Decimal("3680.00"))

        confirmed = self.client.post(
            url, {"confirm_amount_change": "1"}, follow=True
        )
        self.assertContains(confirmed, "updated on invoice")
        item.refresh_from_db()
        self.assertEqual(item.amount, Decimal("4000.00"))
        self.assertEqual(InvoiceItem.objects.count(), 1)

    def test_bulk_make_invoices_is_idempotent_for_the_same_billing_month(self):
        second_tenant = Tenant.objects.create(
            first_name="Second",
            last_name="Tenant",
            cnic="12345-1234567-9",
        )
        Lease.objects.create(
            tenant=second_tenant,
            unit=self.unleased_unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=Decimal("20000.00"),
            status="active",
        )
        readings = [
            IescoBillReading.objects.create(
                reference_no=unit.electric_meter_num,
                bill_month="AUG 26",
                current_bill=amount,
                arrears="0",
                grand_total=amount,
            )
            for unit, amount in (
                (self.active_unit, "3,680"),
                (self.unleased_unit, "2,500"),
            )
        ]
        url = reverse("invoices:iesco_bill_make_invoices_bulk")
        payload = {"reading_id": [str(reading.pk) for reading in readings]}

        first = self.client.post(url, payload, follow=True)
        self.assertContains(first, "2 created")
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual(InvoiceItem.objects.count(), 2)

        second = self.client.post(url, payload, follow=True)
        self.assertContains(second, "2 unchanged")
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual(InvoiceItem.objects.count(), 2)

    def test_reference_detail_shows_august_and_september_saved_bills(self):
        for month, amount in (("AUG 26", "3,680"), ("SEP 26", "4,100")):
            IescoBillReading.objects.create(
                reference_no=self.active_unit.electric_meter_num,
                bill_month=month,
                current_bill=amount,
                arrears="0",
                grand_total=amount,
            )

        response = self.client.get(
            reverse(
                "invoices:iesco_bill_reading_detail",
                args=[self.active_unit.electric_meter_num],
            )
        )

        self.assertContains(response, "AUG 26")
        self.assertContains(response, "SEP 26")

    def test_standalone_bill_cannot_be_posted_to_invoice(self):
        reading = IescoBillReading.objects.create(
            reference_no=self.standalone.reference_no,
            bill_month="AUG 26",
            grand_total="3,680",
        )
        response = self.client.post(
            reverse("invoices:iesco_bill_post_to_invoice", args=[reading.pk]),
            {"posting_month": "2026-08"},
            follow=True,
        )
        self.assertContains(response, "Assign this reference number to a unit")
        reading.refresh_from_db()
        self.assertIsNone(reading.posted_at)
        self.assertFalse(InvoiceItem.objects.exists())

    def test_uploaded_pdf_is_linked_from_list_and_detail(self):
        reading = IescoBillReading.objects.create(
            reference_no=self.active_unit.electric_meter_num,
            bill_month="AUG 26",
            grand_total="3,680",
        )
        upload = SimpleUploadedFile(
            "iesco-bill.pdf",
            b"%PDF-1.4\n% test IESCO PDF\n",
            content_type="application/pdf",
        )
        with tempfile.TemporaryDirectory() as media_root, self.settings(
            MEDIA_ROOT=media_root
        ):
            response = self.client.post(
                reverse("invoices:iesco_bill_pdf_upload", args=[reading.pk]),
                {"bill_pdf": upload},
                follow=True,
            )
            self.assertContains(response, "IESCO bill PDF uploaded")
            reading.refresh_from_db()
            self.assertTrue(reading.bill_pdf.name.endswith("AUG-26.pdf"))
            listing = self.client.get(reverse("invoices:iesco_bill_reading_list"))
            self.assertContains(listing, reading.bill_pdf.url)
