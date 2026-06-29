from django.db import migrations, models


DEFAULT_CATEGORIES = [
    ("tenant_photo", "Tenant Photo", 10),
    ("lease_agreement", "Lease Agreement", 20),
    ("lease_history_agreement", "Lease History Agreement", 30),
    ("lease_condition_photo", "Lease Condition Photo", 40),
    ("police_verification", "Police Verification", 50),
    ("other", "Other", 100),
]


def seed_categories(apps, schema_editor):
    LeaseDocumentCategory = apps.get_model("leases", "LeaseDocumentCategory")
    for code, name, sort_order in DEFAULT_CATEGORIES:
        LeaseDocumentCategory.objects.get_or_create(
            code=code,
            defaults={"name": name, "sort_order": sort_order, "is_active": True},
        )


def unseed_categories(apps, schema_editor):
    LeaseDocumentCategory = apps.get_model("leases", "LeaseDocumentCategory")
    LeaseDocumentCategory.objects.filter(code__in=[row[0] for row in DEFAULT_CATEGORIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0041_pcrphoto_sort_order_leasedocument_sharelink"),
    ]

    operations = [
        migrations.CreateModel(
            name="LeaseDocumentCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=50)),
            ],
            options={
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.RunPython(seed_categories, unseed_categories),
    ]
