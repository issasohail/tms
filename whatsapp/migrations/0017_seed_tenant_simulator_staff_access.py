from django.conf import settings
from django.db import migrations


SIMULATOR_GROUP = "Tenant Simulator"
INITIAL_USERNAMES = ("admin", "fida")
MIGRATION_NOTE = "Created by WhatsApp tenant simulator access migration 0017."


def add_initial_simulator_access(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    Account = apps.get_model(app_label, model_name)
    Group = apps.get_model("auth", "Group")
    Property = apps.get_model("properties", "Property")
    StaffPropertyAccess = apps.get_model("whatsapp", "WhatsAppStaffPropertyAccess")

    simulator_group, _created = Group.objects.get_or_create(name=SIMULATOR_GROUP)
    users = Account.objects.filter(
        username__in=INITIAL_USERNAMES,
        is_active=True,
        is_staff=True,
    )
    properties = list(Property.objects.all())
    for user in users:
        user.groups.add(simulator_group)
        for property_obj in properties:
            access, created = StaffPropertyAccess.objects.get_or_create(
                staff_user_id=user.pk,
                property_id=property_obj.pk,
                defaults={"is_active": True, "notes": MIGRATION_NOTE},
            )
            if not created and not access.is_active:
                access.is_active = True
                access.save(update_fields=["is_active"])


def remove_initial_simulator_access(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    Account = apps.get_model(app_label, model_name)
    Group = apps.get_model("auth", "Group")
    StaffPropertyAccess = apps.get_model("whatsapp", "WhatsAppStaffPropertyAccess")

    simulator_group = Group.objects.filter(name=SIMULATOR_GROUP).first()
    users = Account.objects.filter(username__in=INITIAL_USERNAMES)
    if simulator_group:
        for user in users:
            user.groups.remove(simulator_group)
        if not simulator_group.user_set.exists() and not simulator_group.permissions.exists():
            simulator_group.delete()
    StaffPropertyAccess.objects.filter(notes=MIGRATION_NOTE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0016_whatsapp_role_simulator_media_batches"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            add_initial_simulator_access,
            remove_initial_simulator_access,
        ),
    ]
