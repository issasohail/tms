from django.db import migrations, models


ESTAMP_CODE = "estamp_paper"


def seed_estamp_category(apps, schema_editor):
    LeaseDocumentCategory = apps.get_model("leases", "LeaseDocumentCategory")
    LeaseDocumentCategory.objects.update_or_create(
        code=ESTAMP_CODE,
        defaults={
            "name": "E-Stamp Paper",
            "is_active": True,
            "sort_order": 55,
        },
    )


def unseed_estamp_category(apps, schema_editor):
    LeaseDocument = apps.get_model("leases", "LeaseDocument")
    LeaseDocumentCategory = apps.get_model("leases", "LeaseDocumentCategory")
    if not LeaseDocument.objects.filter(category=ESTAMP_CODE).exists():
        LeaseDocumentCategory.objects.filter(code=ESTAMP_CODE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("leases", "0092_update_renewal_maintenance_inspection_clauses"),
    ]

    operations = [
        migrations.AlterField(
            model_name="leasedocument",
            name="category",
            field=models.CharField(
                choices=[
                    ("tenant_photo", "Tenant Photo"),
                    ("cnic_front", "CNIC Front"),
                    ("cnic_back", "CNIC Back"),
                    ("lease_agreement", "Lease Agreement"),
                    ("lease_renewal_agreement", "Lease Renewal Agreement"),
                    ("estamp_paper", "E-Stamp Paper"),
                    ("police_verification", "Police Verification"),
                    ("property_condition_report", "Property Condition Report"),
                    ("utility_bill", "Utility Bill"),
                    ("income_proof", "Income Proof"),
                    ("employment_letter", "Employment Letter"),
                    ("reference_letter", "Reference Letter"),
                    ("other", "Other"),
                ],
                default="other",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="estamp_max_age_days",
            field=models.PositiveIntegerField(
                default=30,
                help_text=(
                    "Warn before using an E-Stamp Paper older than this number of days. "
                    "Set to 0 to disable the age restriction."
                ),
            ),
        ),
        migrations.AlterModelOptions(
            name="leasedocument",
            options={
                "ordering": ["-uploaded_at", "-id"],
                "permissions": [
                    (
                        "override_estamp_age",
                        "Can use an E-Stamp Paper older than the configured maximum age",
                    )
                ],
            },
        ),
        migrations.RunPython(seed_estamp_category, unseed_estamp_category),
    ]
