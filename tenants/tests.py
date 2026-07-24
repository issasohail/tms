from django.test import TestCase

# Create your tests here.

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from leases.services.lease_expiry import (
    attach_lease_expiry_countdown,
    get_lease_expiry_countdown,
)


class LeaseExpiryCountdownTests(SimpleTestCase):
    def make_lease(self, *, days=30, status="active", has_end_date=True):
        end_date = timezone.localdate() + timedelta(days=days) if has_end_date else None
        return SimpleNamespace(status=status, end_date=end_date)

    def test_exactly_60_days_displays(self):
        result = get_lease_expiry_countdown(self.make_lease(days=60))
        self.assertIsNotNone(result)
        self.assertEqual(result.label, "60 days left")

    def test_fewer_than_60_days_displays_correct_value(self):
        result = get_lease_expiry_countdown(self.make_lease(days=45))
        self.assertEqual(result.days_left, 45)
        self.assertEqual(result.label, "45 days left")

    def test_one_day_uses_singular_wording(self):
        result = get_lease_expiry_countdown(self.make_lease(days=1))
        self.assertEqual(result.label, "1 day left")

    def test_more_than_60_days_is_hidden(self):
        self.assertIsNone(get_lease_expiry_countdown(self.make_lease(days=61)))

    def test_missing_end_date_is_hidden(self):
        self.assertIsNone(
            get_lease_expiry_countdown(self.make_lease(has_end_date=False))
        )

    def test_inactive_lease_is_hidden(self):
        self.assertIsNone(
            get_lease_expiry_countdown(self.make_lease(days=30, status="inactive"))
        )

    def test_ended_lease_is_hidden(self):
        self.assertIsNone(get_lease_expiry_countdown(self.make_lease(days=-1)))

    def test_attached_value_uses_same_shared_calculation(self):
        lease = self.make_lease(days=20)
        shared_result = get_lease_expiry_countdown(lease)
        attach_lease_expiry_countdown(lease)
        self.assertEqual(lease.expiry_days_left, shared_result.days_left)
        self.assertEqual(lease.expiry_countdown_label, shared_result.label)


class TenantListExpiryMarkupTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template_source = (
            Path(__file__).resolve().parent
            / "templates"
            / "tenants"
            / "tenant_list.html"
        ).read_text(encoding="utf-8")

    def test_desktop_countdown_is_below_end_date(self):
        desktop_end = self.template_source.index(
            'End: <span class="{% if lease.expiry_countdown_label %}'
        )
        desktop_countdown = self.template_source.index(
            'lease-expiry-countdown lease-expiry-countdown-desktop', desktop_end
        )
        desktop_block_end = self.template_source.index("</div>", desktop_countdown)
        self.assertLess(desktop_end, desktop_countdown)
        self.assertLess(desktop_countdown, desktop_block_end)

    def test_mobile_countdown_is_between_start_and_end(self):
        mobile_start = self.template_source.index('class="tcv2-date-start"')
        mobile_countdown = self.template_source.index(
            'lease-expiry-countdown lease-expiry-countdown-mobile', mobile_start
        )
        mobile_end = self.template_source.index('class="tcv2-date-end"', mobile_countdown)
        self.assertLess(mobile_start, mobile_countdown)
        self.assertLess(mobile_countdown, mobile_end)

    def test_reduced_motion_disables_animation(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.template_source)
        reduced_motion_start = self.template_source.index(
            "@media (prefers-reduced-motion: reduce)"
        )
        reduced_motion_block = self.template_source[
            reduced_motion_start : reduced_motion_start + 220
        ]
        self.assertIn("animation:none !important", reduced_motion_block)

class PendingRegistrationWorkflowTests(SimpleTestCase):
    def test_cnic_normalization_ignores_all_non_digits(self):
        from tenants.models import normalize_cnic
        self.assertEqual(normalize_cnic("61101-1234567-1"), "6110112345671")
        self.assertEqual(normalize_cnic("61101 1234567 1"), "6110112345671")

    def test_role_history_url_exists_in_urlconf(self):
        from django.urls import reverse
        self.assertTrue(reverse("tenants:tenant_role_history", args=[25]).endswith("/tenants/25/role-history/"))


class RegistrationOnboardingTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        self.user = get_user_model().objects.create_user(
            "reviewer", email="reviewer@example.com", password="test-pass"
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_tenantregistrationsubmission"),
            Permission.objects.get(codename="change_tenantregistrationsubmission"),
            Permission.objects.get(codename="change_tenant"),
            Permission.objects.get(codename="add_lease"),
            Permission.objects.get(codename="view_lease"),
            Permission.objects.get(codename="change_lease"),
        )

    def make_shell(self):
        from tenants.models import Tenant

        return Tenant.objects.create(
            first_name="New",
            last_name="Registration",
            phone="03001234567",
            cnic="",
            is_active=False,
        )

    def public_post_data(self, **overrides):
        data = {
            "first_name": "Applicant",
            "last_name": "Person",
            "phone": "03001234567",
            "proposer-first_name": "Primary",
            "proposer-last_name": "Proposer",
            "proposer-cnic": "6110112345671",
            "proposer-phone": "03001111111",
            "seconder-first_name": "Primary",
            "seconder-last_name": "Seconder",
            "seconder-cnic": "6110112345672",
            "seconder-phone": "03002222222",
            "vehicle-TOTAL_FORMS": "0",
        }
        data.update(overrides)
        return data

    def make_required_parties(self, submission):
        from tenants.models import PendingRegistrationPerson

        PendingRegistrationPerson.objects.create(
            submission=submission,
            role=PendingRegistrationPerson.ROLE_PROPOSER,
            first_name="Primary",
            last_name="Proposer",
            cnic="6110112345671",
            phone="03001111111",
        )
        PendingRegistrationPerson.objects.create(
            submission=submission,
            role=PendingRegistrationPerson.ROLE_SECONDER,
            first_name="Primary",
            last_name="Seconder",
            cnic="6110112345672",
            phone="03002222222",
        )

    def make_unit(self, suffix=""):
        from properties.models import Property, Unit

        prop = Property.objects.create(
            property_name=f"Registration Property {suffix}",
            owner_name="Owner",
            owner_cnic=f"44{suffix or '0'}".ljust(13, "4")[:13],
            type="house",
            property_type="house",
            total_units=1,
        )
        return prop, Unit.objects.create(property=prop, unit_number=f"R-{suffix or '1'}")

    def test_required_party_phones_are_validated_before_any_submission_write(self):
        from django.urls import reverse
        from tenants.models import TenantRegistrationSubmission
        from tenants.views import tenant_registration_token

        for missing in ("phone", "proposer-phone", "seconder-phone"):
            shell = self.make_shell()
            data = self.public_post_data()
            data.pop(missing)
            response = self.client.post(
                reverse("tenants:tenant_public_registration", args=[tenant_registration_token(shell)]),
                data,
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(TenantRegistrationSubmission.objects.filter(tenant=shell).count(), 0)

    def test_public_registration_rejects_tenant_and_family_as_required_parties(self):
        from django.urls import reverse
        from tenants.models import TenantRegistrationSubmission
        from tenants.views import tenant_registration_token

        tenant_shell = self.make_shell()
        tenant_cnic = "3520212345672"
        tenant_conflict_data = self.public_post_data(
            cnic=tenant_cnic,
            **{"proposer-cnic": tenant_cnic},
        )
        tenant_response = self.client.post(
            reverse(
                "tenants:tenant_public_registration",
                args=[tenant_registration_token(tenant_shell)],
            ),
            tenant_conflict_data,
        )

        self.assertEqual(tenant_response.status_code, 200)
        self.assertContains(
            tenant_response,
            "Proposer cannot be the tenant. Enter an unrelated third party.",
        )
        self.assertContains(
            tenant_response,
            "Proposer and seconder cannot be the tenant or a family member",
        )
        self.assertEqual(
            TenantRegistrationSubmission.objects.filter(
                tenant=tenant_shell
            ).count(),
            0,
        )

        family_shell = self.make_shell()
        family_cnic = "6110112345673"
        family_conflict_data = self.public_post_data(
            cnic="3520212345674",
            **{
                "family-0-name": "Family Member",
                "family-0-cnic": family_cnic,
                "proposer-cnic": family_cnic,
            },
        )
        family_response = self.client.post(
            reverse(
                "tenants:tenant_public_registration",
                args=[tenant_registration_token(family_shell)],
            ),
            family_conflict_data,
        )

        self.assertEqual(family_response.status_code, 200)
        self.assertContains(
            family_response,
            "Proposer cannot be a family member. Enter an unrelated third party.",
        )
        self.assertEqual(
            TenantRegistrationSubmission.objects.filter(
                tenant=family_shell
            ).count(),
            0,
        )

    def test_empty_witness_is_optional_but_started_witness_requires_phone(self):
        from tenants.forms import TenantPublicRegistrationForm

        empty = TenantPublicRegistrationForm(
            self.public_post_data(), role_data=self.public_post_data()
        )
        self.assertTrue(empty.is_valid(), empty.errors)
        started_data = self.public_post_data(**{"witness1-first_name": "Witness"})
        started = TenantPublicRegistrationForm(started_data, role_data=started_data)
        self.assertFalse(started.is_valid())
        self.assertIn("Witness 1 phone is required", str(started.non_field_errors()))

    def test_invalid_public_registration_exposes_dynamic_values_for_restoration(self):
        from django.urls import reverse
        from tenants.views import tenant_registration_token

        shell = self.make_shell()
        data = self.public_post_data(
            **{
                "family-0-name": "Saved Family Member",
                "family-0-cnic": "6110112345673",
                "witness1-first_name": "Saved Witness",
            }
        )

        response = self.client.post(
            reverse(
                "tenants:tenant_public_registration",
                args=[tenant_registration_token(shell)],
            ),
            data,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["submitted_form_data"]["family-0-name"],
            ["Saved Family Member"],
        )
        self.assertEqual(
            response.context["submitted_form_data"]["proposer-first_name"],
            ["Primary"],
        )
        self.assertEqual(
            response.context["submitted_form_data"]["witness1-first_name"],
            ["Saved Witness"],
        )
        self.assertContains(response, 'id="registrationSubmittedData"')

    def test_public_registration_uses_one_responsive_draft_recovery_handler(self):
        from django.urls import reverse
        from tenants.views import tenant_registration_token

        shell = self.make_shell()
        response = self.client.get(
            reverse(
                "tenants:tenant_public_registration",
                args=[tenant_registration_token(shell)],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tmsTenantRegistrationDraft:")
        self.assertContains(response, "sessionStorage.setItem")
        self.assertContains(response, "restoreRegistrationDraft();")
        self.assertContains(response, 'window.addEventListener("pagehide"')

    def test_unpermitted_edit_returns_403(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from tenants.models import TenantRegistrationSubmission

        submission = TenantRegistrationSubmission.objects.create(
            tenant=self.make_shell(), submitted_data=self.public_post_data()
        )
        user = get_user_model().objects.create_user(
            "no-permission", email="no-permission@example.com", password="test-pass"
        )
        self.client.force_login(user)
        response = self.client.get(
            reverse("tenants:registration_submission_edit", args=[submission.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_editing_person_cnic_refreshes_match_and_proposed_updates(self):
        from django.urls import reverse
        from tenants.models import PendingRegistrationPerson, Tenant, TenantRegistrationSubmission

        target = Tenant.objects.create(
            first_name="Existing", last_name="Match", phone="03009999999", cnic="6110112345671"
        )
        submission = TenantRegistrationSubmission.objects.create(
            tenant=self.make_shell(), submitted_data=self.public_post_data()
        )
        PendingRegistrationPerson.objects.create(
            submission=submission, role=PendingRegistrationPerson.ROLE_PROPOSER,
            first_name="Proposer", phone="03001111111",
        )
        PendingRegistrationPerson.objects.create(
            submission=submission, role=PendingRegistrationPerson.ROLE_SECONDER,
            first_name="Seconder", phone="03002222222",
        )
        witness = PendingRegistrationPerson.objects.create(
            submission=submission, role=PendingRegistrationPerson.ROLE_WITNESS_1,
            first_name="Different", phone="03008888888", cnic="4220112345671",
            proposed_updates={"stale": {"existing": "x", "submitted": "y"}},
        )
        data = self.public_post_data(**{
            "witness1-first_name": "Different",
            "witness1-cnic": target.cnic,
            "witness1-phone": "03008888888",
        })
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("tenants:registration_submission_edit", args=[submission.pk]), data
        )
        self.assertEqual(response.status_code, 302)
        witness.refresh_from_db()
        self.assertEqual(witness.matched_tenant, target)
        self.assertNotIn("stale", witness.proposed_updates)
        self.assertIn("first_name", witness.proposed_updates)

    def test_edit_submission_can_add_family_relationship_and_vehicle(self):
        from django.urls import reverse
        from leases.models import LeaseRelationshipType, LeaseVehicleType, PendingLeaseVehicleSubmission
        from tenants.models import PendingRegistrationPerson, TenantRegistrationSubmission

        submission = TenantRegistrationSubmission.objects.create(
            tenant=self.make_shell(),
            submitted_data=self.public_post_data(number_of_family_member="1"),
        )
        self.make_required_parties(submission)
        vehicle_type = LeaseVehicleType.objects.create(name="Car", code="edit-car")
        data = self.public_post_data(**{
            "number_of_family_member": "1",
            "new-family-0-first_name": "Family",
            "new-family-0-last_name": "Member",
            "new-family-0-phone": "03003333333",
            "new-family-0-relationship_type": "__new__",
            "new-family-0-relationship_new": "Cousin",
            "new-vehicle-0-vehicle_type": str(vehicle_type.pk),
            "new-vehicle-0-registration_number": "ABC-123",
            "new-vehicle-0-make": "Honda",
        })

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("tenants:registration_submission_edit", args=[submission.pk]), data
        )

        self.assertEqual(response.status_code, 302)
        family = submission.pending_people.get(role=PendingRegistrationPerson.ROLE_FAMILY)
        relationship = LeaseRelationshipType.objects.get(name="Cousin")
        self.assertEqual(family.relationship_type_id, relationship.pk)
        self.assertEqual(family.relationship, relationship.code)
        vehicle = PendingLeaseVehicleSubmission.objects.get(
            pending_tenant_submission=submission
        )
        self.assertEqual(vehicle.registration_number, "ABC-123")
        self.assertEqual(vehicle.make, "Honda")

    def test_edit_submission_shows_existing_family_and_vehicle_values(self):
        from django.urls import reverse
        from leases.models import LeaseVehicleType, PendingLeaseVehicleSubmission
        from tenants.models import PendingRegistrationPerson, TenantRegistrationSubmission

        submission = TenantRegistrationSubmission.objects.create(
            tenant=self.make_shell(), submitted_data=self.public_post_data()
        )
        self.make_required_parties(submission)
        PendingRegistrationPerson.objects.create(
            submission=submission,
            role=PendingRegistrationPerson.ROLE_FAMILY,
            first_name="VisibleFamily",
            last_name="Record",
        )
        vehicle_type = LeaseVehicleType.objects.create(name="Bike", code="edit-bike")
        PendingLeaseVehicleSubmission.objects.create(
            pending_tenant_submission=submission,
            tenant=submission.tenant,
            vehicle_type=vehicle_type,
            registration_number="VISIBLE-456",
        )

        self.client.force_login(self.user)
        response = self.client.get(
            reverse("tenants:registration_submission_edit", args=[submission.pk])
        )

        self.assertContains(response, "VisibleFamily")
        self.assertContains(response, "VISIBLE-456")
        self.assertContains(response, "Add new relationship")

    def test_shell_cnic_collision_requires_and_performs_explicit_merge(self):
        from django.urls import reverse
        from tenants.models import Tenant, TenantRegistrationSubmission
        from tenants.views import _registration_submission_comparison

        real = Tenant.objects.create(
            first_name="Real", last_name="Tenant", phone="03007777777", cnic="3520212345671"
        )
        shell = self.make_shell()
        submission = TenantRegistrationSubmission.objects.create(
            tenant=shell,
            submitted_data={
                "first_name": "Real", "last_name": "Tenant",
                "phone": "03006666666", "cnic": real.cnic,
            },
        )
        self.make_required_parties(submission)
        self.client.force_login(self.user)
        url = reverse("tenants:registration_submission_review", args=[submission.pk])
        blocked = self.client.post(url, {"action": "approve_and_create_lease"})
        self.assertEqual(blocked.status_code, 302)
        submission.refresh_from_db()
        self.assertEqual(submission.status, submission.STATUS_PENDING)

        data = {"action": "approve_and_create_lease", "collision_action": "merge"}
        for row in _registration_submission_comparison(submission):
            if row["changed"]:
                data[f"decision_{row['field']}"] = "accept_submitted"
        data["decision_first_name"] = "update_submitted"
        data["updated_first_name"] = "Updated"
        prop, unit = self.make_unit("2")
        unit.internet_charges = 1800
        unit.security_deposit_amount = 50000
        unit.save(update_fields=["internet_charges", "security_deposit_amount"])
        data.update({"property": prop.pk, "unit": unit.pk})
        merged = self.client.post(url, data)
        submission.refresh_from_db()
        self.assertRedirects(
            merged,
            reverse("leases:lease_detail", args=[submission.created_lease_id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(submission.tenant, real)
        real.refresh_from_db()
        self.assertEqual(real.first_name, "Updated")
        self.assertEqual(submission.created_lease.unit, unit)
        self.assertEqual(submission.created_lease.status, "active")
        self.assertEqual(submission.created_lease.internet_charges, 1800)
        self.assertEqual(submission.created_lease.security_deposit, 50000)
        self.assertFalse(Tenant.objects.filter(pk=shell.pk).exists())
        self.assertEqual(Tenant.objects.filter(cnic_digits="3520212345671").count(), 1)
        repeated = self.client.post(url, data)
        self.assertRedirects(
            repeated,
            reverse("leases:lease_detail", args=[submission.created_lease_id]),
            fetch_redirect_response=False,
        )
        from leases.models import Lease
        self.assertEqual(Lease.objects.filter(registration_submission=submission).count(), 1)

    def test_tenant_only_approval_processes_required_people_without_lease(self):
        from django.urls import reverse
        from tenants.models import TenantRegistrationSubmission
        from tenants.views import _registration_submission_comparison

        submission = TenantRegistrationSubmission.objects.create(
            tenant=self.make_shell(),
            submitted_data={
                "first_name": "Approved",
                "last_name": "Applicant",
                "phone": "03001234567",
                "cnic": "3520212345672",
                "occupation": "",
                "relation": "S/O.",
            },
        )
        self.make_required_parties(submission)
        data = {"action": "approve_registration"}
        for row in _registration_submission_comparison(submission):
            if row["changed"]:
                data[f"decision_{row['field']}"] = "accept_submitted"
        data["decision_occupation"] = "update_submitted"
        data["updated_occupation"] = "Engineer"
        data["decision_relation"] = "update_submitted"
        data["updated_relation"] = "D/O."

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("tenants:registration_submission_review", args=[submission.pk]),
            data,
        )

        submission.refresh_from_db()
        self.assertEqual(submission.status, submission.STATUS_APPROVED)
        self.assertIsNone(submission.created_lease_id)
        submission.tenant.refresh_from_db()
        self.assertEqual(submission.tenant.occupation, "Engineer")
        self.assertEqual(submission.tenant.relation, "D/O.")
        self.assertEqual(
            submission.pending_people.filter(processed_tenant__isnull=False).count(),
            2,
        )
        self.assertRedirects(
            response,
            reverse("tenants:tenant_detail", args=[submission.tenant_id]),
            fetch_redirect_response=False,
        )

    def test_required_parties_cannot_be_tenant_family_or_each_other(self):
        from tenants.models import (
            PendingRegistrationPerson,
            Tenant,
            TenantRegistrationSubmission,
        )
        from tenants.services.registration_workflow import (
            registration_required_party_reviews,
        )

        tenant = Tenant.objects.create(
            first_name="Existing",
            last_name="Tenant",
            phone="03001234567",
            cnic="3520212345672",
        )
        submission = TenantRegistrationSubmission.objects.create(
            tenant=tenant,
            submitted_data={"cnic": tenant.cnic},
        )
        proposer = PendingRegistrationPerson.objects.create(
            submission=submission,
            role=PendingRegistrationPerson.ROLE_PROPOSER,
            first_name="Existing",
            last_name="Tenant",
            cnic=tenant.cnic,
            phone="03001234567",
            matched_tenant=tenant,
        )
        family = PendingRegistrationPerson.objects.create(
            submission=submission,
            role=PendingRegistrationPerson.ROLE_FAMILY,
            first_name="Family",
            last_name="Member",
            cnic="6110112345672",
            phone="03003333333",
        )
        seconder = PendingRegistrationPerson.objects.create(
            submission=submission,
            role=PendingRegistrationPerson.ROLE_SECONDER,
            first_name="Family",
            last_name="Member",
            cnic=family.cnic,
            phone="03003333333",
        )

        reviews = {
            review["role"]: review
            for review in registration_required_party_reviews(submission)
        }

        self.assertIn(
            "must be someone other than the tenant",
            reviews[proposer.role]["missing"],
        )
        self.assertIn(
            "must not be a family member",
            reviews[seconder.role]["missing"],
        )

        seconder.cnic = proposer.cnic
        seconder.matched_tenant = tenant
        seconder.save(update_fields=["cnic", "matched_tenant"])
        reviews = {
            review["role"]: review
            for review in registration_required_party_reviews(submission)
        }
        self.assertIn(
            "must be different from the other required party",
            reviews[seconder.role]["missing"],
        )

    def test_existing_tenant_update_assigns_family_to_current_lease_without_duplicates(self):
        from django.urls import reverse
        from leases.models import Lease, LeaseFamilyMember
        from tenants.models import (
            PendingRegistrationPerson,
            Tenant,
            TenantRegistrationSubmission,
        )

        tenant = Tenant.objects.create(
            first_name="Existing",
            last_name="Tenant",
            phone="03001234567",
            cnic="3520212345672",
        )
        prop, unit = self.make_unit("8")
        lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=timezone.localdate() - timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=300),
            monthly_rent=25000,
            status="active",
        )
        submission = TenantRegistrationSubmission.objects.create(
            tenant=tenant,
            submitted_data={
                "first_name": tenant.first_name,
                "last_name": tenant.last_name,
                "phone": tenant.phone,
                "cnic": tenant.cnic,
            },
        )
        self.make_required_parties(submission)
        PendingRegistrationPerson.objects.create(
            submission=submission,
            role=PendingRegistrationPerson.ROLE_FAMILY,
            first_name="New",
            last_name="Family",
            cnic="6110112345673",
            phone="03003333333",
            relationship="other",
        )
        tenant_count_before = Tenant.objects.count()
        lease_count_before = Lease.objects.count()

        self.client.force_login(self.user)
        response = self.client.post(
            reverse(
                "tenants:registration_submission_review",
                args=[submission.pk],
            ),
            {"action": "approve_registration"},
        )

        submission.refresh_from_db()
        lease.refresh_from_db()
        self.assertEqual(submission.status, submission.STATUS_APPROVED)
        self.assertEqual(submission.tenant, tenant)
        self.assertIsNone(submission.created_lease_id)
        self.assertEqual(Lease.objects.count(), lease_count_before)
        self.assertEqual(Tenant.objects.filter(pk=tenant.pk).count(), 1)
        self.assertEqual(Tenant.objects.count(), tenant_count_before + 3)
        self.assertEqual(
            LeaseFamilyMember.objects.filter(
                lease=lease,
                primary_tenant=tenant,
                family_member__cnic_digits="6110112345673",
            ).count(),
            1,
        )
        self.assertIsNotNone(lease.proposer_id)
        self.assertIsNotNone(lease.seconder_id)
        self.assertRedirects(
            response,
            reverse("leases:lease_detail", args=[lease.pk]),
            fetch_redirect_response=False,
        )

    def test_approval_keeps_missing_photo_path_and_shows_warning(self):
        from django.contrib.messages import get_messages
        from django.urls import reverse
        from tenants.models import TenantRegistrationSubmission
        from tenants.views import _registration_submission_comparison

        missing_path = "tenants/registration_submissions/132/ishaq.jpg"
        submission = TenantRegistrationSubmission.objects.create(
            tenant=self.make_shell(),
            submitted_data={
                "first_name": "Approved",
                "last_name": "Applicant",
                "phone": "03001234567",
                "cnic": "3520212345672",
            },
        )
        submission.photo.name = missing_path
        submission.save(update_fields=["photo"])
        self.make_required_parties(submission)
        data = {"action": "approve_registration"}
        for row in _registration_submission_comparison(submission):
            if row["changed"]:
                data[f"decision_{row['field']}"] = "accept_submitted"

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("tenants:registration_submission_review", args=[submission.pk]),
            data,
        )

        submission.refresh_from_db()
        submission.tenant.refresh_from_db()
        self.assertEqual(submission.status, submission.STATUS_APPROVED)
        self.assertEqual(submission.tenant.photo.name, missing_path)
        response_messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(
            any(
                "Photo file is missing" in message and missing_path in message
                for message in response_messages
            )
        )

    def test_approval_keeps_missing_registration_person_photo_path(self):
        from django.contrib.messages import get_messages
        from django.urls import reverse
        from tenants.models import PendingRegistrationPerson, TenantRegistrationSubmission
        from tenants.views import _registration_submission_comparison

        missing_path = (
            "tenants/registration_people/10/proposer/"
            "932f30dc984f41a5ac6e0d9520afbac5.jpeg"
        )
        submission = TenantRegistrationSubmission.objects.create(
            tenant=self.make_shell(),
            submitted_data={
                "first_name": "Approved",
                "last_name": "Applicant",
                "phone": "03001234567",
                "cnic": "3520212345672",
            },
        )
        self.make_required_parties(submission)
        proposer = submission.pending_people.get(
            role=PendingRegistrationPerson.ROLE_PROPOSER
        )
        proposer.photo.name = missing_path
        proposer.save(update_fields=["photo"])
        data = {"action": "approve_registration"}
        for row in _registration_submission_comparison(submission):
            if row["changed"]:
                data[f"decision_{row['field']}"] = "accept_submitted"

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("tenants:registration_submission_review", args=[submission.pk]),
            data,
        )

        submission.refresh_from_db()
        proposer.refresh_from_db()
        proposer.processed_tenant.refresh_from_db()
        self.assertEqual(submission.status, submission.STATUS_APPROVED)
        self.assertEqual(proposer.processed_tenant.photo.name, missing_path)
        response_messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(
            any(
                "Proposer" in message
                and "Photo file is missing" in message
                and missing_path in message
                for message in response_messages
            )
        )

    def test_active_unit_lease_opens_date_correction_modal_and_allows_retry(self):
        from django.urls import reverse
        from leases.models import Lease
        from tenants.models import Tenant, TenantRegistrationSubmission

        submission = TenantRegistrationSubmission.objects.create(
            tenant=self.make_shell(),
            submitted_data={
                "first_name": "New",
                "last_name": "Registration",
                "phone": "03001234567",
                "cnic": "",
            },
        )
        self.make_required_parties(submission)
        prop, unit = self.make_unit("7")
        active_start = timezone.localdate() - timedelta(days=30)
        active_end = timezone.localdate() + timedelta(days=30)
        occupant = Tenant.objects.create(
            first_name="Current",
            last_name="Occupant",
            phone="03009990000",
            cnic="3520299999999",
        )
        active_lease = Lease.objects.create(
            tenant=occupant,
            unit=unit,
            start_date=active_start,
            end_date=active_end,
            monthly_rent=25000,
            status="active",
        )
        url = reverse("tenants:registration_submission_review", args=[submission.pk])
        detail_url = reverse("tenants:registration_submission_detail", args=[submission.pk])
        data = {
            "action": "approve_registration",
            "property": prop.pk,
            "unit": unit.pk,
        }

        self.client.force_login(self.user)
        blocked = self.client.post(url, data, follow=True)

        submission.refresh_from_db()
        suggested_start = active_end + timedelta(days=1)
        self.assertEqual(submission.status, submission.STATUS_PENDING)
        self.assertIsNone(submission.created_lease_id)
        self.assertContains(blocked, 'id="registrationApprovalErrorModal"')
        self.assertContains(blocked, f"active Lease #{active_lease.pk}")
        self.assertContains(blocked, suggested_start.isoformat())
        self.assertContains(blocked, f'value="{prop.pk}" selected')
        self.assertContains(blocked, f'value="{unit.pk}" data-property="{prop.pk}" selected')

        data["lease_start_date"] = suggested_start.isoformat()
        retried = self.client.post(url, data)

        submission.refresh_from_db()
        self.assertIsNotNone(submission.created_lease_id)
        self.assertEqual(submission.created_lease.start_date, suggested_start)
        self.assertEqual(submission.created_lease.status, "active")
        self.assertRedirects(
            retried,
            reverse("leases:lease_detail", args=[submission.created_lease_id]),
            fetch_redirect_response=False,
        )

    def test_attach_workflow_links_each_role_family_vehicle_once(self):
        from leases.models import (
            Lease, LeaseFamilyMember, LeaseVehicle, LeaseVehicleType,
            PendingLeaseVehicleSubmission,
        )
        from properties.models import Property, Unit
        from tenants.models import PendingRegistrationPerson, Tenant, TenantRegistrationSubmission
        from tenants.services.registration_workflow import attach_registration_to_lease

        primary = Tenant.objects.create(first_name="Primary", last_name="Tenant", phone="03000000001", cnic="1111111111111")
        submission = TenantRegistrationSubmission.objects.create(tenant=primary, submitted_data={})
        roles = {}
        for index, role in enumerate((
            PendingRegistrationPerson.ROLE_PROPOSER,
            PendingRegistrationPerson.ROLE_SECONDER,
            PendingRegistrationPerson.ROLE_WITNESS_1,
            PendingRegistrationPerson.ROLE_WITNESS_2,
            PendingRegistrationPerson.ROLE_FAMILY,
        ), start=2):
            tenant = Tenant.objects.create(first_name=role, last_name="Person", phone=f"0300000000{index}", cnic=str(index) * 13)
            roles[role] = tenant
            PendingRegistrationPerson.objects.create(
                submission=submission, role=role, first_name=tenant.first_name,
                phone=tenant.phone, cnic=tenant.cnic, matched_tenant=tenant,
            )
        prop = Property.objects.create(
            property_name="Test Property", owner_name="Owner", owner_cnic="3333333333333",
            type="house", property_type="house", total_units=1,
        )
        unit = Unit.objects.create(property=prop, unit_number="1")
        lease = Lease.objects.create(
            tenant=primary, unit=unit, start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=330), monthly_rent=25000,
        )
        active_history = lease.renewals.create(
            renewal_number=1,
            start_date=lease.start_date,
            end_date=lease.end_date,
            lease_months=lease.lease_months,
            agreement_date=lease.agreement_date,
            monthly_rent=lease.monthly_rent,
            society_maintenance=lease.society_maintenance,
            water_charges=lease.water_charges,
            bill_water_charges=lease.bill_water_charges,
            bill_recurring_charges=lease.bill_recurring_charges,
            internet_charges=lease.internet_charges,
            agreement_charges=lease.agreement_charges,
            security_deposit=lease.security_deposit,
            rent_increase_percent=lease.rent_increase_percent,
            is_original=True,
        )
        vehicle_type, _ = LeaseVehicleType.objects.get_or_create(
            code="registration-test-car", defaults={"name": "Registration Test Car"}
        )
        pending_vehicle = PendingLeaseVehicleSubmission.objects.create(
            pending_tenant_submission=submission, tenant=primary,
            vehicle_type=vehicle_type, registration_number="ABC-123",
        )
        missing_vehicle_path = "leases/vehicles/pending/photos/missing-abc-123.jpg"
        missing_book_path = "leases/vehicles/pending/registration_book/missing-abc-123.jpg"
        pending_vehicle.vehicle_photo.name = missing_vehicle_path
        pending_vehicle.registration_book_photo.name = missing_book_path
        pending_vehicle.save(
            update_fields=["vehicle_photo", "registration_book_photo"]
        )
        missing_files = []
        attach_registration_to_lease(
            submission, lease, self.user, missing_files=missing_files
        )
        attach_registration_to_lease(submission, lease, self.user)
        lease.refresh_from_db()
        self.assertEqual(lease.proposer, roles[PendingRegistrationPerson.ROLE_PROPOSER])
        self.assertEqual(lease.seconder, roles[PendingRegistrationPerson.ROLE_SECONDER])
        self.assertEqual(lease.witness1_tenant, roles[PendingRegistrationPerson.ROLE_WITNESS_1])
        self.assertEqual(lease.witness2_tenant, roles[PendingRegistrationPerson.ROLE_WITNESS_2])
        active_history.refresh_from_db()
        self.assertEqual(
            active_history.witness1_tenant,
            roles[PendingRegistrationPerson.ROLE_WITNESS_1],
        )
        self.assertEqual(
            active_history.witness2_tenant,
            roles[PendingRegistrationPerson.ROLE_WITNESS_2],
        )
        self.assertEqual(LeaseFamilyMember.objects.filter(lease=lease).count(), 1)
        self.assertEqual(LeaseVehicle.objects.filter(lease=lease).count(), 1)
        vehicle = LeaseVehicle.objects.get(lease=lease)
        self.assertEqual(vehicle.vehicle_photo.name, missing_vehicle_path)
        self.assertEqual(vehicle.registration_book_photo.name, missing_book_path)
        self.assertTrue(any("Vehicle Photo file is missing" in item for item in missing_files))
        self.assertTrue(
            any("Registration Book Photo file is missing" in item for item in missing_files)
        )
        submission.created_lease = lease
        submission.status = submission.STATUS_APPROVED
        submission.save(update_fields=["created_lease", "status"])
        self.assertFalse(submission.is_editable)
        self.assertEqual(Lease.objects.filter(pk=lease.pk).count(), 1)

    def test_processing_failed_submission_can_be_corrected_and_retried(self):
        from tenants.models import TenantRegistrationSubmission

        submission = TenantRegistrationSubmission.objects.create(
            tenant=self.make_shell(),
            submitted_data=self.public_post_data(),
            status=TenantRegistrationSubmission.STATUS_PROCESSING_FAILED,
        )

        self.assertTrue(submission.is_editable)


class DateOfBirthSafetyTests(SimpleTestCase):
    def test_future_date_of_birth_is_rejected(self):
        from datetime import date, timedelta

        from django.core.exceptions import ValidationError

        from core.utils.identity import validate_date_of_birth

        with self.assertRaisesMessage(
            ValidationError, "Date of birth cannot be in the future."
        ):
            validate_date_of_birth(date.today() + timedelta(days=1))


class CNICIdentityOCRViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        self.user = get_user_model().objects.create_user(
            username="cnic-ocr-user", password="x"
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tenants", codename="add_tenant"
            ),
            Permission.objects.get(
                content_type__app_label="tenants", codename="change_tenant"
            ),
        )
        self.client.force_login(self.user)

    def test_ocr_suggestion_requires_review_and_returns_identity_fields(self):
        from unittest.mock import patch

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        with patch(
            "tenants.services.cnic_ocr.extract_cnic_identity",
            return_value={
                "engine": "openai",
                "fields": {
                    "first_name": "Asif Hussain",
                    "last_name": "Babar Khan",
                    "cnic": "71501-1986137-7",
                    "date_of_birth": "2000-02-25",
                },
                "confidence": 96,
                "warnings": ["No personal address was found on the back."],
            },
        ):
            response = self.client.post(
                reverse("tenants:cnic_identity_ocr"),
                {
                    "cnic_front": SimpleUploadedFile(
                        "front.jpg", b"front-image", content_type="image/jpeg"
                    ),
                    "cnic_back": SimpleUploadedFile(
                        "back.jpg", b"back-image", content_type="image/jpeg"
                    ),
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        fields = {item["name"]: item for item in payload["fields"]}
        self.assertEqual(fields["first_name"]["value"], "Asif Hussain")
        self.assertEqual(fields["date_of_birth"]["display"], "02/25/2000")
        self.assertEqual(fields["date_of_birth"]["cnic_display"], "25.02.2000")
        self.assertTrue(payload["can_overwrite"])

    def test_signed_public_registration_ocr_fills_blanks_without_overwrite_permission(self):
        from unittest.mock import patch

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        from tenants.models import Tenant
        from tenants.views import tenant_registration_token

        tenant = Tenant.objects.create(
            first_name="Registration",
            last_name="Applicant",
            cnic="71501-0000000-1",
        )
        self.client.logout()
        token = tenant_registration_token(tenant)
        with patch(
            "tenants.services.cnic_ocr.extract_cnic_identity",
            return_value={
                "engine": "openai",
                "fields": {
                    "temporary_address": "Mohallah Noor Colony, Jutial, Gilgit",
                    "temporary_address_urdu": "محلہ نور کالونی، جوٹیال، گلگت",
                },
                "confidence": 94,
                "warnings": [],
            },
        ):
            response = self.client.post(
                reverse("tenants:public_cnic_identity_ocr", args=[token]),
                {
                    "cnic_front": SimpleUploadedFile(
                        "front.jpg", b"front", content_type="image/jpeg"
                    ),
                    "cnic_back": SimpleUploadedFile(
                        "back.jpg", b"back", content_type="image/jpeg"
                    ),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["can_overwrite"])
