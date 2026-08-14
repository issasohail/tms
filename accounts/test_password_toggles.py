from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse


class PasswordVisibilityToggleTests(TestCase):
    def test_login_page_has_accessible_password_toggle(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, 'class="btn password-toggle js-password-toggle"')
        self.assertContains(response, 'aria-label="Show password"')
        self.assertContains(response, "fa-eye-slash")

    def test_user_access_page_has_password_and_confirmation_toggles(self):
        user = get_user_model().objects.create_user(
            username="password-toggle-admin",
            email="password-toggle@example.com",
            password="test-password",
        )
        user.user_permissions.add(Permission.objects.get(codename="add_account"))
        self.client.force_login(user)

        response = self.client.get(reverse("accounts:user_access_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="btn btn-outline-secondary js-password-toggle"', count=2)
        self.assertContains(response, 'data-target="id_password1"')
        self.assertContains(response, 'data-target="id_password2"')
