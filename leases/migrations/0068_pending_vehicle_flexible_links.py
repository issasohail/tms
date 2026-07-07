import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0067_rename_leases_leas_lease_i_veh_idx_leases_leas_lease_i_d66a32_idx_and_more"),
        ("tenants", "0018_tenant_family_member_adults_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pendingleasevehiclesubmission",
            name="lease",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="pending_vehicle_submissions",
                to="leases.lease",
            ),
        ),
        migrations.AddField(
            model_name="pendingleasevehiclesubmission",
            name="pending_tenant_submission",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="pending_vehicle_submissions",
                to="tenants.tenantregistrationsubmission",
            ),
        ),
        migrations.AddField(
            model_name="pendingleasevehiclesubmission",
            name="source",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddIndex(
            model_name="pendingleasevehiclesubmission",
            index=models.Index(
                fields=["tenant", "status", "submitted_at"],
                name="leases_pveh_tenant_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="pendingleasevehiclesubmission",
            index=models.Index(
                fields=["pending_tenant_submission", "status", "submitted_at"],
                name="leases_pveh_reg_status_idx",
            ),
        ),
    ]
