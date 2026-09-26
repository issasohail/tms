import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0037_finalize_property_zila_fk"),
        ("tenants", "0032_map_tenant_punjab_relation"),
    ]

    operations = [
        migrations.AddField(
            model_name="property",
            name="owner_tenant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owned_properties",
                to="tenants.tenant",
            ),
        ),
        migrations.AddField(
            model_name="property",
            name="caretaker_tenant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="caretaken_properties",
                to="tenants.tenant",
            ),
        ),
    ]
