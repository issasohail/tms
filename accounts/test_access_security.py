from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountPropertyAccess
from properties.models import Property


Account = get_user_model()


class AccountPermissionHardeningTests(TestCase):
    def setUp(self):
        self.admin = Account.objects.create_superuser(
            username="root-admin", email="root@example.com", password="test-pass-123"
        )
        self.staff = Account.objects.create_user(
            username="staff-user", email="staff@example.com", password="test-pass-123", is_staff=True
        )
        self.target = Account.objects.create_user(
            username="target-user", email="target@example.com", password="test-pass-123", is_staff=True
        )

    def perm(self, codename):
        return Permission.objects.get(content_type__app_label="accounts", codename=codename)

    def test_staff_flag_alone_cannot_manage_groups(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("accounts:group_access_list"))
        self.assertEqual(response.status_code, 302)

    def test_impersonation_requires_custom_permission(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("accounts:impersonate_start", args=[self.target.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("impersonate_user_id", self.client.session)

    def test_non_superuser_cannot_impersonate_superuser(self):
        self.staff.user_permissions.add(self.perm("impersonate_account"))
        self.client.force_login(self.staff)

        response = self.client.post(reverse("accounts:impersonate_start", args=[self.admin.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("impersonate_user_id", self.client.session)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)

    def test_permission_autosave_preserves_custom_security_permissions(self):
        self.staff.user_permissions.add(
            self.perm("change_account"),
            self.perm("grant_account_permissions"),
        )
        access_all = self.perm("access_all_properties")
        self.target.user_permissions.add(access_all)
        view_account = self.perm("view_account")
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("accounts:user_permission_autosave", args=[self.target.pk]),
            {"permissions": [str(view_account.pk)]},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.target.user_permissions.filter(pk=access_all.pk).exists())


class PropertyScopeTests(TestCase):
    def setUp(self):
        self.user = Account.objects.create_user(
            username="scoped", email="scoped@example.com", password="test-pass-123", is_staff=True
        )
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="properties", codename="view_property")
        )
        self.allowed = Property.objects.create(
            property_name="Allowed Property",
            owner_name="Owner",
            owner_cnic="35202-1234567-1",
            type="Building",
            property_type="apartment",
            total_units=1,
        )
        self.denied = Property.objects.create(
            property_name="Denied Property",
            owner_name="Owner",
            owner_cnic="35202-1234567-2",
            type="Building",
            property_type="apartment",
            total_units=1,
        )
        AccountPropertyAccess.objects.create(account=self.user, property=self.allowed)
        self.client.force_login(self.user)

    def test_property_list_only_contains_assigned_properties(self):
        response = self.client.get(reverse("properties:property_list"))
        self.assertContains(response, "Allowed Property")
        self.assertNotContains(response, "Denied Property")

    def test_direct_property_detail_outside_scope_is_forbidden(self):
        response = self.client.get(reverse("properties:property_detail", args=[self.denied.pk]))
        self.assertEqual(response.status_code, 403)
