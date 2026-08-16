import datetime

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_globalsettings_late_fee_automation_start_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalsettings",
            name="late_fee_reminder_time",
            field=models.TimeField(
                default=datetime.time(9, 0),
                help_text="Pakistan time when automatic late-fee reminders are checked.",
            ),
        ),
        migrations.AddField(
            model_name="globalsettings",
            name="monthly_billing_time",
            field=models.TimeField(
                default=datetime.time(9, 5),
                help_text="Pakistan time when the automatic monthly billing check runs.",
            ),
        ),
    ]
