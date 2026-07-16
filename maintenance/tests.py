from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from leases.models import Lease
from maintenance.models import MaintenanceRequest, MaintenanceRequestMedia
from maintenance.public_links import make_public_maintenance_token
from properties.models import Property, Unit
from tenants.models import Tenant


class PublicMaintenanceRequestTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            first_name="Ali",
            last_name="Khan",
            phone="03001234567",
            cnic="1234567890123",
        )
        self.property = Property.objects.create(
            property_name="Test Heights",
            owner_name="Owner One",
            owner_cnic="1111122222333",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="A-101",
        )
        today = timezone.localdate()
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=today,
            end_date=today.replace(year=today.year + 1),
            monthly_rent=Decimal("25000.00"),
            security_deposit=Decimal("50000.00"),
            status="active",
        )
        self.token = make_public_maintenance_token(self.lease)
        self.url = reverse("maintenance:public_request_create", args=[self.token])

    def test_secure_token_link_generation_opens_public_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Heights")
        self.assertContains(response, "A-101")
        self.assertContains(response, "Ali Khan")
        self.assertNotContains(response, "Priority")

    def test_public_request_submission_creates_request(self):
        response = self.client.post(
            self.url,
            {
                "submission_key": "submit-one",
                "title": "Kitchen sink leak",
                "description": "Water is leaking under the sink.",
            },
        )
        self.assertEqual(response.status_code, 302)
        item = MaintenanceRequest.objects.get()
        self.assertEqual(item.tenant, self.tenant)
        self.assertEqual(item.lease, self.lease)
        self.assertEqual(item.unit, self.unit)
        self.assertEqual(item.source, "public_link")
        self.assertEqual(item.status, "new")
        self.assertEqual(item.description, "Water is leaking under the sink.")

    def test_multiple_file_upload(self):
        files = [
            SimpleUploadedFile("photo.jpg", b"jpg-data", content_type="image/jpeg"),
            SimpleUploadedFile("clip.webm", b"webm-data", content_type="video/webm"),
            SimpleUploadedFile("note.pdf", b"pdf-data", content_type="application/pdf"),
        ]
        response = self.client.post(
            self.url,
            {
                "submission_key": "submit-files",
                "title": "Door issue",
                "description": "Door lock is loose.",
                "files": files,
            },
        )
        self.assertEqual(response.status_code, 302)
        item = MaintenanceRequest.objects.get()
        self.assertEqual(item.media.count(), 3)
        for media in item.media.all():
            self.assertIn("test-heights_a-101_", media.file.name.lower())

    def test_duplicate_refresh_protection(self):
        payload = {
            "submission_key": "same-browser-post",
            "title": "No electricity",
            "description": "Breaker keeps tripping.",
        }
        first = self.client.post(self.url, payload)
        second = self.client.post(self.url, payload)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(MaintenanceRequest.objects.count(), 1)

    def test_confirmation_page(self):
        response = self.client.post(
            self.url,
            {
                "submission_key": "confirm-page",
                "title": "Window repair",
                "description": "Window does not close.",
            },
        )
        confirmation = self.client.get(response["Location"])
        item = MaintenanceRequest.objects.get()
        self.assertContains(confirmation, f"Request #{item.id}")
        self.assertContains(confirmation, "Ali Khan")
        self.assertContains(confirmation, "Window Repair")
        self.assertContains(confirmation, "Window does not close.")

    def test_download_confirmation(self):
        response = self.client.post(
            self.url,
            {
                "submission_key": "download-page",
                "title": "Bathroom tile",
                "description": "Tile is broken.",
            },
        )
        download_url = response["Location"].rstrip("/") + "/download/"
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertContains(download, "Bathroom Tile")
