from django.db import migrations, models
import django.db.models.deletion


def preserve_existing_staff_property_access(apps, schema_editor):
    Account = apps.get_model("accounts", "Account")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_type, _ = ContentType.objects.get_or_create(app_label="accounts", model="account")
    permission, _ = Permission.objects.get_or_create(
        content_type=content_type,
        codename="access_all_properties",
        defaults={"name": "Can access all properties"},
    )
    through = Account.user_permissions.through
    rows = [
        through(account_id=account_id, permission_id=permission.pk)
        for account_id in Account.objects.filter(is_staff=True, is_active=True).values_list("pk", flat=True)
    ]
    through.objects.bulk_create(rows, ignore_conflicts=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_alter_account_whatsapp_number"),
        ("properties", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="account",
            options={
                "permissions": [
                    ("manage_roles", "Can manage user roles and groups"),
                    ("grant_account_permissions", "Can grant account permissions"),
                    ("manage_property_access", "Can manage staff property access"),
                    ("impersonate_account", "Can impersonate other accounts"),
                    ("assign_staff_status", "Can assign staff status"),
                    ("access_all_properties", "Can access all properties"),
                ]
            },
        ),
        migrations.CreateModel(
            name="AccountPropertyAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="property_access", to="accounts.account")),
                ("property", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="account_access", to="properties.property")),
            ],
            options={"ordering": ("property__property_name", "property_id")},
        ),
        migrations.AddConstraint(
            model_name="accountpropertyaccess",
            constraint=models.UniqueConstraint(fields=("account", "property"), name="accounts_account_property_access_unique"),
        ),
        migrations.RunPython(preserve_existing_staff_property_access, noop_reverse),
    ]
