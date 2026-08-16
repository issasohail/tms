from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_globalsettings_scheduler_times"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalsettings",
            name="late_fee_skip_current_month",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Do not send reminders or apply reminder-based late fees to invoices "
                    "issued in the current calendar month. They become eligible next month."
                ),
            ),
        ),
        migrations.AddField(
            model_name="globalsettings",
            name="late_fee_staff_summary_enabled",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Send the accounts staff member a WhatsApp summary after a late-fee "
                    "run processes reminders."
                ),
            ),
        ),
    ]
