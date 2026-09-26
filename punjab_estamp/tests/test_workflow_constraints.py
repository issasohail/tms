from datetime import date

from django.db import IntegrityError, transaction
from django.test import TransactionTestCase

from leases.models import Lease
from leases.models_renewal import LeaseRenewal
from properties.models import Property, Unit
from punjab_estamp.models import LeaseEStampWorkflow
from tenants.models import Tenant


class LeaseEStampWorkflowConstraintTests(TransactionTestCase):
    def setUp(self):
        tenant = Tenant.objects.create(
            first_name="Constraint",
            last_name="Tenant",
            cnic="35202-1234567-1",
        )
        property_obj = Property.objects.create(
            property_name="Constraint Property",
            owner_name="Owner",
            owner_cnic="35202-7654321-1",
            type="house",
            property_type="house",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="C-1")
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent="25000.00",
        )
        self.original_history = LeaseRenewal.objects.create(
            lease=self.lease,
            renewal_number=1,
            is_original=True,
            start_date=self.lease.start_date,
            end_date=self.lease.end_date,
            monthly_rent=self.lease.monthly_rent,
        )

    def test_original_workflow_cannot_have_null_history(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LeaseEStampWorkflow.objects.create(
                    lease=self.lease,
                    lease_history=None,
                )

    def test_same_original_history_cannot_have_two_workflows(self):
        LeaseEStampWorkflow.objects.create(
            lease=self.lease,
            lease_history=self.original_history,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LeaseEStampWorkflow.objects.create(
                    lease=self.lease,
                    lease_history=self.original_history,
                )

    def test_later_renewal_can_have_its_own_workflow(self):
        later_history = LeaseRenewal.objects.create(
            lease=self.lease,
            renewal_number=2,
            start_date=date(2027, 1, 1),
            end_date=date(2027, 12, 31),
            monthly_rent="27500.00",
        )
        LeaseEStampWorkflow.objects.create(
            lease=self.lease,
            lease_history=self.original_history,
        )
        later = LeaseEStampWorkflow.objects.create(
            lease=self.lease,
            lease_history=later_history,
        )

        self.assertEqual(later.lease_history, later_history)

