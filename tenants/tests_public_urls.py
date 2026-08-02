from types import SimpleNamespace
from urllib.parse import unquote
from unittest.mock import patch

from django.core import signing
from django.test import SimpleTestCase

from core.public_urls import build_public_url
from tenants.models import Tenant
from tenants.views import (
    TENANT_REGISTRATION_SALT,
    _registration_link_payload,
    tenant_registration_message,
    tenant_registration_token,
)


class PublicRegistrationInvitationTests(SimpleTestCase):
    def setUp(self):
        self.tenant = Tenant(
            pk=55,
            first_name="Muneera",
            last_name="Khan",
            phone="03001234567",
            cnic="",
            is_active=False,
        )

    def test_signed_invitation_uses_canonical_kirayas_url_and_remains_valid(self):
        token = tenant_registration_token(self.tenant)
        url = build_public_url(
            "tenants:tenant_public_registration", args=[token]
        )

        self.assertTrue(url.startswith("https://kirayas.com/tenants/registration/"))
        self.assertIn(token, url)
        self.assertNotIn("tms.sonazconsultancy.online", url)
        self.assertNotIn("sonazconsultancy.online", url)
        self.assertNotIn("/tms/tenants/", url)
        self.assertEqual(
            signing.loads(token, salt=TENANT_REGISTRATION_SALT),
            {"tenant_id": self.tenant.pk},
        )

    def test_whatsapp_registration_message_contains_exact_kirayas_link(self):
        url = build_public_url(
            "tenants:tenant_public_registration",
            args=[tenant_registration_token(self.tenant)],
        )
        message = tenant_registration_message(self.tenant, url)

        self.assertIn(f"Hello {self.tenant.get_full_name()},", message)
        self.assertIn(url, message)
        self.assertIn("This link will expire in 7 days.", message)
        self.assertNotIn("/tms/", message)

    def test_registration_payload_whatsapp_workflow_uses_kirayas_message(self):
        request = SimpleNamespace(
            user=SimpleNamespace(whatsapp_number="03009999999")
        )
        with patch(
            "tenants.views.GlobalSettings.get_solo",
            return_value=SimpleNamespace(
                whatsapp_number="",
                country_code="+92",
            ),
        ):
            payload = _registration_link_payload(request, self.tenant)

        whatsapp_url = unquote(payload["whatsapp_url"])
        self.assertIn(payload["link"], whatsapp_url)
        self.assertIn("This link will expire in 7 days.", whatsapp_url)
        self.assertNotIn("/tms/", whatsapp_url)
