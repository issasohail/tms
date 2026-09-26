from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("leases", "0101_alter_lease_electric_unit_rate"),
        ("punjab_estamp", "0001_configuration_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="LeaseEStampWorkflow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("ready", "Ready"),
                            ("challan_generated", "Challan Generated"),
                            ("paid", "Paid"),
                            ("stamp_issued", "Stamp Issued"),
                            ("uploaded", "Uploaded to TMS"),
                            ("invalid", "Invalid"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="ready",
                        max_length=24,
                    ),
                ),
                ("applicant_snapshot", models.JSONField(blank=True, default=dict)),
                ("property_snapshot", models.JSONField(blank=True, default=dict)),
                ("portal_snapshot", models.JSONField(blank=True, default=dict)),
                ("challan_number", models.CharField(blank=True, db_index=True, max_length=100)),
                ("psid", models.CharField(blank=True, db_index=True, max_length=100)),
                ("stamp_number", models.CharField(blank=True, db_index=True, max_length=100)),
                ("challan_history", models.JSONField(blank=True, default=list)),
                ("replacement_count", models.PositiveSmallIntegerField(default=0)),
                ("last_portal_error", models.TextField(blank=True)),
                ("challan_generated_at", models.DateTimeField(blank=True, null=True)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("stamp_issued_at", models.DateTimeField(blank=True, null=True)),
                ("uploaded_at", models.DateTimeField(blank=True, null=True)),
                ("invalidated_at", models.DateTimeField(blank=True, null=True)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_punjab_estamp_workflows",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "lease",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="punjab_estamp_workflows",
                        to="leases.lease",
                    ),
                ),
                (
                    "lease_document",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="punjab_estamp_workflow",
                        to="leases.leasedocument",
                    ),
                ),
                (
                    "lease_history",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="punjab_estamp_workflow",
                        to="leases.leaserenewal",
                    ),
                ),
                (
                    "purpose",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="workflows",
                        to="punjab_estamp.punjabestamppurpose",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_punjab_estamp_workflows",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Lease Punjab e-Stamp workflow",
                "verbose_name_plural": "Lease Punjab e-Stamp workflows",
                "ordering": ["-updated_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["lease", "status"],
                        name="punjab_es_lease_status_idx",
                    )
                ],
            },
        ),
    ]

