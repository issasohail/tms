from django.db import migrations, models
from django.db.models import Max


CLAUSE_BODY = (
    "Upon move-out during a billing month, recurring monthly charges shall be prorated "
    "from the first day of that month through the lease end date. Billable occupancy "
    "will be rounded upward using the [PRORATION_INTERVAL_LABEL] "
    "([PRORATION_INTERVAL_DAYS]-day) billing interval selected for this lease, capped "
    "at the number of calendar days in that month. Metered electricity is excluded "
    "from this proration and will be billed separately according to actual consumption "
    "through the lease end date."
)


def seed_proration_clause(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")
    AgreementPlaceholder = apps.get_model("leases", "AgreementPlaceholder")

    if not DefaultClause.objects.filter(
        body__contains="[PRORATION_INTERVAL_DAYS]", is_active=True
    ).exists():
        next_number = (
            DefaultClause.objects.filter(is_active=True).aggregate(value=Max("clause_number"))["value"]
            or 0
        ) + 1
        DefaultClause.objects.create(
            clause_number=next_number,
            category="general",
            body=CLAUSE_BODY,
            is_active=True,
        )

    placeholders = (
        ("PRORATION_INTERVAL_DAYS", "Proration Interval Days", "Numeric move-out billing block."),
        ("PRORATION_INTERVAL_LABEL", "Proration Interval Label", "Readable move-out billing block."),
    )
    for offset, (key, label, description) in enumerate(placeholders, start=1):
        AgreementPlaceholder.objects.update_or_create(
            key=key,
            defaults={
                "label": label,
                "description": description,
                "category": "Financial",
                "source_type": "system",
                "resolver_key": key,
                "is_active": True,
                "sort_order": 900 + offset,
            },
        )


def remove_proration_clause(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")
    AgreementPlaceholder = apps.get_model("leases", "AgreementPlaceholder")
    DefaultClause.objects.filter(body=CLAUSE_BODY).delete()
    AgreementPlaceholder.objects.filter(
        key__in=["PRORATION_INTERVAL_DAYS", "PRORATION_INTERVAL_LABEL"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_globalsettings_end_lease_proration_interval_days"),
        ("leases", "0075_alter_leasevehicle_owner_cnic_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="lease",
            name="proration_interval_days",
            field=models.PositiveSmallIntegerField(
                blank=True,
                choices=[
                    (1, "Daily"),
                    (5, "5 days"),
                    (7, "Weekly"),
                    (10, "10 days"),
                    (15, "15 days"),
                ],
                help_text="Leave blank to use the system move-out proration interval.",
                null=True,
            ),
        ),
        migrations.RunPython(seed_proration_clause, remove_proration_clause),
    ]
