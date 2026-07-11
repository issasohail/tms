from django.db import migrations, models


def seed(apps, schema_editor):
    Model = apps.get_model("leases", "AgreementSignatureTemplate")
    Model.objects.get_or_create(name="Default Signature Page")


class Migration(migrations.Migration):
    dependencies = [("leases", "0071_remove_legacy_witness_fields")]
    operations = [
        migrations.CreateModel(
            name="AgreementSignatureTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(default="Default Signature Page", max_length=100, unique=True)),
                ("heading", models.CharField(default="Proposer, Seconder and Witness Signatures", max_length=160)),
                ("proposer_declaration", models.TextField(default="I recommend the applicant for tenancy and confirm the information stated below.")),
                ("seconder_declaration", models.TextField(default="I support the proposal for tenancy and confirm the information stated below.")),
                ("witness_declaration", models.TextField(default="I confirm that I witnessed the execution of this agreement.")),
                ("footer_text", models.TextField(blank=True, default="")),
                ("show_phone", models.BooleanField(default=True)),
                ("show_address", models.BooleanField(default=True)),
                ("show_thumb_impression", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Agreement signature template", "verbose_name_plural": "Agreement signature templates"},
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
