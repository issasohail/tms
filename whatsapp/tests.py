from datetime import date, timedelta
from decimal import Decimal
import base64
import hashlib
import hmac
import importlib
import json
import re
import tempfile
import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.apps import apps as django_apps
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader, PdfWriter

from whatsapp.services.estamp_processor import match_properties, match_unit
from whatsapp.services.payment_matching import extract_payment_text_fields
from whatsapp.services.openai_ocr import (
    _normalize as normalize_openai_receipt,
    extract_receipt_with_openai,
    validate_payment_receipt,
)
from whatsapp.services.whatsapp_ai import (
    WhatsAppAIAssistant,
    _ocr_looks_like_payment,
    detect_intent,
    process_inbound_whatsapp_message,
)
from core.models import GlobalSettings
from core.utils.identity import format_phone
from leases.models import Lease
from invoices.models import Invoice
from payments.models import Payment, PaymentDetail
from leases.models_lease_photos import LeaseMedia
from properties.models import Property, Unit
from tenants.models import Tenant
from whatsapp.models import (
    PendingWhatsAppMaintenance,
    PendingWhatsAppMedia,
    PendingWhatsAppPayment,
    WhatsAppConversation,
    WhatsAppHandover,
    WhatsAppMessageLog,
    WhatsAppStaffPropertyAccess,
    WhatsAppStaffRoutingRule,
)
from whatsapp.services.ai.orchestrator import WhatsAppAIOrchestrator, fallback_decision
from whatsapp.services.ai.safety import mask_sensitive_text
from whatsapp.services.ai.tool_registry import ToolContext, execute_tool
from whatsapp.services.ai_config import WhatsAppAIConfig
from whatsapp.services.handover.lifecycle import (
    accept_handover,
    close_handover,
    mark_call_requested,
    mark_called,
    return_handover_to_ai,
)
from whatsapp.services.handover.notifications import notify_new_handover
from whatsapp.services.handover.relay import relay_staff_reply
from whatsapp.services.handover.routing import eligible_staff, staff_can_access_handover
from whatsapp.services.handover.workflow import handle_active_tenant_message, handle_staff_handover_message
from whatsapp.services.identity.mode_resolver import infer_mode
from whatsapp.services.identity.sender_resolver import resolve_sender
from whatsapp.services.role_mode import resolve_mode, staff_menu_text
from whatsapp.services.tenant_context import resolve_tenant_and_last_lease
from whatsapp.views import _log_webhook_payload


class WhatsAppStaffAccessManagementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            "admin",
            password="test",
            email="admin-whatsapp@example.com",
            whatsapp_number="+923001111111",
        )
        self.manager = User.objects.create_user(
            "fida",
            password="test",
            email="fida-whatsapp@example.com",
            whatsapp_number="+923002222222",
            is_staff=True,
        )
        self.property = Property.objects.create(
            property_name="Access Property",
            owner_name="Owner",
            owner_cnic="37405-1212121-1",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )

    def test_seed_migration_assigns_both_configured_users(self):
        migration = importlib.import_module(
            "whatsapp.migrations.0017_seed_tenant_simulator_staff_access"
        )

        migration.add_initial_simulator_access(django_apps, None)

        group = Group.objects.get(name="Tenant Simulator")
        self.assertTrue(self.admin.groups.filter(pk=group.pk).exists())
        self.assertTrue(self.manager.groups.filter(pk=group.pk).exists())
        self.assertTrue(
            WhatsAppStaffPropertyAccess.objects.filter(
                staff_user=self.admin,
                property=self.property,
                is_active=True,
            ).exists()
        )
        self.assertTrue(
            WhatsAppStaffPropertyAccess.objects.filter(
                staff_user=self.manager,
                property=self.property,
                is_active=True,
            ).exists()
        )

    def test_superuser_can_manage_simulator_and_property_access(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("whatsapp:staff_access"),
            {
                "staff_id": self.manager.pk,
                "simulator_enabled": "on",
                "property_ids": [str(self.property.pk)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WhatsApp access updated for fida")
        self.assertTrue(self.manager.groups.filter(name="Tenant Simulator").exists())
        self.assertTrue(
            WhatsAppStaffPropertyAccess.objects.filter(
                staff_user=self.manager,
                property=self.property,
                is_active=True,
            ).exists()
        )

    def test_non_superuser_cannot_open_staff_access_ui(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse("whatsapp:staff_access"))

        self.assertEqual(response.status_code, 403)


class PendingWhatsAppMediaApprovalTests(TestCase):
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
        self.user = get_user_model().objects.create_user("media-approver", password="test")
        self.user.user_permissions.add(
            *Permission.objects.filter(
                codename__in=["change_globalsettings", "view_globalsettings", "view_maintenancerequest"]
            )
        )
        self.client.force_login(self.user)
        self.tenant = Tenant.objects.create(
            first_name="Media",
            last_name="Tenant",
            phone="+923001234567",
            cnic="37405-1234567-1",
        )
        self.property = Property.objects.create(
            property_name="Media Property",
            owner_name="Owner",
            owner_cnic="37405-7654321-1",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="M-01",
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

    def _pending(self, filename="approval-source.pdf", **overrides):
        values = {
            "phone": self.tenant.phone,
            "file": ContentFile(b"%PDF-1.4 test media", name=filename),
            "original_filename": filename,
            "media_type": "application/pdf",
            "purpose": PendingWhatsAppMedia.PURPOSE_OTHER,
            "tenant": self.tenant,
            "lease": self.lease,
            "property": self.property,
            "unit": self.unit,
        }
        values.update(overrides)
        return PendingWhatsAppMedia.objects.create(**values)

    def _approve(self, pending, destination=None):
        data = {"media_destination": destination} if destination is not None else {}
        return self.client.post(
            reverse("core:pending_approval_approve", args=["media", pending.pk]),
            data,
            follow=True,
        )

    def test_missing_maintenance_media_returns_json_error_without_creating_ticket(self):
        media = PendingWhatsAppMedia.objects.create(
            phone=self.tenant.phone,
            file="whatsapp/pending/missing-maintenance-photo.jpg",
            original_filename="missing-maintenance-photo.jpg",
            media_type="image/jpeg",
            purpose=PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
        )
        pending = PendingWhatsAppMaintenance.objects.create(
            phone=self.tenant.phone,
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            issue_type="Leak",
            description="Pipe is leaking.",
        )
        pending.media.add(media)

        response = self.client.post(
            reverse("core:pending_approval_approve", args=["maintenance", pending.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertIn("missing from storage", response.json()["message"])
        pending.refresh_from_db()
        self.assertIsNone(pending.created_request)

    def test_processing_maintenance_media_waits_instead_of_reporting_missing(self):
        media = PendingWhatsAppMedia.objects.create(
            phone=self.tenant.phone,
            file="whatsapp/pending/processing/tenant-video.mp4",
            original_filename="tenant-video.mp4",
            media_type="video",
            purpose=PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            processing=True,
        )
        pending = PendingWhatsAppMaintenance.objects.create(
            phone=self.tenant.phone,
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            issue_type="Other",
            description="Video attached.",
        )
        pending.media.add(media)

        response = self.client.post(
            reverse("core:pending_approval_approve", args=["maintenance", pending.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("still downloading", response.json()["message"])
        self.assertNotIn("missing from storage", response.json()["message"])
        pending.refresh_from_db()
        self.assertEqual(pending.status, PendingWhatsAppMaintenance.STATUS_PENDING)
        self.assertIsNone(pending.created_request)

    @patch("whatsapp.services.queue.enqueue_pending_media_download")
    def test_stale_processing_media_can_be_retried(self, enqueue_download):
        media = PendingWhatsAppMedia.objects.create(
            phone=self.tenant.phone,
            file="whatsapp/pending/processing/tenant-video.mp4",
            original_filename="tenant-video.mp4",
            media_type="video",
            whatsapp_media_id="media-123",
            processing=True,
        )
        PendingWhatsAppMedia.objects.filter(pk=media.pk).update(
            updated_at=timezone.now() - timedelta(minutes=5)
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("core:retry_pending_media_download", args=[media.pk])
            )

        media.refresh_from_db()
        self.assertRedirects(
            response,
            reverse("core:pending_approval_detail", args=["media", media.pk]),
        )
        self.assertTrue(media.processing)
        self.assertIn("Download retry requested.", media.ai_notes)
        enqueue_download.assert_called_once_with(media.pk)

    @patch("whatsapp.services.queue.enqueue_pending_media_download")
    def test_recent_processing_media_is_not_queued_twice(self, enqueue_download):
        media = PendingWhatsAppMedia.objects.create(
            phone=self.tenant.phone,
            file="whatsapp/pending/processing/tenant-video.mp4",
            original_filename="tenant-video.mp4",
            media_type="video",
            whatsapp_media_id="media-123",
            processing=True,
        )

        response = self.client.post(
            reverse("core:retry_pending_media_download", args=[media.pk])
        )

        self.assertRedirects(
            response,
            reverse("core:pending_approval_detail", args=["media", media.pk]),
        )
        enqueue_download.assert_not_called()

    def _jpeg_upload(self, name="frame.jpg", color="blue"):
        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (80, 60), color).save(buffer, format="JPEG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")

    def test_selected_whatsapp_video_frames_are_saved_as_pending_photos(self):
        video = self._pending(
            filename="unit-walkthrough.mp4",
            file=ContentFile(b"test video bytes", name="unit-walkthrough.mp4"),
            media_type="video",
            purpose=PendingWhatsAppMedia.PURPOSE_UNIT,
            target_kind=PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
        )

        response = self.client.post(
            reverse("core:save_pending_video_frames", args=[video.pk]),
            {"frames": [self._jpeg_upload("first.jpg"), self._jpeg_upload("second.jpg", "red")]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        video.refresh_from_db()
        self.assertIsNotNone(video.batch_key)
        frames = PendingWhatsAppMedia.objects.filter(
            batch_key=video.batch_key,
            media_type="image",
        ).order_by("pk")
        self.assertEqual(frames.count(), 2)
        self.assertTrue(all("[Extracted video frame]" in frame.ai_notes for frame in frames))
        self.assertTrue(all(frame.file.storage.exists(frame.file.name) for frame in frames))

        detail = self.client.get(
            reverse("core:pending_approval_detail", args=["media", video.pk])
        )
        self.assertContains(detail, "Extract More Photos")
        self.assertContains(detail, 'name="selected_media_ids"', count=3)
        self.assertContains(detail, 'name="media_selection" value="explicit"')

    def test_explicit_frame_approval_attaches_only_selected_photos(self):
        video = self._pending(
            filename="unit-walkthrough.mp4",
            file=ContentFile(b"test video bytes", name="unit-walkthrough.mp4"),
            media_type="video",
            purpose=PendingWhatsAppMedia.PURPOSE_UNIT,
            target_kind=PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
        )
        response = self.client.post(
            reverse("core:save_pending_video_frames", args=[video.pk]),
            {"frames": [self._jpeg_upload()]},
        )
        frame_id = response.json()["created_ids"][0]

        approval = self.client.post(
            reverse("core:pending_approval_approve", args=["media", video.pk]),
            {
                "media_destination": f"unit_photo:{self.unit.pk}",
                "media_selection": "explicit",
                "selected_media_ids": [str(frame_id)],
            },
        )

        self.assertEqual(approval.status_code, 302)
        video.refresh_from_db()
        frame = PendingWhatsAppMedia.objects.get(pk=frame_id)
        self.assertEqual(video.status, PendingWhatsAppMedia.STATUS_REJECTED)
        self.assertEqual(frame.status, PendingWhatsAppMedia.STATUS_APPROVED)
        self.assertEqual(self.unit.media_files.filter(file_type="image").count(), 1)

    @override_settings(WHATSAPP_MAX_INBOUND_VIDEO_BYTES=32 * 1024 * 1024)
    @patch("whatsapp.services.whatsapp.WhatsAppService.download_media_to_file")
    def test_background_video_download_uses_large_streaming_limit(self, download_to_file):
        from whatsapp.tasks import download_pending_media

        video = PendingWhatsAppMedia.objects.create(
            phone=self.tenant.phone,
            file="whatsapp/pending/processing/large-video.mp4",
            original_filename="large-video.mp4",
            media_type="video",
            whatsapp_media_id="video-123",
            purpose=PendingWhatsAppMedia.PURPOSE_UNIT,
            unit=self.unit,
            processing=True,
        )

        def streamed_download(media_id, destination, max_bytes):
            self.assertEqual(media_id, "video-123")
            self.assertEqual(max_bytes, 32 * 1024 * 1024)
            destination.write(b"streamed video")
            return {"downloaded_size": len(b"streamed video")}

        download_to_file.side_effect = streamed_download
        download_pending_media(video.pk)

        video.refresh_from_db()
        self.assertFalse(video.processing)
        self.assertTrue(video.file.storage.exists(video.file.name))
        self.assertIn("Downloaded WhatsApp media size", video.ai_notes)

    def test_multiple_pending_videos_are_all_preserved_and_rendered(self):
        media_rows = []
        for index in range(3):
            media_rows.append(
                PendingWhatsAppMedia.objects.create(
                    phone=self.tenant.phone,
                    file=ContentFile(b"video-bytes-" + str(index).encode(), name=f"clip-{index}.mp4"),
                    original_filename=f"clip-{index}.mp4",
                    media_type="video/mp4",
                    whatsapp_media_id=f"provider-video-{index}",
                    purpose=PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
                    tenant=self.tenant, lease=self.lease, property=self.property, unit=self.unit,
                )
            )
        pending = PendingWhatsAppMaintenance.objects.create(
            phone=self.tenant.phone, tenant=self.tenant, lease=self.lease,
            property=self.property, unit=self.unit, issue_type="Other",
            description="Three maintenance videos.",
        )
        pending.media.add(*media_rows)

        response = self.client.post(
            reverse("core:pending_approval_approve", args=["maintenance", pending.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        pending.refresh_from_db()
        final_media = list(pending.created_request.media.order_by("source_order", "id"))
        self.assertEqual(len(final_media), 3)
        self.assertEqual([m.source_order for m in final_media], [1, 2, 3])
        self.assertEqual([m.source_provider_media_id for m in final_media], [
            "provider-video-0", "provider-video-1", "provider-video-2"
        ])
        detail = self.client.get(reverse("maintenance:request_detail", args=[pending.created_request_id]))
        self.assertContains(detail, "<video", count=3)
        self.assertContains(detail, "3 attachments")

    def test_duplicate_provider_media_id_is_not_copied_twice(self):
        first = self._pending(
            filename="same-name.mp4", file=ContentFile(b"video-one", name="same-name.mp4"),
            media_type="video/mp4", purpose=PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
            whatsapp_media_id="same-provider-id",
        )
        second = self._pending(
            filename="same-name-duplicate.mp4", file=ContentFile(b"video-two", name="same-name-duplicate.mp4"),
            media_type="video/mp4", purpose=PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
            whatsapp_media_id="same-provider-id",
        )
        pending = PendingWhatsAppMaintenance.objects.create(
            phone=self.tenant.phone, tenant=self.tenant, lease=self.lease,
            property=self.property, unit=self.unit, issue_type="Other", description="Duplicate provider media.",
        )
        pending.media.add(first, second)
        response = self.client.post(
            reverse("core:pending_approval_approve", args=["maintenance", pending.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        pending.refresh_from_db()
        self.assertEqual(pending.created_request.media.count(), 1)
        second.refresh_from_db()
        self.assertIn("Duplicate provider media ID skipped", second.ai_notes)

    def test_approved_maintenance_media_is_renamed_and_keeps_original_name(self):
        media = self._pending(
            filename="tenant-leak-photo.jpg",
            media_type="image/jpeg",
            purpose=PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        )
        pending = PendingWhatsAppMaintenance.objects.create(
            phone=self.tenant.phone,
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            issue_type="Leak",
            description="Pipe is leaking.",
        )
        pending.media.add(media)

        response = self.client.post(
            reverse("core:pending_approval_approve", args=["maintenance", pending.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        pending.refresh_from_db()
        approved_media = pending.created_request.media.get()
        self.assertEqual(approved_media.original_filename, "tenant-leak-photo.jpg")
        self.assertIn("Media-Property_M-01_", approved_media.display_filename)
        self.assertEqual(approved_media.file_size, len(b"%PDF-1.4 test media"))

    def test_missing_source_file_can_be_approved_with_warning(self):
        pending = PendingWhatsAppMedia.objects.create(
            phone=self.tenant.phone,
            file="whatsapp/pending/missing-source.pdf",
            original_filename="missing-source.pdf",
            purpose=PendingWhatsAppMedia.PURPOSE_LEASE,
            target_kind=PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT,
            lease=self.lease,
        )

        response = self._approve(
            pending,
            f"lease_document:{self.lease.pk}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "missing source file(s) were marked approved but could not be attached to the destination",
        )

    def test_media_can_be_moved_to_pending_maintenance(self):
        pending = self._pending(
            filename="leaking-pipe.jpg",
            media_type="image/jpeg",
            ai_notes="Water leak under the sink.",
        )

        response = self.client.post(
            reverse(
                "core:pending_approval_approve",
                args=["media", pending.pk],
            ),
            {"media_destination": "maintenance"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        maintenance = PendingWhatsAppMaintenance.objects.get()
        pending.refresh_from_db()
        self.assertEqual(
            pending.purpose,
            PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
        )
        self.assertEqual(pending.status, PendingWhatsAppMedia.STATUS_PENDING)
        self.assertEqual(maintenance.lease, self.lease)
        self.assertEqual(maintenance.unit, self.unit)
        self.assertTrue(maintenance.media.filter(pk=pending.pk).exists())
        self.assertIn("Water", maintenance.issue_type)

    def test_pending_list_shows_media_attachment_and_move_column(self):
        pending = self._pending(
            filename="maintenance-photo.jpg",
            media_type="image/jpeg",
        )

        response = self.client.get(reverse("core:pending_approvals"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Attachment")
        self.assertContains(response, "Move To")
        self.assertContains(response, pending.file.url)
        self.assertContains(response, "Maintenance")

    def test_missing_source_file_records_approval_without_destination_document(self):
        from leases.models import LeaseDocument

        pending = PendingWhatsAppMedia.objects.create(
            phone=self.tenant.phone,
            file="whatsapp/pending/missing-status-source.pdf",
            original_filename="missing-status-source.pdf",
            purpose=PendingWhatsAppMedia.PURPOSE_LEASE,
            target_kind=PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT,
            lease=self.lease,
        )

        self._approve(pending, f"lease_document:{self.lease.pk}")

        pending.refresh_from_db()
        self.assertEqual(pending.status, PendingWhatsAppMedia.STATUS_APPROVED)
        self.assertIsNotNone(pending.approved_at)
        self.assertEqual(pending.approved_by, self.user)
        self.assertIn("Approved without destination attachment", pending.ai_notes)
        self.assertFalse(LeaseDocument.objects.filter(lease=self.lease).exists())

    def test_other_media_without_target_cannot_be_approved(self):
        pending = self._pending(lease=None, property=None, unit=None)

        response = self._approve(pending)

        pending.refresh_from_db()
        self.assertEqual(pending.status, PendingWhatsAppMedia.STATUS_PENDING)
        self.assertContains(response, "Choose Lease Gallery, Lease Document, Property Photo, or Unit Photo before approval.")

    def test_successful_lease_gallery_approval_creates_lease_media(self):
        pending = self._pending(
            filename="lease-gallery.mp4",
            media_type="video",
        )

        self._approve(pending, f"lease_photo:{self.lease.pk}")

        pending.refresh_from_db()
        media = LeaseMedia.objects.get(lease=self.lease)
        self.assertTrue(media.file.storage.exists(media.file.name))
        self.assertEqual(pending.status, PendingWhatsAppMedia.STATUS_APPROVED)

    def test_successful_property_approval_creates_property_media(self):
        from properties.models import PropertyMedia

        pending = self._pending()

        self._approve(pending, f"property_photo:{self.property.pk}")

        pending.refresh_from_db()
        media = PropertyMedia.objects.get(property=self.property)
        self.assertTrue(media.file.storage.exists(media.file.name))
        self.assertEqual(pending.status, PendingWhatsAppMedia.STATUS_APPROVED)

    def test_successful_unit_approval_creates_unit_media(self):
        from properties.models import UnitMedia

        pending = self._pending(filename="unit-source.pdf")

        self._approve(pending, f"unit_photo:{self.unit.pk}")

        pending.refresh_from_db()
        media = UnitMedia.objects.get(unit=self.unit)
        self.assertTrue(media.file.storage.exists(media.file.name))
        self.assertEqual(pending.status, PendingWhatsAppMedia.STATUS_APPROVED)

    def test_successful_lease_document_approval_creates_lease_document(self):
        from leases.models import LeaseDocument

        pending = self._pending(filename="lease-document.pdf")

        self._approve(pending, f"lease_document:{self.lease.pk}")

        pending.refresh_from_db()
        document = LeaseDocument.objects.get(lease=self.lease)
        self.assertTrue(document.file.storage.exists(document.file.name))
        self.assertEqual(pending.status, PendingWhatsAppMedia.STATUS_APPROVED)

    def test_successful_estamp_approval_validates_and_creates_estamp_document(self):
        from leases.models import LeaseDocument

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        payload = BytesIO()
        writer.write(payload)
        submitter = get_user_model().objects.create_user(
            "estamp-submitter",
            email="estamp-submitter@example.com",
            is_staff=True,
        )
        pending = self._pending(
            filename="received-estamp.pdf",
            file=ContentFile(payload.getvalue(), name="received-estamp.pdf"),
            purpose=PendingWhatsAppMedia.PURPOSE_LEASE,
            target_kind=PendingWhatsAppMedia.TARGET_LEASE_ESTAMP,
            submitted_by_staff=submitter,
        )

        response = self._approve(
            pending,
            f"{PendingWhatsAppMedia.TARGET_LEASE_ESTAMP}:{self.lease.pk}",
        )

        self.assertEqual(response.status_code, 200)
        pending.refresh_from_db()
        document = LeaseDocument.objects.get(lease=self.lease)
        self.assertEqual(document.category, "estamp_paper")
        self.assertEqual(document.uploaded_by, submitter)
        self.assertIn("StampPaper", document.original_filename)
        self.assertTrue(document.file.storage.exists(document.file.name))
        self.assertEqual(pending.status, PendingWhatsAppMedia.STATUS_APPROVED)

    def test_destination_storage_failure_rolls_back_approval_status(self):
        from properties.models import PropertyMedia

        pending = self._pending(filename="storage-failure.pdf")

        with patch(
            "properties.models.PropertyMedia.objects.create",
            side_effect=OSError("storage unavailable"),
        ):
            response = self._approve(pending, f"property_photo:{self.property.pk}")

        pending.refresh_from_db()
        self.assertEqual(pending.status, PendingWhatsAppMedia.STATUS_PENDING)
        self.assertFalse(PropertyMedia.objects.filter(property=self.property).exists())
        self.assertContains(response, "The media could not be saved to the selected destination.")

    def test_other_media_can_be_reclassified_to_pending_payment(self):
        pending = self._pending(filename="payment-receipt.jpg", media_type="image")
        ocr_result = {
            "amount": Decimal("63580.00"),
            "date": timezone.datetime(2026, 7, 20).date(),
            "reference": "718126681061",
            "confidence": 96,
            "bank_information": {"channel": "Bank Transfer"},
        }

        with patch("whatsapp.services.media_processor.run_payment_ocr", return_value=ocr_result):
            response = self._approve(pending, "payment_receipt")

        pending.refresh_from_db()
        payment = PendingWhatsAppPayment.objects.get(screenshot=pending.file.name)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending.status, PendingWhatsAppMedia.STATUS_PENDING)
        self.assertEqual(pending.purpose, PendingWhatsAppMedia.PURPOSE_PAYMENT)
        self.assertEqual(payment.amount, Decimal("63580.00"))
        self.assertEqual(payment.lease, self.lease)
        self.assertEqual(payment.status, PendingWhatsAppPayment.STATUS_PENDING)

    def test_pending_payment_screenshot_can_be_reclassified_to_unit_photo(self):
        media = self._pending(
            filename="not-a-payment.jpg",
            media_type="image",
            purpose=PendingWhatsAppMedia.PURPOSE_PAYMENT,
        )
        payment = PendingWhatsAppPayment.objects.create(
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            phone=self.tenant.phone,
            screenshot=media.file.name,
            amount=Decimal("1000.00"),
        )

        response = self.client.post(
            reverse("core:pending_approval_approve", args=["payment", payment.pk]),
            {
                "approval_action": "reclassify",
                "reclassify_destination": f"unit_photo:{self.unit.pk}",
            },
        )

        media.refresh_from_db()
        payment.refresh_from_db()
        self.assertRedirects(
            response,
            reverse("core:pending_approval_detail", args=["media", media.pk]),
        )
        self.assertEqual(media.purpose, PendingWhatsAppMedia.PURPOSE_UNIT)
        self.assertEqual(media.target_kind, PendingWhatsAppMedia.TARGET_UNIT_PHOTO)
        self.assertEqual(media.status, PendingWhatsAppMedia.STATUS_PENDING)
        self.assertTrue(payment.rejected)
        self.assertEqual(payment.status, PendingWhatsAppPayment.STATUS_REJECTED)

    def test_payment_approval_posts_payment_and_sends_whatsapp_confirmation(self):
        pending = PendingWhatsAppPayment.objects.create(
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            phone=self.tenant.phone,
            amount=Decimal("63580.00"),
            date=timezone.datetime(2026, 7, 20).date(),
            reference="718126681061",
        )

        with patch("whatsapp.services.whatsapp.WhatsAppService.send_payment_confirmation") as send_confirmation:
            response = self.client.post(
                reverse("core:pending_approval_approve", args=["payment", pending.pk]),
            )

        pending.refresh_from_db()
        self.assertRedirects(response, reverse("core:pending_approvals"))
        self.assertTrue(pending.approved)
        self.assertIsNotNone(pending.created_payment)
        send_confirmation.assert_called_once()
        self.assertEqual(send_confirmation.call_args.kwargs["phone_number"], self.tenant.phone)
        self.assertIn("Rs. 63,580.00", send_confirmation.call_args.kwargs["message"])

    def test_pending_payment_detail_shows_ocr_and_tenant_confirmed_values(self):
        pending = PendingWhatsAppPayment.objects.create(
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            phone=self.tenant.phone,
            amount=Decimal("63580.00"),
            date=timezone.datetime(2026, 7, 20).date(),
            ocr_json={
                "ocr_amount": "6.00",
                "tenant_amount": "63580",
                "ocr_date": "2026-07-20",
                "tenant_date": "2026-07-20",
            },
        )

        response = self.client.get(
            reverse("core:pending_approval_detail", args=["payment", pending.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OCR Amount")
        self.assertContains(response, "Rs. 6.00")
        self.assertContains(response, "Tenant Amount")
        self.assertContains(response, "Rs. 63580")


class WhatsAppAssistantIntentTests(SimpleTestCase):
    def test_tenant_self_service_intents(self):
        cases = {
            "inspection sheet": "inspection",
            "send latest invoice": "latest_invoice",
            "need payment receipt": "payment_receipt",
            "agreement copy": "agreement",
            "maintenance status": "maintenance_status",
            "meter reading": "meter",
            "family members": "family",
            "police verification": "police_verification",
            "i want to move out": "move_out",
            "renew my lease": "renewal",
            "remaining rent": "balance",
        }
        for text, intent in cases.items():
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text), intent)

    def test_bathroom_is_not_classified_as_available_room(self):
        self.assertEqual(detect_intent("bathroom pipe leaking"), "maintenance")
        self.assertEqual(detect_intent("bedroom light kharab"), "maintenance")
        self.assertEqual(detect_intent("available unit"), "availability")

    def test_payment_text_parser_understands_yesterday(self):
        parsed = extract_payment_text_fields("paid Rs 12000 yesterday ref ABCD12345")
        self.assertEqual(parsed["amount"], Decimal("12000"))
        self.assertEqual(parsed["date"], timezone.localdate() - timedelta(days=1))
        self.assertEqual(parsed["reference"], "ABCD12345")

    def test_openai_receipt_normalizes_currency_amount_and_long_date(self):
        parsed = normalize_openai_receipt(
            {
                "document_type": "bank transfer receipt",
                "is_payment_receipt": True,
                "amount": "Rs. 63,580.00",
                "date": "July 20, 2026 at 10:20",
                "confidence": 97,
            }
        )

        self.assertEqual(parsed["amount"], Decimal("63580.00"))
        self.assertEqual(parsed["date"], timezone.datetime(2026, 7, 20).date())
        self.assertTrue(parsed["is_payment_receipt"])

    def test_explicit_non_payment_image_is_not_classified_as_receipt(self):
        self.assertFalse(
            _ocr_looks_like_payment(
                {
                    "is_payment_receipt": False,
                    "document_type": "lease document",
                    "amount": Decimal("63580.00"),
                    "confidence": 99,
                }
            )
        )

    def test_multiple_requests_produce_multiple_tool_calls(self):
        decision = fallback_decision("Mera balance aur last payment bata dein aur bathroom mein pani leak ho raha hai")
        self.assertEqual(
            [item.name for item in decision.tool_calls],
            ["get_tenant_balance", "get_last_payment", "create_maintenance_draft"],
        )

    def test_provider_failure_uses_rules(self):
        config = WhatsAppAIConfig(True, "openai", "test-model", "basic", False, True, routing_enabled=True)
        orchestrator = WhatsAppAIOrchestrator(config)
        with patch.object(orchestrator, "_openai_decision", side_effect=RuntimeError("offline")):
            decision, fallback_used, error, _usage = orchestrator._decision("my balance", {})
        self.assertTrue(fallback_used)
        self.assertIn("offline", error)
        self.assertEqual(decision.tool_calls[0].name, "get_tenant_balance")

    def test_sensitive_values_are_masked(self):
        masked = mask_sensitive_text("CNIC 37405-1234567-1 and document 12345678901234")
        self.assertNotIn("37405-1234567-1", masked)
        self.assertNotIn("12345678901234", masked)

    def test_ai_cannot_override_server_identity_ids(self):
        context = ToolContext(sender=None, conversation=None, message_log=None, lease=None)
        result = execute_tool("get_tenant_balance", {"tenant_id": 999, "lease_id": 999}, context)
        self.assertFalse(result["ok"])

    def test_unknown_ai_tool_is_rejected(self):
        context = ToolContext(sender=None, conversation=None, message_log=None, lease=None)
        self.assertFalse(execute_tool("django_orm_query", {}, context)["ok"])

    def test_exact_menu_commands_remain_supported(self):
        self.assertEqual(detect_intent("1"), "balance")

    def test_call_request_detection_does_not_ask_preference(self):
        decision = fallback_decision("Please call me")
        self.assertTrue(decision.handover)
        self.assertNotIn("preference", decision.handover_reason.lower())


class WhatsAppDeferredMediaQueueTests(SimpleTestCase):
    @patch("whatsapp.services.queue.threading.Thread")
    @patch("whatsapp.services.queue.get_whatsapp_ai_config")
    def test_local_configuration_uses_thread_for_deferred_media_download(
        self, get_config, thread_class
    ):
        from whatsapp.services.queue import enqueue_pending_media_download

        get_config.return_value = SimpleNamespace(use_celery=False)

        result = enqueue_pending_media_download(42)

        self.assertEqual(result, "thread")
        thread_class.assert_called_once()
        self.assertFalse(thread_class.call_args.kwargs["daemon"])
        thread_class.return_value.start.assert_called_once()

    @patch("whatsapp.services.queue.threading.Thread")
    @patch(
        "whatsapp.tasks.download_pending_media_task.delay",
        side_effect=RuntimeError("Redis unavailable"),
    )
    @patch("whatsapp.services.queue.get_whatsapp_ai_config")
    def test_celery_queue_failure_uses_non_daemon_thread_fallback(
        self, get_config, task_delay, thread_class
    ):
        from whatsapp.services.queue import enqueue_pending_media_download

        get_config.return_value = SimpleNamespace(use_celery=True)

        result = enqueue_pending_media_download(42)

        self.assertEqual(result, "thread")
        task_delay.assert_called_once_with(42)
        self.assertFalse(thread_class.call_args.kwargs["daemon"])
        thread_class.return_value.start.assert_called_once()


@override_settings(
    OPENAI_API_KEY="test-key",
    WHATSAPP_AI_OCR_IMAGE_DETAIL="low",
    WHATSAPP_AI_OCR_HIGH_DETAIL_FALLBACK=True,
    WHATSAPP_AI_OCR_MAX_IMAGE_DIMENSION=1600,
    WHATSAPP_AI_OCR_MAX_OUTPUT_TOKENS=300,
)
class OpenAIReceiptOCRTests(SimpleTestCase):
    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    def _file(self):
        return ContentFile(self.PNG, name="receipt.png")

    def _result(self, **overrides):
        data = {
            "document_type": "payment_receipt",
            "amount": "63580.00",
            "transaction_date": "2026-07-20",
            "reference_id": "0718126681061",
            "recipient_name": None,
            "sender_name": None,
            "bank_name": "Test Bank",
            "confidence": {
                "document_type": 0.99,
                "amount": 0.99,
                "transaction_date": 0.98,
                "reference_id": 0.97,
            },
        }
        data.update(overrides)
        usage = SimpleNamespace(
            input_tokens=120,
            output_tokens=55,
            total_tokens=175,
            input_tokens_details=SimpleNamespace(cached_tokens=10),
        )
        return SimpleNamespace(output_text=json.dumps(data), usage=usage)

    @patch("whatsapp.services.openai_ocr._openai_client")
    def test_clear_receipt_uses_low_detail_once_and_limits_output(self, openai):
        openai.return_value.responses.create.return_value = self._result()

        with self.assertLogs("whatsapp.services.openai_ocr", level="INFO") as logs:
            result = extract_receipt_with_openai(
                self._file(), "gpt-4o-mini", message_id="wamid.low", receipt_expected=True
            )

        self.assertTrue(result["validation"]["is_valid"])
        self.assertEqual(result["reference"], "0718126681061")
        self.assertEqual(openai.return_value.responses.create.call_count, 1)
        request = openai.return_value.responses.create.call_args.kwargs
        self.assertEqual(request["max_output_tokens"], 300)
        self.assertEqual(request["input"][0]["content"][1]["detail"], "low")
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        usage_log = " ".join(logs.output)
        self.assertIn("input_tokens=120", usage_log)
        self.assertIn("cached_tokens=10", usage_log)
        self.assertNotIn("test-key", usage_log)

    @patch("whatsapp.services.openai_ocr._openai_client")
    def test_missing_amount_retries_once_at_high_detail(self, openai):
        openai.return_value.responses.create.side_effect = [
            self._result(amount=None),
            self._result(),
        ]

        result = extract_receipt_with_openai(
            self._file(), "gpt-4o-mini", message_id="wamid.fallback", receipt_expected=True
        )

        calls = openai.return_value.responses.create.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs["input"][0]["content"][1]["detail"], "low")
        self.assertEqual(calls[1].kwargs["input"][0]["content"][1]["detail"], "high")
        self.assertTrue(result["validation"]["is_valid"])
        self.assertTrue(result["fallback_used"])

    @patch("whatsapp.services.openai_ocr._openai_client")
    def test_missing_optional_field_does_not_retry(self, openai):
        openai.return_value.responses.create.return_value = self._result(bank_name=None)

        result = extract_receipt_with_openai(
            self._file(), "gpt-4o-mini", message_id="wamid.optional", receipt_expected=True
        )

        self.assertTrue(result["validation"]["is_valid"])
        self.assertEqual(openai.return_value.responses.create.call_count, 1)

    @patch("whatsapp.services.openai_ocr._openai_client")
    def test_invalid_date_retries_once(self, openai):
        openai.return_value.responses.create.side_effect = [
            self._result(transaction_date="2026-02-31"),
            self._result(),
        ]

        result = extract_receipt_with_openai(
            self._file(), "gpt-4o-mini", message_id="wamid.date", receipt_expected=True
        )

        self.assertEqual(openai.return_value.responses.create.call_count, 2)
        self.assertTrue(result["validation"]["is_valid"])

    @patch("whatsapp.services.openai_ocr._openai_client")
    def test_missing_reference_after_fallback_stays_invalid(self, openai):
        openai.return_value.responses.create.side_effect = [
            self._result(reference_id=None),
            self._result(reference_id=None),
        ]

        result = extract_receipt_with_openai(
            self._file(), "gpt-4o-mini", message_id="wamid.reference", receipt_expected=True
        )

        self.assertEqual(openai.return_value.responses.create.call_count, 2)
        self.assertFalse(result["validation"]["is_valid"])
        self.assertIn("reference_id", result["validation"]["missing_fields"])

    @patch("whatsapp.services.openai_ocr._openai_client")
    def test_malformed_json_falls_back_only_once(self, openai):
        malformed = SimpleNamespace(output_text="not-json", usage=SimpleNamespace())
        openai.return_value.responses.create.side_effect = [malformed, self._result()]

        result = extract_receipt_with_openai(
            self._file(), "gpt-4o-mini", message_id="wamid.json", receipt_expected=True
        )

        self.assertEqual(openai.return_value.responses.create.call_count, 2)
        self.assertTrue(result["validation"]["is_valid"])

    @patch("whatsapp.services.openai_ocr._openai_client")
    def test_quota_error_is_safe_and_does_not_retry_uncontrollably(self, openai):
        openai.return_value.responses.create.side_effect = RuntimeError("insufficient_quota")

        result = extract_receipt_with_openai(
            self._file(), "gpt-4o-mini", message_id="wamid.quota", receipt_expected=True
        )

        self.assertEqual(result["engine"], "unavailable")
        self.assertEqual(openai.return_value.responses.create.call_count, 1)

    def test_validator_normalizes_money_date_and_reference(self):
        result = validate_payment_receipt(
            {
                "amount": "PKR 63,580.00",
                "transaction_date": "20-07-2026",
                "reference_id": " 0718 1266 81061 ",
            }
        )

        self.assertTrue(result["is_valid"])
        self.assertEqual(result["normalized_data"]["amount"], Decimal("63580.00"))
        self.assertEqual(result["normalized_data"]["transaction_date"], "2026-07-20")
        self.assertEqual(result["normalized_data"]["reference"], "0718126681061")


class WhatsAppControlledAssistantTests(TestCase):
    def setUp(self):
        cache.clear()
        self.phone = "+923001112233"
        self.tenant = Tenant.objects.create(first_name="Ahmed", last_name="Khan", phone=self.phone, cnic="37405-1111111-1")
        self.property = Property.objects.create(
            property_name="Test Residency", owner_name="Owner", owner_cnic="37405-2222222-2",
            type="Residential", property_type="apartment", total_units=2,
        )
        self.unit = Unit.objects.create(property=self.property, unit_number="A-04", status="occupied")
        self.lease = Lease.objects.create(
            tenant=self.tenant, unit=self.unit, start_date=timezone.localdate() - timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=335), monthly_rent=Decimal("25000"), status="active",
        )
        User = get_user_model()
        self.staff1 = User.objects.create_user(
            "accounts1", email="accounts1@example.com", whatsapp_number="+923009990001", is_staff=True
        )
        self.staff2 = User.objects.create_user(
            "accounts2", email="accounts2@example.com", whatsapp_number="+923009990002", is_staff=True
        )
        self.unauthorized = User.objects.create_user(
            "outsider", email="outsider@example.com", whatsapp_number="+923009990003", is_staff=True
        )
        WhatsAppStaffRoutingRule.objects.create(property=self.property, department="general", staff_user=self.staff1, priority=1)
        WhatsAppStaffRoutingRule.objects.create(property=self.property, department="general", staff_user=self.staff2, priority=2)
        self.conversation = WhatsAppConversation.objects.create(
            phone_number=self.phone, tenant=self.tenant, selected_lease=self.lease,
            selected_mode=WhatsAppConversation.MODE_TENANT,
            mode_expires_at=timezone.now() + timedelta(hours=1),
        )
        self.message = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND, phone_number=self.phone,
            wa_message_id="wamid.base", message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED, payload={"type": "text", "text": {"body": "Human please"}},
        )
        self.handover = WhatsAppHandover.objects.create(
            conversation=self.conversation, tenant=self.tenant, lease=self.lease,
            property=self.property, unit=self.unit, tenant_phone=self.phone,
            reason="Requested staff", tenant_message="Human please", department="general",
        )

    def test_tenant_only_sender_resolves_active_lease(self):
        sender = resolve_sender(self.phone)
        self.assertTrue(sender.has_active_tenant)
        self.assertFalse(sender.has_staff)
        self.assertEqual(sender.active_leases, [self.lease])

    def test_tenant_unit_photo_command_returns_fixed_no_login_link(self):
        from properties.public_upload_links import read_unit_photo_upload_token

        self.handover.status = WhatsAppHandover.STATUS_RESOLVED
        self.handover.save(update_fields=["status", "updated_at"])
        self.message.payload = {
            "type": "text",
            "text": {"body": "upload unit photos"},
        }
        self.message.save(update_fields=["payload", "updated_at"])

        response, intent, metadata = WhatsAppAIAssistant(
            service=MagicMock()
        )._handle(self.message, self.conversation)

        self.assertEqual(intent, "unit_photo_upload_link")
        self.assertIn("No login is required", response)
        self.assertIn(self.property.property_name, response)
        self.assertIn(self.unit.unit_number, response)
        token_match = re.search(
            r"/properties/public/unit-photo-upload/([^/\s]+)/",
            response,
        )
        self.assertIsNotNone(token_match)
        token = token_match.group(1)
        token_data = read_unit_photo_upload_token(token)
        self.assertEqual(token_data["lease_id"], self.lease.pk)
        self.assertEqual(token_data["unit_id"], self.unit.pk)
        self.assertEqual(metadata["lease"], self.lease)

    def test_staff_unit_target_returns_forwardable_lease_bound_link(self):
        WhatsAppStaffPropertyAccess.objects.create(
            staff_user=self.staff1,
            property=self.property,
            is_active=True,
        )
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
            context={
                "staff_upload_kind": PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
            },
        )

        response = WhatsAppAIAssistant(service=MagicMock())._select_staff_upload_target(
            self.message,
            conversation,
            self.staff1,
            {
                "type": "unit",
                "id": self.unit.pk,
                "label": f"{self.property.property_name} / {self.unit.unit_number}",
            },
        )

        conversation.refresh_from_db()
        self.assertIn("Confirm upload target", response)
        response = WhatsAppAIAssistant(service=MagicMock())._confirm_staff_upload_target(
            self.message,
            conversation,
            self.staff1,
        )
        conversation.refresh_from_db()
        self.assertIn("Secure gallery upload link", response)
        self.assertIn("no login required", response)
        self.assertIn("forward it to the tenant", response)
        self.assertEqual(
            conversation.context["staff_upload_lease_id"],
            self.lease.pk,
        )

    def test_staff_menu_registration_option_sends_public_registration_link(self):
        self.assertIn("12. New Tenant Registration", staff_menu_text(self.staff1))
        tenant_count = Tenant.objects.count()
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
        )
        message = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.staff.new-tenant-registration",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "12"}},
        )

        response, intent, _metadata = WhatsAppAIAssistant(service=MagicMock())._handle(
            message, conversation
        )

        self.assertEqual(intent, "staff")
        self.assertIn("Tenant registration link created", response)
        self.assertIn(reverse("tenants:tenant_public_registration_new"), response)
        self.assertIn("Pending Approval", response)
        self.assertEqual(Tenant.objects.count(), tenant_count)

    @patch("whatsapp.services.whatsapp_ai.notify_staff_pending_request")
    @patch("whatsapp.services.whatsapp_ai.inspect_estamp_pdf")
    def test_direct_staff_estamp_matches_property_and_waits_for_approval(
        self, inspect_estamp, notify_pending
    ):
        WhatsAppStaffPropertyAccess.objects.create(
            staff_user=self.staff1,
            property=self.property,
            is_active=True,
        )
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
        )
        document_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.staff.estamp.document",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_DOCUMENT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={
                "type": "document",
                "document": {
                    "filename": "estamp.pdf",
                    "mime_type": "application/pdf",
                },
            },
        )
        staged = PendingWhatsAppMedia.objects.create(
            conversation=conversation,
            original_whatsapp_message=document_log,
            phone=self.staff1.whatsapp_number,
            file=ContentFile(b"%PDF-1.4 staged", name="estamp.pdf"),
            original_filename="estamp.pdf",
            media_type="document",
        )
        document_log.api_response = {"simulator_pending_media_id": staged.pk}
        document_log.save(update_fields=["api_response"])
        inspect_estamp.return_value = {
            "page_count": 1,
            "notes_text": "Notes: Property Test Residency, Unit A-04",
            "source": "embedded_pdf_text",
            "is_estamp": True,
        }
        assistant = WhatsAppAIAssistant(service=MagicMock())

        response, intent, _metadata = assistant._handle_media_message(
            document_log,
            conversation,
            "",
            "document",
            resolve_sender(
                self.staff1.whatsapp_number,
                conversation=conversation,
            ),
        )

        conversation.refresh_from_db()
        staged.refresh_from_db()
        self.assertEqual(intent, "staff_estamp_lease_confirm")
        self.assertIn("Test Residency", response)
        self.assertIn("Current tenant: Ahmed Khan", response)
        self.assertEqual(
            conversation.pending_state, "staff_estamp_lease_confirm"
        )
        self.assertEqual(
            staged.target_kind, PendingWhatsAppMedia.TARGET_LEASE_ESTAMP
        )
        self.assertEqual(staged.status, PendingWhatsAppMedia.STATUS_PENDING)
        self.assertIsNone(staged.lease)

        lease_confirm = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.staff.estamp.lease-confirm",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "YES"}},
        )
        response, intent, _metadata = assistant._handle(
            lease_confirm, conversation
        )

        staged.refresh_from_db()
        conversation.refresh_from_db()
        self.assertEqual(intent, "staff_estamp_submitted")
        self.assertIn("administrator approval", response)
        self.assertEqual(staged.lease, self.lease)
        self.assertEqual(staged.property, self.property)
        self.assertEqual(staged.unit, self.unit)
        self.assertEqual(staged.tenant, self.tenant)
        self.assertEqual(staged.status, PendingWhatsAppMedia.STATUS_PENDING)
        self.assertEqual(conversation.pending_state, "")
        notify_pending.assert_called_once_with("upload", staged)

    def test_estamp_property_match_lists_multiple_current_tenants_inline(self):
        WhatsAppStaffPropertyAccess.objects.create(
            staff_user=self.staff1,
            property=self.property,
            is_active=True,
        )
        second_tenant = Tenant.objects.create(
            first_name="Sara",
            last_name="Ali",
            phone="+923001112244",
            cnic="37405-1111111-2",
        )
        second_lease = Lease.objects.create(
            tenant=second_tenant,
            unit=self.unit,
            start_date=timezone.localdate() - timedelta(days=5),
            end_date=timezone.localdate() + timedelta(days=360),
            monthly_rent=Decimal("27000"),
            status="active",
        )
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
        )

        response, intent, _metadata = WhatsAppAIAssistant(
            service=MagicMock()
        )._set_estamp_property_confirmation(
            conversation,
            self.staff1,
            self.property,
            candidate_unit=self.unit,
            source_label="your entry",
        )

        conversation.refresh_from_db()
        self.assertEqual(intent, "staff_estamp_lease_selection")
        self.assertEqual(
            conversation.pending_state, "staff_estamp_lease_selection"
        )
        self.assertIn("Property: Test Residency", response)
        self.assertIn("Current tenant: Ahmed Khan", response)
        self.assertIn("Current tenant: Sara Ali", response)
        self.assertIn("Reply with a number or CANCEL", response)
        self.assertEqual(
            set(conversation.context["staff_estamp_lease_options"]),
            {self.lease.pk, second_lease.pk},
        )

    def test_password_protected_estamp_prompts_then_saves_unlocked_pdf(self):
        WhatsAppStaffPropertyAccess.objects.create(
            staff_user=self.staff1,
            property=self.property,
            is_active=True,
        )
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
            pending_state="staff_waiting_upload",
        )
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt("correct-secret")
        encrypted = BytesIO()
        writer.write(encrypted)
        document_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.staff.estamp.encrypted",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_DOCUMENT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={
                "type": "document",
                "document": {
                    "filename": "protected-estamp.pdf",
                    "mime_type": "application/pdf",
                },
            },
        )
        staged = PendingWhatsAppMedia.objects.create(
            conversation=conversation,
            original_whatsapp_message=document_log,
            phone=self.staff1.whatsapp_number,
            file=ContentFile(
                encrypted.getvalue(), name="protected-estamp.pdf"
            ),
            original_filename="protected-estamp.pdf",
            media_type="document",
        )
        document_log.api_response = {"simulator_pending_media_id": staged.pk}
        document_log.save(update_fields=["api_response"])
        assistant = WhatsAppAIAssistant(service=MagicMock())

        response, intent, _metadata = assistant._handle_media_message(
            document_log,
            conversation,
            "",
            "document",
            resolve_sender(
                self.staff1.whatsapp_number,
                conversation=conversation,
            ),
        )

        conversation.refresh_from_db()
        staged.refresh_from_db()
        self.assertEqual(intent, "staff_estamp_password")
        self.assertIn("enter the PDF password", response)
        self.assertEqual(conversation.pending_state, "staff_estamp_password")
        self.assertEqual(
            conversation.context["staff_estamp_pending_media_id"], staged.pk
        )
        self.assertEqual(staged.status, PendingWhatsAppMedia.STATUS_PENDING)

        password_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.staff.estamp.password",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={
                "type": "text",
                "text": {"body": "correct-secret"},
            },
        )
        response, intent, _metadata = assistant._handle(
            password_log, conversation
        )

        password_log.refresh_from_db()
        conversation.refresh_from_db()
        staged.refresh_from_db()
        self.assertEqual(intent, "staff_estamp_property_lookup")
        self.assertIn("identify the property", response)
        self.assertEqual(
            password_log.payload["text"]["body"], "[PDF password redacted]"
        )
        self.assertNotIn("correct-secret", str(conversation.context))
        staged.file.open("rb")
        try:
            unlocked_reader = PdfReader(staged.file, strict=False)
            self.assertFalse(unlocked_reader.is_encrypted)
            self.assertEqual(len(unlocked_reader.pages), 1)
        finally:
            staged.file.close()

    def test_upload_documents_starts_fresh_and_clears_previous_batch_target(self):
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
            context={
                "staff_upload_kind": PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT,
                "staff_upload_batch_key": str(uuid.uuid4()),
                "staff_upload_property_id": self.property.pk,
                "staff_upload_unit_id": self.unit.pk,
                "staff_upload_lease_id": self.lease.pk,
                "staff_upload_target_label": "Old upload target",
            },
        )
        message = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.staff.upload-documents.fresh",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "7"}},
        )
        assistant = WhatsAppAIAssistant(service=MagicMock())

        response = assistant._handle_staff_message(
            message,
            conversation,
            "7",
            "text",
            resolve_sender(
                self.staff1.whatsapp_number,
                conversation=conversation,
            ),
        )

        conversation.refresh_from_db()
        self.assertIn("send the image or document", response)
        self.assertEqual(conversation.pending_state, "staff_waiting_upload")
        for key in (
            "staff_upload_kind",
            "staff_upload_batch_key",
            "staff_upload_property_id",
            "staff_upload_unit_id",
            "staff_upload_lease_id",
            "staff_upload_target_label",
        ):
            self.assertNotIn(key, conversation.context)

    def test_estamp_property_and_unit_matching_are_scoped_and_label_aware(self):
        other_property = Property.objects.create(
            property_name="Other Residency",
            owner_name="Other Owner",
            owner_cnic="37405-3333333-3",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        other_unit = Unit.objects.create(
            property=other_property,
            unit_number="7",
            status="occupied",
        )

        property_matches = match_properties(
            "Notes: Test Residency, Room A-04",
            [self.property, other_property],
        )
        unit_matches = match_unit(
            "Notes: Test Residency, Room A-04",
            [self.unit, other_unit],
        )

        self.assertEqual(
            [item["property"] for item in property_matches],
            [self.property],
        )
        self.assertEqual(unit_matches, [self.unit])

    def test_tenant_payment_receipt_is_ocr_read_and_staged_for_approval(self):
        self.conversation.pending_state = "tenant_waiting_payment_receipt"
        self.conversation.save(update_fields=["pending_state", "updated_at"])
        image_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.phone,
            wa_message_id="wamid.payment.receipt.ocr",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_IMAGE,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "image", "image": {"caption": "", "filename": "receipt.jpg"}},
        )
        staged = PendingWhatsAppMedia.objects.create(
            conversation=self.conversation,
            original_whatsapp_message=image_log,
            phone=self.phone,
            file=ContentFile(b"jpg", name="receipt.jpg"),
            original_filename="receipt.jpg",
            media_type="image",
            lease=self.lease,
            tenant=self.tenant,
            property=self.property,
            unit=self.unit,
        )
        image_log.api_response = {"simulator_pending_media_id": staged.pk}
        image_log.save(update_fields=["api_response"])
        ocr_result = {
            "amount": Decimal("63580.00"),
            "date": timezone.datetime(2026, 7, 20).date(),
            "reference": "718126681061",
            "confidence": 97,
            "bank_information": {"channel": "Bank Transfer"},
        }

        with patch("whatsapp.services.whatsapp_ai.run_payment_ocr", return_value=ocr_result):
            response, intent, metadata = WhatsAppAIAssistant(service=MagicMock())._handle_media_message(
                image_log,
                self.conversation,
                "",
                "image",
                resolve_sender(self.phone, conversation=self.conversation),
            )

        staged.refresh_from_db()
        self.conversation.refresh_from_db()
        self.assertEqual(intent, "payment_receipt_confirmation")
        self.assertIn("Amount: Rs. 63,580.00", response)
        self.assertIn("Date: 20-07-2026", response)
        self.assertIn("Reply YES", response)
        self.assertEqual(staged.purpose, PendingWhatsAppMedia.PURPOSE_PAYMENT)
        self.assertFalse(PendingWhatsAppPayment.objects.exists())
        self.assertEqual(self.conversation.pending_state, "payment_receipt_confirmation")

        confirm_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.phone,
            wa_message_id="wamid.payment.receipt.confirm",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "YES"}},
        )
        confirmed_response, confirmed_intent, confirmed_metadata = WhatsAppAIAssistant(
            service=MagicMock()
        )._handle(confirm_log, self.conversation)

        payment = PendingWhatsAppPayment.objects.get(
            pk=confirmed_metadata["pending_payment_id"]
        )
        self.conversation.refresh_from_db()
        self.assertEqual(confirmed_intent, "payment_confirmed")
        self.assertIn("Amount: Rs. 63,580.00", confirmed_response)
        self.assertIn("confirmation shortly after bank verification", confirmed_response)
        self.assertEqual(payment.amount, Decimal("63580.00"))
        self.assertEqual(payment.status, PendingWhatsAppPayment.STATUS_CONFIRMED)
        self.assertTrue(payment.confirmed_by_tenant)
        self.assertEqual(payment.ocr_json["ocr_amount"], "63580.00")
        self.assertEqual(payment.ocr_json["tenant_amount"], "63580.00")
        self.assertEqual(self.conversation.pending_state, "")
        self.assertIn("6. Upload Payment Receipt", WhatsAppAIAssistant()._tenant_welcome_menu(self.lease))

    def test_tenant_can_correct_wrong_ocr_amount_before_pending_payment(self):
        staged = PendingWhatsAppMedia.objects.create(
            conversation=self.conversation,
            original_whatsapp_message=self.message,
            phone=self.phone,
            file=ContentFile(b"jpg", name="receipt-correction.jpg"),
            original_filename="receipt-correction.jpg",
            media_type="image",
            lease=self.lease,
            tenant=self.tenant,
            property=self.property,
            unit=self.unit,
        )
        assistant = WhatsAppAIAssistant(service=MagicMock())
        assistant._prepare_payment_receipt_confirmation(
            self.message,
            self.conversation,
            self.lease,
            staged,
            "6",
            ocr_json={
                "amount": Decimal("6.00"),
                "date": timezone.datetime(2026, 7, 20).date(),
                "reference": "718126681061",
                "confidence": 60,
                "is_payment_receipt": True,
            },
        )
        correction_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.phone,
            wa_message_id="wamid.payment.receipt.correct-amount",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "AMOUNT 63580"}},
        )

        correction_response, correction_intent, _metadata = assistant._handle(
            correction_log, self.conversation
        )

        self.assertEqual(correction_intent, "payment_receipt_correction")
        self.assertIn("Amount: Rs. 63,580.00", correction_response)
        self.assertIn("OCR originally read: Rs. 6.00", correction_response)
        self.assertFalse(PendingWhatsAppPayment.objects.exists())

        confirm_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.phone,
            wa_message_id="wamid.payment.receipt.correct-confirm",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "YES"}},
        )
        _response, intent, metadata = assistant._handle(confirm_log, self.conversation)
        payment = PendingWhatsAppPayment.objects.get(pk=metadata["pending_payment_id"])

        self.assertEqual(intent, "payment_confirmed")
        self.assertEqual(payment.amount, Decimal("63580.00"))
        self.assertEqual(payment.ocr_json["ocr_amount"], "6.00")
        self.assertEqual(payment.ocr_json["tenant_amount"], "63580")
        self.assertTrue(payment.ocr_json["tenant_corrected_amount"])
        self.assertIn("OCR recognized amount: Rs. 6.00", payment.ai_notes)
        self.assertIn("Tenant confirmed amount: Rs. 63,580.00", payment.ai_notes)

    def test_payment_menu_choice_six_is_never_used_as_receipt_amount(self):
        staged = PendingWhatsAppMedia.objects.create(
            conversation=self.conversation,
            original_whatsapp_message=self.message,
            phone=self.phone,
            file=ContentFile(b"jpg", name="receipt-no-ocr.jpg"),
            original_filename="receipt-no-ocr.jpg",
            media_type="image",
            lease=self.lease,
            tenant=self.tenant,
            property=self.property,
            unit=self.unit,
        )
        self.conversation.pending_state = "tenant_upload_type"
        self.conversation.context["pending_media_id"] = staged.pk
        self.conversation.save(update_fields=["pending_state", "context", "updated_at"])
        assistant = WhatsAppAIAssistant(service=MagicMock())

        with patch(
            "whatsapp.services.whatsapp_ai.run_payment_ocr",
            return_value={"engine": "unavailable", "confidence": 0, "text": ""},
        ):
            response, intent, _metadata = assistant._consume_tenant_upload_type(
                self.message, self.conversation, "6", self.lease
            )

        self.conversation.refresh_from_db()
        self.assertEqual(intent, "payment_receipt_staff_review")
        self.assertIn("saved for staff review", response)
        self.assertNotIn("payment_receipt_review", self.conversation.context)
        self.assertFalse(PendingWhatsAppPayment.objects.exists())

    def test_staff_only_sender_opens_staff_inbox(self):
        sender = resolve_sender(self.staff1.whatsapp_number)
        conversation = WhatsAppConversation.objects.create(phone_number=self.staff1.whatsapp_number)
        self.assertEqual(resolve_mode(conversation, "staff inbox", sender), WhatsAppConversation.MODE_STAFF)

    def test_dual_role_can_choose_tenant_mode(self):
        self.staff1.whatsapp_number = self.phone
        self.staff1.save(update_fields=["whatsapp_number"])
        sender = resolve_sender(self.phone)
        self.conversation.selected_mode = ""
        self.conversation.mode_expires_at = None
        self.conversation.pending_state = "mode_selection"
        self.conversation.save()
        self.assertEqual(resolve_mode(self.conversation, "tenant", sender), WhatsAppConversation.MODE_TENANT)

    def test_dual_role_can_choose_staff_mode(self):
        self.staff1.whatsapp_number = self.phone
        self.staff1.save(update_fields=["whatsapp_number"])
        sender = resolve_sender(self.phone)
        self.conversation.selected_mode = ""
        self.conversation.mode_expires_at = None
        self.conversation.pending_state = "mode_selection"
        self.conversation.save()
        self.assertEqual(resolve_mode(self.conversation, "staff", sender), WhatsAppConversation.MODE_STAFF)

    def test_role_option_two_opens_staff_main_menu(self):
        self.staff1.whatsapp_number = self.phone
        self.staff1.save(update_fields=["whatsapp_number"])
        self.conversation.selected_mode = ""
        self.conversation.mode_expires_at = None
        self.conversation.pending_state = "mode_selection"
        self.conversation.save()
        self.message.payload = {"type": "text", "text": {"body": "2"}}
        self.message.save(update_fields=["payload", "updated_at"])

        response, intent, _metadata = WhatsAppAIAssistant(
            service=MagicMock()
        )._handle(self.message, self.conversation)

        self.conversation.refresh_from_db()
        self.assertEqual(intent, "staff")
        self.assertIn("Staff Inbox / Menu", response)
        self.assertFalse(response.startswith("Lease Management"))
        self.assertNotIn("Lease Management\n\n1. Create Lease", response)
        self.assertEqual(
            self.conversation.selected_mode,
            WhatsAppConversation.MODE_STAFF,
        )

    def test_active_staff_menu_two_still_opens_lease_management(self):
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
        )
        message = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.staff.active-menu-two",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "2"}},
        )

        response, intent, _metadata = WhatsAppAIAssistant(
            service=MagicMock()
        )._handle(message, conversation)

        conversation.refresh_from_db()
        self.assertEqual(intent, "staff")
        self.assertIn("Lease Management", response)
        self.assertNotIn("Staff Inbox / Menu", response)
        self.assertEqual(conversation.pending_state, "staff_lease_management")

    def test_guest_role_selection_is_not_reused_as_guest_menu_option(self):
        self.staff1.whatsapp_number = self.phone
        self.staff1.save(update_fields=["whatsapp_number"])
        self.conversation.selected_mode = ""
        self.conversation.mode_expires_at = None
        self.conversation.pending_state = "mode_selection"
        self.conversation.save()
        self.message.payload = {"type": "text", "text": {"body": "4"}}
        self.message.save(update_fields=["payload", "updated_at"])

        response, intent, _metadata = WhatsAppAIAssistant(
            service=MagicMock()
        )._handle(self.message, self.conversation)

        self.conversation.refresh_from_db()
        self.assertEqual(intent, "guest")
        self.assertIn("Guest Menu", response)
        self.assertNotEqual(self.conversation.pending_state, "suggestion_capture")
        self.assertEqual(
            self.conversation.selected_mode,
            WhatsAppConversation.MODE_GUEST,
        )

    def test_staff_mode_is_available_when_same_phone_matches_stale_tenant(self):
        Tenant.objects.create(
            first_name="Second", last_name="Tenant", phone=self.phone, cnic="37405-4444444-4"
        )
        self.staff1.whatsapp_number = self.phone
        self.staff1.save(update_fields=["whatsapp_number"])
        self.conversation.selected_mode = ""
        self.conversation.mode_expires_at = None
        self.conversation.pending_state = ""
        self.conversation.save()
        sender = resolve_sender(self.phone, conversation=self.conversation)
        self.assertFalse(sender.ambiguous)
        self.assertEqual(resolve_mode(self.conversation, "hi", sender), "choose_mode")
        sender = resolve_sender(self.phone, conversation=self.conversation)
        self.assertEqual(resolve_mode(self.conversation, "staff", sender), WhatsAppConversation.MODE_STAFF)

    def test_multiple_tenant_matches_start_verified_tenant_selection(self):
        second_tenant = Tenant.objects.create(
            first_name="Second", last_name="Tenant", phone=self.phone, cnic="37405-5555555-5"
        )
        second_unit = Unit.objects.create(property=self.property, unit_number="B-05", status="occupied")
        Lease.objects.create(
            tenant=second_tenant, unit=second_unit,
            start_date=timezone.localdate() - timedelta(days=10),
            end_date=timezone.localdate() + timedelta(days=100),
            monthly_rent=Decimal("20000"), status="active",
        )
        self.staff1.whatsapp_number = self.phone
        self.staff1.save(update_fields=["whatsapp_number"])
        self.conversation.selected_mode = ""
        self.conversation.mode_expires_at = None
        self.conversation.pending_state = "mode_selection"
        self.conversation.save()
        sender = resolve_sender(self.phone, conversation=self.conversation)
        self.assertEqual(resolve_mode(self.conversation, "tenant", sender), "choose_tenant_identity")
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.pending_state, "tenant_identity_selection")
        self.assertEqual(len(self.conversation.context["tenant_identity_options"]), 2)

    def test_stale_tenant_phone_record_is_not_shown_as_an_account(self):
        Tenant.objects.create(
            first_name="Old", last_name="Account", phone=self.phone, cnic="37405-9999999-9"
        )

        sender = resolve_sender(self.phone)

        self.assertEqual(sender.tenant_matches, [self.tenant])
        self.assertFalse(sender.ambiguous)

    def test_whatsapp_balance_uses_canonical_lease_allocations(self):
        Invoice.objects.create(
            lease=self.lease,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate(),
            amount=Decimal("18300.00"),
            status="cancelled",
        )
        payment = Payment.objects.create(
            lease=self.lease,
            payment_date=timezone.localdate(),
            amount=Decimal("18300.00"),
        )
        PaymentDetail.objects.create(
            payment=payment,
            lease_amount=Decimal("0.00"),
            security_amount=Decimal("18300.00"),
        )

        from whatsapp.services.tenant_context import build_lease_context

        self.assertEqual(build_lease_context(self.lease).balance, Decimal("0.00"))

    def test_staff_lease_action_accepts_property_unit_shortcut(self):
        basement = Property.objects.create(
            property_name="F56 Basement", owner_name="Owner", owner_cnic="37405-7777777-7",
            type="Residential", property_type="apartment", total_units=1,
        )
        room = Unit.objects.create(property=basement, unit_number="F56-ROOM# 02", status="occupied")
        room_lease = Lease.objects.create(
            tenant=self.tenant, unit=room,
            start_date=timezone.localdate() - timedelta(days=10),
            end_date=timezone.localdate() + timedelta(days=100),
            monthly_rent=Decimal("18000"), status="active",
        )
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=basement)
        assistant = WhatsAppAIAssistant(service=MagicMock())
        staff_conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
        )

        prompt = assistant._start_staff_lease_target(staff_conversation, self.staff1, "lease_balance")
        response = assistant._consume_staff_lease_target(
            self.message, staff_conversation, "f56-room2", self.staff1
        )

        self.assertIn("F56 Basement", prompt)
        self.assertIn("Lease Balance", response)
        self.assertIn(room.unit_number, response)
        self.assertIn(str(room_lease.get_balance), response)
        self.assertIn("10. Change unit", response)
        self.assertIn("11. Change property", response)
        staff_conversation.refresh_from_db()
        self.assertEqual(staff_conversation.pending_state, "staff_selected_lease_menu")
        self.assertEqual(staff_conversation.selected_lease, room_lease)

        follow_up = assistant._consume_staff_menu_state(
            self.message, staff_conversation, "tenant details", self.staff1
        )

        self.assertIn("Tenant Summary", follow_up)
        self.assertIn("Selected: F56 Basement", follow_up)

    def test_unit_only_staff_shortcut_asks_for_property(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        assistant = WhatsAppAIAssistant(service=MagicMock())
        staff_conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
        )
        assistant._start_staff_lease_target(staff_conversation, self.staff1, "lease_view")

        response = assistant._consume_staff_lease_target(
            self.message, staff_conversation, "flat 7", self.staff1
        )

        self.assertIn("Which property contains unit 7?", response)
        staff_conversation.refresh_from_db()
        self.assertEqual(staff_conversation.pending_state, "staff_lease_target_property")

    def test_room_number_search_does_not_treat_one_digit_as_phone(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        room_tenant = Tenant.objects.create(
            first_name="Room", last_name="Seven", phone="+923008888888", cnic="37405-8888888-8"
        )
        room_unit = Unit.objects.create(property=self.property, unit_number="ROOM# 07", status="occupied")
        room_lease = Lease.objects.create(
            tenant=room_tenant, unit=room_unit,
            start_date=timezone.localdate() - timedelta(days=5),
            end_date=timezone.localdate() + timedelta(days=100),
            monthly_rent=Decimal("17000"), status="active",
        )
        unrelated_tenant = Tenant.objects.create(
            first_name="Phone", last_name="Contains Seven", phone="+923007777777", cnic="37405-7777777-7"
        )
        unrelated_unit = Unit.objects.create(property=self.property, unit_number="C-01", status="occupied")
        Lease.objects.create(
            tenant=unrelated_tenant, unit=unrelated_unit,
            start_date=timezone.localdate() - timedelta(days=5),
            end_date=timezone.localdate() + timedelta(days=100),
            monthly_rent=Decimal("16000"), status="active",
        )

        matches = WhatsAppAIAssistant(service=MagicMock())._staff_search_leases(self.staff1, "room7")

        self.assertEqual(matches, [room_lease])

    def test_staff_result_list_accepts_a_revised_text_search(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        assistant = WhatsAppAIAssistant(service=MagicMock())
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            pending_state="staff_search_selection",
            context={
                "staff_search_action": "lease_view",
                "staff_search_options": [{"type": "lease", "id": self.lease.pk}],
            },
        )

        with patch.object(assistant, "_consume_staff_search_query", return_value="revised results") as consume:
            response = assistant._consume_staff_search_selection(
                self.message, conversation, "I want to view room7", self.staff1
            )

        self.assertEqual(response, "revised results")
        consume.assert_called_once_with(self.message, conversation, "I want view room7", self.staff1)

    def test_guided_lease_action_accepts_tenant_phone(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        assistant = WhatsAppAIAssistant(service=MagicMock())
        conversation = WhatsAppConversation.objects.create(phone_number=self.staff1.whatsapp_number)
        assistant._start_staff_lease_target(conversation, self.staff1, "lease_balance")

        response = assistant._consume_staff_lease_target(
            self.message, conversation, self.tenant.phone, self.staff1
        )

        self.assertIn("Lease Balance", response)
        self.assertIn(self.unit.unit_number, response)

    def test_selected_staff_can_act_as_tenant_with_live_actions_and_exit(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        simulator_group, _created = Group.objects.get_or_create(name="Tenant Simulator")
        self.staff1.groups.add(simulator_group)
        assistant = WhatsAppAIAssistant(service=MagicMock())
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode="",
            pending_state="mode_selection",
        )
        start_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.simulator.start",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": f"Tenant {self.tenant.phone}"}},
        )

        response, start_intent, _metadata = assistant._handle(start_log, conversation)

        self.assertEqual(start_intent, "staff")
        self.assertIn("ACTING AS TENANT (LIVE)", response)
        conversation.refresh_from_db()
        self.assertEqual(conversation.context["staff_tenant_simulation"]["tenant_id"], self.tenant.pk)
        simulated_identity = resolve_sender(self.staff1.whatsapp_number, conversation=conversation)
        self.assertTrue(simulated_identity.has_staff)
        self.assertTrue(simulated_identity.has_active_tenant)

        maintenance_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.simulator.maintenance",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "maintenance request"}},
        )
        live_response, live_intent, _metadata = assistant._handle(maintenance_log, conversation)
        self.assertNotEqual(live_intent, "staff_tenant_simulation_read_only")
        self.assertNotIn("read-only", live_response.lower())

        exit_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.simulator.exit",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "to staff"}},
        )
        exit_response, exit_intent, _metadata = assistant._handle(exit_log, conversation)
        self.assertEqual(exit_intent, "staff_tenant_simulation_ended")
        self.assertIn("Staff Inbox / Menu", exit_response)
        conversation.refresh_from_db()
        self.assertNotIn("staff_tenant_simulation", conversation.context)

    def test_selected_staff_can_open_tenant_testing_by_tenant_number(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        simulator_group, _created = Group.objects.get_or_create(name="Tenant Simulator")
        self.staff1.groups.add(simulator_group)
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            pending_state="mode_selection",
        )
        message = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.simulator.tenant-number",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": f"Tenant {self.tenant.pk}"}},
        )

        response, intent, _metadata = WhatsAppAIAssistant(service=MagicMock())._handle(
            message, conversation
        )

        self.assertEqual(intent, "staff")
        self.assertIn("ACTING AS TENANT (LIVE)", response)
        self.assertIn(self.tenant.get_full_name(), response)

    def test_entering_tenant_testing_clears_previous_upload_and_receipt_state(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        simulator_group, _created = Group.objects.get_or_create(name="Tenant Simulator")
        self.staff1.groups.add(simulator_group)
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            pending_state="staff_waiting_upload",
            context={
                "staff_upload_kind": PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT,
                "staff_upload_batch_key": str(uuid.uuid4()),
                "staff_upload_lease_id": self.lease.pk,
                "pending_media_id": 999,
                "pending_payment_id": 998,
                "payment_receipt_review": {"media_id": 999},
                "pending_maintenance_id": 997,
                "maintenance_draft": {"issue_type": "Other"},
            },
        )
        message = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.simulator.clear-stale-state",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": f"Tenant {self.tenant.phone}"}},
        )

        response, intent, _metadata = WhatsAppAIAssistant(service=MagicMock())._handle(
            message, conversation
        )

        self.assertEqual(intent, "staff")
        self.assertIn("ACTING AS TENANT (LIVE)", response)
        self.assertNotIn("payment details", response)
        conversation.refresh_from_db()
        for key in (
            "staff_upload_kind",
            "staff_upload_batch_key",
            "staff_upload_lease_id",
            "pending_media_id",
            "pending_payment_id",
            "payment_receipt_review",
            "pending_maintenance_id",
            "maintenance_draft",
        ):
            self.assertNotIn(key, conversation.context)

    def test_tenant_assist_replies_to_staff_number_with_selected_location(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        simulator_group, _created = Group.objects.get_or_create(name="Tenant Simulator")
        self.staff1.groups.add(simulator_group)
        service = MagicMock()
        assistant = WhatsAppAIAssistant(service=service)
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            pending_state="mode_selection",
        )
        start_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.assist.location.start",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": f"Tenant {self.tenant.phone}"}},
        )
        assistant._handle(start_log, conversation)
        conversation.refresh_from_db()
        conversation.mode_expires_at = timezone.now() - timedelta(minutes=1)
        conversation.save(update_fields=["mode_expires_at", "updated_at"])
        service.reset_mock()
        menu_log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.assist.location.menu",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "menu"}},
        )

        assistant.handle_inbound_message(menu_log)

        sent_phone, sent_text = service.send_text.call_args.args[:2]
        self.assertEqual(sent_phone, self.staff1.whatsapp_number)
        self.assertIn("ACTING AS TENANT (LIVE)", sent_text)
        self.assertIn(f"{self.property.property_name} / {self.unit.unit_number}", sent_text)
        self.assertIn("Type EXIT", sent_text)

    def test_staff_lease_menu_exposes_and_selects_lease_photo_upload(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
            pending_state="staff_lease_management",
        )
        assistant = WhatsAppAIAssistant(service=MagicMock())

        kind_menu = assistant._consume_staff_menu_state(
            self.message, conversation, "5", self.staff1
        )
        conversation.refresh_from_db()
        self.assertIn("Lease Photos", kind_menu)
        self.assertEqual(conversation.pending_state, "staff_lease_upload_kind")

        property_menu = assistant._consume_staff_menu_state(
            self.message, conversation, "2", self.staff1
        )
        conversation.refresh_from_db()
        self.assertIn(self.property.property_name, property_menu)
        self.assertEqual(conversation.context["staff_lease_target"]["action"], "lease_photo")

        assistant._consume_staff_menu_state(self.message, conversation, "1", self.staff1)
        conversation.refresh_from_db()
        assistant._consume_staff_menu_state(self.message, conversation, "1", self.staff1)
        conversation.refresh_from_db()
        self.assertEqual(conversation.pending_state, "staff_upload_target_confirmation")
        assistant._consume_staff_menu_state(self.message, conversation, "YES", self.staff1)
        conversation.refresh_from_db()
        self.assertEqual(conversation.pending_state, "staff_waiting_upload")
        self.assertEqual(
            conversation.context["staff_upload_kind"],
            PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
        )
        self.assertEqual(conversation.context["staff_upload_lease_id"], self.lease.pk)

    def test_staff_media_lease_photos_use_property_then_unit_steps(self):
        property_obj = Property.objects.create(
            property_name="F35 Building", owner_name="Owner", owner_cnic="37405-7777777-7",
            type="Residential", property_type="apartment", total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="F35-FLAT# 01", status="occupied")
        lease = Lease.objects.create(
            tenant=self.tenant, unit=unit,
            start_date=timezone.localdate() - timedelta(days=10),
            end_date=timezone.localdate() + timedelta(days=100),
            monthly_rent=Decimal("18000"), status="active",
        )
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=property_obj)
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
            pending_state="staff_property_media_menu",
        )
        assistant = WhatsAppAIAssistant(service=MagicMock())

        property_menu = assistant._consume_staff_menu_state(
            self.message, conversation, "3", self.staff1
        )
        conversation.refresh_from_db()
        self.assertIn("1. F35 Building", property_menu)
        self.assertEqual(conversation.pending_state, "staff_upload_target_query")

        unit_menu = assistant._consume_staff_menu_state(
            self.message, conversation, "1", self.staff1
        )
        conversation.refresh_from_db()
        self.assertIn("Select unit for F35 Building", unit_menu)
        self.assertIn(unit.unit_number, unit_menu)
        self.assertEqual(conversation.pending_state, "staff_upload_target_selection")

        response = assistant._consume_staff_menu_state(
            self.message, conversation, "1", self.staff1
        )
        conversation.refresh_from_db()
        self.assertIn("Confirm upload target", response)
        response = assistant._consume_staff_menu_state(
            self.message, conversation, "YES", self.staff1
        )
        conversation.refresh_from_db()
        self.assertIn("Target selected", response)
        self.assertEqual(conversation.pending_state, "staff_waiting_upload")
        self.assertEqual(conversation.context["staff_upload_lease_id"], lease.pk)
        self.assertEqual(
            conversation.context["staff_upload_kind"],
            PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
        )

    def test_staff_upload_confirmation_back_restarts_target_selection(self):
        WhatsAppStaffPropertyAccess.objects.create(
            staff_user=self.staff1,
            property=self.property,
        )
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
            context={
                "staff_upload_kind": PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
            },
        )
        assistant = WhatsAppAIAssistant(service=MagicMock())
        assistant._select_staff_upload_target(
            self.message,
            conversation,
            self.staff1,
            {
                "type": "unit",
                "id": self.unit.pk,
                "label": f"{self.property.property_name} / {self.unit.unit_number}",
            },
        )
        conversation.refresh_from_db()
        self.assertEqual(
            conversation.pending_state,
            "staff_upload_target_confirmation",
        )

        response = assistant._handle_staff_message(
            self.message,
            conversation,
            "BACK",
            "text",
            resolve_sender(
                self.staff1.whatsapp_number,
                conversation=conversation,
            ),
        )

        conversation.refresh_from_db()
        self.assertIn("Select the target property", response)
        self.assertEqual(conversation.pending_state, "staff_upload_target_query")
        self.assertNotIn("staff_upload_batch_key", conversation.context)
        self.assertNotIn("staff_upload_unit_id", conversation.context)
        self.assertEqual(
            conversation.context["staff_upload_kind"],
            PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
        )

    def test_staff_media_lease_photo_shortcut_skips_property_and_unit_menus(self):
        property_obj = Property.objects.create(
            property_name="F35 Building", owner_name="Owner", owner_cnic="37405-7777777-7",
            type="Residential", property_type="apartment", total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="F35-FLAT# 01", status="occupied")
        lease = Lease.objects.create(
            tenant=self.tenant, unit=unit,
            start_date=timezone.localdate() - timedelta(days=10),
            end_date=timezone.localdate() + timedelta(days=100),
            monthly_rent=Decimal("18000"), status="active",
        )
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=property_obj)
        conversation = WhatsAppConversation.objects.create(phone_number=self.staff1.whatsapp_number)
        assistant = WhatsAppAIAssistant(service=MagicMock())
        assistant._start_staff_upload_target_search(
            conversation,
            PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
            "Send the target.",
            self.staff1,
        )

        response = assistant._consume_staff_upload_target_query(
            self.message, conversation, "f35-1", self.staff1
        )

        conversation.refresh_from_db()
        self.assertIn("Confirm upload target", response)
        response = assistant._consume_staff_menu_state(
            self.message, conversation, "YES", self.staff1
        )
        conversation.refresh_from_db()
        self.assertIn("Target selected", response)
        self.assertEqual(conversation.pending_state, "staff_waiting_upload")
        self.assertEqual(conversation.context["staff_upload_lease_id"], lease.pk)

    def test_staff_media_sentence_resolves_structured_target_without_name_false_positive(self):
        property_obj = Property.objects.create(
            property_name="F35 Building", owner_name="Owner", owner_cnic="37405-7777777-7",
            type="Residential", property_type="apartment", total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="F35-FLAT# 01", status="occupied")
        lease = Lease.objects.create(
            tenant=self.tenant, unit=unit,
            start_date=timezone.localdate() - timedelta(days=10),
            end_date=timezone.localdate() + timedelta(days=100),
            monthly_rent=Decimal("18000"), status="active",
        )
        unrelated_property = Property.objects.create(
            property_name="Other Place", owner_name="Owner", owner_cnic="37405-8888888-8",
            type="Residential", property_type="apartment", total_units=2,
        )
        for index, first_name in enumerate(("Danish", "Nisar"), start=1):
            tenant = Tenant.objects.create(
                first_name=first_name, last_name="Example", phone=f"+92300888888{index}",
                cnic=f"37405-888888{index}-{index}",
            )
            other_unit = Unit.objects.create(
                property=unrelated_property, unit_number=f"B-{index}", status="occupied"
            )
            Lease.objects.create(
                tenant=tenant, unit=other_unit,
                start_date=timezone.localdate() - timedelta(days=10),
                end_date=timezone.localdate() + timedelta(days=100),
                monthly_rent=Decimal("17000"), status="active",
            )
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=property_obj)
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=unrelated_property)
        conversation = WhatsAppConversation.objects.create(phone_number=self.staff1.whatsapp_number)
        assistant = WhatsAppAIAssistant(service=MagicMock())
        assistant._start_staff_upload_target_search(
            conversation,
            PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
            "Send the target.",
            self.staff1,
        )

        response = assistant._consume_staff_upload_target_query(
            self.message, conversation, "this is for f35 flat 1 lease photos", self.staff1
        )

        conversation.refresh_from_db()
        self.assertIn("Confirm upload target", response)
        response = assistant._consume_staff_menu_state(
            self.message, conversation, "YES", self.staff1
        )
        conversation.refresh_from_db()
        self.assertIn("Target selected", response)
        self.assertNotIn("Danish", response)
        self.assertNotIn("Nisar", response)
        self.assertEqual(conversation.pending_state, "staff_waiting_upload")
        self.assertEqual(conversation.context["staff_upload_lease_id"], lease.pk)
        self.assertEqual(
            assistant._staff_search_leases(self.staff1, "this is for the lease photos"),
            [],
        )

    def test_staff_media_upload_can_resolve_by_tenant_name(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        conversation = WhatsAppConversation.objects.create(phone_number=self.staff1.whatsapp_number)
        assistant = WhatsAppAIAssistant(service=MagicMock())
        assistant._start_staff_upload_target_search(
            conversation,
            PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT,
            "Send the target.",
            self.staff1,
        )

        response = assistant._consume_staff_upload_target_query(
            self.message, conversation, "Ahmed Khan", self.staff1
        )

        conversation.refresh_from_db()
        self.assertIn("Confirm upload target", response)
        response = assistant._consume_staff_menu_state(
            self.message, conversation, "YES", self.staff1
        )
        conversation.refresh_from_db()
        self.assertIn("Target selected", response)
        self.assertEqual(conversation.pending_state, "staff_waiting_upload")
        self.assertEqual(conversation.context["staff_upload_lease_id"], self.lease.pk)

    def test_tenant_account_selection_shows_property_and_opens_without_cnic_step(self):
        second_tenant = Tenant.objects.create(
            first_name="Second", last_name="Tenant", phone=self.phone, cnic="37405-5555555-5"
        )
        second_property = Property.objects.create(
            property_name="City Heights", owner_name="Owner", owner_cnic="37405-3333333-3",
            type="Residential", property_type="apartment", total_units=1,
        )
        second_unit = Unit.objects.create(property=second_property, unit_number="B-02", status="occupied")
        Lease.objects.create(
            tenant=second_tenant, unit=second_unit,
            start_date=timezone.localdate() - timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=335),
            monthly_rent=Decimal("30000"), status="active",
        )
        self.conversation.selected_mode = ""
        self.conversation.mode_expires_at = None
        self.conversation.pending_state = "tenant_identity_selection"
        self.conversation.context = {"tenant_identity_options": [self.tenant.pk, second_tenant.pk]}
        self.conversation.save()

        assistant = WhatsAppAIAssistant(service=MagicMock())
        identity = resolve_sender(self.phone, conversation=self.conversation)
        options_text = assistant._tenant_identity_options_text(identity.tenant_matches)
        self.assertIn("Test Residency / Unit A-04", options_text)
        self.assertIn("City Heights / Unit B-02", options_text)
        self.assertNotIn("CNIC", options_text)

        response, intent, metadata = assistant._consume_tenant_identity_selection(
            self.conversation, "1", identity
        )
        self.conversation.refresh_from_db()
        self.assertEqual(intent, "tenant_welcome")
        self.assertIn("Test Residency / A-04", response)
        self.assertEqual(metadata["tenant"], self.tenant)
        self.assertEqual(self.conversation.pending_state, "")
        self.assertEqual(self.conversation.selected_lease, self.lease)

    def test_role_simulator_runs_staff_conversation_without_changing_phone(self):
        self.staff1.user_permissions.add(
            Permission.objects.get(codename="change_whatsappmessagelog")
        )
        self.client.force_login(self.staff1)
        response = self.client.post(
            reverse("whatsapp:simulator"),
            {
                "role": "staff",
                "staff": self.staff1.pk,
                "message": "hi",
                "delivery_phone": self.phone,
                "new_session": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff Inbox / Menu")
        self.staff1.refresh_from_db()
        self.assertEqual(self.staff1.whatsapp_number, "+923009990001")

    def test_additional_maintenance_photo_uses_same_pending_request(self):
        pending = PendingWhatsAppMaintenance.objects.create(
            conversation=self.conversation,
            original_whatsapp_message=self.message,
            phone=self.phone,
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            issue_type="Plumbing",
            urgency="normal",
            description="Pipe leak",
        )
        self.conversation.pending_state = "pending_maintenance"
        self.conversation.context["pending_maintenance_id"] = pending.pk
        self.conversation.save(update_fields=["pending_state", "context", "updated_at"])
        extra_log = WhatsAppMessageLog.objects.create(
            direction="inbound",
            phone_number=self.phone,
            wa_message_id="wamid.maintenance.photo2",
            message_type="image",
            status="received",
            payload={"type": "image", "image": {"caption": "another angle", "filename": "angle.jpg"}},
        )
        staged_media = PendingWhatsAppMedia.objects.create(
            conversation=self.conversation,
            phone=self.phone,
            file=ContentFile(b"jpg", name="angle.jpg"),
            original_filename="angle.jpg",
            media_type="image",
        )
        extra_log.api_response = {"simulator_pending_media_id": staged_media.pk}
        extra_log.save(update_fields=["api_response"])
        assistant = WhatsAppAIAssistant(service=MagicMock())
        response, intent, _metadata = assistant._handle_media_message(
            extra_log,
            self.conversation,
            "another angle",
            "image",
            resolve_sender(self.phone, conversation=self.conversation),
        )
        pending.refresh_from_db()
        self.assertEqual(intent, "maintenance_media_attached")
        self.assertIn("same maintenance request", response)
        self.assertEqual(list(pending.media.values_list("pk", flat=True)), [staged_media.pk])

    def test_maintenance_media_recovers_from_stale_generic_upload_state(self):
        pending = PendingWhatsAppMaintenance.objects.create(
            conversation=self.conversation,
            original_whatsapp_message=self.message,
            phone=self.phone,
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            issue_type="Plumbing",
            urgency="normal",
            description="Pipe leak",
        )
        self.conversation.pending_state = "tenant_upload_type"
        self.conversation.context["pending_maintenance_id"] = pending.pk
        self.conversation.context["pending_media_id"] = 999999
        self.conversation.save(update_fields=["pending_state", "context", "updated_at"])

        media_log = WhatsAppMessageLog.objects.create(
            direction="inbound",
            phone_number=self.phone,
            wa_message_id="wamid.maintenance.stale-upload-state",
            message_type="image",
            status="received",
            payload={
                "type": "image",
                "image": {"filename": "maintenance-photo.jpg"},
            },
        )
        staged_media = PendingWhatsAppMedia.objects.create(
            conversation=self.conversation,
            phone=self.phone,
            file=ContentFile(b"image", name="maintenance-photo.jpg"),
            original_filename="maintenance-photo.jpg",
            media_type="image",
        )
        media_log.api_response = {"simulator_pending_media_id": staged_media.pk}
        media_log.save(update_fields=["api_response"])

        assistant = WhatsAppAIAssistant(service=MagicMock())
        response, intent, _metadata = assistant._handle_media_message(
            media_log,
            self.conversation,
            "",
            "image",
            resolve_sender(self.phone, conversation=self.conversation),
        )

        pending.refresh_from_db()
        self.conversation.refresh_from_db()
        self.assertEqual(intent, "maintenance_media_attached")
        self.assertIn("same maintenance request", response)
        self.assertEqual(
            list(pending.media.values_list("pk", flat=True)),
            [staged_media.pk],
        )
        self.assertEqual(self.conversation.pending_state, "pending_maintenance")
        self.assertNotIn("pending_media_id", self.conversation.context)

    def test_maintenance_media_rejects_request_from_another_conversation(self):
        other_conversation = WhatsAppConversation.objects.create(
            phone_number="+923001110099",
        )
        other_pending = PendingWhatsAppMaintenance.objects.create(
            conversation=other_conversation,
            phone="+923001110099",
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            issue_type="Plumbing",
            urgency="normal",
            description="Another tenant's request",
        )
        self.conversation.pending_state = "pending_maintenance"
        self.conversation.context["pending_maintenance_id"] = other_pending.pk
        self.conversation.save(update_fields=["pending_state", "context", "updated_at"])

        media_log = WhatsAppMessageLog.objects.create(
            direction="inbound",
            phone_number=self.phone,
            wa_message_id="wamid.maintenance.cross-conversation",
            message_type="video",
            status="received",
            payload={
                "type": "video",
                "video": {"filename": "maintenance-video.mp4"},
            },
        )
        staged_media = PendingWhatsAppMedia.objects.create(
            conversation=self.conversation,
            phone=self.phone,
            file=ContentFile(b"video", name="maintenance-video.mp4"),
            original_filename="maintenance-video.mp4",
            media_type="video",
        )
        media_log.api_response = {"simulator_pending_media_id": staged_media.pk}
        media_log.save(update_fields=["api_response"])

        _response, intent, _metadata = WhatsAppAIAssistant(
            service=MagicMock()
        )._handle_media_message(
            media_log,
            self.conversation,
            "",
            "video",
            resolve_sender(self.phone, conversation=self.conversation),
        )

        other_pending.refresh_from_db()
        self.conversation.refresh_from_db()
        self.assertEqual(intent, "media_pending")
        self.assertFalse(other_pending.media.filter(pk=staged_media.pk).exists())
        self.assertNotIn("pending_maintenance_id", self.conversation.context)
        self.assertEqual(self.conversation.pending_state, "tenant_upload_type")

    def test_unsupported_album_event_does_not_break_open_maintenance_batch(self):
        pending = PendingWhatsAppMaintenance.objects.create(
            conversation=self.conversation,
            original_whatsapp_message=self.message,
            phone=self.phone,
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            issue_type="Plumbing",
            urgency="normal",
            description="Pipe leak",
        )
        self.conversation.pending_state = "pending_maintenance"
        self.conversation.context["pending_maintenance_id"] = pending.pk
        self.conversation.save(update_fields=["pending_state", "context", "updated_at"])
        unsupported_log = WhatsAppMessageLog.objects.create(
            direction="inbound",
            phone_number=self.phone,
            wa_message_id="wamid.maintenance.album.unsupported",
            message_type="unsupported",
            status="received",
            payload={"type": "unsupported"},
        )

        response, intent, metadata = WhatsAppAIAssistant(service=MagicMock())._handle(
            unsupported_log, self.conversation
        )

        self.conversation.refresh_from_db()
        self.assertEqual(response, "")
        self.assertEqual(intent, "maintenance_unsupported_ignored")
        self.assertEqual(metadata["pending_maintenance_id"], pending.pk)
        self.assertEqual(self.conversation.pending_state, "pending_maintenance")
        self.assertEqual(self.conversation.context["pending_maintenance_id"], pending.pk)

    def test_unsupported_album_event_does_not_break_staff_lease_photo_batch(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        batch_key = uuid.uuid4()
        staff_conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
            pending_state="staff_waiting_upload",
            context={
                "staff_upload_kind": PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
                "staff_upload_batch_key": str(batch_key),
                "staff_upload_lease_id": self.lease.pk,
                "staff_upload_property_id": self.property.pk,
                "staff_upload_unit_id": self.unit.pk,
                "staff_upload_target_label": "Test Residency / A-04",
            },
        )
        unsupported_log = WhatsAppMessageLog.objects.create(
            direction="inbound",
            phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.staff.lease.album.unsupported",
            message_type="unsupported",
            status="received",
            payload={"type": "unsupported"},
        )

        response, intent, metadata = WhatsAppAIAssistant(service=MagicMock())._handle(
            unsupported_log, staff_conversation
        )

        staff_conversation.refresh_from_db()
        self.assertEqual(response, "")
        self.assertEqual(intent, "staff_upload_unsupported_ignored")
        self.assertEqual(metadata["staff_user"], self.staff1)
        self.assertEqual(staff_conversation.pending_state, "staff_waiting_upload")
        self.assertEqual(staff_conversation.context["staff_upload_batch_key"], str(batch_key))
        self.assertEqual(
            staff_conversation.context["staff_upload_kind"],
            PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
        )

    def test_done_closes_open_maintenance_batch(self):
        pending = PendingWhatsAppMaintenance.objects.create(
            conversation=self.conversation,
            phone=self.phone,
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
            description="Leak",
        )
        self.conversation.pending_state = "pending_maintenance"
        self.conversation.context["pending_maintenance_id"] = pending.pk
        self.conversation.save(update_fields=["pending_state", "context", "updated_at"])
        result = WhatsAppAIAssistant(service=MagicMock())._consume_global_pending_state(
            self.message,
            self.conversation,
            "DONE",
            resolve_sender(self.phone, conversation=self.conversation),
        )
        self.assertEqual(result[1], "maintenance_submitted")
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.pending_state, "")

    def test_staff_building_photo_is_batched_for_selected_accessible_property(self):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        staff_conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            mode_expires_at=timezone.now() + timedelta(hours=1),
            pending_state="staff_property_media_menu",
        )
        assistant = WhatsAppAIAssistant(service=MagicMock())
        message = WhatsAppMessageLog.objects.create(
            direction="inbound", phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.staff.photo.menu", message_type="text", status="received",
            payload={"type": "text", "text": {"body": "1"}},
        )
        assistant._consume_staff_menu_state(message, staff_conversation, "1", self.staff1)
        staff_conversation.refresh_from_db()
        self.assertEqual(staff_conversation.pending_state, "staff_upload_target_query")
        assistant._consume_staff_menu_state(message, staff_conversation, "Test Residency#1", self.staff1)
        staff_conversation.refresh_from_db()
        self.assertEqual(staff_conversation.pending_state, "staff_upload_target_confirmation")
        assistant._consume_staff_menu_state(message, staff_conversation, "YES", self.staff1)
        staff_conversation.refresh_from_db()
        self.assertEqual(staff_conversation.pending_state, "staff_waiting_upload")

        media_log = WhatsAppMessageLog.objects.create(
            direction="inbound", phone_number=self.staff1.whatsapp_number,
            wa_message_id="wamid.staff.photo.file", message_type="image", status="received",
            payload={"type": "image", "image": {"caption": "front", "filename": "front.jpg"}},
        )
        staged = PendingWhatsAppMedia.objects.create(
            conversation=staff_conversation, phone=self.staff1.whatsapp_number,
            file=ContentFile(b"jpg", name="front.jpg"), original_filename="front.jpg", media_type="image",
        )
        media_log.api_response = {"simulator_pending_media_id": staged.pk}
        media_log.save(update_fields=["api_response"])
        response, intent, _metadata = assistant._handle_media_message(
            media_log,
            staff_conversation,
            "front",
            "image",
            resolve_sender(self.staff1.whatsapp_number, conversation=staff_conversation),
        )
        staged.refresh_from_db()
        self.assertEqual(intent, "staff_upload_batched")
        self.assertIn("approval batch", response)
        self.assertEqual(staged.target_kind, PendingWhatsAppMedia.TARGET_PROPERTY_PHOTO)
        self.assertEqual(staged.property, self.property)
        self.assertEqual(staged.submitted_by_staff, self.staff1)

    @patch("whatsapp.services.whatsapp_ai.notify_staff_pending_request")
    def test_dual_role_staff_generic_media_never_inherits_tenant_lease(
        self, _notify_pending
    ):
        self.staff1.whatsapp_number = self.phone
        self.staff1.save(update_fields=["whatsapp_number"])
        self.conversation.selected_mode = WhatsAppConversation.MODE_STAFF
        self.conversation.mode_expires_at = timezone.now() + timedelta(hours=1)
        self.conversation.pending_state = ""
        self.conversation.save()
        media_log = WhatsAppMessageLog.objects.create(
            direction="inbound",
            phone_number=self.phone,
            wa_message_id="wamid.staff.generic-no-tenant-context",
            message_type="image",
            status="received",
            payload={"type": "image", "image": {"filename": "generic.jpg"}},
        )
        staged = PendingWhatsAppMedia.objects.create(
            conversation=self.conversation,
            phone=self.phone,
            file=ContentFile(b"jpg", name="generic.jpg"),
            original_filename="generic.jpg",
            media_type="image",
            tenant=self.tenant,
            lease=self.lease,
            property=self.property,
            unit=self.unit,
        )
        media_log.api_response = {"simulator_pending_media_id": staged.pk}
        media_log.save(update_fields=["api_response"])

        _response, intent, _metadata = WhatsAppAIAssistant(
            service=MagicMock()
        )._handle_media_message(
            media_log,
            self.conversation,
            "",
            "image",
            resolve_sender(self.phone, conversation=self.conversation),
        )

        staged.refresh_from_db()
        self.conversation.refresh_from_db()
        self.assertEqual(intent, "media_pending")
        self.assertEqual(self.conversation.pending_state, "staff_upload_type")
        self.assertEqual(staged.submitted_by_staff, self.staff1)
        self.assertIsNone(staged.tenant)
        self.assertIsNone(staged.lease)
        self.assertIsNone(staged.property)
        self.assertIsNone(staged.unit)

    @patch("whatsapp.services.whatsapp_ai.notify_staff_pending_request")
    def test_staff_unit_photos_share_one_batch_and_exact_target(
        self, notify_pending
    ):
        self.staff1.whatsapp_number = self.phone
        self.staff1.save(update_fields=["whatsapp_number"])
        f35 = Property.objects.create(
            property_name="F35",
            owner_name="Owner",
            owner_cnic="37405-3000000-1",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        f35_unit = Unit.objects.create(
            property=f35,
            unit_number="F35-FLAT# 01",
            status="occupied",
        )
        f35_lease = Lease.objects.create(
            tenant=self.tenant,
            unit=f35_unit,
            start_date=timezone.localdate() - timedelta(days=10),
            end_date=timezone.localdate() + timedelta(days=100),
            monthly_rent=Decimal("18000"),
            status="active",
        )
        f54 = Property.objects.create(
            property_name="F54",
            owner_name="Owner",
            owner_cnic="37405-3000000-2",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        f54_unit = Unit.objects.create(
            property=f54,
            unit_number="F54-FLAT# 05",
            status="vacant",
        )
        f56 = Property.objects.create(
            property_name="F56",
            owner_name="Owner",
            owner_cnic="37405-3000000-3",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        f56_unit = Unit.objects.create(
            property=f56,
            unit_number="F56-FLAT# 05",
            status="vacant",
        )
        for property_obj in (f35, f54, f56):
            WhatsAppStaffPropertyAccess.objects.create(
                staff_user=self.staff1,
                property=property_obj,
            )
        conversation = self.conversation
        conversation.staff_user = self.staff1
        conversation.tenant = self.tenant
        conversation.selected_lease = f35_lease
        conversation.selected_property = f35
        conversation.selected_unit = f35_unit
        conversation.selected_mode = WhatsAppConversation.MODE_STAFF
        conversation.mode_expires_at = timezone.now() + timedelta(hours=1)
        conversation.pending_state = ""
        conversation.context = {}
        conversation.save()
        assistant = WhatsAppAIAssistant(service=MagicMock())
        assistant._start_staff_upload_target_search(
            conversation,
            PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
            "Send the target.",
            self.staff1,
        )

        confirmation = assistant._consume_staff_upload_target_query(
            self.message,
            conversation,
            "F56 flat 5",
            self.staff1,
        )

        conversation.refresh_from_db()
        self.assertIn("F56 / F56-FLAT# 05", confirmation)
        self.assertEqual(
            conversation.pending_state,
            "staff_upload_target_confirmation",
        )
        self.assertEqual(
            conversation.context["staff_upload_unit_id"],
            f56_unit.pk,
        )
        assistant._consume_staff_menu_state(
            self.message, conversation, "YES", self.staff1
        )
        conversation.refresh_from_db()
        expected_batch_key = uuid.UUID(
            conversation.context["staff_upload_batch_key"]
        )

        for index in range(4):
            media_log = WhatsAppMessageLog.objects.create(
                direction="inbound",
                phone_number=self.phone,
                wa_message_id=f"wamid.staff.f56-photo-{index}",
                message_type="image",
                status="received",
                payload={
                    "type": "image",
                    "image": {"filename": f"f56-{index}.jpg"},
                },
            )
            staged = PendingWhatsAppMedia.objects.create(
                conversation=conversation,
                phone=self.phone,
                file=ContentFile(b"jpg", name=f"f56-{index}.jpg"),
                original_filename=f"f56-{index}.jpg",
                media_type="image",
                tenant=self.tenant,
                lease=f35_lease,
                property=f35,
                unit=f54_unit,
            )
            media_log.api_response = {"simulator_pending_media_id": staged.pk}
            media_log.save(update_fields=["api_response"])
            _response, intent, _metadata = assistant._handle_media_message(
                media_log,
                conversation,
                "",
                "image",
                resolve_sender(self.phone, conversation=conversation),
            )
            self.assertEqual(intent, "staff_upload_batched")

        rows = list(
            PendingWhatsAppMedia.objects.filter(batch_key=expected_batch_key)
            .order_by("pk")
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual({row.batch_key for row in rows}, {expected_batch_key})
        self.assertEqual(
            {row.purpose for row in rows},
            {PendingWhatsAppMedia.PURPOSE_UNIT},
        )
        self.assertEqual(
            {row.target_kind for row in rows},
            {PendingWhatsAppMedia.TARGET_UNIT_PHOTO},
        )
        self.assertEqual({row.property_id for row in rows}, {f56.pk})
        self.assertEqual({row.unit_id for row in rows}, {f56_unit.pk})
        self.assertNotIn(f35.pk, {row.property_id for row in rows})
        self.assertNotIn(f54_unit.pk, {row.unit_id for row in rows})
        self.assertEqual(
            {row.submitted_by_staff_id for row in rows},
            {self.staff1.pk},
        )
        notify_pending.assert_not_called()

    @patch("whatsapp.services.whatsapp_ai.notify_staff_pending_request")
    def test_done_submits_once_and_returns_staff_menu(self, notify_pending):
        WhatsAppStaffPropertyAccess.objects.create(staff_user=self.staff1, property=self.property)
        batch_key = uuid.uuid4()
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            pending_state="staff_waiting_upload",
            context={
                "staff_upload_kind": PendingWhatsAppMedia.TARGET_LEASE_DOCUMENT,
                "staff_upload_batch_key": str(batch_key),
                "staff_upload_lease_id": self.lease.pk,
                "staff_upload_property_id": self.property.pk,
                "staff_upload_unit_id": self.unit.pk,
                "staff_upload_target_options": [{"type": "lease", "id": self.lease.pk}],
                "staff_upload_target_label": "Test Residency / A-04",
            },
        )
        assistant = WhatsAppAIAssistant(service=MagicMock())

        for index in range(1, 4):
            media_log = WhatsAppMessageLog.objects.create(
                direction="inbound",
                phone_number=self.staff1.whatsapp_number,
                wa_message_id=f"wamid.staff.batch.{index}",
                message_type="document",
                status="received",
                payload={
                    "type": "document",
                    "document": {"filename": f"file-{index}.pdf"},
                },
            )
            staged = PendingWhatsAppMedia.objects.create(
                conversation=conversation,
                phone=self.staff1.whatsapp_number,
                file=ContentFile(b"pdf", name=f"file-{index}.pdf"),
                original_filename=f"file-{index}.pdf",
                media_type="document",
            )
            media_log.api_response = {"simulator_pending_media_id": staged.pk}
            media_log.save(update_fields=["api_response"])

            response, intent, _metadata = assistant._handle_media_message(
                media_log,
                conversation,
                "",
                "document",
                resolve_sender(self.staff1.whatsapp_number, conversation=conversation),
            )

            self.assertEqual(intent, "staff_upload_batched")
            self.assertIn(f"Photo/file {index} added", response)
            notify_pending.assert_not_called()

        done_response = assistant._consume_staff_menu_state(
            self.message, conversation, "DONE", self.staff1
        )

        self.assertIn("3 file(s)", done_response[0])
        self.assertIn("Staff Inbox / Menu", done_response[0])
        notify_pending.assert_called_once()
        conversation.refresh_from_db()
        self.assertEqual(conversation.pending_state, "")
        for key in (
            "staff_upload_kind",
            "staff_upload_batch_key",
            "staff_upload_property_id",
            "staff_upload_unit_id",
            "staff_upload_lease_id",
            "staff_upload_target_options",
            "staff_upload_target_label",
        ):
            self.assertNotIn(key, conversation.context)

    @patch("whatsapp.services.whatsapp_ai.notify_staff_pending_request")
    def test_done_with_zero_files_does_not_notify(self, notify_pending):
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.staff1.whatsapp_number,
            staff_user=self.staff1,
            selected_mode=WhatsAppConversation.MODE_STAFF,
            pending_state="staff_waiting_upload",
            context={
                "staff_upload_kind": PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
                "staff_upload_batch_key": str(uuid.uuid4()),
                "staff_upload_property_id": self.property.pk,
                "staff_upload_unit_id": self.unit.pk,
                "staff_upload_target_label": "Test Residency / A-04",
            },
        )

        response = WhatsAppAIAssistant(
            service=MagicMock()
        )._consume_staff_menu_state(
            self.message, conversation, "DONE", self.staff1
        )

        self.assertEqual(response[1], "staff_upload_empty")
        self.assertIn("No files were received", response[0])
        self.assertNotIn("submitted for approval with", response[0])
        self.assertIn("Staff Inbox / Menu", response[0])
        notify_pending.assert_not_called()
        conversation.refresh_from_db()
        self.assertEqual(conversation.pending_state, "")
        self.assertNotIn("staff_upload_batch_key", conversation.context)

    def test_f56_flat_5_text_resolves_exact_unit(self):
        f35 = Property.objects.create(
            property_name="F35",
            owner_name="Owner",
            owner_cnic="37405-4000000-1",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        f54 = Property.objects.create(
            property_name="F54",
            owner_name="Owner",
            owner_cnic="37405-4000000-2",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        f56 = Property.objects.create(
            property_name="F56",
            owner_name="Owner",
            owner_cnic="37405-4000000-3",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        Unit.objects.create(
            property=f35, unit_number="F35-FLAT# 05", status="vacant"
        )
        Unit.objects.create(
            property=f54, unit_number="F54-FLAT# 05", status="vacant"
        )
        f56_unit = Unit.objects.create(
            property=f56, unit_number="F56-FLAT# 05", status="vacant"
        )
        assistant = WhatsAppAIAssistant(service=MagicMock())
        properties = [f35, f54, f56]

        for text in ("f56 flat 5", "F56-FLAT# 05"):
            property_obj, unit_hint = assistant._resolve_staff_property_unit_text(
                text, properties
            )
            unit = assistant._match_unit_text(
                unit_hint,
                property_obj,
                list(Unit.objects.filter(property=property_obj)),
            )
            self.assertEqual(property_obj, f56)
            self.assertEqual(unit, f56_unit)

    def test_shared_batch_groups_into_one_pending_approval_row(self):
        from core.views import _group_pending_media

        batch_key = uuid.uuid4()
        rows = [
            PendingWhatsAppMedia.objects.create(
                phone=self.phone,
                file=ContentFile(b"jpg", name=f"group-{index}.jpg"),
                original_filename=f"group-{index}.jpg",
                media_type="image",
                purpose=PendingWhatsAppMedia.PURPOSE_UNIT,
                target_kind=PendingWhatsAppMedia.TARGET_UNIT_PHOTO,
                batch_key=batch_key,
                property=self.property,
                unit=self.unit,
            )
            for index in range(4)
        ]

        grouped = _group_pending_media(rows)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0].pending_group_count, 4)
        self.assertEqual(grouped[0].pending_group_items, rows)

    def test_maintenance_menu_waits_for_details_before_showing_classification(self):
        response, intent, _metadata = WhatsAppAIAssistant(
            service=MagicMock()
        )._start_guided_maintenance(
            self.message,
            self.conversation,
            "3",
            self.lease,
        )

        self.assertEqual(intent, "maintenance_details_prompt")
        self.assertIn("location and details", response)
        self.assertNotIn("I read this as", response)
        self.assertNotIn("Other (normal)", response)

    def test_lease_photo_approval_routes_to_lease_gallery(self):
        from core.views import _attach_pending_media_from_core

        pending = PendingWhatsAppMedia.objects.create(
            conversation=self.conversation,
            phone=self.phone,
            file=ContentFile(b"jpg", name="lease-photo.jpg"),
            original_filename="lease-photo.jpg",
            media_type="image",
            purpose=PendingWhatsAppMedia.PURPOSE_LEASE,
            target_kind=PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
            lease=self.lease,
            tenant=self.tenant,
            property=self.property,
            unit=self.unit,
        )
        with patch("leases.models_lease_photos.LeaseMedia.objects.create") as create_gallery_photo:
            _attach_pending_media_from_core(pending, self.staff1)
        create_gallery_photo.assert_called_once()
        self.assertEqual(create_gallery_photo.call_args.kwargs["lease"], self.lease)
        self.assertEqual(create_gallery_photo.call_args.kwargs["media_type"], "image")

    def test_dual_role_tenant_intent_is_inferred(self):
        self.staff1.whatsapp_number = self.phone
        self.staff1.save(update_fields=["whatsapp_number"])
        sender = resolve_sender(self.phone)
        self.assertEqual(infer_mode("What is my balance?", sender)[0], "tenant")

    def test_dual_role_staff_intent_is_inferred(self):
        self.staff1.whatsapp_number = self.phone
        self.staff1.save(update_fields=["whatsapp_number"])
        sender = resolve_sender(self.phone)
        self.assertEqual(infer_mode("Show pending tenant handovers", sender)[0], "staff")

    def test_staff_notification_contains_tenant_number(self):
        service = MagicMock()
        service.send_text.return_value = {"ok": True}
        notify_new_handover(self.handover, service=service)
        self.assertIn(format_phone(self.phone), service.send_text.call_args.args[1])

    def test_staff_accepts_handover(self):
        accepted = accept_handover(self.handover, self.staff1)
        self.assertEqual(accepted.assigned_staff, self.staff1)

    def test_second_staff_cannot_accept_owned_handover(self):
        accept_handover(self.handover, self.staff1)
        with self.assertRaises(ValueError):
            accept_handover(self.handover, self.staff2)

    def test_staff_reply_is_relayed_to_tenant(self):
        accept_handover(self.handover, self.staff1)
        service = MagicMock()
        service.send_text.return_value = {"ok": True}
        relay_staff_reply(self.handover, self.staff1, "Receipt sent to accounts.", service=service)
        self.assertEqual(service.send_text.call_args.args[0], self.phone)
        self.assertIn("Management:", service.send_text.call_args.args[1])

    def test_tenant_reply_notifies_assigned_staff(self):
        accept_handover(self.handover, self.staff1)
        self.conversation.handover_active = True
        self.conversation.save(update_fields=["handover_active"])
        update = WhatsAppMessageLog.objects.create(
            direction="inbound", phone_number=self.phone, wa_message_id="wamid.update",
            message_type="text", status="received", payload={"type": "text", "text": {"body": "Any update?"}},
        )
        service = MagicMock()
        service.send_text.return_value = {"ok": True}
        reply = handle_active_tenant_message(update, self.conversation, "Any update?", service=service)
        self.assertIn("awaiting staff response", reply)
        self.assertEqual(service.send_text.call_args.args[0], self.staff1.whatsapp_number)

    def test_staff_marks_tenant_called(self):
        accept_handover(self.handover, self.staff1)
        mark_call_requested(self.handover, self.staff1)
        called = mark_called(self.handover, self.staff1)
        self.assertEqual(called.status, WhatsAppHandover.STATUS_CALLED)

    def test_call_action_shows_number_without_call_preference(self):
        accept_handover(self.handover, self.staff1)
        text = handle_staff_handover_message(self.message, self.conversation, f"CALL {self.handover.reference}", self.staff1)
        self.assertIn(format_phone(self.phone), text)
        self.assertNotIn("prefer", text.lower())

    def test_staff_closes_handover(self):
        accept_handover(self.handover, self.staff1)
        closed = close_handover(self.handover, self.staff1)
        self.assertEqual(closed.status, WhatsAppHandover.STATUS_CLOSED)
        closed.conversation.refresh_from_db()
        self.assertFalse(closed.conversation.handover_active)

    def test_staff_returns_handover_to_ai(self):
        accept_handover(self.handover, self.staff1)
        returned = return_handover_to_ai(self.handover, self.staff1)
        self.assertEqual(returned.status, WhatsAppHandover.STATUS_RETURNED_TO_AI)
        returned.conversation.refresh_from_db()
        self.assertEqual(returned.conversation.selected_mode, WhatsAppConversation.MODE_TENANT)

    def test_unauthorized_staff_cannot_access_handover(self):
        self.assertFalse(staff_can_access_handover(self.unauthorized, self.handover))
        with self.assertRaises(PermissionError):
            accept_handover(self.handover, self.unauthorized)

    def test_property_level_routing(self):
        self.assertEqual(eligible_staff(self.handover)[:2], [self.staff1, self.staff2])
        self.assertNotIn(self.unauthorized, eligible_staff(self.handover))

    def test_accounts_issue_routes_to_accounts_staff(self):
        config = GlobalSettings.get_solo()
        config.whatsapp_accounts_staff = self.staff1
        config.save(update_fields=["whatsapp_accounts_staff"])
        self.handover.department = "accounts"
        self.handover.save(update_fields=["department"])
        self.assertIn(self.staff1, eligible_staff(self.handover))

    def test_maintenance_issue_routes_to_maintenance_staff(self):
        config = GlobalSettings.get_solo()
        config.whatsapp_maintenance_staff = self.staff2
        config.save(update_fields=["whatsapp_maintenance_staff"])
        self.handover.department = "maintenance"
        self.handover.save(update_fields=["department"])
        self.assertIn(self.staff2, eligible_staff(self.handover))

    def test_media_reply_is_relayed(self):
        accept_handover(self.handover, self.staff1)
        media = PendingWhatsAppMedia.objects.create(
            conversation=self.conversation, phone=self.staff1.whatsapp_number,
            original_filename="photo.jpg", media_type="image", file=ContentFile(b"jpg", name="photo.jpg"),
        )
        service = MagicMock()
        service.send_text.return_value = {"ok": True}
        service.send_image_bytes.return_value = {"ok": True}
        relay_staff_reply(self.handover, self.staff1, "See attached", media=media, service=service)
        service.send_image_bytes.assert_called_once()

    def test_ambiguous_tenant_phone_is_blocked(self):
        other = Tenant.objects.create(first_name="Other", last_name="Tenant", phone=self.phone, cnic="37405-3333333-3")
        Lease.objects.create(
            tenant=other, unit=self.unit, start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=30), monthly_rent=10000, status="active",
        )
        sender = resolve_sender(self.phone)
        self.assertTrue(sender.ambiguous)
        self.assertIsNone(sender.tenant)
        self.assertFalse(sender.has_tenant)

    def test_tenant_mode_does_not_expose_staff_context(self):
        self.staff1.whatsapp_number = self.phone
        self.staff1.other = "private staff notes"
        self.staff1.save(update_fields=["whatsapp_number", "other"])
        sender = resolve_sender(self.phone)
        self.assertNotIn("private staff notes", str(sender.property_permissions))

    def test_conversation_history_does_not_cross_phone_numbers(self):
        from whatsapp.services.ai.context_builder import build_safe_context
        other_conversation = WhatsAppConversation.objects.create(phone_number="+923007777777")
        WhatsAppMessageLog.objects.create(
            direction="inbound", phone_number=other_conversation.phone_number, wa_message_id="wamid.secret",
            message_type="text", status="received", payload={"type": "text", "text": {"body": "OTHER TENANT SECRET"}},
        )
        context = build_safe_context(resolve_sender(self.phone), self.conversation, lease=self.lease)
        self.assertNotIn("OTHER TENANT SECRET", str(context))

    def test_conversation_summary_keeps_older_phone_after_many_newer_status_rows(self):
        from whatsapp.views import _conversation_summary

        older_phone = "+923007777778"
        WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=older_phone,
            wa_message_id="wamid.older.phone",
            message_type="text",
            status="received",
            payload={"type": "text", "text": {"body": "older conversation"}},
        )
        WhatsAppMessageLog.objects.bulk_create([
            WhatsAppMessageLog(
                direction=WhatsAppMessageLog.DIRECTION_STATUS,
                phone_number=self.phone,
                wa_message_id=f"wamid.status.{index}",
                message_type="status",
                status="delivered",
            )
            for index in range(305)
        ])

        phones = {row["phone_number"] for row in _conversation_summary()}

        self.assertIn(older_phone, phones)
        self.assertIn(self.phone, phones)

    def test_selected_conversation_context_includes_matching_phone_and_active_lease(self):
        from whatsapp.views import _selected_conversation_context

        context = _selected_conversation_context("+92-300-111-2233")

        self.assertEqual(context["tenant_id"], self.tenant.pk)
        self.assertEqual(context["tenant_phone"], self.phone)
        self.assertEqual(
            context["active_leases"],
            [
                {
                    "id": self.lease.pk,
                    "property_name": self.property.property_name,
                    "unit_number": self.unit.unit_number,
                    "end_date": self.lease.end_date,
                }
            ],
        )

    def test_conversation_filters_search_messages_tenant_and_location(self):
        from whatsapp.views import _conversation_summary, _filter_conversation_summary

        self.message.payload = {
            "type": "text",
            "text": {"body": "The kitchen tap is leaking badly"},
        }
        self.message.save(update_fields=["payload", "updated_at"])
        summary = _conversation_summary()

        message_result = _filter_conversation_summary(
            summary, search_query="kitchen tap"
        )
        tenant_result = _filter_conversation_summary(
            summary, tenant_id=self.tenant.pk
        )
        property_result = _filter_conversation_summary(
            summary, location=f"property:{self.property.pk}"
        )
        unit_result = _filter_conversation_summary(
            summary, location=f"unit:{self.unit.pk}"
        )

        for result in [message_result, tenant_result, property_result, unit_result]:
            self.assertEqual([row["phone_number"] for row in result], [self.phone])

    def test_whatsapp_filter_options_use_short_property_names_and_tenant_name_only(self):
        self.staff1.is_superuser = True
        self.staff1.save(update_fields=["is_superuser"])
        self.client.force_login(self.staff1)
        response = self.client.get(reverse("whatsapp:webhook_log_list"))

        property_option = next(
            option
            for option in response.context["property_filter_options"]
            if option["value"] == f"property:{self.property.pk}"
        )
        unit_option = next(
            option
            for option in response.context["unit_filter_options"]
            if option["value"] == f"unit:{self.unit.pk}"
        )
        tenant_select = re.search(
            r'id="whatsappTenantFilter".*?</select>',
            response.content.decode(),
            flags=re.DOTALL,
        ).group(0)

        self.assertEqual(property_option["label"], "All Test Res")
        self.assertEqual(unit_option["label"], "Test Res / A-04")
        self.assertIn(f">{self.tenant.get_full_name()}</option>", tenant_select)
        self.assertNotIn(self.tenant.phone, tenant_select)

    def test_duplicate_webhook_message_is_ignored(self):
        payload = {"entry": [{"id": "entry", "changes": [{"field": "messages", "value": {"messages": [{
            "from": self.phone, "id": "wamid.duplicate", "type": "text", "text": {"body": "hello"}
        }]}}]}]}
        with patch("whatsapp.views._queue_ai_message"):
            _log_webhook_payload(payload)
            _log_webhook_payload(payload)
        self.assertEqual(WhatsAppMessageLog.objects.filter(wa_message_id="wamid.duplicate").count(), 1)

    def test_database_processing_state_allows_only_one_worker(self):
        message = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=self.phone,
            wa_message_id="wamid.worker-race",
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": "hello"}},
        )

        with patch.object(WhatsAppAIAssistant, "handle_inbound_message") as handle:
            process_inbound_whatsapp_message(message)
            process_inbound_whatsapp_message(message)

        message.refresh_from_db()
        self.assertEqual(handle.call_count, 1)
        self.assertEqual(message.api_response["ai_processing"]["state"], "complete")

    @override_settings(WHATSAPP_APP_SECRET="test-secret")
    def test_webhook_signature_is_verified(self):
        body = json.dumps({"object": "whatsapp_business_account"}).encode()
        signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        response = self.client.post(
            reverse("whatsapp:webhook"), data=body, content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=f"sha256={signature}",
        )
        self.assertEqual(response.status_code, 200)
        denied = self.client.post(reverse("whatsapp:webhook"), data=body, content_type="application/json")
        self.assertEqual(denied.status_code, 403)


