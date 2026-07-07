# Generated manually for TMS lease vehicle tracking

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_default_vehicle_types(apps, schema_editor):
    LeaseVehicleType = apps.get_model("leases", "LeaseVehicleType")

    default_types = [
        ("car", "Car", 10),
        ("motorcycle", "Motorcycle", 20),
        ("rickshaw", "Rickshaw", 30),
        ("van", "Van", 40),
        ("truck", "Truck", 50),
        ("bicycle", "Bicycle", 60),
        ("other", "Other", 999),
    ]

    for code, name, sort_order in default_types:
        LeaseVehicleType.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "is_active": True,
                "sort_order": sort_order,
            },
        )


def reverse_seed_default_vehicle_types(apps, schema_editor):
    LeaseVehicleType = apps.get_model("leases", "LeaseVehicleType")
    LeaseVehicleType.objects.filter(
        code__in=[
            "car",
            "motorcycle",
            "rickshaw",
            "van",
            "truck",
            "bicycle",
            "other",
        ]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("leases", "0063_inspection_quantities"),
        ("tenants", "0018_tenant_family_member_adults_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LeaseVehicleType",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=80)),
                ("code", models.SlugField(max_length=80, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=50)),
            ],
            options={
                "verbose_name": "Lease Vehicle Type",
                "verbose_name_plural": "Lease Vehicle Types",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.CreateModel(
            name="LeaseVehicle",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("registration_number", models.CharField(max_length=50)),
                ("make", models.CharField(blank=True, max_length=80)),
                ("model", models.CharField(blank=True, max_length=80)),
                ("color", models.CharField(blank=True, max_length=50)),
                ("year", models.PositiveIntegerField(blank=True, null=True)),
                ("owner_name", models.CharField(blank=True, max_length=120)),
                ("owner_cnic", models.CharField(blank=True, max_length=30)),
                ("parking_slot", models.CharField(blank=True, max_length=50)),
                (
                    "registration_book_photo",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="leases/vehicles/registration_book/",
                    ),
                ),
                (
                    "vehicle_photo",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="leases/vehicles/photos/",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "lease",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vehicles",
                        to="leases.lease",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="lease_vehicles",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "vehicle_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lease_vehicles",
                        to="leases.leasevehicletype",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "vehicle_type__sort_order",
                    "vehicle_type__name",
                    "registration_number",
                ],
            },
        ),
        migrations.CreateModel(
            name="PendingLeaseVehicleSubmission",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("registration_number", models.CharField(max_length=50)),
                ("make", models.CharField(blank=True, max_length=80)),
                ("model", models.CharField(blank=True, max_length=80)),
                ("color", models.CharField(blank=True, max_length=50)),
                ("year", models.PositiveIntegerField(blank=True, null=True)),
                ("owner_name", models.CharField(blank=True, max_length=120)),
                ("owner_cnic", models.CharField(blank=True, max_length=30)),
                ("parking_slot", models.CharField(blank=True, max_length=50)),
                (
                    "registration_book_photo",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="leases/vehicles/pending/registration_book/",
                    ),
                ),
                (
                    "vehicle_photo",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="leases/vehicles/pending/photos/",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_notes", models.TextField(blank=True)),
                (
                    "lease",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pending_vehicle_submissions",
                        to="leases.lease",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pending_lease_vehicle_submissions",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "vehicle_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pending_vehicle_submissions",
                        to="leases.leasevehicletype",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_vehicle_submissions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-submitted_at"],
            },
        ),
        migrations.AddIndex(
            model_name="leasevehicle",
            index=models.Index(
                fields=["lease", "is_active"],
                name="leases_leas_lease_i_veh_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="leasevehicle",
            index=models.Index(
                fields=["registration_number"],
                name="leases_leas_reg_no_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="leasevehicle",
            constraint=models.UniqueConstraint(
                fields=("lease", "registration_number"),
                name="uniq_vehicle_registration_per_lease",
            ),
        ),
        migrations.AddIndex(
            model_name="pendingleasevehiclesubmission",
            index=models.Index(
                fields=["lease", "status", "submitted_at"],
                name="leases_pveh_lease_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="pendingleasevehiclesubmission",
            index=models.Index(
                fields=["status", "submitted_at"],
                name="leases_pveh_status_time_idx",
            ),
        ),
        migrations.RunPython(
            seed_default_vehicle_types,
            reverse_seed_default_vehicle_types,
        ),
    ]
