import json

from django.test import TestCase, override_settings
from django.urls import reverse

from invoices.models import IescoBillReading


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
    def test_latest_completed_month_paid_status_is_parsed(self):
        from invoices.iesco_bill_fetch import parse_bill

        html = """
            <span class="charges-bd-en">Grand Total</span>
            <span class="charges-bd-val">3,682</span>
            <span class="right-panel-en">BILL MONTH</span>
            <div class="right-main-val">AUG 26</div>
            <span class="right-panel-en--due">DUE DATE</span>
            <div class="right-main-val--due">17 SEP 26</div>
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

    def test_reference_number_must_be_fourteen_digits(self):
        from invoices.iesco_bill_fetch import parse_bill

        with self.assertRaisesMessage(ValueError, "exactly 14 digits"):
            parse_bill("", "123")
