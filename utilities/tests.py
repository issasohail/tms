from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from properties.models import Property, Unit

from .models import Utility


class UtilityListPerformanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "utility-performance-admin",
            email="utility-performance@example.com",
            password="test",
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Utility Performance Property",
            owner_name="Owner",
            owner_cnic="37405-5656565-1",
            type="Residential",
            property_type="apartment",
            total_units=2,
        )
        Unit.objects.bulk_create(
            [
                Unit(property=self.property, unit_number="UP-01"),
                Unit(property=self.property, unit_number="UP-02"),
            ]
        )
        today = timezone.localdate()
        Utility.objects.bulk_create(
            [
                Utility(
                    property=self.property,
                    utility_type="water",
                    amount=Decimal("1000.00") + index,
                    billing_date=today - timedelta(days=index),
                    due_date=today + timedelta(days=7),
                    distribution_method="equal",
                )
                for index in range(12)
            ]
        )

    def test_normal_list_uses_one_count_and_no_property_n_plus_one(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("utility_list"))

        self.assertEqual(response.status_code, 200)
        sql = [query["sql"].lower() for query in queries.captured_queries]
        self.assertEqual(
            sum(
                "select count(*)" in query
                and "from `utilities_utility`" in query
                for query in sql
            ),
            1,
        )
        self.assertFalse(
            any(
                "from `properties_property`" in query
                and "where `properties_property`.`id`" in query
                for query in sql
            )
        )
        self.assertContains(response, "(2 units)")

