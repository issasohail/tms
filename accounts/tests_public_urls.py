from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from accounts.password_reset import PublicPasswordResetForm


class PublicPasswordResetFormTests(SimpleTestCase):
    @override_settings(PUBLIC_BASE_URL="https://staging.example.com")
    def test_password_reset_email_uses_canonical_public_domain(self):
        form = PublicPasswordResetForm()

        with patch(
            "django.contrib.auth.forms.PasswordResetForm.save"
        ) as parent_save:
            form.save(use_https=False, request=object())

        kwargs = parent_save.call_args.kwargs
        self.assertEqual(kwargs["domain_override"], "staging.example.com")
        self.assertTrue(kwargs["use_https"])
