from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PunjabEStampDistrict",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("portal_value", models.CharField(max_length=40, unique=True)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=50)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "Punjab e-Stamp district",
                "verbose_name_plural": "Punjab e-Stamp districts",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.CreateModel(
            name="PunjabEStampPurpose",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255, unique=True)),
                ("portal_value", models.CharField(max_length=40, unique=True)),
                ("denomination", models.PositiveIntegerField(default=100)),
                ("default_continuation_sheets", models.PositiveSmallIntegerField(default=1)),
                ("active", models.BooleanField(default=True)),
                ("is_default_for_lease", models.BooleanField(default=False)),
                ("sort_order", models.PositiveIntegerField(default=50)),
            ],
            options={
                "verbose_name": "Punjab e-Stamp purpose",
                "verbose_name_plural": "Punjab e-Stamp purposes",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.CreateModel(
            name="PunjabEStampRelation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=40, unique=True)),
                ("portal_value", models.CharField(max_length=40, unique=True)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=50)),
            ],
            options={
                "verbose_name": "Punjab e-Stamp relation",
                "verbose_name_plural": "Punjab e-Stamp relations",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.CreateModel(
            name="PunjabEStampTehsil",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("portal_value", models.CharField(db_index=True, max_length=40)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=50)),
                (
                    "district",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tehsils",
                        to="punjab_estamp.punjabestampdistrict",
                    ),
                ),
            ],
            options={
                "verbose_name": "Punjab e-Stamp tehsil",
                "verbose_name_plural": "Punjab e-Stamp tehsils",
                "ordering": ["district__sort_order", "sort_order", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="punjabestamptehsil",
            constraint=models.UniqueConstraint(
                fields=("district", "name"),
                name="uniq_punjab_estamp_tehsil_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="punjabestamptehsil",
            constraint=models.UniqueConstraint(
                fields=("district", "portal_value"),
                name="uniq_punjab_estamp_tehsil_portal",
            ),
        ),
    ]

