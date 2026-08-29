import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("properties", "0032_photolinkrenewalrequest_publicphotolink_and_more"),
        ("tenants", "0030_temporary_registration_upload"),
    ]

    operations = [
        migrations.AddField(
            model_name="photolinkrenewalrequest",
            name="tenant",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="photo_link_requests", to="tenants.tenant"),
        ),
        migrations.AddField(
            model_name="photolinkrenewalrequest",
            name="interested_in",
            field=models.ManyToManyField(blank=True, related_name="photo_link_requests", to="tenants.tenantinteresttype"),
        ),
    ]
