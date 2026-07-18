from django.db import migrations


CLAUSE_19 = (
    "That the Electricity Meter reading is [ELECTRICITY_METER_READING] as on "
    "[METER_READING_DATE]. [SMART_METER_ELECTRICITY_TERMS]"
)


def update_clause_19(apps, schema_editor):
    AgreementPlaceholder = apps.get_model("leases", "AgreementPlaceholder")
    DefaultClause = apps.get_model("leases", "DefaultClause")
    Lease = apps.get_model("leases", "Lease")
    LeaseAgreementClause = apps.get_model("leases", "LeaseAgreementClause")
    LeaseRenewal = apps.get_model("leases", "LeaseRenewal")
    LeaseRenewalClause = apps.get_model("leases", "LeaseRenewalClause")

    default_clause = DefaultClause.objects.filter(
        clause_number=19,
        is_active=True,
    ).first()
    if default_clause:
        default_clause.body = CLAUSE_19
        default_clause.category = "general"
        default_clause.save(update_fields=["body", "category", "updated_at"])
    else:
        DefaultClause.objects.create(
            clause_number=19,
            category="general",
            body=CLAUSE_19,
            is_active=True,
        )

    AgreementPlaceholder.objects.update_or_create(
        key="SMART_METER_ELECTRICITY_TERMS",
        defaults={
            "label": "Smart Meter Electricity Terms",
            "description": (
                "Shows electricity unit-rate, fixed-charge, and prepaid-programming "
                "wording only when the unit is marked as having a smart meter."
            ),
            "category": "Utilities",
            "source_type": "system",
            "resolver_key": "SMART_METER_ELECTRICITY_TERMS",
            "is_active": True,
            "sort_order": 957,
        },
    )

    active_lease_ids = list(
        Lease.objects.filter(status="active").values_list("id", flat=True)
    )
    for lease_id in active_lease_ids:
        LeaseAgreementClause.objects.update_or_create(
            lease_id=lease_id,
            clause_number=19,
            defaults={"template_text": CLAUSE_19, "is_customized": False},
        )

    active_history_ids = LeaseRenewal.objects.filter(
        lease_id__in=active_lease_ids
    ).values_list("id", flat=True)
    for history_id in active_history_ids.iterator():
        LeaseRenewalClause.objects.update_or_create(
            renewal_id=history_id,
            clause_number=19,
            defaults={"template_text": CLAUSE_19, "is_customized": False},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0080_add_overnight_occupant_water_charge_clause"),
    ]

    operations = [
        migrations.RunPython(update_clause_19, migrations.RunPython.noop),
    ]
