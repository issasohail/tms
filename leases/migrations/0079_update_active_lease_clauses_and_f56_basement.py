from decimal import Decimal

from django.db import migrations


CLAUSES = {
    2: (
        "[ADDITIONAL_MONTHLY_CHARGES_CLAUSE]"
        "The total monthly payment, consisting of rent and all applicable recurring "
        "charges, is Rs. [TOTAL_MONTHLY]/- ([TOTAL_MONTHLY_IN_WORDS] Rupees Only)."
    ),
    3: (
        "That one month's advance payment of Rs. [TOTAL_MONTHLY]/- "
        "([TOTAL_MONTHLY_IN_WORDS] Rupees Only) has been paid by the Tenant and received "
        "by the Owner. Thereafter, the total monthly payment shall be paid in advance on "
        "or before [DUE_DATE]. [LATE_FEE_CLAUSE]The Tenant agrees that failure to make "
        "payment on time may result in the suspension or disconnection of utility services, "
        "including electricity, water supply, and internet. [SMART_METER_PAYMENT_CLAUSE]"
    ),
    4: (
        "That a further sum of Rs. [SECURITY_DEPOSIT]/- "
        "([SECURITY_DEPOSIT_IN_WORDS] Rupees Only) will be paid by the Tenant to the Owner "
        "as Security before taking possession. [SECURITY_INSTALLMENT_CLAUSE]"
        "The security is refundable after 30 days from the time of vacation of said premises "
        "after deducting breakage, damages, and clearance of all utility bills (Electricity, "
        "Sui Gas, Society/Building Maintenance Charges, Telephone, etc.)."
    ),
    11: (
        "That the said premises will be used strictly for residential purposes only. No music "
        "or loud noise is allowed at any time. No person other than those named in this Lease "
        "is allowed inside the premises. The Tenant must meet all other visitors outside the "
        "building."
    ),
}


PLACEHOLDERS = (
    (
        "ADDITIONAL_MONTHLY_CHARGES_CLAUSE",
        "Additional Monthly Charges Clause",
        "Shows only non-zero society/building maintenance, water, and internet charges.",
    ),
    (
        "LATE_FEE_CLAUSE",
        "Late Fee Clause",
        "Uses the effective global or lease-specific late-fee settings.",
    ),
    (
        "SECURITY_INSTALLMENT_CLAUSE",
        "Security Installment Clause",
        "Shows only when a security installment amount or date is entered.",
    ),
    (
        "SMART_METER_PAYMENT_CLAUSE",
        "Smart Meter Payment Clause",
        "Shows only when the leased unit is marked as having a smart meter.",
    ),
    (
        "WATER_CHARGES",
        "Water Charges",
        "Monthly water charge stored on the lease.",
    ),
    (
        "INTERNET_CHARGES",
        "Internet Charges",
        "Monthly internet charge stored on the lease.",
    ),
)


def update_clauses_and_settings(apps, schema_editor):
    AgreementPlaceholder = apps.get_model("leases", "AgreementPlaceholder")
    DefaultClause = apps.get_model("leases", "DefaultClause")
    Lease = apps.get_model("leases", "Lease")
    LeaseAgreementClause = apps.get_model("leases", "LeaseAgreementClause")
    LeaseLateFeeSettings = apps.get_model("leases", "LeaseLateFeeSettings")
    LeaseRenewal = apps.get_model("leases", "LeaseRenewal")
    LeaseRenewalClause = apps.get_model("leases", "LeaseRenewalClause")
    GlobalSettings = apps.get_model("core", "GlobalSettings")
    Unit = apps.get_model("properties", "Unit")

    for clause_number, body in CLAUSES.items():
        default_clause = DefaultClause.objects.filter(
            clause_number=clause_number,
            is_active=True,
        ).first()
        if default_clause:
            default_clause.body = body
            default_clause.category = "general"
            default_clause.save(update_fields=["body", "category", "updated_at"])
        else:
            DefaultClause.objects.create(
                clause_number=clause_number,
                category="general",
                body=body,
                is_active=True,
            )

    for sort_offset, (key, label, description) in enumerate(PLACEHOLDERS, start=1):
        AgreementPlaceholder.objects.update_or_create(
            key=key,
            defaults={
                "label": label,
                "description": description,
                "category": "Financial" if key != "SMART_METER_PAYMENT_CLAUSE" else "Utilities",
                "source_type": "system",
                "resolver_key": key,
                "is_active": True,
                "sort_order": 950 + sort_offset,
            },
        )

    active_lease_ids = list(
        Lease.objects.filter(status="active").values_list("id", flat=True)
    )
    for lease_id in active_lease_ids:
        for clause_number, body in CLAUSES.items():
            LeaseAgreementClause.objects.update_or_create(
                lease_id=lease_id,
                clause_number=clause_number,
                defaults={"template_text": body, "is_customized": False},
            )

    active_histories = LeaseRenewal.objects.filter(lease_id__in=active_lease_ids)
    for history_id in active_histories.values_list("id", flat=True).iterator():
        for clause_number, body in CLAUSES.items():
            LeaseRenewalClause.objects.update_or_create(
                renewal_id=history_id,
                clause_number=clause_number,
                defaults={"template_text": body, "is_customized": False},
            )

    GlobalSettings.objects.update_or_create(
        pk=1,
        defaults={
            "late_fee_enabled": True,
            "late_fee_type": "fixed",
            "late_fee_amount": Decimal("1000.00"),
            "late_fee_percent": Decimal("0.00"),
            "late_fee_grace_days": 5,
            "late_fee_reminder_interval_days": 5,
        },
    )

    Lease.objects.all().update(late_fee=Decimal("1000.00"))
    for lease_id in Lease.objects.values_list("id", flat=True).iterator():
        LeaseLateFeeSettings.objects.update_or_create(
            lease_id=lease_id,
            defaults={
                "late_fee_enabled": True,
                "late_fee_type": "fixed",
                "late_fee_amount": Decimal("1000.00"),
                "late_fee_percent": Decimal("0.00"),
                "late_fee_grace_days": 5,
                "reminder_interval_days": 5,
            },
        )

    Unit.objects.filter(
        property__property_name__iexact="F56 Basement",
        unit_number__icontains="ROOM",
    ).update(
        bedrooms=1,
        bathrooms=1,
        kitchens=1,
        hall=0,
        ceiling_fan=1,
        exhaust_fan=2,
        ceiling_lights=3,
        stove=0,
        keys=2,
        wardrobes=1,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_globalsettings_default_lease_months"),
        ("properties", "0024_backfill_unit_building_types"),
        ("leases", "0078_lease_lease_months_leaserenewal_lease_months"),
    ]

    operations = [
        migrations.RunPython(
            update_clauses_and_settings,
            migrations.RunPython.noop,
        ),
    ]
