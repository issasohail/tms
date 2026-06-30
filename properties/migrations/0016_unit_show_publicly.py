from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("properties", "0015_alter_propertymedia_file_alter_unitmedia_file"),
    ]

    operations = [
        migrations.AddField(
            model_name="unit",
            name="show_publicly",
            field=models.BooleanField(
                default=True,
                help_text="If unchecked, this unit will not appear in WhatsApp/public vacancy lists.",
                verbose_name="Show in Public Vacancy List",
            ),
        ),
    ]
