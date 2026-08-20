from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("maintenance", "0009_maintenance_media_source_metadata"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="maintenancerequestmedia",
            index=models.Index(
                fields=["request", "is_active"],
                name="maint_media_req_active_idx",
            ),
        ),
    ]
