from datetime import timedelta
from decimal import Decimal

from django.db.models import Case, DecimalField, Exists, F, Max, OuterRef, Sum, Value, When
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.shortcuts import render
from django.utils import timezone

from expenses.models import Expense
from invoices.models import Invoice
from leases.models import Lease, LeaseRenewal
from payments.models import Payment
from properties.models import Property, Unit
from smart_meter.models import LiveReading
from smart_meter.utils.tenants import attach_active_tenant_names
from tenants.models import Tenant

ZERO = Decimal("0.00")
METER_ONLINE_MINUTES = 3


def dashboard(request):
    today = timezone.now().date()

    total_properties = Property.objects.count()
    total_units = Unit.objects.count()

    current_lease_unit_ids = set(
        Lease.objects.filter(
            start_date__lte=today,
            end_date__gte=today,
        )
        .exclude(status__in=["ended", "terminated"])
        .values_list("unit_id", flat=True)
        .distinct()
    )
    current_history_unit_ids = set(
        LeaseRenewal.objects.filter(
            start_date__lte=today,
            end_date__gte=today,
        )
        .values_list("lease__unit_id", flat=True)
        .distinct()
    )
    occupied_unit_ids = current_lease_unit_ids | current_history_unit_ids
    occupied_units = len(occupied_unit_ids)
    vacancy_rate = round(
        ((total_units - occupied_units) / total_units * 100) if total_units > 0 else 0,
        1,
    )

    total_tenants = Tenant.objects.filter(is_active=True).count()

    thirty_days_ago = today - timedelta(days=30)

    net_income = (
        Payment.objects.filter(payment_date__gte=thirty_days_ago)
        .aggregate(
            total=Coalesce(Sum("amount"), Value(ZERO), output_field=DecimalField())
        )
        .get("total")
        or ZERO
    )

    recent_payments = list(
        Payment.objects.select_related(
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
        )
        .select_related("payment_method")
        .order_by("-payment_date", "-id")[:5]
    )

    recent_lease_ids = [
        payment.lease_id for payment in recent_payments if payment.lease_id
    ]

    invoice_totals = {
        row["lease_id"]: row["total"] or ZERO
        for row in (
            Invoice.objects.filter(lease_id__in=recent_lease_ids)
            .values("lease_id")
            .annotate(
                total=Coalesce(
                    Sum("amount"),
                    Value(ZERO),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )
        )
    }

    payment_totals = {
        row["lease_id"]: row["total"] or ZERO
        for row in (
            Payment.objects.filter(lease_id__in=recent_lease_ids)
            .values("lease_id")
            .annotate(
                total=Coalesce(
                    Sum(
                        Case(
                            When(
                                detail__isnull=False,
                                then=F("detail__lease_amount"),
                            ),
                            default=F("amount"),
                            output_field=DecimalField(max_digits=12, decimal_places=2),
                        )
                    ),
                    Value(ZERO),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )
        )
    }

    for payment in recent_payments:
        lease_id = payment.lease_id
        payment.dashboard_balance = invoice_totals.get(
            lease_id, ZERO
        ) - payment_totals.get(lease_id, ZERO)

    upcoming_invoices = (
        Invoice.objects.select_related(
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
        )
        .filter(
            due_date__gte=today,
            due_date__lte=today + timedelta(days=15),
            status__in=["unpaid", "partially_paid"],
        )
        .order_by("due_date", "id")[:5]
    )

    meter_offline_cutoff = timezone.now() - timedelta(minutes=METER_ONLINE_MINUTES)
    offline_meter_readings = list(
        LiveReading.objects.select_related(
            "meter",
            "meter__unit",
            "meter__unit__property",
        )
        .filter(
            meter__is_active=True,
            ts__lt=meter_offline_cutoff,
        )
        .order_by("ts", "meter__unit__property__property_name", "meter__unit__unit_number")[:10]
    )
    attach_active_tenant_names(
        offline_meter_readings,
        lambda reading: reading.meter.unit_id if reading.meter else None,
    )

    ending_soon_histories = list(
        LeaseRenewal.objects.select_related(
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
        )
        .filter(
            end_date__gte=today,
            end_date__lte=today + timedelta(days=40),
        )
        .order_by("end_date", "id")
    )

    history_lease_ids = {history.lease_id for history in ending_soon_histories}
    ending_soon_leases = [
        {
            "lease": history.lease,
            "tenant": history.lease.tenant,
            "unit": history.lease.unit,
            "end_date": history.end_date,
            "total_payment": history.total_monthly_amount,
            "security_deposit": history.security_deposit,
            "url": reverse("leases:lease_detail", args=[history.lease.pk]),
            "source": history.history_label,
        }
        for history in ending_soon_histories
    ]

    fallback_ending_leases = (
        Lease.objects.select_related(
            "tenant",
            "unit",
            "unit__property",
        )
        .filter(
            end_date__gte=today,
            end_date__lte=today + timedelta(days=40),
        )
        .exclude(status__in=["ended", "terminated"])
        .exclude(id__in=history_lease_ids)
        .order_by("end_date", "id")
    )
    ending_soon_leases.extend(
        {
            "lease": lease,
            "tenant": lease.tenant,
            "unit": lease.unit,
            "end_date": lease.end_date,
            "total_payment": lease.total_payment,
            "security_deposit": lease.security_deposit,
            "url": reverse("leases:lease_detail", args=[lease.pk]),
            "source": "Lease",
        }
        for lease in fallback_ending_leases
    )
    ending_soon_leases.sort(key=lambda row: (row["end_date"], row["lease"].pk))

    current_lease = Lease.objects.filter(
        unit_id=OuterRef("pk"),
        start_date__lte=today,
        end_date__gte=today,
    ).exclude(status__in=["ended", "terminated"])
    current_history = LeaseRenewal.objects.filter(
        lease__unit_id=OuterRef("pk"),
        start_date__lte=today,
        end_date__gte=today,
    )
    vacant_units = (
        Unit.objects.select_related("property")
        .annotate(
            has_current_lease=Exists(current_lease),
            has_current_history=Exists(current_history),
        )
        .filter(has_current_lease=False, has_current_history=False)
        .exclude(status="maintenance")
        .order_by("property__property_name", "unit_number")
    )

    recent_expenses = Expense.objects.select_related("property").order_by(
        "-date", "-id"
    )[:5]

    current_history_lease_ids = set(
        LeaseRenewal.objects.filter(
            start_date__lte=today,
            end_date__gte=today,
            lease__tenant__is_active=True,
        )
        .values_list("lease_id", flat=True)
        .distinct()
    )
    active_leases = list(
        Lease.objects.select_related(
            "tenant",
            "unit",
            "unit__property",
        )
        .filter(
            tenant__is_active=True,
            id__in=current_history_lease_ids,
        )
        .order_by("tenant_id", "-start_date", "-id")
    )
    active_lease_ids = {lease.id for lease in active_leases}
    fallback_active_leases = list(
        Lease.objects.select_related(
            "tenant",
            "unit",
            "unit__property",
        )
        .filter(
            tenant__is_active=True,
            start_date__lte=today,
            end_date__gte=today,
        )
        .exclude(status__in=["ended", "terminated"])
        .exclude(id__in=active_lease_ids)
        .order_by("tenant_id", "-start_date", "-id")
    )
    active_leases.extend(fallback_active_leases)

    lease_ids = [lease.id for lease in active_leases]

    active_invoice_totals = {
        row["lease_id"]: row["total"] or ZERO
        for row in (
            Invoice.objects.filter(lease_id__in=lease_ids)
            .values("lease_id")
            .annotate(
                total=Coalesce(
                    Sum("amount"),
                    Value(ZERO),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )
        )
    }

    active_payment_totals = {
        row["lease_id"]: {
            "total": row["total"] or ZERO,
            "last_payment_date": row["last_payment_date"],
        }
        for row in (
            Payment.objects.filter(lease_id__in=lease_ids)
            .values("lease_id")
            .annotate(
                total=Coalesce(
                    Sum(
                        Case(
                            When(
                                detail__isnull=False,
                                then=F("detail__lease_amount"),
                            ),
                            default=F("amount"),
                            output_field=DecimalField(max_digits=12, decimal_places=2),
                        )
                    ),
                    Value(ZERO),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                last_payment_date=Max("payment_date"),
            )
        )
    }

    tenant_balances = []
    seen_tenants = set()

    for lease in active_leases:
        tenant = lease.tenant

        if tenant.pk in seen_tenants:
            continue

        seen_tenants.add(tenant.pk)

        payments_info = active_payment_totals.get(lease.id, {})
        balance = active_invoice_totals.get(lease.id, ZERO) - payments_info.get(
            "total",
            ZERO,
        )

        tenant_balances.append(
            {
                "id": tenant.pk,
                "full_name": tenant.get_full_name(),
                "balance": balance,
                "last_payment_date": payments_info.get("last_payment_date"),
                "current_lease": lease,
            }
        )

    tenant_balances.sort(key=lambda row: row["balance"], reverse=True)

    context = {
        "total_properties": total_properties,
        "total_units": total_units,
        "occupied_units": occupied_units,
        "vacancy_rate": vacancy_rate,
        "total_tenants": total_tenants,
        "net_income": net_income,
        "recent_payments": recent_payments,
        "upcoming_invoices": upcoming_invoices,
        "offline_meter_readings": offline_meter_readings,
        "meter_online_minutes": METER_ONLINE_MINUTES,
        "ending_soon_leases": ending_soon_leases,
        "vacant_units": vacant_units,
        "recent_expenses": recent_expenses,
        "tenant_balances": tenant_balances[:10],
    }

    return render(request, "dashboard/dashboard.html", context)
