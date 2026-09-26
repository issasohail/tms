from django.db import migrations, models


def normalize_relation(value):
    return " ".join(str(value or "").upper().replace(".", "").split())


def map_relations(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    Relation = apps.get_model("punjab_estamp", "PunjabEStampRelation")
    relations = {
        normalize_relation(relation.name): relation.pk
        for relation in Relation.objects.all()
    }
    for tenant in Tenant.objects.all().iterator():
        relation_id = relations.get(normalize_relation(tenant.relation))
        if relation_id:
            tenant.relation_fk_id = relation_id
            tenant.save(update_fields=["relation_fk"])


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0031_tenant_punjab_relation_staging"),
    ]

    operations = [
        migrations.RunPython(map_relations, migrations.RunPython.noop),
        migrations.RenameField(
            model_name="tenant",
            old_name="relation",
            new_name="relation_legacy",
        ),
        migrations.RenameField(
            model_name="tenant",
            old_name="relation_fk",
            new_name="relation",
        ),
        migrations.AlterField(
            model_name="tenant",
            name="relation_legacy",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text="Original textual relation retained for migration audit.",
                max_length=40,
            ),
        ),
    ]
