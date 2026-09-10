from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from leases.models import Lease
from payments.models import Payment, PaymentDetail
from properties.models import Property, Unit
from smart_meter.models import (
    Meter,
    MeterPrepaidPaymentTopup,
    MeterPrepaidPilot,
    MeterPrepaidRecharge,
    MeterSettings,
)
from smart_meter.services.prepaid_payments import queue_payment_electricity_topup
from tenants.models import Tenant


class PrepaidPaymentTopupTests(TestCase):
    def setUp(self):
        MeterSettings.objects.update_or_create(
            pk=1,
            defaults={
                "prepaid_reads_enabled": True,
                "prepaid_writes_enabled": True,
                "prepaid_payment_topups_enabled": True,
            },
        )
        property_obj = Property.objects.create(
            property_name="Prepaid Payment Test",
            owner_name="Owner",
            owner_cnic="1234512345678",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(property=property_obj, unit_number="1")
        tenant = Tenant.objects.create(first_name="Prepaid", last_name="Tenant", cnic="1234512345679")
        lease = Lease.objects.create(
            tenant=tenant,
            unit=unit,
            start_date="2026-01-01",
            end_date="2026-12-31",
            monthly_rent=Decimal("1000.00"),
        )
        self.meter = Meter.objects.create(
            meter_number="260305519999",
            unit=unit,
            billing_mode="prepaid_pilot",
        )
        MeterPrepaidPilot.objects.create(meter=self.meter, status="active_test")
        payment = Payment.objects.create(lease=lease, amount=Decimal("500.00"))
        self.detail = PaymentDetail.objects.create(
            payment=payment,
            lease_amount=Decimal("500.00"),
            electricity_amount=Decimal("200.00"),
            electricity_meter=self.meter,
        )

    @patch("smart_meter.services.prepaid_payments.queue_prepaid_money_transaction")
    def test_electricity_allocation_queues_once(self, mocked_queue):
        recharge = MeterPrepaidRecharge.objects.create(
            pilot=self.meter.prepaid_pilot,
            transaction_id="PAYMENTTOPUP0001",
            amount=Decimal("200.00"),
            status="pending",
        )
        mocked_queue.return_value = (recharge, object())

        first = queue_payment_electricity_topup(self.detail.pk)
        second = queue_payment_electricity_topup(self.detail.pk)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.allocated_amount, Decimal("200.00"))
        mocked_queue.assert_called_once()
        self.assertEqual(MeterPrepaidPaymentTopup.objects.count(), 1)

    @patch("smart_meter.services.prepaid_payments.queue_prepaid_money_transaction")
    def test_postpaid_meter_is_not_transmitted(self, mocked_queue):
        self.meter.billing_mode = "postpaid"
        self.meter.save(update_fields=["billing_mode"])

        record = queue_payment_electricity_topup(self.detail.pk)

        self.assertEqual(record.status, "failed")
        self.assertIn("not enabled", record.error)
        mocked_queue.assert_not_called()
