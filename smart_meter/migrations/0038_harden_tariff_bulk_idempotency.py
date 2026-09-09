import uuid

from django.db import migrations, models


def populate_bulk_item_submission_keys(apps, schema_editor):
    BulkItem = apps.get_model("smart_meter", "MeterTariffBulkItem")
    for item in BulkItem.objects.filter(submission_key__isnull=True).only("pk").iterator():
        BulkItem.objects.filter(pk=item.pk).update(submission_key=uuid.uuid4())


class Migration(migrations.Migration):

    dependencies = [
        ("smart_meter", "0037_correct_invalid_tariff_capabilities"),
    ]

    operations = [
        migrations.AddField(
            model_name="metertariffbulkitem",
            name="is_processing",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="metertariffbulkitem",
            name="processing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="metertariffbulkitem",
            name="submission_key",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(
            populate_bulk_item_submission_keys,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="metertariffbulkitem",
            name="submission_key",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
