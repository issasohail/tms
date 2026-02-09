from django.core.management.base import BaseCommand
from django.utils import timezone
from properties.models import Property, Unit, ExpenseDistribution
from tenants.models import Tenant
from leases.models import Lease
from utilities.models import Utility
from payments.models import Payment
import random
from datetime import datetime, timedelta


class Command(BaseCommand):
    help = 'Creates dummy data for the property management system'

    def handle(self, *args, **options):
        # Clear existing data (optional)
        # Property.objects.all().delete()
        # Unit.objects.all().delete()
        # etc...

        # 1. Create Properties
        property1 = Property.objects.create(
            property_name="Sunrise Apartments",
            owner_name="John Smith",
            owner_address="123 Main St, Anytown",
            owner_cnic="12345-6789012-3",
            owner_phone="555-1234",
            property_address1="456 Oak Avenue",
            property_address2="Floor 3",
            property_city="Metropolis",
            property_state="California",
            property_zipcode="90001",
            type="Residential",
            property_type="apartment",
            total_units=10,
            description="Luxury apartment complex"
        )

        property2 = Property.objects.create(
            property_name="Downtown Offices",
            owner_name="Acme Corp",
            owner_address="789 Business Rd",
            owner_cnic="98765-4321098-7",
            owner_phone="555-5678",
            property_address1="100 Commerce Street",
            property_address2="Suite 500",
            property_city="Metropolis",
            property_state="California",
            property_zipcode="90002",
            type="Commercial",
            property_type="commercial",
            total_units=5,
            description="Class A office space"
        )

        # 2. Create Units
        units = []
        for i in range(1, 6):
            unit = Unit.objects.create(
                property=property1,
                unit_number=f"A{i}",
                monthly_rent=random.randint(800, 2000),
                status="vacant" if i % 3 == 0 else "occupied"
            )
            units.append(unit)

        # 3. Create Tenants
        tenants = []
        for i in range(1, 6):
            tenant = Tenant.objects.create(
                name=f"Tenant {i}",
                phone=f"555-100{i}",
                email=f"tenant{i}@example.com",
                cnic=f"13579-246810{i}-0",
                address=f"{i}00 Tenant Street"
            )
            tenants.append(tenant)

        # 4. Create Leases
        today = timezone.now().date()
        for i, (unit, tenant) in enumerate(zip(units, tenants)):
            Lease.objects.create(
                property_name=property1,
                unit=unit,
                tenant=tenant,
                lease_start_date=today - timedelta(days=30*i),
                lease_end_date=today + timedelta(days=365 - 30*i)
            )

        # 5. Create Utilities
        utility_types = ['water', 'electricity', 'gas', 'maintenance']
        for prop in [property1, property2]:
            for i in range(3):
                Utility.objects.create(
                    property=prop,
                    utility_type=random.choice(utility_types),
                    amount=random.randint(50, 300),
                    billing_date=today - timedelta(days=30*i),
                    due_date=today + timedelta(days=15 - 30*i),
                    distribution_method=random.choice(
                        ['equal', 'per_person', 'usage'])
                )

        # 6. Create Payments
        leases = Lease.objects.all()
        for lease in leases:
            for i in range(2):
                Payment.objects.create(
                    lease=lease,
                    amount=lease.unit.monthly_rent,
                    payment_date=today - timedelta(days=15*i),
                    payment_method=random.choice(
                        ['cash', 'check', 'bank transfer'])
                )

        self.stdout.write(self.style.SUCCESS(
            'Successfully created dummy data'))
