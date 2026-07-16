from datetime import timedelta
from decimal import Decimal
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from whatsapp.services.payment_matching import extract_payment_text_fields
from whatsapp.services.whatsapp_ai import detect_intent
from core.models import GlobalSettings
from core.utils.identity import format_phone
from leases.models import Lease
from properties.models import Property, Unit
from tenants.models import Tenant
from whatsapp.models import (
    PendingWhatsAppMedia,
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
