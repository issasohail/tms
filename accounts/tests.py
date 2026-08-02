from django.test import SimpleTestCase

from .models import Account


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
