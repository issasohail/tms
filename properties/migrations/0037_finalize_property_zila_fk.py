from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0036_map_property_zila_tehsil"),
    ]

    operations = [
        migrations.RenameField(
            model_name="property",
            old_name="zila",
            new_name="zila_legacy",
        ),
        migrations.RenameField(
            model_name="property",
            old_name="zila_fk",
            new_name="zila",
        ),
        migrations.AlterField(
            model_name="property",
            name="zila_legacy",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text="Original textual Zila value retained for migration audit.",
                max_length=120,
            ),
        ),
    ]

