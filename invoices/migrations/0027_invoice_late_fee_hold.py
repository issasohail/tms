from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("invoices", "0026_invoicelatefeereminder_uniq_invoice_late_fee_reminder_number_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="late_fee_hold_reason",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="invoice",
            name="late_fee_hold_until",
            field=models.DateField(
                blank=True,
                db_index=True,
                help_text="Do not send reminders or apply reminder-based late fees through this date.",
                null=True,
            ),
        ),
    ]
