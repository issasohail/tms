from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

CLAUSE_BODY = """The following persons are authorized to reside at the leased property together with the Tenant. No person other than those listed below shall reside at the property without the prior knowledge and consent of the Second Party/Landlord.

The Tenant shall notify the Second Party/Landlord if any person whose name is not listed below stays at the property for more than two consecutive nights.

{{authorized_occupants_table}}

The Tenant confirms that the information provided above is accurate and agrees to notify the Second Party/Landlord promptly of any addition, removal, or change in the authorized occupants of the property."""


def forwards(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")
    AgreementPlaceholder = apps.get_model("leases", "AgreementPlaceholder")
    number = 27
    DefaultClause.objects.update_or_create(
        clause_number=number, is_active=True,
        defaults={"body": CLAUSE_BODY, "category": "general"},
    )
    placeholders = [
        ("authorized_occupants_table", "Authorized Occupants Table", "HTML table of family members linked to this lease", 210),
        ("authorized_occupants_names", "Authorized Occupants Names", "Comma-separated names of authorized occupants", 211),
        ("authorized_occupants_count", "Authorized Occupants Count", "Number of authorized occupants", 212),
    ]
    for key, label, description, order in placeholders:
        AgreementPlaceholder.objects.update_or_create(
            key=key,
            defaults={"label": label, "description": description, "category": "Lease Parties", "source_type": "system", "resolver_key": key, "is_active": True, "sort_order": order},
        )


def backwards(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")
    AgreementPlaceholder = apps.get_model("leases", "AgreementPlaceholder")
    DefaultClause.objects.filter(clause_number=27, body=CLAUSE_BODY).delete()
    AgreementPlaceholder.objects.filter(key__in=["authorized_occupants_table", "authorized_occupants_names", "authorized_occupants_count"]).delete()


class Migration(migrations.Migration):
    dependencies = [("leases", "0069_lease_has_vehicle_vehicle_upload_paths"), ("tenants", "0019_pending_registration_people")]
    operations = [
        migrations.AddField(model_name="lease", name="proposer", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="leases_proposed", to="tenants.tenant")),
        migrations.AddField(model_name="lease", name="seconder", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="leases_seconded", to="tenants.tenant")),
        migrations.AddField(model_name="lease", name="witness1_tenant", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="leases_witnessed_as_first", to="tenants.tenant")),
        migrations.AddField(model_name="lease", name="witness2_tenant", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="leases_witnessed_as_second", to="tenants.tenant")),
        migrations.AddField(model_name="leaserenewal", name="witness1_tenant", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lease_renewals_witnessed_as_first", to="tenants.tenant")),
        migrations.AddField(model_name="leaserenewal", name="witness2_tenant", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lease_renewals_witnessed_as_second", to="tenants.tenant")),
        migrations.AddField(model_name="agreementversion", name="party_snapshot", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="agreementversion", name="finalized_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(forwards, backwards),
    ]
