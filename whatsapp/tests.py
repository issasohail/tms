from datetime import timedelta
from decimal import Decimal
import hashlib
import hmac
import importlib
import json
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.apps import apps as django_apps
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from whatsapp.services.payment_matching import extract_payment_text_fields
from whatsapp.services.whatsapp_ai import WhatsAppAIAssistant, detect_intent
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
from whatsapp.services.role_mode import resolve_mode
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
                codename__in=["change_globalsettings", "view_globalsettings"]
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
        payment = PendingWhatsAppPayment.objects.get(pk=metadata["pending_payment_id"])
        self.conversation.refresh_from_db()
        self.assertEqual(intent, "payment_pending")
        self.assertIn("Amount: Rs. 63,580.00", response)
        self.assertIn("Date: 20-07-2026", response)
        self.assertIn("confirmation shortly after bank verification", response)
        self.assertEqual(staged.purpose, PendingWhatsAppMedia.PURPOSE_PAYMENT)
        self.assertEqual(payment.amount, Decimal("63580.00"))
        self.assertEqual(payment.status, PendingWhatsAppPayment.STATUS_PENDING)
        self.assertEqual(self.conversation.pending_state, "")
        self.assertIn("6. Upload Payment Receipt", WhatsAppAIAssistant()._tenant_welcome_menu(self.lease))

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
        self.assertEqual(conversation.pending_state, "staff_waiting_upload")
        self.assertEqual(
            conversation.context["staff_upload_kind"],
            PendingWhatsAppMedia.TARGET_LEASE_PHOTO,
        )
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

    def test_duplicate_webhook_message_is_ignored(self):
        payload = {"entry": [{"id": "entry", "changes": [{"field": "messages", "value": {"messages": [{
            "from": self.phone, "id": "wamid.duplicate", "type": "text", "text": {"body": "hello"}
        }]}}]}]}
        with patch("whatsapp.views._queue_ai_message"):
            _log_webhook_payload(payload)
            _log_webhook_payload(payload)
        self.assertEqual(WhatsAppMessageLog.objects.filter(wa_message_id="wamid.duplicate").count(), 1)

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
