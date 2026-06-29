import django.core.validators
import django.db.models.deletion
import leases.models
from django.conf import settings
from django.db import migrations, models


def copy_pcr_order_to_sort_order(apps, schema_editor):
    PCRPhoto = apps.get_model("leases", "PCRPhoto")
    for photo in PCRPhoto.objects.all().only("id", "order", "sort_order"):
        if not photo.sort_order:
            photo.sort_order = photo.order or 0
            photo.save(update_fields=["sort_order"])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("leases", "0040_remove_leaseunitoccupancy_one_active_occupancy_per_lease_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="pcrphoto",
            name="sort_order",
            field=models.PositiveIntegerField(db_index=True, default=0),
        ),
        migrations.RunPython(copy_pcr_order_to_sort_order, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="pcrphoto",
            options={"ordering": ["sort_order", "created_at", "id"]},
        ),
        migrations.CreateModel(
            name="LeaseDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "file",
                    models.FileField(
                        max_length=255,
                        upload_to=leases.models.lease_document_upload_to,
                        validators=[
                            django.core.validators.FileExtensionValidator(
                                ["pdf", "jpg", "jpeg", "png", "webp", "xls", "xlsx", "doc", "docx", "txt", "csv"]
                            )
                        ],
                    ),
                ),
                ("original_filename", models.CharField(blank=True, max_length=255)),
                ("display_name", models.CharField(blank=True, max_length=255)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("tenant_photo", "Tenant Photo"),
                            ("lease_agreement", "Lease Agreement"),
                            ("lease_history_agreement", "Lease History Agreement"),
                            ("lease_condition_photo", "Lease Condition Photo"),
                            ("police_verification", "Police Verification"),
                            ("other", "Other"),
                        ],
                        default="other",
                        max_length=40,
                    ),
                ),
                ("description", models.TextField(blank=True)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "lease",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="documents", to="leases.lease"),
                ),
                (
                    "lease_history",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="documents",
                        to="leases.leaserenewal",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="lease_documents_uploaded",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-uploaded_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="LeaseFileShareLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(default=leases.models.lease_file_share_token, max_length=64, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="lease_file_share_links_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="share_links",
                        to="leases.leasedocument",
                    ),
                ),
                (
                    "lease",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="file_share_links", to="leases.lease"),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="leasedocument",
            index=models.Index(fields=["lease", "is_active", "uploaded_at"], name="leases_leas_lease_i_fc3210_idx"),
        ),
        migrations.AddIndex(
            model_name="leasedocument",
            index=models.Index(fields=["category"], name="leases_leas_categor_9efe37_idx"),
        ),
        migrations.AddIndex(
            model_name="leasefilesharelink",
            index=models.Index(fields=["token", "expires_at", "is_active"], name="leases_leas_token_8c2884_idx"),
        ),
    ]
