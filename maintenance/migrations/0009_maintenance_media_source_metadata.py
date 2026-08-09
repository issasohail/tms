from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("maintenance", "0008_alter_maintenancerequestmedia_file")]

    operations = [
        migrations.AddField(model_name="maintenancerequestmedia", name="source_pending_media_id", field=models.PositiveBigIntegerField(blank=True, db_index=True, null=True)),
        migrations.AddField(model_name="maintenancerequestmedia", name="source_provider_media_id", field=models.CharField(blank=True, db_index=True, max_length=160)),
        migrations.AddField(model_name="maintenancerequestmedia", name="source_whatsapp_message_id", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="maintenancerequestmedia", name="source_message_timestamp", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="maintenancerequestmedia", name="source_media_type", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="maintenancerequestmedia", name="source_file_size", field=models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="maintenancerequestmedia", name="source_checksum", field=models.CharField(blank=True, db_index=True, max_length=64)),
        migrations.AddField(model_name="maintenancerequestmedia", name="source_order", field=models.PositiveIntegerField(default=0)),
    ]
