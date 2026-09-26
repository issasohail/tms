from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0034_unit_iesco_bill_active"),
        ("punjab_estamp", "0001_configuration_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="property",
            name="zila_fk",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="properties",
                to="punjab_estamp.punjabestampdistrict",
                verbose_name="Zila / District",
            ),
        ),
        migrations.AddField(
            model_name="property",
            name="tehsil",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="properties",
                to="punjab_estamp.punjabestamptehsil",
            ),
        ),
    ]

