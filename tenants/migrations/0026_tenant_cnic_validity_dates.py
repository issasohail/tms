from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0025_registration_onboarding_safety"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="cnic_expiry_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="cnic_issue_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="temporary_address_urdu",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="tenant",
            name="permanent_address_urdu",
            field=models.TextField(blank=True, default=""),
        ),
    ]
