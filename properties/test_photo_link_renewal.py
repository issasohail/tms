import tempfile
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import GlobalSettings
from leases.models import Lease, WhatsAppTemplate
from leases.models_renewal import LeaseRenewal
from leases.views_lease_photos import _sign_lease_media_token
from leases.whatsapp import render_unit_whatsapp_template
from properties.models import (
    PhotoLinkRenewalRequest,
    Property,
    PublicPhotoLink,
    Unit,
    UnitMedia,
)
from properties.services.photo_link_renewal import (
    LINK_LIFETIME,
    create_public_photo_link,
    create_renewal_request,
    handle_staff_photo_link_command,
    public_link_share_text,
    public_link_url,
    reusable_public_photo_link,
)
from properties.views import _sign_media_token
from tenants.models import Tenant, TenantInterestType
from whatsapp.models import (
    WhatsAppMessageLog,
    WhatsAppStaffActionLog,
    WhatsAppStaffPropertyAccess,
    WhatsAppStaffRoutingRule,
)


@override_settings(PUBLIC_BASE_URL="https://kirayas.test")
class SecurePhotoLinkTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media_dir = tempfile.TemporaryDirectory()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_dir.name)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        cls._media_dir.cleanup()

    def setUp(self):
        self.property = Property.objects.create(
            property_name="Photo Security Property",
            owner_name="Owner",
            owner_cnic="35202-1234567-1",
            type="Residential",
            property_type="apartment",
            total_units=2,
        )
        self.unit = Unit.objects.create(
            property=self.property, unit_number="A-1", status="occupied"
        )
        self.tenant = Tenant.objects.create(
            first_name="Photo",
            last_name="Tenant",
            phone="03001234567",
            cnic="35202-7654321-1",
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=timezone.localdate() - timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=330),
            monthly_rent=Decimal("25000"),
            status="active",
        )
        self.history = LeaseRenewal.objects.create(
            lease=self.lease,
            renewal_number=1,
            start_date=self.lease.start_date,
            end_date=self.lease.end_date,
            monthly_rent=self.lease.monthly_rent,
        )

    def _targets(self, gallery_type):
        values = {"property_obj": self.property}
        if gallery_type == PublicPhotoLink.GALLERY_UNIT:
            values["unit"] = self.unit
        elif gallery_type == PublicPhotoLink.GALLERY_LEASE:
            values["lease"] = self.lease
        elif gallery_type == PublicPhotoLink.GALLERY_LEASE_HISTORY:
            values.update(lease=self.lease, lease_history=self.history)
        return values

    def _expired_link(self, gallery_type=PublicPhotoLink.GALLERY_UNIT):
        link = create_public_photo_link(gallery_type, **self._targets(gallery_type))
        PublicPhotoLink.objects.filter(pk=link.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        link.refresh_from_db()
        return link

    def _staff(self, username="photo-staff", phone="03009998888"):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            whatsapp_number=phone,
            is_staff=True,
        )
        WhatsAppStaffPropertyAccess.objects.create(
            staff_user=user, property=self.property
        )
        return user

    def _message(self, phone, body):
        return WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=phone,
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": body}},
        )

    def test_all_four_compact_gallery_types_open(self):
        for gallery_type in dict(PublicPhotoLink.GALLERY_CHOICES):
            with self.subTest(gallery_type=gallery_type):
                link = create_public_photo_link(
                    gallery_type, **self._targets(gallery_type)
                )
                response = self.client.get(
                    reverse("public_photo_link", kwargs={"token": link.token})
                )
                self.assertEqual(response.status_code, 200)

    def test_token_is_opaque_and_canonical_url_is_short(self):
        link = create_public_photo_link(
            PublicPhotoLink.GALLERY_LEASE_HISTORY,
            **self._targets(PublicPhotoLink.GALLERY_LEASE_HISTORY),
        )
        self.assertEqual(len(link.token), 32)
        self.assertRegex(link.token, r"^[A-Za-z0-9_-]{32}$")
        self.assertEqual(public_link_url(link), f"https://kirayas.test/p/{link.token}/")

    def test_expired_gallery_shows_form_and_file_is_denied(self):
        media = UnitMedia.objects.create(
            unit=self.unit,
            file=SimpleUploadedFile("terms.pdf", b"photo-data", content_type="application/pdf"),
            original_filename="terms.pdf",
        )
        link = self._expired_link()
        gallery = self.client.get(reverse("public_photo_link", args=[link.token]))
        file_response = self.client.get(reverse("public_photo_file", args=[link.token, media.pk]))
        self.assertContains(gallery, "This photo link has expired", status_code=200)
        self.assertContains(gallery, "Request a new link")
        self.assertEqual(file_response.status_code, 404)

    def test_invalid_tampered_and_deleted_targets_never_show_form(self):
        invalid = self.client.get(reverse("public_photo_link", args=["not-a-real-token"]))
        self.assertContains(invalid, "This photo link is invalid", status_code=404)
        self.assertNotContains(invalid, "Request a new link", status_code=404)

        link = create_public_photo_link(
            PublicPhotoLink.GALLERY_UNIT,
            property_obj=self.property,
            unit=self.unit,
        )
        self.unit.delete()
        deleted = self.client.get(reverse("public_photo_link", args=[link.token]))
        self.assertContains(deleted, "This photo link is invalid", status_code=404)
        self.assertNotContains(deleted, "Request a new link", status_code=404)

    def test_expired_legacy_property_link_can_request_but_tampered_cannot(self):
        old_time = time.time() - (49 * 60 * 60)
        with patch("django.core.signing.time.time", return_value=old_time):
            token = _sign_media_token("property", self.property.pk)
        expired = self.client.get(reverse("properties:media_public_share", args=[token]))
        tampered = self.client.get(
            reverse("properties:media_public_share", args=[f"{token}x"])
        )
        self.assertContains(expired, "Request a new link", status_code=200)
        self.assertContains(tampered, "This photo link is invalid", status_code=404)
        self.assertNotContains(tampered, "Request a new link", status_code=404)

    def test_expired_legacy_lease_link_can_request_but_tampered_cannot(self):
        old_time = time.time() - (49 * 60 * 60)
        with patch("django.core.signing.time.time", return_value=old_time):
            token = _sign_lease_media_token(self.lease.pk, self.history.pk)
        expired = self.client.get(reverse("leases:photos_public_share", args=[token]))
        tampered = self.client.get(
            reverse("leases:photos_public_share", args=[f"{token}x"])
        )
        self.assertContains(expired, "Request a new link", status_code=200)
        self.assertContains(tampered, "This photo link is invalid", status_code=404)
        self.assertNotContains(tampered, "Request a new link", status_code=404)

    @patch("properties.services.photo_link_renewal.notify_staff_of_renewal_request")
    def test_request_form_normalizes_and_masks_phone(self, notify):
        link = self._expired_link()
        response = self.client.post(
            reverse("public_photo_link", args=[link.token]),
            {"full_name": "Visitor Name", "phone": "0300 123 4567"},
            REMOTE_ADDR="192.0.2.10",
        )
        renewal = PhotoLinkRenewalRequest.objects.get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "authorized property staff")
        self.assertNotContains(response, renewal.requester_phone)
        self.assertContains(response, renewal.requester_phone[-4:])
        self.assertEqual(renewal.requester_phone, "923001234567")
        notify.assert_called_once_with(renewal)

    @patch("properties.services.photo_link_renewal.notify_staff_of_renewal_request")
    def test_request_creates_one_inactive_prospect_and_records_interest(self, notify):
        interest = TenantInterestType.objects.create(
            name="Apartment", code="apartment-interest"
        )
        first, created = create_renewal_request(
            gallery_type=PublicPhotoLink.GALLERY_UNIT,
            property_obj=self.property,
            unit=self.unit,
            requester_name="Visitor Name",
            requester_phone="0300 123 4567",
            interest_type_ids=[str(interest.pk)],
        )
        second, second_created = create_renewal_request(
            gallery_type=PublicPhotoLink.GALLERY_PROPERTY,
            property_obj=self.property,
            requester_name="Visitor Changed Name",
            requester_phone="923001234567",
            interest_type_ids=[str(interest.pk)],
        )
        self.assertTrue(created)
        self.assertTrue(second_created)
        self.assertEqual(first.tenant_id, second.tenant_id)
        prospect = Tenant.objects.get(pk=first.tenant_id)
        self.assertFalse(prospect.is_active)
        self.assertEqual(prospect.phone, "923001234567")
        self.assertQuerySetEqual(first.interested_in.all(), [interest])
        self.assertQuerySetEqual(prospect.interested_in.all(), [interest])
        self.assertEqual(notify.call_count, 2)

    @patch("properties.services.photo_link_renewal.notify_staff_of_renewal_request")
    def test_validation_duplicate_cooldown_and_ip_throttle(self, notify):
        with self.assertRaises(ValueError):
            create_renewal_request(
                gallery_type=PublicPhotoLink.GALLERY_UNIT,
                property_obj=self.property,
                unit=self.unit,
                requester_name="X",
                requester_phone="12",
            )
        first, created = create_renewal_request(
            gallery_type=PublicPhotoLink.GALLERY_UNIT,
            property_obj=self.property,
            unit=self.unit,
            requester_name="Visitor Name",
            requester_phone="03001234567",
            request_ip="192.0.2.20",
        )
        duplicate, duplicate_created = create_renewal_request(
            gallery_type=PublicPhotoLink.GALLERY_UNIT,
            property_obj=self.property,
            unit=self.unit,
            requester_name="Visitor Again",
            requester_phone="+92 300 1234567",
            request_ip="192.0.2.20",
        )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first, duplicate)
        self.assertEqual(notify.call_count, 1)

        for index in range(1, 5):
            create_renewal_request(
                gallery_type=PublicPhotoLink.GALLERY_UNIT,
                property_obj=self.property,
                unit=self.unit,
                requester_name="Visitor Name",
                requester_phone=f"0301000000{index}",
                request_ip="192.0.2.20",
            )
        blocked, blocked_created = create_renewal_request(
            gallery_type=PublicPhotoLink.GALLERY_UNIT,
            property_obj=self.property,
            unit=self.unit,
            requester_name="Visitor Name",
            requester_phone="03019999999",
            request_ip="192.0.2.20",
        )
        self.assertIsNone(blocked)
        self.assertFalse(blocked_created)

    @patch("properties.services.photo_link_renewal.WhatsAppService.send_text")
    def test_request_notifies_only_authorized_property_staff(self, send_text):
        send_text.return_value = {"ok": True}
        staff = self._staff()
        unrelated_property = Property.objects.create(
            property_name="Unrelated",
            owner_name="Owner",
            owner_cnic="35202-1111111-1",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        unrelated = get_user_model().objects.create_user(
            username="unrelated",
            email="unrelated@example.com",
            whatsapp_number="03007776666",
            is_staff=True,
        )
        WhatsAppStaffPropertyAccess.objects.create(
            staff_user=unrelated, property=unrelated_property
        )
        renewal, _ = create_renewal_request(
            gallery_type=PublicPhotoLink.GALLERY_UNIT,
            property_obj=self.property,
            unit=self.unit,
            requester_name="Visitor Name",
            requester_phone="03001234567",
        )
        renewal.refresh_from_db()
        self.assertEqual(renewal.assigned_staff, staff)
        send_text.assert_called_once()
        self.assertEqual(send_text.call_args.args[0], staff.whatsapp_number)
        self.assertNotIn("/p/", send_text.call_args.args[1])

    @patch("properties.services.photo_link_renewal.WhatsAppService.send_text")
    def test_request_is_preserved_when_staff_notification_fails(self, send_text):
        send_text.side_effect = RuntimeError("Meta unavailable")
        self._staff()
        renewal, created = create_renewal_request(
            gallery_type=PublicPhotoLink.GALLERY_PROPERTY,
            property_obj=self.property,
            requester_name="Visitor Name",
            requester_phone="03001234567",
        )
        renewal.refresh_from_db()
        self.assertTrue(created)
        self.assertEqual(renewal.status, PhotoLinkRenewalRequest.STATUS_PENDING)
        self.assertEqual(renewal.whatsapp_status, "failed")
        self.assertIn("Meta unavailable", renewal.whatsapp_error)

    @patch("properties.services.photo_link_renewal.WhatsAppService.send_text")
    def test_routing_rule_is_an_authorized_fallback(self, send_text):
        send_text.return_value = {"ok": True}
        staff = get_user_model().objects.create_user(
            username="route-staff",
            email="route@example.com",
            whatsapp_number="03005554444",
            is_staff=True,
        )
        WhatsAppStaffRoutingRule.objects.create(
            property=self.property,
            department=WhatsAppStaffRoutingRule.DEPARTMENT_LEASING,
            staff_user=staff,
            priority=1,
        )
        renewal, _ = create_renewal_request(
            gallery_type=PublicPhotoLink.GALLERY_PROPERTY,
            property_obj=self.property,
            requester_name="Visitor Name",
            requester_phone="03001234567",
        )
        renewal.refresh_from_db()
        self.assertEqual(renewal.assigned_staff, staff)

    @patch("properties.services.photo_link_renewal.WhatsAppService.send_text")
    def test_authorized_approve_creates_48_hour_link_and_is_idempotent(self, send_text):
        send_text.return_value = {"ok": True}
        staff = self._staff()
        renewal = PhotoLinkRenewalRequest.objects.create(
            gallery_type=PublicPhotoLink.GALLERY_UNIT,
            property=self.property,
            unit=self.unit,
            requester_name="Visitor",
            requester_phone="923001234567",
        )
        message = self._message(staff.whatsapp_number, f"APPROVE {renewal.reference}")
        before = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            response = handle_staff_photo_link_command(message)
        renewal.refresh_from_db()
        self.assertEqual(response, "")
        self.assertEqual(renewal.status, PhotoLinkRenewalRequest.STATUS_APPROVED)
        self.assertGreaterEqual(
            renewal.fresh_link.expires_at,
            before + LINK_LIFETIME - timedelta(seconds=2),
        )
        self.assertLessEqual(
            renewal.fresh_link.expires_at,
            before + LINK_LIFETIME + timedelta(seconds=2),
        )
        self.assertEqual(
            send_text.call_args.args[0],
            "".join(character for character in staff.whatsapp_number if character.isdigit()),
        )
        self.assertIn(renewal.fresh_link.token, send_text.call_args.args[1])

        again = handle_staff_photo_link_command(message)
        self.assertIn("already approved", again)
        self.assertEqual(PublicPhotoLink.objects.filter(renewal_request=renewal).count(), 1)

    def test_unauthorized_approval_is_blocked_and_audited(self):
        staff = get_user_model().objects.create_user(
            username="blocked-staff",
            email="blocked@example.com",
            whatsapp_number="03003332222",
            is_staff=True,
        )
        renewal = PhotoLinkRenewalRequest.objects.create(
            gallery_type=PublicPhotoLink.GALLERY_PROPERTY,
            property=self.property,
            requester_name="Visitor",
            requester_phone="923001234567",
        )
        response = handle_staff_photo_link_command(
            self._message(staff.whatsapp_number, f"APPROVE {renewal.reference}")
        )
        renewal.refresh_from_db()
        self.assertIn("not authorized", response)
        self.assertEqual(renewal.status, PhotoLinkRenewalRequest.STATUS_PENDING)
        self.assertFalse(PublicPhotoLink.objects.filter(renewal_request=renewal).exists())
        self.assertTrue(
            WhatsAppStaffActionLog.objects.filter(
                staff_user=staff,
                status=WhatsAppStaffActionLog.ACTION_STATUS_BLOCKED,
            ).exists()
        )

    def test_reject_creates_no_link_and_duplicate_is_idempotent(self):
        staff = self._staff()
        renewal = PhotoLinkRenewalRequest.objects.create(
            gallery_type=PublicPhotoLink.GALLERY_PROPERTY,
            property=self.property,
            requester_name="Visitor",
            requester_phone="923001234567",
        )
        message = self._message(staff.whatsapp_number, f"REJECT {renewal.reference}")
        self.assertIn("Rejected", handle_staff_photo_link_command(message))
        renewal.refresh_from_db()
        self.assertEqual(renewal.status, PhotoLinkRenewalRequest.STATUS_REJECTED)
        self.assertFalse(PublicPhotoLink.objects.filter(renewal_request=renewal).exists())
        self.assertIn("already rejected", handle_staff_photo_link_command(message))

    @patch("properties.services.photo_link_renewal.WhatsAppService.send_text")
    def test_approval_is_preserved_when_whatsapp_send_raises(self, send_text):
        send_text.side_effect = RuntimeError("Meta unavailable")
        staff = self._staff()
        renewal = PhotoLinkRenewalRequest.objects.create(
            gallery_type=PublicPhotoLink.GALLERY_PROPERTY,
            property=self.property,
            requester_name="Visitor",
            requester_phone="923001234567",
        )
        with self.captureOnCommitCallbacks(execute=True):
            handle_staff_photo_link_command(
                self._message(staff.whatsapp_number, f"APPROVE {renewal.reference}")
            )
        renewal.refresh_from_db()
        self.assertEqual(renewal.status, PhotoLinkRenewalRequest.STATUS_APPROVED)
        self.assertIsNotNone(renewal.fresh_link)
        self.assertEqual(renewal.whatsapp_status, "approved_send_failed")

    def test_reusable_link_message_and_vacancy_template_use_same_url(self):
        first = reusable_public_photo_link(
            PublicPhotoLink.GALLERY_UNIT,
            property_obj=self.property,
            unit=self.unit,
        )
        second = reusable_public_photo_link(
            PublicPhotoLink.GALLERY_UNIT,
            property_obj=self.property,
            unit=self.unit,
        )
        self.assertEqual(first.pk, second.pk)
        text = public_link_share_text(first)
        self.assertIn(public_link_url(first), text)
        self.assertIn("Expires:", text)

        WhatsAppTemplate.objects.update_or_create(
            template_type=WhatsAppTemplate.TEMPLATE_VACANCY,
            defaults={
                "name": "Vacancy",
                "body": "Unit [UNIT_NUMBER]\n[UNIT_PHOTO_LINK]",
                "is_active": True,
            },
        )
        request = Mock()
        request.user = Mock(is_authenticated=False)
        _, rendered = render_unit_whatsapp_template(
            WhatsAppTemplate.TEMPLATE_VACANCY, self.unit, request=request
        )
        self.assertIn(public_link_url(first), rendered)
        self.assertIn("secure 48-hour link", rendered)
        self.assertIn("Expires:", rendered)

    @patch("whatsapp.services.whatsapp_ai.get_whatsapp_ai_config")
    @patch("accounts.whatsapp_password_reset.handle_whatsapp_password_reset_request", return_value=False)
    @patch("properties.services.photo_link_renewal.handle_staff_photo_link_command", return_value="Handled")
    @patch("whatsapp.services.whatsapp.WhatsAppService.send_text")
    def test_command_dispatch_runs_even_when_ai_is_disabled(
        self, send_text, command, password_reset, ai_config
    ):
        from whatsapp.services.whatsapp_ai import process_inbound_whatsapp_message

        ai_config.return_value.enabled = False
        message = self._message("923009998888", "REJECT PLR-A1B2C3D4")
        process_inbound_whatsapp_message(message)
        command.assert_called_once_with(message)
        send_text.assert_called_once_with(message.phone_number, "Handled")
