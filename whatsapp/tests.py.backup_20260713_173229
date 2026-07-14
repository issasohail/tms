from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase
from django.utils import timezone

from whatsapp.services.payment_matching import extract_payment_text_fields
from whatsapp.services.whatsapp_ai import detect_intent


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
