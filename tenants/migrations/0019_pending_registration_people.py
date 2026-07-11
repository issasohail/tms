from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("tenants", "0018_tenant_family_member_adults_and_more")]
    operations = [
        migrations.CreateModel(
            name="PendingRegistrationPerson",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("family_member","Family Member"),("proposer","Proposer"),("seconder","Seconder"),("witness1","Witness 1"),("witness2","Witness 2")], max_length=20)),
                ("relationship", models.CharField(blank=True, max_length=30)),
                ("relationship_type_id", models.PositiveIntegerField(blank=True, null=True)),
                ("first_name", models.CharField(blank=True, max_length=50)),
                ("last_name", models.CharField(blank=True, max_length=50)),
                ("father_husband_name", models.CharField(blank=True, max_length=120)),
                ("cnic", models.CharField(blank=True, max_length=30)),
                ("cnic_digits", models.CharField(blank=True, db_index=True, max_length=13)),
                ("date_of_birth", models.DateField(blank=True, null=True)),
                ("phone", models.CharField(blank=True, max_length=20)),
                ("address", models.TextField(blank=True)),
                ("photo", models.ImageField(blank=True, null=True, upload_to="tenants/registration_people/")),
                ("cnic_front", models.ImageField(blank=True, null=True, upload_to="tenants/registration_people/")),
                ("cnic_back", models.ImageField(blank=True, null=True, upload_to="tenants/registration_people/")),
                ("proposed_updates", models.JSONField(blank=True, default=dict)),
                ("field_decisions", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("pending","Pending"),("approved","Approved"),("review_later","Review Later"),("rejected","Rejected"),("processed","Processed")], default="pending", max_length=20)),
                ("processing_result", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("matched_tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pending_registration_roles", to="tenants.tenant")),
                ("processed_tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="processed_registration_roles", to="tenants.tenant")),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_pending_registration_people", to=settings.AUTH_USER_MODEL)),
                ("submission", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pending_people", to="tenants.tenantregistrationsubmission")),
            ],
            options={"ordering":["submission_id","role","id"]},
        ),
        migrations.AddIndex(model_name="pendingregistrationperson", index=models.Index(fields=["submission","role","status"], name="tenants_pen_submiss_6f4b14_idx")),
        migrations.AddIndex(model_name="pendingregistrationperson", index=models.Index(fields=["cnic_digits"], name="tenants_pen_cnic_di_1e64d4_idx")),
    ]
