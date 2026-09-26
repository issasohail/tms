from django.db import migrations


def digits(value):
    return "".join(character for character in str(value or "") if character.isdigit())


def backfill_property_tenant_links(apps, schema_editor):
    Property = apps.get_model("properties", "Property")
    Tenant = apps.get_model("tenants", "Tenant")

    tenants_by_cnic = {}
    duplicate_cnics = set()
    for tenant in Tenant.objects.exclude(cnic_digits="").iterator():
        cnic = digits(tenant.cnic_digits)
        if not cnic:
            continue
        if cnic in tenants_by_cnic:
            duplicate_cnics.add(cnic)
        else:
            tenants_by_cnic[cnic] = tenant.pk
    for cnic in duplicate_cnics:
        tenants_by_cnic.pop(cnic, None)

    for property_obj in Property.objects.all().iterator():
        updates = []
        owner_id = tenants_by_cnic.get(digits(property_obj.owner_cnic))
        caretaker_id = tenants_by_cnic.get(digits(property_obj.caretaker_cnic))
        if owner_id:
            property_obj.owner_tenant_id = owner_id
            updates.append("owner_tenant")
        if caretaker_id:
            property_obj.caretaker_tenant_id = caretaker_id
            updates.append("caretaker_tenant")
        if updates:
            property_obj.save(update_fields=updates)


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0038_property_tenant_links"),
    ]

    operations = [
        migrations.RunPython(
            backfill_property_tenant_links,
            migrations.RunPython.noop,
        ),
    ]
