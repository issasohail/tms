from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse


class PublicRegistrationSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.signup_url = reverse("accounts:signup")
        self.valid_data = {
            "username": "pending-applicant",
            "email": "pending-applicant@example.com",
            "first_name": "Pending",
            "last_name": "Applicant",
            "whatsapp_number": "+923001234567",
            "password1": "Strong-Test-Password-927!",
            "password2": "Strong-Test-Password-927!",
            "website": "",
        }

    def test_signup_page_is_public(self):
        self.assertEqual(self.client.get(self.signup_url).status_code, 200)

    def test_signup_creates_inactive_unprivileged_user_without_login(self):
        submitted = {
            **self.valid_data,
            "is_active": "on",
            "is_staff": "on",
            "is_superuser": "on",
            "groups": ["1"],
            "user_permissions": ["1"],
        }

        response = self.client.post(self.signup_url, submitted)

        self.assertRedirects(response, reverse("login"))
        user = get_user_model().objects.get(username="pending-applicant")
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.groups.exists())
        self.assertFalse(user.user_permissions.exists())
        self.assertNotIn("_auth_user_id", self.client.session)
        protected_response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(protected_response.status_code, 302)
        self.assertIn("/accounts/login/", protected_response.url)

    def test_pending_user_cannot_login_and_gets_pending_message(self):
        self.client.post(self.signup_url, self.valid_data)

        response = self.client.post(
            reverse("login"),
            {"username": "pending-applicant", "password": self.valid_data["password1"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Your registration is awaiting administrator approval.",
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_honeypot_rejects_bot_submission(self):
        response = self.client.post(
            self.signup_url,
            {**self.valid_data, "website": "https://spam.example"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            get_user_model().objects.filter(username="pending-applicant").exists()
        )

    def test_signup_rate_limit_blocks_sixth_attempt(self):
        for index in range(5):
            response = self.client.post(
                self.signup_url,
                {**self.valid_data, "username": f"bad-{index}", "password2": "different"},
                REMOTE_ADDR="192.0.2.10",
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            self.signup_url,
            {**self.valid_data, "username": "rate-limited"},
            REMOTE_ADDR="192.0.2.10",
        )

        self.assertEqual(response.status_code, 429)
        self.assertFalse(
            get_user_model().objects.filter(username="rate-limited").exists()
        )

    def test_signup_post_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            csrf_client.post(self.signup_url, self.valid_data).status_code,
            403,
        )


class RegistrationApprovalTests(TestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model().objects
        self.pending = users.create_user(
            username="approval-target",
            email="approval-target@example.com",
            password="Strong-Test-Password-927!",
            is_active=False,
        )
        self.ordinary = users.create_user(
            username="ordinary-user",
            email="ordinary@example.com",
            password="Strong-Test-Password-927!",
        )
        self.approver = users.create_user(
            username="registration-approver",
            email="approver@example.com",
            password="Strong-Test-Password-927!",
        )
        self.approver.user_permissions.add(
            Permission.objects.get(codename="change_account")
        )

    def test_unauthorized_user_cannot_approve(self):
        self.client.force_login(self.ordinary)

        self.client.post(
            reverse("accounts:user_registration_approve", args=[self.pending.pk])
        )

        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)

    def test_authorized_user_can_approve_then_user_can_login(self):
        self.client.force_login(self.approver)
        response = self.client.post(
            reverse("accounts:user_registration_approve", args=[self.pending.pk])
        )

        self.assertRedirects(
            response,
            reverse("accounts:user_access_list"),
            fetch_redirect_response=False,
        )
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.is_active)
        self.client.logout()
        self.assertTrue(
            self.client.login(
                username="approval-target",
                password="Strong-Test-Password-927!",
            )
        )

    def test_reject_keeps_pending_account_inactive(self):
        self.client.force_login(self.approver)

        self.client.post(
            reverse("accounts:user_registration_reject", args=[self.pending.pk])
        )

        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)
        self.ordinary.refresh_from_db()
        self.assertTrue(self.ordinary.is_active)
