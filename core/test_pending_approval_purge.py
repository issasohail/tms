from datetime import timedelta
from io import StringIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.pending_approval_purge import hard_delete_pending_objects
from whatsapp.models import PendingWhatsAppMedia, PendingWhatsAppPayment


class PendingApprovalPurgeTests(TransactionTestCase):
    def setUp(self):
        self.media_dir = TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def _media(self, *, status=PendingWhatsAppMedia.STATUS_REJECTED):
        return PendingWhatsAppMedia.objects.create(
            file=SimpleUploadedFile("pending-photo.jpg", b"pending-photo-bytes"),
            original_filename="pending-photo.jpg",
            media_type="image/jpeg",
            status=status,
        )

    def test_hard_delete_removes_unreferenced_physical_file(self):
        media = self._media()
        storage = media.file.storage
        name = media.file.name
        self.assertTrue(storage.exists(name))

        hard_delete_pending_objects([media])

        self.assertFalse(PendingWhatsAppMedia.objects.filter(pk=media.pk).exists())
        self.assertFalse(storage.exists(name))

    def test_shared_file_is_retained_until_last_reference_is_deleted(self):
        media = self._media()
        storage = media.file.storage
        name = media.file.name
        payment = PendingWhatsAppPayment.objects.create(
            screenshot=name,
            status=PendingWhatsAppPayment.STATUS_REJECTED,
            rejected=True,
        )

        hard_delete_pending_objects([media])
        self.assertTrue(storage.exists(name))

        hard_delete_pending_objects([payment])
        self.assertFalse(storage.exists(name))

    def test_management_command_is_dry_run_by_default_then_executes(self):
        media = self._media()
        old_time = timezone.now() - timedelta(days=31)
        PendingWhatsAppMedia.objects.filter(pk=media.pk).update(created_at=old_time)
        output = StringIO()

        call_command("purge_pending_approvals", days=30, stdout=output)
        self.assertTrue(PendingWhatsAppMedia.objects.filter(pk=media.pk).exists())
        self.assertIn("Dry run only", output.getvalue())

        call_command(
            "purge_pending_approvals",
            days=30,
            execute=True,
            stdout=StringIO(),
        )
        self.assertFalse(PendingWhatsAppMedia.objects.filter(pk=media.pk).exists())


class PendingApprovalAuthorizationTests(TransactionTestCase):
    def setUp(self):
        self.media_dir = TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_user(
            username="queue-manager",
            email="queue-manager@example.com",
            password="test-password",
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="change_globalsettings")
        )
        self.client.force_login(self.user)
        self.media = PendingWhatsAppMedia.objects.create(
            file=SimpleUploadedFile("approval.jpg", b"approval-bytes"),
            original_filename="approval.jpg",
            media_type="image/jpeg",
        )

    def test_core_queue_permission_alone_cannot_approve_media(self):
        response = self.client.post(
            reverse(
                "core:pending_approval_approve",
                args=["media", self.media.pk],
            )
        )

        self.assertEqual(response.status_code, 403)
        self.media.refresh_from_db()
        self.assertEqual(self.media.status, PendingWhatsAppMedia.STATUS_PENDING)
