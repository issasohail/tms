from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from .models import Account
from .views import signup


class AccountNameTests(SimpleTestCase):
    def test_get_full_name_combines_first_and_last_name(self):
        account = Account(
            username="approver",
            first_name="Maintenance",
            last_name="Manager",
        )

        self.assertEqual(account.get_full_name(), "Maintenance Manager")

    def test_get_full_name_returns_empty_string_when_names_are_blank(self):
        account = Account(username="approver")

        self.assertEqual(account.get_full_name(), "")


class SignupViewTests(SimpleTestCase):
    def test_authenticated_user_can_open_signup_form(self):
        request = RequestFactory().get("/tms/accounts/signup/")
        request.user = SimpleNamespace(is_authenticated=True)

        with patch("accounts.views.render", return_value=HttpResponse()) as render_mock:
            response = signup(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(render_mock.call_args.args[1], "accounts/signup.html")