class SettingsEmbeddedLayoutTests(TestCase):
    """Phase 2: settings-tool pages must not render base.html's navbar when
    embedded, and must preserve ?embed=1 across their own redirects."""

    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="staff_embed", password="pass1234", is_staff=True, is_superuser=True
        )
        self.client.force_login(self.staff)

    def test_full_page_mode_renders_navbar(self):
        response = self.client.get(reverse("whatsapp:webhook_log_list"))
        self.assertContains(response, 'class="tms-nav')

    def test_embedded_mode_does_not_render_navbar(self):
        response = self.client.get(reverse("whatsapp:webhook_log_list") + "?embed=1")
        self.assertNotContains(response, 'class="tms-nav')

    def test_send_reply_from_embedded_iframe_stays_embedded(self):
        """Reproduces the reported bug: sending a WhatsApp reply from within
        the Settings > WhatsApp Logs iframe must redirect back into embedded
        mode, not to a full page (which renders its own navbar nested
        inside the outer Settings page's navbar)."""
        with patch(
            "whatsapp.services.whatsapp.WhatsAppService.send_text",
            return_value={"ok": True},
        ):
            response = self.client.post(
                reverse("whatsapp:webhook_log_list") + "?embed=1",
                {"phone_number": "03001234567", "message_text": "hello"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("embed=1", response["Location"])

    def test_send_reply_validation_error_stays_embedded(self):
        response = self.client.post(
            reverse("whatsapp:webhook_log_list") + "?embed=1",
            {"phone_number": "", "message_text": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("embed=1", response["Location"])

    def test_non_embedded_reply_redirect_has_no_embed_flag(self):
        with patch(
            "whatsapp.services.whatsapp.WhatsAppService.send_text",
            return_value={"ok": True},
        ):
            response = self.client.post(
                reverse("whatsapp:webhook_log_list"),
                {"phone_number": "03001234567", "message_text": "hello"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("embed=1", response["Location"])

    def test_utility_template_edit_redirect_preserves_embed(self):
        from whatsapp.models import WhatsAppUtilityTemplate

        template, _ = WhatsAppUtilityTemplate.objects.get_or_create(
            key="invoice_notice", defaults={"template_name": "invoice_notice", "language_code": "en"}
        )
        response = self.client.post(
            reverse("whatsapp:utility_template_edit", args=[template.pk]) + "?embed=1",
            {
                "template_name": "invoice_notice",
                "language_code": "en",
                "body_text": "",
                "body_variables": "[]",
                "button_label": "",
                "button_parameter_source": "",
                "is_active": "on",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("embed=1", response["Location"])


class TenantLatestLeaseResolutionTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(
            property_name="Context Property", owner_name="Owner", owner_cnic="37405-5656565-6",
            type="Residential", property_type="apartment", total_units=3,
        )
        self.unit1 = Unit.objects.create(property=self.property, unit_number="C-1")
        self.unit2 = Unit.objects.create(property=self.property, unit_number="C-2")
        self.phone = "03001234567"

    def _tenant(self, **kwargs):
        defaults = dict(
            first_name="Latest", last_name="Tenant", cnic="61101-1212121-1", phone=self.phone,
        )
        defaults.update(kwargs)
        return Tenant.objects.create(**defaults)

    def test_active_lease_is_preferred(self):
        tenant = self._tenant()
        Lease.objects.create(
            tenant=tenant, unit=self.unit1, start_date=date.today() - timedelta(days=400),
            end_date=date.today() - timedelta(days=30), monthly_rent=10000, status="ended",
        )
        active = Lease.objects.create(
            tenant=tenant, unit=self.unit2, start_date=date.today() - timedelta(days=10),
            end_date=date.today() + timedelta(days=300), monthly_rent=12000, status="active",
        )
        resolution = resolve_tenant_and_last_lease("+92-300-1234567")
        self.assertEqual(resolution.tenant, tenant)
        self.assertEqual(resolution.lease, active)
        self.assertEqual(resolution.lease_status, "active")

    def test_latest_ended_lease_is_returned(self):
        tenant = self._tenant()
        older = Lease.objects.create(
            tenant=tenant, unit=self.unit1, start_date=date.today() - timedelta(days=700),
            end_date=date.today() - timedelta(days=400), monthly_rent=9000, status="ended",
        )
        latest = Lease.objects.create(
            tenant=tenant, unit=self.unit2, start_date=date.today() - timedelta(days=300),
            end_date=date.today() - timedelta(days=20), monthly_rent=11000, status="terminated",
        )
        resolution = resolve_tenant_and_last_lease(self.phone)
        self.assertNotEqual(resolution.lease, older)
        self.assertEqual(resolution.lease, latest)
        self.assertEqual(resolution.lease_status, "ended")

    def test_pending_draft_is_not_used_as_latest_real_tenancy(self):
        tenant = self._tenant()
        ended = Lease.objects.create(
            tenant=tenant, unit=self.unit1, start_date=date.today() - timedelta(days=200),
            end_date=date.today() - timedelta(days=5), monthly_rent=10000, status="ended",
        )
        Lease.objects.create(
            tenant=tenant, unit=self.unit2, start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=300), monthly_rent=12000, status="pending_approval",
        )
        resolution = resolve_tenant_and_last_lease(self.phone)
        self.assertEqual(resolution.lease, ended)

    def test_tenant_without_lease_still_resolves(self):
        tenant = self._tenant()
        resolution = resolve_tenant_and_last_lease(self.phone)
        self.assertEqual(resolution.tenant, tenant)
        self.assertIsNone(resolution.lease)
        self.assertEqual(resolution.lease_status, "")

    def test_phone_format_variants_match_same_tenant(self):
        tenant = self._tenant(phone="+923001234567")
        self.assertEqual(resolve_tenant_and_last_lease("0300-1234567").tenant, tenant)


class PaymentClaimTests(TestCase):
    """Phase 3: WhatsApp payment-claim reply handling."""

    def setUp(self):
        self.phone = "+923001234567"
        self.property = Property.objects.create(
            property_name="Test Plaza", owner_name="Owner", owner_cnic="12345-1234567-8",
            type="residential", property_type="apartment", total_units=2,
        )
        self.unit = Unit.objects.create(property=self.property, unit_number="U-1")

    def _inbound(self, text, wa_id="wamid.claim"):
        return WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND, phone_number=self.phone,
            wa_message_id=wa_id, message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED, payload={"type": "text", "text": {"body": text}},
        )

    def test_active_tenant_payment_claim_returns_balance_and_latest_payment(self):
        tenant = Tenant.objects.create(first_name="Amina", last_name="Raza", cnic="61101-1111111-1", phone=self.phone)
        lease = Lease.objects.create(
            tenant=tenant, unit=self.unit, start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=335), monthly_rent=15000, status="active",
        )
        from core.models import PaymentMethod
        method, _ = PaymentMethod.objects.get_or_create(code="bank_transfer", defaults={"name": "Bank Transfer"})
        Payment.objects.create(lease=lease, amount=15000, payment_date=date.today() - timedelta(days=2), payment_method=method)

        conversation = WhatsAppConversation.objects.create(phone_number=self.phone, tenant=tenant, selected_lease=lease)
        message = self._inbound("I already paid")

        response, intent, metadata = WhatsAppAIAssistant(service=MagicMock())._handle(message, conversation)

        self.assertEqual(intent, "payment_claim")
        self.assertIn("Amina", response)
        self.assertIn("receipt", response.lower())
        self.assertIn("landlord", response.lower())
        conversation.refresh_from_db()
        self.assertEqual(conversation.pending_state, "tenant_waiting_payment_receipt")

    def test_ended_lease_tenant_payment_claim_is_recognized(self):
        """This is the core Phase 3 bug: a tenant whose only lease has ended
        was previously indistinguishable from a stranger and got no
        recognition at all."""
        tenant = Tenant.objects.create(first_name="Bilal", last_name="Sheikh", cnic="61101-2222222-2", phone=self.phone)
        Lease.objects.create(
            tenant=tenant, unit=self.unit, start_date=date.today() - timedelta(days=400),
            end_date=date.today() - timedelta(days=30), monthly_rent=12000, status="ended",
        )
        conversation = WhatsAppConversation.objects.create(phone_number=self.phone)
        message = self._inbound("Already paid")

        response, intent, metadata = WhatsAppAIAssistant(service=MagicMock())._handle(message, conversation)

        self.assertEqual(intent, "payment_claim")
        self.assertIn("Bilal", response)
        self.assertNotIn("guest", intent)

    def test_no_recent_payment_response_branch(self):
        tenant = Tenant.objects.create(first_name="Chaudhry", last_name="Amir", cnic="61101-3333333-3", phone=self.phone)
        lease = Lease.objects.create(
            tenant=tenant, unit=self.unit, start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=335), monthly_rent=10000, status="active",
        )
        conversation = WhatsAppConversation.objects.create(phone_number=self.phone, tenant=tenant, selected_lease=lease)
        message = self._inbound("payment done")

        response, intent, metadata = WhatsAppAIAssistant(service=MagicMock())._handle(message, conversation)

        self.assertEqual(intent, "payment_claim")
        self.assertIn("have not yet found", response.lower())

    def test_unmatched_phone_gets_generic_receipt_request(self):
        conversation = WhatsAppConversation.objects.create(phone_number=self.phone)
        message = self._inbound("I sent the money")

        response, intent, metadata = WhatsAppAIAssistant(service=MagicMock())._handle(message, conversation)

        self.assertEqual(intent, "payment_claim")
        self.assertIn("property, unit, or invoice number", response)

    def test_one_inbound_message_produces_one_reply(self):
        tenant = Tenant.objects.create(first_name="Dawood", last_name="Iqbal", cnic="61101-4444444-4", phone=self.phone)
        lease = Lease.objects.create(
            tenant=tenant, unit=self.unit, start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=335), monthly_rent=10000, status="active",
        )
        conversation = WhatsAppConversation.objects.create(phone_number=self.phone, tenant=tenant, selected_lease=lease)
        message = self._inbound("bill paid")

        result = WhatsAppAIAssistant(service=MagicMock())._handle(message, conversation)

        self.assertEqual(len(result), 3)
        self.assertIsInstance(result[0], str)

    def test_does_not_hijack_message_with_other_pending_state_in_progress(self):
        tenant = Tenant.objects.create(first_name="Erum", last_name="Wali", cnic="61101-5555555-5", phone=self.phone)
        lease = Lease.objects.create(
            tenant=tenant, unit=self.unit, start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=335), monthly_rent=10000, status="active",
        )
        conversation = WhatsAppConversation.objects.create(
            phone_number=self.phone, tenant=tenant, selected_lease=lease, pending_state="suggestion_capture",
        )
        message = self._inbound("already paid for the suggestion program")

        response, intent, metadata = WhatsAppAIAssistant(service=MagicMock())._handle(message, conversation)

        self.assertNotEqual(intent, "payment_claim")


class ChatExportTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(
            property_name="Export Plaza", owner_name="Owner", owner_cnic="12345-1234567-9",
            type="residential", property_type="apartment", total_units=2,
        )
        self.unit = Unit.objects.create(property=self.property, unit_number="E-1")
        self.tenant = Tenant.objects.create(
            first_name="Zara", last_name="Malik", cnic="61101-9999999-9", phone="03009998888",
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant, unit=self.unit, start_date=date.today(),
            end_date=date.today() + timedelta(days=300), monthly_rent=20000, status="active",
        )
        WhatsAppConversation.objects.create(
            phone_number="03009998888", tenant=self.tenant, selected_lease=self.lease, selected_mode="tenant",
        )
        self.msg_in = WhatsAppMessageLog.objects.create(
            direction="inbound", phone_number="03009998888", tenant=self.tenant, lease=self.lease,
            wa_message_id="wamid.export.in", message_type="text", status="received",
            payload={"type": "text", "text": {"body": "What is my balance?"}},
        )
        from whatsapp.models import WhatsAppAIInteractionLog
        WhatsAppAIInteractionLog.objects.create(
            message_log=self.msg_in, phone_number="03009998888", intent="balance", provider="openai",
            model="gpt-4o-mini", confidence=95, ai_prompt="system prompt api_key=sk-shouldnotleak123456",
            ai_response="Your balance is Rs 5000", prompt_tokens=100, completion_tokens=20,
        )
        self.msg_out = WhatsAppMessageLog.objects.create(
            direction="outbound", phone_number="03009998888", tenant=self.tenant, lease=self.lease,
            wa_message_id="wamid.export.out", message_type="text", status="sent",
            payload={"type": "text", "text": {"body": "Your outstanding balance is Rs. 5,000.00."}},
        )
        # A second, unrelated conversation to check isolation / all-chats aggregation.
        self.other_tenant = Tenant.objects.create(
            first_name="Omar", last_name="Sheikh", cnic="61101-8888888-8", phone="03007776666",
        )
        WhatsAppMessageLog.objects.create(
            direction="inbound", phone_number="03007776666", tenant=self.other_tenant,
            wa_message_id="wamid.other.in", message_type="text", status="received",
            payload={"type": "text", "text": {"body": "Hello"}},
        )
        from whatsapp.models import WhatsAppWebhookLog
        WhatsAppWebhookLog.objects.create(payload={"raw": "meta webhook status event"})

        User = get_user_model()
        self.staff = User.objects.create_user(
            username="export_staff", password="pass1234", is_staff=True, is_superuser=True,
        )

    def test_unauthenticated_user_cannot_export(self):
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888")
        self.assertNotEqual(response.status_code, 200)

    def test_unauthorized_user_cannot_export(self):
        User = get_user_model()
        User.objects.create_user(username="nobody", password="pass1234", is_staff=False, email="nobody@example.com")
        self.client.login(username="nobody", password="pass1234")
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888")
        self.assertNotEqual(response.status_code, 200)

    def test_authorized_user_can_export_selected_conversation(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888")
        self.assertEqual(response.status_code, 200)

    def test_content_type_is_json(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888")
        self.assertEqual(response["Content-Type"], "application/json")

    def test_content_disposition_has_attachment_filename(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".json", response["Content-Disposition"])

    def test_inbound_and_outbound_messages_included(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888&privacy=full")
        data = json.loads(response.content)
        directions = [m["direction"] for m in data["messages"]]
        self.assertIn("inbound", directions)
        self.assertIn("outbound", directions)
        texts = [m["message"] for m in data["messages"]]
        self.assertIn("What is my balance?", texts)
        self.assertIn("Your outstanding balance is Rs. 5,000.00.", texts)

    def test_messages_ordered_chronologically(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888")
        data = json.loads(response.content)
        timestamps = [m["timestamp"] for m in data["messages"]]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_ai_interaction_included_with_tokens_and_response(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888&privacy=full")
        data = json.loads(response.content)
        inbound = next(m for m in data["messages"] if m["direction"] == "inbound")
        self.assertEqual(len(inbound["ai_interactions"]), 1)
        ai = inbound["ai_interactions"][0]
        self.assertEqual(ai["intent"], "balance")
        self.assertEqual(ai["prompt_tokens"], 100)
        self.assertEqual(ai["completion_tokens"], 20)
        self.assertEqual(ai["response"], "Your balance is Rs 5000")

    def test_secrets_recursively_redacted(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888&privacy=full")
        self.assertNotIn("sk-shouldnotleak123456", response.content.decode())
        self.assertIn("[REDACTED]", response.content.decode())

    def test_masked_export_masks_phone_and_cnic(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888&privacy=masked")
        data = json.loads(response.content)
        self.assertNotEqual(data["conversation"]["phone_number"], "03009998888")

    def test_full_data_preserves_ordinary_fields(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888&privacy=full")
        data = json.loads(response.content)
        self.assertEqual(data["conversation"]["phone_number"], "03009998888")
        self.assertEqual(data["conversation"]["tenant"]["name"], self.tenant.get_full_name())
        self.assertEqual(data["conversation"]["lease"]["property"], "Export Plaza")
        self.assertEqual(data["conversation"]["lease"]["unit"], "E-1")

    def test_single_conversation_does_not_leak_other_phone(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888&privacy=full")
        self.assertNotIn("Hello", response.content.decode())
        self.assertNotIn("03007776666", response.content.decode())

    def test_export_all_includes_multiple_conversations(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_all_chats") + "?privacy=full")
        data = json.loads(response.content)
        self.assertGreaterEqual(data["export"]["conversation_count"], 2)
        phones = [c["conversation"]["phone_number"] for c in data["conversations"]]
        self.assertTrue(any("3009998888" in p for p in phones))
        self.assertTrue(any("3007776666" in p for p in phones))

    def test_raw_webhook_logs_not_included(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_all_chats") + "?privacy=full")
        self.assertNotIn("meta webhook status event", response.content.decode())

    def test_no_media_binary_or_base64_exported(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888")
        content = response.content.decode()
        # A crude but effective check: no long base64-looking blob present.
        self.assertNotRegex(content, r"[A-Za-z0-9+/]{200,}={0,2}")

    def test_media_metadata_included_when_present(self):
        from whatsapp.models import PendingWhatsAppMedia
        media_msg = WhatsAppMessageLog.objects.create(
            direction="inbound", phone_number="03009998888", tenant=self.tenant,
            wa_message_id="wamid.export.media", message_type="image", status="received",
            payload={"type": "image"},
        )
        PendingWhatsAppMedia.objects.create(
            original_whatsapp_message=media_msg, media_type="image", purpose="payment",
            original_filename="receipt.jpg", whatsapp_media_id="media123", status="pending",
        )
        self.client.force_login(self.staff)
        response = self.client.get(reverse("whatsapp:export_chat") + "?phone=03009998888&privacy=full")
        data = json.loads(response.content)
        media_row = next(m for m in data["messages"] if m["wa_message_id"] == "wamid.export.media")
        self.assertIsNotNone(media_row["media"])
        self.assertEqual(media_row["media"]["purpose"], "payment")
        self.assertEqual(media_row["media"]["filename"], "receipt.jpg")
