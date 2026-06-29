# Generated for public maintenance request Phase 1 upload extensions.

import django.core.validators
import maintenance.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0007_maintenancerequest_lease_source_tenant"),
    ]

    operations = [
        migrations.AlterField(
            model_name="maintenancerequestmedia",
            name="file",
            field=models.FileField(
                max_length=255,
                upload_to=maintenance.models.maintenance_media_upload_to,
                validators=[
                    django.core.validators.FileExtensionValidator(
                        [
                            "jpg",
                            "jpeg",
                            "png",
                            "webp",
                            "heic",
                            "heif",
                            "pdf",
                            "mp4",
                            "mov",
                            "webm",
                            "avi",
                            "mkv",
                            "doc",
                            "docx",
                            "xls",
                            "xlsx",
                            "txt",
                            "csv",
                        ]
                    )
                ],
            ),
        ),
    ]
