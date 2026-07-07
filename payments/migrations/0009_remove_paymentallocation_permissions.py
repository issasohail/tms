from django.db import migrations


def remove_old_paymentallocation_permissions(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    old_content_types = ContentType.objects.filter(
        app_label="payments",
        model="paymentallocation",
    )
    Permission.objects.filter(content_type__in=old_content_types).delete()
    old_content_types.delete()


def noop_reverse(apps, schema_editor):
    # Do not recreate retired permissions on rollback.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("payments", "0008_rename_paymentallocation_paymentdetail"),
    ]

    operations = [
        migrations.RunPython(remove_old_paymentallocation_permissions, noop_reverse),
    ]
