from django.test import TestCase


class SecurityDepositBalanceTests(TestCase):
    def setUp(self):
        from datetime import date, timedelta

        from leases.models import Lease
        from properties.models import Property, Unit
        from tenants.models import Tenant

        property_obj = Property.objects.create(
            property_name="Security Test Property",
            owner_name="Test Owner",
            owner_cnic="61101-1111111-1",
            type="Residential",
            property_type="Building",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="S-1")
        tenant = Tenant.objects.create(
            first_name="Security",
            last_name="Tenant",
            cnic="61101-2222222-2",
        )
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            monthly_rent=10000,
            security_deposit=18300,
        )

    def test_required_amount_adjustment_does_not_reduce_payment_form_balance(self):
        from invoices.models import SecurityDepositTransaction
        from payments.views.payments import _attach_cached_lease_financials

        SecurityDepositTransaction.objects.create(
            lease=self.lease,
            type="ADJUST",
            amount=18300,
            notes="Required security changed from 0.00 to 18300",
        )

        lease = _attach_cached_lease_financials([self.lease])[0]

        self.assertEqual(lease.security_due, 18300)

    def test_paid_checkbox_creates_payment_in_row(self):
        from invoices.models import SecurityDepositTransaction
        from leases.views import record_security_paid_from_lease_form

        self.lease.security_deposit_paid = True
        self.lease.save(update_fields=["security_deposit_paid"])

        row = record_security_paid_from_lease_form(self.lease)

        self.assertEqual(row.type, "PAYMENT")
        self.assertEqual(row.amount, 18300)
        self.assertEqual(
            SecurityDepositTransaction.objects.filter(
                lease=self.lease,
                type="PAYMENT",
            ).count(),
            1,
        )


# Create your tests here.


class PaymentLedgerLinkTests(TestCase):
    def setUp(self):
        from datetime import date, timedelta
        from decimal import Decimal
        from django.contrib.auth import get_user_model
        from leases.models import Lease
        from payments.models import Payment
        from properties.models import Property, Unit
        from tenants.models import Tenant

        prop = Property.objects.create(
            property_name="Ledger Link Property", owner_name="Owner", owner_cnic="37405-1000000-1",
            type="Residential", property_type="apartment", total_units=1,
        )
        unit = Unit.objects.create(property=prop, unit_number="L-01")
        tenant = Tenant.objects.create(first_name="Ledger", last_name="Tenant", cnic="37405-2000000-2")
        self.lease = Lease.objects.create(
            tenant=tenant, unit=unit, start_date=date.today(), end_date=date.today()+timedelta(days=365),
            monthly_rent=Decimal("10000.00"), status="active",
        )
        self.payment = Payment.objects.create(
            lease=self.lease, payment_date=date.today(), amount=Decimal("5000.00"), reference_number="LEDGER-LINK"
        )
        from payments.services.payment_detail import rebuild_payment_detail
        rebuild_payment_detail(
            payment=self.payment, lease_amount=Decimal("5000.00"), security_amount=Decimal("0.00"),
            reason="Payment ledger link test setup",
        )
        self.admin = get_user_model().objects.create_superuser(
            username="ledger-link-admin", email="ledger@example.com", password="test"
        )

    def test_payment_detail_ledger_button_focuses_and_validates_payment(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        detail = self.client.get(reverse("payments:payment_detail", args=[self.payment.pk]))
        self.assertContains(detail, "Ledger")
        self.assertContains(detail, f"payment_id={self.payment.pk}")
        ledger = self.client.get(
            reverse("leases:lease_ledger_by_pk", args=[self.lease.pk]),
            {"payment_id": self.payment.pk},
        )
        self.assertEqual(ledger.status_code, 200)
        self.assertContains(ledger, 'id="focused-payment-row"')

    def test_payment_focus_cannot_reference_another_lease_payment(self):
        from django.urls import reverse
        from payments.models import Payment
        other = Payment.objects.create(
            lease=self.lease.__class__.objects.create(
                tenant=self.lease.tenant,
                unit=self.lease.unit.__class__.objects.create(
                    property=self.lease.unit.property, unit_number="L-02"
                ),
                start_date=self.lease.start_date,
                end_date=self.lease.end_date,
                monthly_rent=self.lease.monthly_rent,
                status="active",
            ),
            payment_date=self.payment.payment_date,
            amount=self.payment.amount,
        )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("leases:lease_ledger_by_pk", args=[self.lease.pk]),
            {"payment_id": other.pk},
        )
        self.assertEqual(response.status_code, 404)
