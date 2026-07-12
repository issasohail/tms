from unittest.mock import Mock

from django.test import TestCase


class AuthorizedOccupantsPlaceholderTests(TestCase):
    def test_registry_contains_new_placeholders(self):
        from leases.utils.utils import PLACEHOLDER_REGISTRY

        self.assertIn("authorized_occupants_table", PLACEHOLDER_REGISTRY)
        self.assertIn("authorized_occupants_names", PLACEHOLDER_REGISTRY)
        self.assertIn("authorized_occupants_count", PLACEHOLDER_REGISTRY)

    def test_table_includes_primary_tenant_and_three_column_layout(self):
        from leases.utils.utils import authorized_occupants_table

        tenant = Mock(cnic="61101-1234567-1")
        tenant.get_full_name.return_value = "Primary Tenant"
        manager = Mock()
        manager.select_related.return_value.filter.return_value.__iter__ = lambda self: (
            iter([])
        )
        lease = Mock(tenant=tenant, family_members=manager)
        html = authorized_occupants_table(lease)
        self.assertIn("Primary Tenant", html)
        self.assertIn("61101-1234567-1", html)
        self.assertEqual(html.count('class="occupant-card"'), 3)
        self.assertNotIn("N/A", html)

    def test_double_curly_placeholder_is_replaced(self):
        from leases.utils.utils import do_replace_placeholders

        tenant = Mock(cnic="1")
        tenant.get_full_name.return_value = "Tenant One"
        manager = Mock()
        manager.select_related.return_value.filter.return_value.__iter__ = lambda self: (
            iter([])
        )
        lease = Mock(tenant=tenant, family_members=manager)
        rendered = do_replace_placeholders("{{authorized_occupants_table}}", lease)
        self.assertIn("Tenant One", rendered)
        self.assertNotIn("{{authorized_occupants_table}}", rendered)
