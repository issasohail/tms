from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from invoices.models import ItemCategory
from properties.models import Property, Unit

from .models import Expense, ExpenseDistribution, ExpenseReceipt


class ExpenseListPerformanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "expense-performance-admin",
            email="expense-performance@example.com",
            password="test",
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Expense Performance Property",
            owner_name="Owner",
            owner_cnic="37405-1212121-1",
            type="Residential",
            property_type="apartment",
            total_units=2,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="EP-01",
        )
        self.other_unit = Unit.objects.create(
            property=self.property,
            unit_number="EP-02",
        )
        self.category = ItemCategory.objects.create(
            name="Expense Performance Category",
            is_active=True,
        )

    def _create_expenses(self, count):
        today = timezone.localdate()
        Expense.objects.bulk_create(
            [
                Expense(
                    property=self.property,
                    unit=self.unit if index % 2 else None,
                    category=self.category,
                    amount=Decimal("100.00") + index,
                    date=today - timedelta(days=index),
                    description=f"Performance expense {index}",
                )
                for index in range(count)
            ]
        )
        expenses = list(
            Expense.objects.filter(
                description__startswith="Performance expense "
            ).order_by("-date", "-id")
        )
        ExpenseReceipt.objects.bulk_create(
            [
                ExpenseReceipt(
                    expense=expenses[0],
                    image="expense_receipts/performance.jpg",
                    add_timestamp=False,
                )
            ]
        )
        ExpenseDistribution.objects.create(
            expense=expenses[0],
            unit=self.other_unit,
            amount=expenses[0].amount,
        )
        return expenses

    def test_list_query_count_is_constant_and_prefetches_rendered_relations(self):
        self._create_expenses(25)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("expenses:expense_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["table"].paginator.count, 25)
        self.assertLessEqual(len(queries), 20)
        sql = [query["sql"].lower() for query in queries.captured_queries]
        self.assertEqual(
            sum("from `expenses_expensereceipt`" in query for query in sql), 1
        )
        self.assertEqual(
            sum("from `expenses_expensedistribution`" in query for query in sql), 1
        )
        self.assertFalse(
            any(
                "count(*)" in query
                and "from `properties_unit`" in query
                and "property_id" in query
                for query in sql
            )
        )
        self.assertContains(response, "expense_receipts/performance.jpg")

    def test_filters_and_exact_pagination_count_are_preserved(self):
        expenses = self._create_expenses(3)

        response = self.client.get(
            reverse("expenses:expense_list"),
            {"category": self.category.pk, "property": self.property.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["table"].paginator.count, 3)
        rendered_ids = {record.pk for record in response.context["table"].data}
        self.assertEqual(rendered_ids, {expense.pk for expense in expenses})
