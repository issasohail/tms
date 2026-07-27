from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0018_pendingwhatsappmedia_processing"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pendingwhatsappmedia",
            name="target_kind",
            field=models.CharField(
                blank=True,
                choices=[
                    ("property_photo", "Building / Property Photo"),
                    ("unit_photo", "Unit Photo"),
                    ("lease_photo", "Lease Gallery Photo"),
                    ("lease_document", "Lease Document"),
                    ("lease_estamp", "Lease E-Stamp Paper"),
                ],
                max_length=24,
            ),
        ),
    ]
