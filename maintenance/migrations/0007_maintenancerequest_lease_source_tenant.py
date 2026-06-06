# Generated for public maintenance request Phase 1.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0047_defaultclause_category_and_more"),
        ("maintenance", "0006_remove_maintenancerequest_building_and_more"),
        ("tenants", "0017_alter_tenant_updated_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="maintenancerequest",
            name="lease",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="maintenance_requests",
                to="leases.lease",
            ),
        ),
        migrations.AddField(
            model_name="maintenancerequest",
            name="source",
            field=models.CharField(
                choices=[("manual", "Manual"), ("public_link", "Public Link")],
                default="manual",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="maintenancerequest",
            name="tenant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="maintenance_requests",
                to="tenants.tenant",
            ),
        ),
    ]
