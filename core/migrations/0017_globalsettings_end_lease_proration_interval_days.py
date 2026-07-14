from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_alter_globalsettings_whatsapp_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalsettings",
            name="end_lease_proration_interval_days",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (1, "Daily"),
                    (5, "5 days"),
                    (7, "Weekly"),
                    (10, "10 days"),
                    (15, "15 days"),
                ],
                default=1,
                help_text="Default billing-day block used when monthly charges are prorated at move-out.",
            ),
        ),
    ]
