from django.db import migrations


CLAUSE_2 = (
    "[ADDITIONAL_MONTHLY_CHARGES_CLAUSE]"
    "The total monthly payment, consisting of rent and all applicable recurring "
    "charges, is Rs. [TOTAL_MONTHLY]/- ([TOTAL_MONTHLY_IN_WORDS] Rupees Only). "
    "If any additional person beyond those named in this Agreement is found staying "
    "overnight at the premises, an additional Rs. 1,000/- per person will be added as "
    "water charges to the monthly billing."
)


def update_clause_2(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")
    Lease = apps.get_model("leases", "Lease")
    LeaseAgreementClause = apps.get_model("leases", "LeaseAgreementClause")
    LeaseRenewal = apps.get_model("leases", "LeaseRenewal")
    LeaseRenewalClause = apps.get_model("leases", "LeaseRenewalClause")

    default_clause = DefaultClause.objects.filter(
        clause_number=2,
        is_active=True,
    ).first()
    if default_clause:
        default_clause.body = CLAUSE_2
        default_clause.category = "general"
        default_clause.save(update_fields=["body", "category", "updated_at"])
    else:
        DefaultClause.objects.create(
            clause_number=2,
            category="general",
            body=CLAUSE_2,
            is_active=True,
        )

    active_lease_ids = list(
        Lease.objects.filter(status="active").values_list("id", flat=True)
    )
    for lease_id in active_lease_ids:
        LeaseAgreementClause.objects.update_or_create(
            lease_id=lease_id,
            clause_number=2,
            defaults={"template_text": CLAUSE_2, "is_customized": False},
        )

    active_history_ids = LeaseRenewal.objects.filter(
        lease_id__in=active_lease_ids
    ).values_list("id", flat=True)
    for history_id in active_history_ids.iterator():
        LeaseRenewalClause.objects.update_or_create(
            renewal_id=history_id,
            clause_number=2,
            defaults={"template_text": CLAUSE_2, "is_customized": False},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0079_update_active_lease_clauses_and_f56_basement"),
    ]

    operations = [
        migrations.RunPython(update_clause_2, migrations.RunPython.noop),
    ]
