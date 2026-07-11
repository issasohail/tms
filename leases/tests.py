from django.test import SimpleTestCase


class AuthorizedOccupantsPlaceholderTests(SimpleTestCase):
    def test_registry_contains_new_placeholders(self):
        from leases.utils.utils import PLACEHOLDER_REGISTRY
        self.assertIn("authorized_occupants_table", PLACEHOLDER_REGISTRY)
        self.assertIn("authorized_occupants_names", PLACEHOLDER_REGISTRY)
        self.assertIn("authorized_occupants_count", PLACEHOLDER_REGISTRY)

    def test_blank_table_has_writable_row(self):
        from unittest.mock import Mock
        from leases.utils.utils import authorized_occupants_table
        manager = Mock()
        manager.select_related.return_value.filter.return_value.__iter__ = lambda self: iter([])
        lease = Mock(family_members=manager)
        html = authorized_occupants_table(lease)
        self.assertIn("<td>1</td>", html)
        self.assertIn("&nbsp;", html)
        self.assertNotIn("N/A", html)
