from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0019_pending_registration_people")]
    operations = [
        migrations.AddField(
            model_name="tenant",
            name="employer_address",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
