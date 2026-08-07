from html import unescape
from urllib.parse import parse_qs, unquote, urlsplit

from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse


MARKETING_URLCONF = "tms.marketing_urls"
PUBLIC_HOST = "kirayas.com"


class MarketingRouteTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST=PUBLIC_HOST)

    def marketing_url(self, name):
        return reverse(name, urlconf=MARKETING_URLCONF)

    def test_named_routes_reverse_and_render(self):
        names = (
            "marketing_home",
            "marketing_features",
            "marketing_how_it_works",
            "marketing_pricing",
            "marketing_faq",
            "marketing_contact",
            "marketing_privacy",
            "marketing_terms",
            "marketing_security",
            "marketing_support",
        )
        for name in names:
            with self.subTest(name=name):
                url = self.marketing_url(name)
                self.assertTrue(url.startswith("/"))
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_home_uses_static_assets_named_links_and_existing_auth_routes(self):
        response = self.client.get(self.marketing_url("marketing_home"))
        body = unescape(response.content.decode())
        self.assertContains(response, "/static/marketing/css/site.css")
        self.assertContains(response, "/static/marketing/js/site.js")
        self.assertContains(response, "/static/marketing/video/kirayas-demo.mp4")
        self.assertContains(response, 'class="home-demo-video"')
        self.assertIn(
            "https://kirayas.com/tms/accounts/login/?next=/tms/",
            body,
        )
        self.assertIn("https://kirayas.com/tms/accounts/signup/", body)
        self.assertNotIn('href="features.html"', body)
        self.assertNotIn("assets/site.", body)

    def test_all_marketing_pages_include_whatsapp_overlay(self):
        for name in (
            "marketing_home",
            "marketing_features",
            "marketing_how_it_works",
            "marketing_pricing",
            "marketing_faq",
            "marketing_contact",
            "marketing_privacy",
            "marketing_terms",
            "marketing_security",
            "marketing_support",
        ):
            with self.subTest(name=name):
                response = self.client.get(self.marketing_url(name))
                body = unescape(response.content.decode())
                self.assertIn("marketing-whatsapp", body)
                self.assertIn('href="/whatsapp/"', body)

    @override_settings(MARKETING_WHATSAPP_NUMBER="+92 (300) 7654321")
    def test_whatsapp_overlay_route_opens_prefilled_message(self):
        response = self.client.get(self.marketing_url("marketing_whatsapp"))
        self.assertEqual(response.status_code, 302)
        target = urlsplit(response["Location"])
        self.assertEqual(
            (target.scheme, target.netloc, target.path),
            ("https", "wa.me", "/923007654321"),
        )
        message = unquote(parse_qs(target.query)["text"][0])
        self.assertIn("Hello Kirayas.com", message)
        self.assertIn("rental management platform", message)

    def test_public_host_root_renders_marketing_home(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "marketing/index.html")

    def test_lan_host_root_uses_local_account_urls(self):
        response = Client(HTTP_HOST="192.168.100.28:8001").get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "marketing/index.html")
        self.assertContains(
            response,
            "http://192.168.100.28:8001/accounts/login/?next=/dashboard/",
        )
        self.assertContains(
            response,
            "http://192.168.100.28:8001/accounts/signup/",
        )

    def test_public_pages_do_not_expose_authenticated_records(self):
        response = self.client.get(self.marketing_url("marketing_home"))
        body = response.content.decode().lower()
        self.assertNotIn("tenant ledger", body)
        self.assertNotIn("logout", body)
        self.assertNotIn("/media/", body)

    def test_existing_tms_routes_still_resolve(self):
        self.assertEqual(resolve("/login/").url_name, "login")
        self.assertEqual(resolve("/tms/accounts/signup/").url_name, "signup")
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("/tms/accounts/login/"))

    def test_tms_path_uses_application_routes_on_public_host(self):
        response = self.client.get("/tms/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "/accounts/login/?next=/tms/",
        )

    def test_legacy_public_account_path_redirects_under_tms_prefix(self):
        response = self.client.get("/accounts/signup/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "https://kirayas.com/tms/accounts/signup/",
        )

    def test_legacy_invoice_path_redirects_under_tms_prefix(self):
        response = self.client.get("/invoices/", {"status": "draft"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "https://kirayas.com/tms/invoices/?status=draft",
        )

    def test_nested_legacy_invoice_path_redirects_under_tms_prefix(self):
        response = self.client.get("/invoices/monthly-billing/12/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "https://kirayas.com/tms/invoices/monthly-billing/12/",
        )

    def test_authenticated_tms_sections_redirect_under_tms_prefix(self):
        paths = (
            "/properties/",
            "/payments/",
            "/tenants/",
            "/leases/",
            "/expenses/",
            "/utilities/",
            "/smart-meter/live/custom/",
            "/reports/reports/",
            "/maintenance/",
            "/handyman/",
            "/settings/",
            "/pending-approvals/",
            "/suggestions/",
            "/notifications/",
            "/whatsapp/simulator/",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    response["Location"],
                    f"https://kirayas.com/tms{path}",
                )

    def test_special_tms_paths_redirect_to_their_legacy_targets(self):
        cases = {
            "/dashboard/": "https://kirayas.com/tms/",
            "/login/": "https://kirayas.com/tms/accounts/login/",
            "/logout/": "https://kirayas.com/tms/accounts/logout/",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], expected)

    def test_public_whatsapp_page_is_not_redirected_to_tms(self):
        response = self.client.get("/whatsapp/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://wa.me/"))

    def test_www_redirect_is_permanent_and_preserves_path_and_query(self):
        response = Client(HTTP_HOST="www.kirayas.com").get(
            "/pricing/", {"source": "website"}
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response["Location"],
            "https://kirayas.com/pricing/?source=website",
        )


