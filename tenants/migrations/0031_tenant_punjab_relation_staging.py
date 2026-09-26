import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("punjab_estamp", "0003_seed_verified_configuration"),
        ("tenants", "0030_temporary_registration_upload"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="relation_fk",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tenants",
                to="punjab_estamp.punjabestamprelation",
            ),
        ),
    ]
