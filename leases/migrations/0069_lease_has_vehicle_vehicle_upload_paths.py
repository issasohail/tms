from django.db import migrations, models

import leases.models


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0068_pending_vehicle_flexible_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="lease",
            name="has_vehicle",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Null = not entered, True = tenant has vehicle, False = tenant confirmed no vehicle.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="leasevehicle",
            name="registration_book_photo",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=leases.models.lease_vehicle_book_upload_to,
            ),
        ),
        migrations.AlterField(
            model_name="leasevehicle",
            name="vehicle_photo",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=leases.models.lease_vehicle_photo_upload_to,
            ),
        ),
    ]