@override_settings(MARKETING_PUBLIC_BASE_URL="https://kirayas.com")
class LogoutRedirectTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST=PUBLIC_HOST)
        self.user = get_user_model().objects.create_user(
            username="logout-user",
            email="logout@example.com",
            password="test-password",
        )

    def assert_logout_clears_session_and_redirects_home(self, method):
        self.client.force_login(self.user)
        session = self.client.session
        session["logout-test-marker"] = "present"
        session.save()
        self.assertIn(SESSION_KEY, self.client.session)

        response = getattr(self.client, method)("/tms/accounts/logout/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://kirayas.com/")
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertNotIn("logout-test-marker", self.client.session)

    def test_get_logout_clears_session_and_redirects_to_marketing_home(self):
        self.assert_logout_clears_session_and_redirects_home("get")

    def test_post_logout_clears_session_and_redirects_to_marketing_home(self):
        self.assert_logout_clears_session_and_redirects_home("post")


class MarketingContactTests(SimpleTestCase):
    valid_data = {
        "full_name": "Ayesha Khan",
        "business_name": "Khan Properties",
        "phone": "+92 300 1112233",
        "email": "ayesha@example.com",
        "units": "11–25",
        "plan": "professional",
        "message": "Please share onboarding details & timing.",
    }

    def setUp(self):
        self.client = Client(HTTP_HOST=PUBLIC_HOST)
        self.url = reverse("marketing_contact", urlconf=MARKETING_URLCONF)

    def test_missing_required_fields_are_rejected(self):
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "message", "This field is required.")
        for field_name in (
            "full_name",
            "business_name",
            "phone",
            "email",
            "units",
            "plan",
        ):
            self.assertNotIn(field_name, response.context["form"].errors)

    @override_settings(MARKETING_WHATSAPP_NUMBER="+92 (300) 7654321")
    def test_message_only_contact_opens_whatsapp(self):
        response = self.client.post(self.url, {"message": "Please tell me more."})
        self.assertEqual(response.status_code, 302)
        message = unquote(parse_qs(urlsplit(response["Location"]).query)["text"][0])
        self.assertIn("Name: Not provided", message)
        self.assertIn("Interested Plan: Not specified", message)
        self.assertIn("Please tell me more.", message)

    @override_settings(MARKETING_WHATSAPP_NUMBER="+92 (300) 7654321")
    def test_valid_contact_is_safely_encoded_to_expected_whatsapp_url(self):
        response = self.client.post(self.url, self.valid_data)
        self.assertEqual(response.status_code, 302)
        target = urlsplit(response["Location"])
        self.assertEqual((target.scheme, target.netloc, target.path), ("https", "wa.me", "/923007654321"))
        message = unquote(parse_qs(target.query)["text"][0])
        self.assertIn("New Kirayas.com Website Inquiry", message)
        self.assertIn("Name: Ayesha Khan", message)
        self.assertIn("Interested Plan: Professional", message)
        self.assertIn("Please share onboarding details & timing.", message)

    @override_settings(MARKETING_WHATSAPP_NUMBER="javascript:alert(1)")
    def test_invalid_destination_never_redirects(self):
        response = self.client.post(self.url, self.valid_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WhatsApp contact is temporarily unavailable")
