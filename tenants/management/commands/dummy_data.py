import random
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from faker import Faker
from properties.models import Property, Unit, ExpenseDistribution
from tenants.models import Tenant
from leases.models import Lease
from invoices.models import Invoice, ChargeType, InvoiceItem
from payments.models import Payment
from documents.models import DocumentCategory, Document, LeaseDocument
from notifications.models import Notification
from reports.models import FinancialReport, Report
from utilities.models import Utility
from django.contrib.auth import get_user_model

User = get_user_model()
fake = Faker()


class Command(BaseCommand):
    help = 'Generate dummy data for all models'

    def handle(self, *args, **options):
        self.stdout.write("Creating dummy data...")

        admin, created = User.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@example.com',
                'is_staff': True,
                'is_superuser': True
            }
        )
        if created:
            admin.set_password('admin123')
            admin.save()
            self.stdout.write("Created admin user")
        else:
            self.stdout.write("Admin user already exists")

        # 1. Expense Distributions
        distributions = []
        for name in ['Water', 'Electricity', 'Maintenance', 'Common Area']:
            dist = ExpenseDistribution.objects.create(
                name=name,
                description=fake.sentence()
            )
            distributions.append(dist)

        # 2. Properties
        properties = []
        # for i in range(5):
        prop1 = Property.objects.create(
            property_name="F56",
            owner_name="Sohail Issa",
            owner_address="House# G10, Golden Jubilee Soceity, GT Road, Rawalpindi",
            owner_cnic="42101-2008010-3",
            owner_phone="03122550183",
            caretaker_name="Fidaur Rehman S/O Amir Sahib Khan",
            caretaker_address="F54,FLAT 4,Golden Jubilee Soceity, GT Road, Rawalpindi",
            caretaker_cnic="15202-3252983-5",
            caretaker_phone="03122550183",
            property_address1="F56 Golden Jubilee Society, GT Road, Rawalpindi",
            property_address2="GT Road",
            property_city="Rawalpindi",
            property_state="Punjab",
            property_zipcode="44000",
            type="Residential",
            property_type=random.choice(
                ['apartment', 'house', 'condo', 'commercial']),
            total_units=12,
            description=fake.paragraph()
        )
        properties.append(prop1)

        prop2 = Property.objects.create(
            property_name="F54",
            owner_name="Sohail Issa",
            owner_address="House# G10, Golden Jubilee Soceity, GT Road, Rawalpindi",
            owner_cnic="42101-2008010-3",
            owner_phone="03122550183",
            caretaker_name="Fidaur Rehman S/O Amir Sahib Khan",
            caretaker_address="F54,FLAT 4,Golden Jubilee Soceity, GT Road, Rawalpindi",
            caretaker_cnic="15202-3252983-5",
            caretaker_phone="03122550183",
            property_address1="F54 Golden Jubilee Society, GT Road, Rawalpindi",
            property_address2="GT Road",
            property_city="Rawalpindi",
            property_state="Punjab",
            property_zipcode="44000",
            type="Residential",
            property_type=random.choice(
                ['apartment', 'house', 'condo', 'commercial']),
            total_units=12,
            description=fake.paragraph()
        )
        properties.append(prop2)

        prop3 = Property.objects.create(
            property_name="F35",
            owner_name="Aneela Ali",
            owner_address="House# G10, Golden Jubilee Soceity, GT Road, Rawalpindi",
            owner_cnic="42101-2008010-3",
            owner_phone="03122550183",
            caretaker_name="Sohail Issa S/O Iqbal Issa",
            caretaker_address="House# G10,Golden Jubilee Soceity, GT Road, Rawalpindi",
            caretaker_cnic="42101-2008010-3",
            caretaker_phone="03122550183",
            property_address1="F35 Golden Jubilee Society, GT Road, Rawalpindi",
            property_address2="GT Road",
            property_city="Rawalpindi",
            property_state="Punjab",
            property_zipcode="44000",
            type="Residential",
            property_type=random.choice(
                ['apartment', 'house', 'condo', 'commercial']),
            total_units=12,
            description=""
        )
        properties.append(prop3)

        prop4 = Property.objects.create(
            property_name="F56 Basement",
            owner_name="Sohail Issa",
            owner_address="House# G10, Golden Jubilee Soceity, GT Road, Rawalpindi",
            owner_cnic="42101-2008010-3",
            owner_phone="03122550183",
            caretaker_name="Fidaur Rehman S/O Amir Sahib Khan",
            caretaker_address="F54,FLAT 4,Golden Jubilee Soceity, GT Road, Rawalpindi",
            caretaker_cnic="15202-3252983-5",
            caretaker_phone="03122550183",
            property_address1="F54 Golden Jubilee Society, GT Road, Rawalpindi",
            property_address2="GT Road",
            property_city="Rawalpindi",
            property_state="Punjab",
            property_zipcode="44000",
            type="Residential",
            property_type=random.choice(
                ['apartment', 'house', 'condo', 'commercial']),
            total_units=16,
            description=fake.paragraph()
        )

        # 3. Units
        units = []

        for i in range(prop1.total_units):
            unit = Unit.objects.create(
                property=prop1,
                unit_number=f"{prop1.property_name[:3].upper()}-FLAT# {i+1:02d}",
                monthly_rent=25000,
                status='vacant',
            )
            units.append(unit)

        for i in range(prop2.total_units):
            unit = Unit.objects.create(
                property=prop2,
                unit_number=f"{prop1.property_name[:3].upper()}-FLAT# {i+1:02d}",
                monthly_rent=25000,
                status='vacant',
            )
            units.append(unit)

        for i in range(prop3.total_units):
            unit = Unit.objects.create(
                property=prop3,
                unit_number=f"{prop1.property_name[:3].upper()}-FLAT# {i+1:02d}",
                monthly_rent=25000,
                status='vacant',
            )
            units.append(unit)

        for i in range(prop4.total_units):
            unit = Unit.objects.create(
                property=prop4,
                unit_number=f"{prop1.property_name[:3].upper()}-ROOM# {i+1:02d}",
                monthly_rent=25000,
                status='vacant',
            )
            units.append(unit)

        # 4. Tenants
        tenants = []
        for i in range(20):
            tenant = Tenant.objects.create(
                first_name=fake.first_name(),
                last_name=fake.last_name(),
                email=fake.email(),
                phone="03122550183",
                cnic=fake.random_number(digits=13, fix_len=True),
                address=fake.address(),
                gender=random.choice(['M', 'F', 'O']),
                emergency_contact_name=fake.name(),
                emergency_contact_phone="03122550183",
                number_of_family_member=str(random.randint(1, 8)),
                notes=fake.paragraph()
            )
            tenants.append(tenant)

        # 5. Leases
        leases = []
        for tenant in random.sample(tenants, 15):
            unit = random.choice([u for u in units if u.status == 'vacant'])
            unit.status = 'occupied'
            unit.save()

            start_date = fake.date_between(start_date='-1y', end_date='today')
            lease = Lease.objects.create(
                tenant=tenant,
                unit=unit,
                start_date=start_date,
                end_date=start_date + timedelta(days=365),
                monthly_rent=unit.monthly_rent,
                security_deposit=unit.monthly_rent * 2,
                status=random.choice(['active', 'ended', 'terminated']),
                notes=fake.paragraph()
            )
            leases.append(lease)

        # 7. Invoices
        invoices = []
        for lease in leases:
            for _ in range(random.randint(1, 12)):
                invoice = Invoice.objects.create(
                    lease=lease,
                    issue_date=fake.date_between(
                        start_date=lease.start_date, end_date='today'),
                    due_date=fake.date_between(
                        start_date='today', end_date='+30d'),
                    amount=lease.monthly_rent,
                    status=random.choice(['draft', 'sent', 'paid', 'overdue'])
                )

                # Invoice Items
                for ct in random.sample(category, random.randint(1, 3)):
                    InvoiceItem.objects.create(
                        invoice=invoice,
                        category=ct,
                        description=f"{ct.name} charge",
                        amount=ct.default_amount if ct.default_amount else random.randint(
                            50, 200),

                        is_recurring=ct.is_recurring
                    )

                invoices.append(invoice)

        # 8. Payments
        for invoice in random.sample(invoices, int(len(invoices) * 0.7)):  # 70% paid
            Payment.objects.create(
                lease=invoice.lease,

                payment_date=fake.date_between(
                    start_date=lease.start_date, end_date='today'),
                amount=invoice.amount,
                payment_method=random.choice(
                    ['cash', 'easypaisa', 'bank_transfer']),
                reference_number=fake.random_number(digits=10, fix_len=True),
                notes=fake.sentence()
            )

        # 9. Document Categories
        categories = []
        for name in ['Lease', 'ID Proof', 'Utility Bill', 'Maintenance']:
            cat = DocumentCategory.objects.create(
                name=name,
                description=fake.sentence()
            )
            categories.append(cat)

        # 10. Documents
        for tenant in random.sample(tenants, 10):
            Document.objects.create(
                tenant=tenant,
                category=random.choice(categories),
                title=f"{fake.word()} document",
                description=fake.sentence()
            )

        # 11. Lease Documents
        for lease in random.sample(leases, 10):
            LeaseDocument.objects.create(
                tenant=lease.tenant,
                category=DocumentCategory.objects.get(name='Lease'),
                title=f"Lease Agreement for {lease.unit}",
                description="Signed lease agreement",
                start_date=lease.start_date,
                end_date=lease.end_date,
                monthly_rent=lease.monthly_rent,
                deposit_amount=lease.security_deposit,
                terms=lease.terms
            )

        # 12. Notification
        for tenant in random.sample(tenants, 15):
            Notification.objects.create(
                tenant=tenant,
                subject=fake.sentence(),
                message=fake.paragraph(),
                notification_type=random.choice(
                    ['email', 'sms', 'call', 'meeting']),
                category=random.choice(
                    ['payment', 'lease', 'maintenance', 'general']),
                created_by=admin
            )

        # 13. Financial Reports
        for _ in range(5):
            start = fake.date_between(start_date='-1y', end_date='-1m')
            FinancialReport.objects.create(
                report_type=random.choice(['monthly', 'quarterly', 'annual']),
                start_date=start,
                end_date=start + timedelta(days=30),
                notes=fake.paragraph()
            )

        # 14. Reports
        for _ in range(3):
            Report.objects.create(
                title=fake.sentence(),
                content=fake.text(),
                description=fake.paragraph(),
                status=random.choice(['pending', 'completed', 'failed']),
                created_by=admin
            )

        # 15. Utilities
        for prop in properties:
            for _ in range(3):
                Utility.objects.create(
                    property=prop,
                    utility_type=random.choice(
                        ['water', 'electricity', 'gas', 'maintenance']),
                    amount=random.randint(100, 500),
                    billing_date=fake.date_between(
                        start_date='-1y', end_date='today'),
                    due_date=fake.date_between(
                        start_date='today', end_date='+30d'),
                    distribution_method=random.choice(
                        ['equal', 'per_person', 'usage']),
                    description=fake.sentence()
                )

        self.stdout.write(self.style.SUCCESS(
            'Successfully created dummy data!'))
