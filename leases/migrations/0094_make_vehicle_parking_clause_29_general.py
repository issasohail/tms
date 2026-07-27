from django.db import migrations


CLAUSE_29 = (
    "All vehicles belonging to the Tenant, occupants, or visitors must be parked "
    "outside the premises in a lawful and safe location. The Tenant is solely "
    "responsible for the parking, security, and use of those vehicles, and the "
    "Owner or Management shall not be liable for any loss, theft, damage, fine, "
    "or related claim."
)

PREVIOUS_CLAUSE_29 = (
    "Motorcycles and other vehicles shall not be brought into or parked in any "
    "residential, hallway, stairway, or other non-designated area of the building. "
    "[PARKING_ASSIGNMENT_TERMS] Reserved motorcycle parking is charged at Rs. "
    "[PARKING_MONTHLY_RATE]/- per space per month and included in monthly billing. "
    "Any vehicle found parked without authorization or outside its assigned space "
    "may incur a penalty of Rs. [UNAUTHORIZED_PARKING_PENALTY]/- for each violation."
)


def apply_clause_29(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")
    DefaultLeaseClause = apps.get_model("leases", "DefaultLeaseClause")
    Lease = apps.get_model("leases", "Lease")
    LeaseAgreementClause = apps.get_model("leases", "LeaseAgreementClause")
    LeaseRenewal = apps.get_model("leases", "LeaseRenewal")
    LeaseRenewalClause = apps.get_model("leases", "LeaseRenewalClause")

    DefaultClause.objects.update_or_create(
        clause_number=29,
        is_active=True,
        defaults={"category": "general", "body": CLAUSE_29},
    )
    DefaultLeaseClause.objects.update_or_create(
        clause_number=29,
        defaults={"template_text": CLAUSE_29, "is_active": True},
    )
    for lease_id in Lease.objects.values_list("pk", flat=True).iterator():
        LeaseAgreementClause.objects.update_or_create(
            lease_id=lease_id,
            clause_number=29,
            defaults={"template_text": CLAUSE_29, "is_customized": False},
        )
    for renewal_id in LeaseRenewal.objects.values_list("pk", flat=True).iterator():
        LeaseRenewalClause.objects.update_or_create(
            renewal_id=renewal_id,
            clause_number=29,
            defaults={"template_text": CLAUSE_29, "is_customized": False},
        )


def reverse_clause_29(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")
    DefaultLeaseClause = apps.get_model("leases", "DefaultLeaseClause")
    LeaseAgreementClause = apps.get_model("leases", "LeaseAgreementClause")
    LeaseRenewalClause = apps.get_model("leases", "LeaseRenewalClause")

    DefaultClause.objects.filter(
        clause_number=29, is_active=True, body=CLAUSE_29
    ).update(category="parking", body=PREVIOUS_CLAUSE_29)
    DefaultLeaseClause.objects.filter(
        clause_number=29, template_text=CLAUSE_29
    ).update(template_text=PREVIOUS_CLAUSE_29)
    LeaseAgreementClause.objects.filter(
        clause_number=29, template_text=CLAUSE_29
    ).update(template_text=PREVIOUS_CLAUSE_29)
    LeaseRenewalClause.objects.filter(
        clause_number=29, template_text=CLAUSE_29
    ).update(template_text=PREVIOUS_CLAUSE_29)


class Migration(migrations.Migration):
    dependencies = [
        ("leases", "0093_estamp_category_settings_permission"),
    ]

    operations = [
        migrations.RunPython(apply_clause_29, reverse_clause_29),
    ]
