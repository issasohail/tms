import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from unittest.mock import patch
from bs4 import BeautifulSoup

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import IescoBillReading, IescoHelperDevice, IescoHelperFetchRun, IescoHelperPairing, IescoStandaloneMeter, Invoice


class IescoHelperTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="iesco-helper-admin", email="helper@example.com", password="password")
        self.other = get_user_model().objects.create_user(username="iesco-helper-other", email="other@example.com", password="password")
        self.client.force_login(self.user)
        self.pair_url = reverse("invoices:iesco_helper_pair")
        self.exchange_url = reverse("invoices:iesco_helper_exchange")
        self.refs_url = reverse("invoices:iesco_device_references")
        self.ingest_url = reverse("invoices:iesco_device_ingest")
        IescoStandaloneMeter.objects.create(reference_no="17146151548911", description="Active meter", is_active=True)
        IescoStandaloneMeter.objects.create(reference_no="17146151548912", description="Inactive meter", is_active=False)

    def create_request(self):
        response = self.client.post(self.pair_url, secure=True)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def exchange(self, token, device_id=None, version="2.0"):
        return self.client.post(
            self.exchange_url,
            json.dumps({"token": token, "device_id": str(device_id or uuid4()), "name": "OFFICE-PC", "version": version}),
            content_type="application/json", secure=True,
        )

    def paired_token(self):
        created = self.create_request()
        token = parse_qs(urlsplit(created["url"]).query)["token"][0]
        response = self.exchange(token)
        self.assertEqual(response.status_code, 200)
        return response.json()["device_token"]

    def test_staff_permission_and_dashboard_actions(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.pair_url).status_code, 403)
        self.assertEqual(self.client.get(reverse("invoices:iesco_bill_reading_list")).status_code, 403)
        self.other.user_permissions.add(Permission.objects.get(codename="change_iescobillreading"))
        self.assertEqual(self.client.post(self.pair_url, secure=True).status_code, 200)
        self.client.force_login(self.user)
        page = self.client.get(reverse("invoices:iesco_bill_reading_list"))
        self.assertContains(page, "Connect This Computer")
        self.assertContains(page, "Download IESCO Helper Setup")
        self.assertContains(page, 'id="iescoConnectionBadge"')
        self.assertContains(page, 'id="iescoReadingsContent"')
        self.assertIsNotNone(BeautifulSoup(page.content, "html.parser").select_one("#iescoReadingsContent .iesco-desktop-view"))

    def test_admin_can_create_pairing_request(self):
        url = reverse("admin:invoices_iescohelperpairing_pair")
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connect This Computer")
        self.assertEqual(IescoHelperPairing.objects.count(), 1)

    def test_local_http_pairing_returns_clear_json_error(self):
        response = self.client.post(self.pair_url)
        self.assertEqual(response.status_code, 400)
        self.assertIn("HTTPS", response.json()["error"])
        self.assertFalse(IescoHelperPairing.objects.exists())

    def test_expired_invalid_and_single_use_pairing(self):
        self.assertEqual(self.exchange("invalid").status_code, 400)
        self.assertEqual(self.exchange("A" * 64).status_code, 401)
        created = self.create_request()
        token = parse_qs(urlsplit(created["url"]).query)["token"][0]
        pairing = IescoHelperPairing.objects.get(pk=created["id"])
        pairing.expires_at = timezone.now() - timedelta(seconds=1)
        pairing.save(update_fields=["expires_at"])
        self.assertEqual(self.exchange(token).status_code, 410)
        self.assertEqual(IescoHelperDevice.objects.count(), 0)
        created = self.create_request()
        token = parse_qs(urlsplit(created["url"]).query)["token"][0]
        self.assertEqual(self.exchange(token).status_code, 200)
        self.assertEqual(self.exchange(token).status_code, 409)
        self.assertEqual(IescoHelperDevice.objects.count(), 1)

    def test_device_token_access_revocation_and_ingest(self):
        token = self.paired_token()
        device = IescoHelperDevice.objects.get()
        self.assertNotEqual(device.token_hash, token)
        self.assertEqual(self.client.get(self.refs_url).status_code, 401)
        headers = {"HTTP_AUTHORIZATION": "Bearer " + token}
        refs = self.client.get(self.refs_url, **headers)
        self.assertEqual(refs.status_code, 200)
        self.assertEqual(refs.json()["references"], ["17146151548911"])
        payload = {"reference_no": "17146151548911", "bill_month": "SEP 26", "grand_total": "100"}
        invoices_before = Invoice.objects.count()
        response = self.client.post(self.ingest_url, json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(IescoBillReading.objects.get().trust_status, IescoBillReading.TRUST_PARSED)
        self.assertEqual(Invoice.objects.count(), invoices_before)
        failed = self.client.post(self.ingest_url, json.dumps({"reference_no": "17146151548912", "bill_month": "SEP 26"}), content_type="application/json", **headers)
        self.assertEqual(failed.status_code, 403)
        self.assertEqual(IescoBillReading.objects.count(), 1)
        device.is_active = False
        device.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(self.refs_url, **headers).status_code, 401)
        self.assertEqual(self.client.post(self.ingest_url, json.dumps(payload), content_type="application/json", **headers).status_code, 401)

    def test_repair_rotates_token_without_duplicate_device(self):
        token = self.paired_token()
        device = IescoHelperDevice.objects.get()
        created = self.create_request()
        pairing_token = parse_qs(urlsplit(created["url"]).query)["token"][0]
        response = self.exchange(pairing_token, device.device_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(IescoHelperDevice.objects.count(), 1)
        self.assertNotEqual(response.json()["device_token"], token)
        self.assertEqual(self.client.get(self.refs_url, HTTP_AUTHORIZATION="Bearer " + token).status_code, 401)

    def test_connect_creates_fetch_all_run_and_reports_progress(self):
        created = self.create_request()
        pairing_token = parse_qs(urlsplit(created["url"]).query)["token"][0]
        exchanged = self.exchange(pairing_token, version="2.1")
        self.assertEqual(exchanged.status_code, 200)
        data = exchanged.json()
        run_id = data["run_id"]
        self.assertEqual(IescoHelperFetchRun.objects.get(pk=run_id).status, "queued")
        status_url = reverse("invoices:iesco_helper_pair_status", args=[created["id"]])
        self.assertEqual(self.client.get(status_url).json()["run_id"], run_id)
        url = reverse("invoices:iesco_device_run_progress", args=[run_id])
        update = {"status": "running", "total": 1, "completed": 0, "succeeded": 0, "failed": 0}
        self.assertEqual(self.client.post(url, json.dumps(update), content_type="application/json").status_code, 401)
        headers = {"HTTP_AUTHORIZATION": "Bearer " + data["device_token"]}
        self.assertEqual(self.client.post(url, json.dumps(update), content_type="application/json", **headers).status_code, 200)
        update.update(status="completed", completed=1, succeeded=1)
        self.assertEqual(self.client.post(url, json.dumps(update), content_type="application/json", **headers).status_code, 200)
        run_status = reverse("invoices:iesco_helper_run_status", args=[run_id])
        self.assertEqual(self.client.get(run_status).json()["succeeded"], 1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(run_status).status_code, 403)

    def test_manual_fetch_run_requires_current_pairing_and_active_reference(self):
        created = self.create_request()
        pairing_token = parse_qs(urlsplit(created["url"]).query)["token"][0]
        data = self.exchange(pairing_token, version="2.1").json()
        auto = IescoHelperFetchRun.objects.get(pk=data["run_id"])
        auto.status = "completed"
        auto.save(update_fields=["status"])
        start_url = reverse("invoices:iesco_helper_run_start")
        self.assertEqual(self.client.post(start_url, {"pairing_id": created["id"], "reference_no": "17146151548912"}).status_code, 400)
        response = self.client.post(start_url, {"pairing_id": created["id"], "reference_no": "17146151548911"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("tms-iesco://fetch?run=", response.json()["url"])
        self.assertEqual(self.client.post(start_url, {"pairing_id": created["id"]}).status_code, 409)

    @patch("invoices.services_iesco.get_bill")
    def test_django_server_does_not_fetch_pitc(self, pitc):
        from .services_iesco import fetch_bill_payload
        with self.assertRaisesMessage(ValidationError, "Server-side PITC fetch is disabled"):
            fetch_bill_payload("17146151548911")
        pitc.assert_not_called()
        self.assertFalse(IescoBillReading.objects.exists())
        self.assertFalse(Invoice.objects.exists())
