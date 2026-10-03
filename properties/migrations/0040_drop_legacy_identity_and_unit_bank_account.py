from django.db import migrations, models
import django.db.models.deletion


def preserve_unit_bank_details(apps, schema_editor):
    Unit = apps.get_model("properties", "Unit")
    PropertyBankAccount = apps.get_model("properties", "PropertyBankAccount")
    for unit in Unit.objects.select_related("property").all().iterator():
        details = (getattr(unit, "bank_account_details", "") or "").strip()
        use_property = getattr(unit, "use_property_bank_account", True)
        if use_property or not details:
            continue
        base_label = f"{unit.unit_number} Unit Account"[:80]
        label = base_label
        suffix = 2
        while PropertyBankAccount.objects.filter(property_id=unit.property_id, account_label=label).exists():
            tail = f" {suffix}"
            label = (base_label[:80-len(tail)] + tail)
            suffix += 1
        account = PropertyBankAccount.objects.create(
            property_id=unit.property_id,
            account_label=label,
            additional_details=details,
            is_default=False,
            is_active=True,
            sort_order=90,
        )
        unit.bank_account_id = account.pk
        unit.save(update_fields=["bank_account"])


class Migration(migrations.Migration):
    dependencies = [("properties", "0039_backfill_property_tenant_links")]
    operations = [
        migrations.AddField(
            model_name="unit",
            name="bank_account",
            field=models.ForeignKey(blank=True, help_text="Optional account assigned to this unit. Leave blank to use the property's default account.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="units", to="properties.propertybankaccount"),
        ),
        migrations.RunPython(preserve_unit_bank_details, migrations.RunPython.noop),
        migrations.RemoveField(model_name="unit", name="use_property_bank_account"),
        migrations.RemoveField(model_name="unit", name="bank_account_details"),
        migrations.RemoveField(model_name="property", name="bank_account_details"),
        migrations.RemoveField(model_name="property", name="owner_prefix"),
        migrations.RemoveField(model_name="property", name="owner_name"),
        migrations.RemoveField(model_name="property", name="owner_father_name"),
        migrations.RemoveField(model_name="property", name="relation"),
        migrations.RemoveField(model_name="property", name="owner_phone"),
        migrations.RemoveField(model_name="property", name="owner_address"),
        migrations.RemoveField(model_name="property", name="owner_cnic"),
        migrations.RemoveField(model_name="property", name="owner_photo"),
        migrations.RemoveField(model_name="property", name="caretaker_prefix"),
        migrations.RemoveField(model_name="property", name="caretaker_name"),
        migrations.RemoveField(model_name="property", name="caretaker_father_name"),
        migrations.RemoveField(model_name="property", name="caretaker_relation"),
        migrations.RemoveField(model_name="property", name="caretaker_address"),
        migrations.RemoveField(model_name="property", name="caretaker_cnic"),
        migrations.RemoveField(model_name="property", name="caretaker_phone"),
    ]
