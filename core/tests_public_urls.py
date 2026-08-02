from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from core.public_urls import build_public_path_url, build_public_url


class PublicUrlBuilderTests(SimpleTestCase):
    def test_named_url_is_absolute_without_request_or_duplicate_slashes(self):
        url = build_public_url("tenants:tenant_public_registration", args=["abc:def"])

        self.assertEqual(
            url,
            "https://kirayas.com/tenants/registration/abc:def/",
        )
        self.assertNotIn("//tenants", url)

    @override_settings(PUBLIC_BASE_URL="https://staging.example.com///")
    def test_environment_override_is_used(self):
        self.assertEqual(
            build_public_url("accounts:signup"),
            "https://staging.example.com/accounts/signup/",
        )

    def test_query_values_are_url_encoded_and_repeated_values_are_preserved(self):
        url = build_public_url(
            "tenants:tenant_public_registration",
            args=["signed:value"],
            query={"next": "/tenant files/", "tag": ["one/two", "three"]},
        )
        parsed = urlsplit(url)

        self.assertEqual(parsed.path, "/tenants/registration/signed:value/")
        self.assertEqual(
            parse_qs(parsed.query),
            {"next": ["/tenant files/"], "tag": ["one/two", "three"]},
        )

    def test_relative_django_reverse_remains_relative(self):
        self.assertEqual(reverse("accounts:signup"), "/accounts/signup/")

    def test_protected_document_share_uses_view_not_direct_media(self):
        url = build_public_url("public_file_share_root", args=["secure-token"])

        self.assertEqual(url, "https://kirayas.com/public/files/secure-token/")
        self.assertNotIn("/media/", url)

    def test_existing_query_string_can_be_extended(self):
        url = build_public_path_url("/accounts/login/?next=/", query={"source": "email"})

        self.assertEqual(
            url,
            "https://kirayas.com/accounts/login/?next=/&source=email",
        )
