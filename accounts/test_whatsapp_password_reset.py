from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.whatsapp_password_reset import (
    PASSWORD_RESET_REQUEST_TEXT,
    create_whatsapp_password_reset_token,
    handle_whatsapp_password_reset_request,
    is_whatsapp_password_reset_request,
    resolve_whatsapp_password_reset_token,
)
from core.models import GlobalSettings
from whatsapp.models import WhatsAppMessageLog
from whatsapp.services.queue import enqueue_whatsapp_ai_message
from whatsapp.views import _log_webhook_payload


@override_settings(
    PUBLIC_BASE_URL="https://kirayas.com",
    MARKETING_WHATSAPP_NUMBER="+923001234567",
)
class WhatsAppPasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="reset-user",
            email="reset@example.com",
            whatsapp_number="+923009990001",
            password="Old-password-123!",
            is_active=True,
        )

    def _inbound_log(self, phone_number="923009990001", body=PASSWORD_RESET_REQUEST_TEXT):
        return WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_INBOUND,
            phone_number=phone_number,
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_RECEIVED,
            payload={"type": "text", "text": {"body": body}},
        )

    def test_login_shows_eye_and_whatsapp_reset_link(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, "js-password-toggle")
        self.assertContains(response, "https://wa.me/923001234567")
        self.assertContains(response, "Forgot password?")

    def test_reset_command_is_case_and_terminal_punctuation_tolerant(self):
        self.assertTrue(
            is_whatsapp_password_reset_request(
                {"type": "text", "text": {"body": "i FORGOT my password and I would like to change it!"}}
            )
        )
        self.assertFalse(
            is_whatsapp_password_reset_request(
                {"type": "text", "text": {"body": "Please reset somebody else's password"}}
            )
        )

    def test_verified_number_receives_one_time_reset_link_and_log_is_redacted(self):
        inbound = self._inbound_log()
        outbound = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_OUTBOUND,
            phone_number=inbound.phone_number,
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            payload={"text": {"body": "temporary reset link"}},
        )
        with patch(
            "accounts.whatsapp_password_reset.WhatsAppService.send_text",
            return_value={"ok": True, "log_id": outbound.pk},
        ) as send_text:
            handled = handle_whatsapp_password_reset_request(inbound)

        self.assertTrue(handled)
        message = send_text.call_args.args[1]
        self.assertIn("https://kirayas.com/accounts/whatsapp-reset/", message)
        self.assertIn("within 20 minutes", message)
        outbound.refresh_from_db()
        self.assertNotIn("https://", str(outbound.payload))
        inbound.refresh_from_db()
        self.assertEqual(inbound.api_response["password_reset"]["state"], "complete")

    def test_unregistered_number_does_not_receive_a_reset_link(self):
        inbound = self._inbound_log(phone_number="923009990099")
        with patch(
            "accounts.whatsapp_password_reset.WhatsAppService.send_text",
            return_value={"ok": True},
        ) as send_text:
            handle_whatsapp_password_reset_request(inbound)

        self.assertNotIn("https://", send_text.call_args.args[1])
        self.assertIn("could not verify", send_text.call_args.args[1])

    def test_reset_page_requires_matching_passwords_and_invalidates_used_token(self):
        token = create_whatsapp_password_reset_token(self.user)
        url = reverse("accounts:whatsapp_password_reset_confirm", kwargs={"token": token})

        response = self.client.get(url)
        self.assertContains(response, 'class="password-toggle js-password-toggle"', count=2)
        response = self.client.post(
            url,
            {
                "new_password1": "New-secure-password-456!",
                "new_password2": "New-secure-password-456!",
            },
        )

        self.assertRedirects(response, reverse("accounts:login"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New-secure-password-456!"))
        self.assertIsNone(resolve_whatsapp_password_reset_token(token))
        self.assertContains(self.client.get(url), "This reset link has expired")

    def test_ambiguous_registered_number_is_not_given_a_link(self):
        get_user_model().objects.create_user(
            username="duplicate-number",
            email="duplicate@example.com",
            whatsapp_number=self.user.whatsapp_number,
            password="Other-password-123!",
            is_active=True,
        )
        inbound = self._inbound_log()
        with patch(
            "accounts.whatsapp_password_reset.WhatsAppService.send_text",
            return_value={"ok": True},
        ) as send_text:
            handle_whatsapp_password_reset_request(inbound)

        self.assertNotIn("https://", send_text.call_args.args[1])

    def test_webhook_forces_reset_processing_when_ai_is_disabled(self):
        payload = {
            "entry": [
                {
                    "id": "business-id",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"display_phone_number": "+923001234567"},
                                "messages": [
                                    {
                                        "id": "wamid-password-reset-test",
                                        "from": "923009990001",
                                        "type": "text",
                                        "text": {"body": PASSWORD_RESET_REQUEST_TEXT},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ]
        }
        with patch("whatsapp.views._queue_ai_message") as queue_message:
            _log_webhook_payload(payload)

        self.assertTrue(queue_message.call_args.kwargs["force"])

    def test_force_option_bypasses_disabled_ai_queue_guard(self):
        disabled_config = type("Config", (), {"enabled": False, "use_celery": False})()
        with (
            patch("whatsapp.services.queue.get_whatsapp_ai_config", return_value=disabled_config),
            patch("whatsapp.services.queue.threading.Thread") as thread,
        ):
            result = enqueue_whatsapp_ai_message(123, force=True)

        self.assertEqual(result, "thread")
        thread.return_value.start.assert_called_once_with()


class WhatsAppPasswordResetConfigurationTests(TestCase):
    @override_settings(MARKETING_WHATSAPP_NUMBER="")
    def test_login_uses_global_whatsapp_number_when_environment_value_is_blank(self):
        GlobalSettings.objects.create(pk=1, whatsapp_number="+923001112222")

        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, "https://wa.me/923001112222")
