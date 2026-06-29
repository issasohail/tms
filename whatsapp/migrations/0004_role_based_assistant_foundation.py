# Generated manually for WhatsApp role-based assistant foundation.

import django.db.models.deletion
import whatsapp.models
from django.conf import settings
from django.db import migrations, models


ROLE_GROUP_NAMES = [
    "Guest",
    "Tenant",
    "Staff",
    "Billing Staff",
    "Property Manager",
    "Administrator",
]


def create_role_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in ROLE_GROUP_NAMES:
        Group.objects.get_or_create(name=name)


def remove_role_groups(apps, schema_editor):
    # Keep role groups on rollback because production users may already be assigned.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0015_alter_propertymedia_file_alter_unitmedia_file"),
        ("tenants", "0017_alter_tenant_updated_at"),
        ("leases", "0047_defaultclause_category_and_more"),
        ("whatsapp", "0003_ai_assistant_pending_workflow"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappconversation",
            name="mode_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="whatsappconversation",
            name="selected_mode",
            field=models.CharField(blank=True, choices=[("guest", "Guest"), ("tenant", "Tenant"), ("staff", "Staff")], max_length=20),
        ),
        migrations.AddField(
            model_name="whatsappconversation",
            name="staff_user",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="whatsapp_conversations", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="whatsappconversation",
            name="tenant",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="whatsapp_conversations", to="tenants.tenant"),
        ),
        migrations.CreateModel(
            name="TrustedDeviceRegistry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_type", models.CharField(choices=[("tenant", "Tenant"), ("staff", "Staff"), ("guest", "Guest")], default="guest", max_length=20)),
                ("phone_number", models.CharField(blank=True, db_index=True, max_length=32)),
                ("whatsapp_id", models.CharField(blank=True, db_index=True, max_length=80)),
                ("device_name", models.CharField(blank=True, max_length=120)),
                ("mac_address", models.CharField(blank=True, max_length=80, null=True)),
                ("browser_fingerprint", models.CharField(blank=True, db_index=True, max_length=160, null=True)),
                ("operating_system", models.CharField(blank=True, max_length=120)),
                ("browser", models.CharField(blank=True, max_length=120)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.TextField(blank=True)),
                ("first_seen", models.DateTimeField(auto_now_add=True)),
                ("last_seen", models.DateTimeField(auto_now=True)),
                ("otp_verified", models.BooleanField(default=False)),
                ("trusted_status", models.CharField(choices=[("pending", "Pending"), ("active", "Active"), ("blocked", "Blocked")], default="pending", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("staff_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="tenants.tenant")),
            ],
            options={
                "ordering": ["-last_seen"],
                "indexes": [
                    models.Index(fields=["phone_number", "last_seen"], name="whatsapp_tr_phone_n_28ceed_idx"),
                    models.Index(fields=["trusted_status", "last_seen"], name="whatsapp_tr_trusted_f49af3_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="WhatsAppExternalLinkToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(default=whatsapp.models.whatsapp_external_link_token, max_length=64, unique=True)),
                ("link_type", models.CharField(choices=[("tenant_registration", "Tenant registration form"), ("lease_creation", "Lease creation form"), ("agreement_view", "Agreement view link"), ("agreement_edit", "Agreement edit link"), ("invoice_view", "Invoice view link"), ("invoice_pdf", "Invoice PDF link"), ("payment_receipt_upload", "Payment receipt upload"), ("maintenance_photo_upload", "Maintenance photo upload")], max_length=40)),
                ("phone_number", models.CharField(blank=True, db_index=True, max_length=32)),
                ("target_app_label", models.CharField(blank=True, max_length=80)),
                ("target_model", models.CharField(blank=True, max_length=80)),
                ("target_object_id", models.PositiveBigIntegerField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("is_active", models.BooleanField(default=True)),
                ("staff_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="whatsapp_external_links_created", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="tenants.tenant")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["token", "expires_at", "is_active"], name="whatsapp_wh_token_e602d4_idx"),
                    models.Index(fields=["link_type", "created_at"], name="whatsapp_wh_link_ty_b9c462_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="WhatsAppStaffPropertyAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("property", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="whatsapp_staff_access", to="properties.property")),
                ("staff_user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="whatsapp_property_access", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["staff_user__username", "property__property_name"],
                "unique_together": {("staff_user", "property")},
            },
        ),
        migrations.CreateModel(
            name="WhatsAppStaffActionLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone_number", models.CharField(blank=True, db_index=True, max_length=32)),
                ("role_name", models.CharField(blank=True, max_length=80)),
                ("selected_mode", models.CharField(blank=True, max_length=20)),
                ("action", models.CharField(max_length=120)),
                ("status", models.CharField(choices=[("allowed", "Allowed"), ("blocked", "Blocked"), ("pending", "Pending")], default="pending", max_length=20)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("lease", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="leases.lease")),
                ("property", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="whatsapp_staff_action_logs", to="properties.property")),
                ("staff_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="whatsapp_staff_action_logs", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="tenants.tenant")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["staff_user", "created_at"], name="whatsapp_wh_staff_u_e11692_idx"),
                    models.Index(fields=["phone_number", "created_at"], name="whatsapp_wh_phone_n_617c17_idx"),
                    models.Index(fields=["status", "created_at"], name="whatsapp_wh_status_e15819_idx"),
                ],
            },
        ),
        migrations.RunPython(create_role_groups, remove_role_groups),
    ]
