import json
import tempfile
from io import BytesIO
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from leases.models_parking_inventory import (
    InventoryItemDefinition,
    UnitInventoryItem,
)

from .models import Property, Unit


class PublicUnitPhotoUploadTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media_directory = tempfile.TemporaryDirectory()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_directory.name)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        cls._media_directory.cleanup()

    def setUp(self):
        from leases.models import Lease
        from tenants.models import Tenant
        from properties.public_upload_links import make_unit_photo_upload_token

        self.tenant = Tenant.objects.create(
            first_name="Public",
            last_name="Uploader",
            phone="+923001234567",
            cnic="37405-1234567-1",
        )
        self.property = Property.objects.create(
            property_name="Upload Property",
            owner_name="Owner",
            owner_cnic="37405-7654321-1",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="U-01",
            status="occupied",
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=timezone.localdate() - timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=335),
            monthly_rent=Decimal("25000"),
            status="active",
        )
        self.token = make_unit_photo_upload_token(self.lease)
        self.url = reverse(
            "properties:public_unit_photo_upload", args=[self.token]
        )

    def _photo(self, name="gallery-photo.jpg", color="blue"):
        output = BytesIO()
        Image.new("RGB", (120, 90), color).save(output, format="JPEG")
        return SimpleUploadedFile(
            name, output.getvalue(), content_type="image/jpeg"
        )

    def test_link_requires_no_login_and_has_fixed_destination(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.property.property_name)
        self.assertContains(response, self.unit.unit_number)
        self.assertContains(response, f"#{self.lease.pk}")
        self.assertContains(response, "No login is required")

    @patch("whatsapp.services.whatsapp_ai.notify_staff_pending_request")
    def test_gallery_photos_are_staged_for_fixed_unit_approval(self, notify_pending):
        from whatsapp.models import PendingWhatsAppMedia

        response = self.client.post(
            self.url,
            {
                "lease_id": "999999",
                "unit_id": "999999",
                "photos": [
                    self._photo(),
                    self._photo("second.jpg", "red"),
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 photo(s) uploaded successfully")
        pending = list(PendingWhatsAppMedia.objects.order_by("pk"))
        self.assertEqual(len(pending), 2)
        self.assertEqual({item.lease_id for item in pending}, {self.lease.pk})
        self.assertEqual({item.property_id for item in pending}, {self.property.pk})
        self.assertEqual({item.unit_id for item in pending}, {self.unit.pk})
        self.assertEqual(
            {item.target_kind for item in pending},
            {PendingWhatsAppMedia.TARGET_UNIT_PHOTO},
        )
        self.assertEqual(len({item.batch_key for item in pending}), 1)
        notify_pending.assert_called_once_with("upload", pending[0])

    def test_invalid_link_is_not_accepted(self):
        response = self.client.get(
            reverse("properties:public_unit_photo_upload", args=["invalid-token"])
        )

        self.assertEqual(response.status_code, 404)

    @patch(
        "properties.public_upload_links.UNIT_PHOTO_UPLOAD_MAX_AGE",
        -1,
    )
    def test_expired_link_is_rejected_without_login_redirect(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 410)
        self.assertContains(response, "upload link has expired", status_code=410)


class UnitListInlineUpdateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="unit-list-user", password="test-password"
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_unit"),
            Permission.objects.get(codename="change_unit"),
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Test Property",
            owner_name="Owner",
            owner_cnic="35202-1234567-1",
            type="Building",
            property_type="apartment",
            total_units=2,
        )
        self.other_property = Property.objects.create(
            property_name="Other Property",
            owner_name="Owner",
            owner_cnic="35202-1234567-2",
            type="Building",
            property_type="apartment",
            total_units=1,
        )
        self.unit_one = Unit.objects.create(
            property=self.property, unit_number="1"
        )
        self.unit_two = Unit.objects.create(
            property=self.property, unit_number="2"
        )
        self.other_unit = Unit.objects.create(
            property=self.other_property, unit_number="1"
        )
        self.url = reverse("properties:unit_inline_update")

    def post_update(self, **payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_single_unit_charge_update_offers_property_bulk_update(self):
        response = self.post_update(
            id=self.unit_one.pk,
            field="internet_charges",
            value="1,500",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["offer_bulk"])
        self.assertEqual(response.json()["property_unit_count"], 2)
        self.unit_one.refresh_from_db()
        self.unit_two.refresh_from_db()
        self.assertEqual(self.unit_one.internet_charges, Decimal("1500.00"))
        self.assertEqual(self.unit_two.internet_charges, Decimal("0.00"))

    def test_bulk_update_is_limited_to_the_same_property(self):
        response = self.post_update(
            id=self.unit_one.pk,
            field="security_deposit_amount",
            value="50000",
            apply_to_property=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["affected_count"], 2)
        self.unit_one.refresh_from_db()
        self.unit_two.refresh_from_db()
        self.other_unit.refresh_from_db()
        self.assertEqual(
            self.unit_one.security_deposit_amount, Decimal("50000.00")
        )
        self.assertEqual(
            self.unit_two.security_deposit_amount, Decimal("50000.00")
        )
        self.assertEqual(
            self.other_unit.security_deposit_amount, Decimal("0.00")
        )

    def test_security_text_is_kept_and_can_be_bulk_updated(self):
        response = self.post_update(
            id=self.unit_one.pk,
            field="security_requires",
            value="Two Months",
            apply_to_property=True,
        )

        self.assertEqual(response.status_code, 200)
        self.unit_two.refresh_from_db()
        self.assertEqual(self.unit_two.security_requires, "Two Months")

    def test_negative_amount_is_rejected(self):
        response = self.post_update(
            id=self.unit_one.pk,
            field="monthly_rent",
            value="-1",
        )

        self.assertEqual(response.status_code, 400)
        self.unit_one.refresh_from_db()
        self.assertEqual(self.unit_one.monthly_rent, Decimal("25000.00"))

    def test_unit_list_shows_each_inventory_item_in_its_own_column(self):
        item = InventoryItemDefinition.objects.create(
            name="Ceiling Fan",
            code="test-ceiling-fan",
            default_quantity=0,
        )
        UnitInventoryItem.objects.create(
            unit=self.unit_one,
            item=item,
            quantity=3,
            condition="Working",
        )

        response = self.client.get(reverse("properties:unit_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Internet Charges")
        self.assertContains(response, "Security")
        self.assertContains(response, "Security Amount")
        self.assertNotContains(response, "Room Amenities")
        self.assertContains(response, 'data-label="Ceiling Fan"')
        self.assertContains(response, "Manage Ceiling Fan inventory")
        self.assertContains(response, ">3</a>", html=False)
        self.assertContains(response, 'id="unitFilterForm"')
        self.assertContains(response, "unitFilterForm.requestSubmit()")

    def test_unit_list_uses_sixty_rows_and_continuous_serial_numbers(self):
        Unit.objects.bulk_create(
            [
                Unit(property=self.property, unit_number=f"extra-{number:02d}")
                for number in range(58)
            ]
        )

        response = self.client.get(reverse("properties:unit_list"), {"page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["table"].paginator.per_page, 60)
        self.assertContains(
            response,
            '<td class="text-center unit-col sn-col">61</td>',
            html=True,
        )
